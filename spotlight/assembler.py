"""Spotlight artifact assembler.

Composes the spotlight.json shape from upstream pipeline output
(``ValidatedLede`` + ``PoolEntry`` + ``SubtopicMeta`` enrichment).
Implements SPOT-09 (per-paper author payload) and produces an artifact
that validates against ``docs/spotlight.schema.json`` (Plan 06-06 Task 1).

Naming contract (CLAUDE.md "Conventions / Naming"):

  Python attribute  →  JSON key
  -----------------    --------
  person_identifier →  personIdentifier
  display_name      →  displayName

The ``_author_to_json`` helper is the SINGLE point in the codebase where
this snake_case → camelCase mapping happens for the published artifact.
The legacy ReCiter person-id prefix is forbidden anywhere in this module
per the naming rule; that prefix lives only inside DynamoDB sort keys
(``load_dynamodb.py``) and is not part of the runtime/Python surface.

Provenance policy (CONTEXT decision Q2.5): per-spotlight ``attempts`` /
review-trace data lives in the DynamoDB SPOTLIGHT_REVIEW# partition
only. The published artifact carries the lede text and paper payload; it
does NOT carry retry transcripts. ``build_artifact`` therefore drops
``ValidatedLede.attempts`` on the floor.

Status filter (defense-in-depth, T-06-06-06): ``build_artifact`` raises
``ValueError`` if any input ``ValidatedLede`` has ``status != "pass"``.
The publish path in Plan 06-07 filters before calling, but accepting only
publish-clean entries here prevents review-queue ledes from leaking into
the artifact through a bad caller.
"""

from __future__ import annotations

import logging
from typing import Iterable

from spotlight.critic import ValidatedLede
from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Author, Paper, PoolEntry
from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)


SPOTLIGHT_VERSION = "spotlight_v1"
DEFAULT_TAXONOMY_VERSION = "taxonomy_v2"




# ---------------------------------------------------------------------------
# JSON shape helpers — single point of snake_case → camelCase translation.
# ---------------------------------------------------------------------------


def _author_to_json(a: Author) -> dict:
    """Serialize an Author dataclass to its JSON artifact shape.

    This is the ONLY place in the codebase that maps the Python
    ``person_identifier`` attribute to the JSON ``personIdentifier`` key.
    SPS uses ``personIdentifier`` as the photo-store join key; lowercasing
    or snake_casing the JSON key would break headshot resolution.
    """
    return {
        "personIdentifier": a.person_identifier,
        "displayName": a.display_name,
        "position": a.position,
    }


def _paper_to_json(p: Paper) -> dict:
    """Serialize a Paper dataclass to its JSON artifact shape.

    Drops impact_score / impact_justification / synopsis: the published
    artifact carries only the editorial-facing fields enumerated in
    docs/spotlight.schema.json's ``Paper`` $def. Pipeline-internal
    metadata stays in DynamoDB.
    """
    return {
        "pmid": p.pmid,
        "title": p.title,
        "journal": p.journal,
        "year": p.year,
        "first_author": _author_to_json(p.first_author),
        "last_author": _author_to_json(p.last_author),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_artifact(
    selected: list[ValidatedLede],
    pool: list[PoolEntry],
    subtopic_metadata: dict[str, SubtopicMeta],
    paper_metadata: dict[str, Paper],
    taxonomy_version: str = DEFAULT_TAXONOMY_VERSION,
) -> dict:
    """Compose the spotlight.json artifact dict.

    Parameters
    ----------
    selected
        ValidatedLede entries whose status is ``"pass"``. Order is
        preserved in the output ``spotlights`` array. Any entry with a
        non-pass status raises ``ValueError`` (defense-in-depth against a
        publisher that forgets to filter the review queue out).
    pool
        Top-50 PoolEntry rows from the rotation selector's input pool.
        Length is preserved in ``pool_snapshot``; the schema bounds it
        between 1 and 50.
    subtopic_metadata
        Lookup ``subtopic_id`` → SubtopicMeta. Plan 06-04's slim shape
        (label, description, parent_topic_label) is sufficient. Plan
        06-07 may pass a richer object with ``display_name`` /
        ``short_description`` attributes; ``getattr`` falls back to the
        canonical label when those D-19 UI fields are absent.
    paper_metadata
        Lookup ``pmid`` → Paper for the papers carried into each lede.
        Keys must include every PMID enumerated in
        ``ValidatedLede.papers_used`` for the selected entries.
    taxonomy_version
        Stamped into the artifact root so consumers can pin against a
        specific hierarchy snapshot. Defaults to
        ``DEFAULT_TAXONOMY_VERSION``.

    Returns
    -------
    dict
        The full artifact dict matching ``docs/spotlight.schema.json``.
        The caller (Plan 06-07 ``backfill_spotlight.py``) is responsible
        for serializing to JSON and uploading to S3.

    Raises
    ------
    ValueError
        If any entry in ``selected`` has ``status != "pass"``.
    """
    bad = [v.subtopic_id for v in selected if v.status != "pass"]
    if bad:
        raise ValueError(
            f"build_artifact requires all entries status=='pass'; found "
            f"non-pass entries for subtopic_ids: {bad}"
        )

    selected_ids = {v.subtopic_id for v in selected}

    spotlight_entries: list[dict] = []
    for vlede in selected:
        meta = subtopic_metadata[vlede.subtopic_id]
        papers = [paper_metadata[pmid] for pmid in vlede.papers_used]
        # D-19: display_name / short_description are UI-only. Fall back
        # to the canonical label / empty string if a slim SubtopicMeta
        # is passed (Plan 06-04's NamedTuple has no display_name field).
        spotlight_entries.append(
            {
                "subtopic_id": vlede.subtopic_id,
                "label": meta.label,
                "display_name": getattr(meta, "display_name", meta.label),
                "short_description": getattr(meta, "short_description", ""),
                "parent_topic": vlede.parent_topic,
                "lede": vlede.lede,
                "papers": [_paper_to_json(p) for p in papers],
            }
        )

    pool_snapshot_entries: list[dict] = []
    for entry in pool:
        pool_snapshot_entries.append(
            {
                "subtopic_id": entry.subtopic_id,
                "pool_score": round(entry.pool_score, 4),
                "parent_topic": entry.parent_topic,
                "was_selected": entry.subtopic_id in selected_ids,
            }
        )

    artifact = {
        "version": SPOTLIGHT_VERSION,
        "generated_at": now_iso(),
        "taxonomy_version": taxonomy_version,
        "spotlights": spotlight_entries,
        "pool_snapshot": pool_snapshot_entries,
    }

    logger.info(
        "build_artifact composed %d spotlight entries and %d pool snapshot rows",
        len(spotlight_entries),
        len(pool_snapshot_entries),
    )
    return artifact


__all__ = [
    "DEFAULT_TAXONOMY_VERSION",
    "SPOTLIGHT_VERSION",
    "build_artifact",
]
