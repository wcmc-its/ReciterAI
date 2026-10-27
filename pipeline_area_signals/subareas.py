"""Subarea assignment for AREASIGNAL items (grant and trial texts).

Publications get subareas from ``assign_subtopics``: one HAIKU_MODEL call per
(activity, area) over the area's approved ``hierarchy_draft_<topic>.json``, a
confidence floor applied in Python, and primary = argmax with the seed-density
tie-break. Grant and trial texts reuse that classifier
(``assign_subtopics.classify_activity``) with a kind-aware system prompt
(``prompts.subarea_system_prompt``) on Haiku 5.5 with reasoning off
(``SUBAREA_MODEL``), the same floor (``confidence_floor`` in
``config/thresholds.json``) and the same tie-break (``assign_subtopics.pick_primary``).
No re-scoring: the classifier reads the stored synopsis and the input's title.

Which areas: every ``topic_scores`` entry at or above ``SUBAREA_ASSIGN_FLOOR``
whose topic has a loaded draft (the ``config/hierarchy_draft_coverage.json``
topics), except that grants skip ``GRANT_EXCLUDED_TOPICS`` (SPS never counts a
grant there).

Contract (additive; SPS ignores unknown fields):

- each ``topic_scores`` entry may carry ``primary_subtopic_id``,
  ``subtopic_ids`` and ``subtopic_confidences`` (absent = unassigned);
- the item carries ``subarea_versions`` {topic_id: draft sha256
  (``assign_subtopics._hierarchy_canonical_sha256``)} for every area it was
  assigned under (assigned or validly unassigned), and
  ``subarea_prompt_version``. Every topic in ``subarea_versions`` was assigned
  under that prompt version.

A topic whose draft moved (a hierarchy recompute changes slugs, D-06), or every
topic when the prompt version moved, is stale and re-assigned. A topic that
lost its draft has its subarea fields stripped: a stale slug fails closed
(absent from subarea views), never shown everywhere. A topic whose call failed
loses its version (so it is retried) and keeps its fields only when they were
assigned under the current draft (a prompt-bump refresh hit by an outage does
not wipe valid slugs).

The drafts live in the gitignored ``.planning/phases/04-subtopic-system/``;
``.dockerignore`` re-includes them so the image carries them
(``tests/test_area_signals_subareas_image.py``).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

import assign_subtopics as ast
from pipeline_area_signals import prompts

log = logging.getLogger(__name__)

# An area qualifies for a subarea call at this dense score: the publication
# score floor and the ranking's qualifying-area cut.
SUBAREA_ASSIGN_FLOOR = 0.3

# Areas where SPS never counts a grant (lib/area-signals.ts
# AREA_SIGNAL_GRANT_EXCLUDED_TOPICS; the pilot's Test A found no signal there).
# A grant's subareas in them would never be read, so they are not bought.
# Trials still get them.
GRANT_EXCLUDED_TOPICS = frozenset({
    "research_infrastructure_workforce",
    "translational_clinical_science",
    "infectious_disease_immunology",
    "medical_education_workforce",
    "biostatistics_quantitative_sciences",
    "biomedical_informatics",
})

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFT_DIR = REPO_ROOT / ".planning" / "phases" / "04-subtopic-system"
COVERAGE_PATH = REPO_ROOT / "config" / "hierarchy_draft_coverage.json"

SUBTOPIC_FIELDS = ("primary_subtopic_id", "subtopic_ids", "subtopic_confidences")

# The subarea classifier runs on Haiku 5.5 with reasoning off; publications
# (assign_subtopics.HAIKU_MODEL, Haiku 5.5 since 2026-10-27) run with adaptive
# thinking at the client's default low effort instead. Haiku 5.5 rejects temperature
# ("temperature is deprecated for this model"), so it is omitted, and it reasons
# by default, so thinking is disabled explicitly. On 200 (item, area) pairs this
# agreed with 4.5 on the primary in 85% where both assigned, and a blind judge
# preferred it in 25 of 40 disagreements (8 for 4.5); reasoning on tied with
# off and was slower. The prompts, and the "none fits" (unassigned) outcome,
# are unchanged: an incorrect assignment is the worst case, so nothing forces
# one. Changing any of these three needs a SUBAREA_PROMPT_VERSION bump.
SUBAREA_MODEL = "us.anthropic.claude-haiku-5-5"
SUBAREA_TEMPERATURE = None
SUBAREA_REQUEST_FIELDS = {"thinking": {"type": "disabled"}}

_APPROVED = ("approved", "auto_approved")


@dataclass(frozen=True)
class Hierarchy:
    """One area's approved draft, shaped for the classifier."""
    topic_meta: dict
    subtopic_defs: list
    valid_ids: frozenset
    draft: dict
    sha256: str


@dataclass
class SubareaContext:
    """The drafts this run assigns against."""
    hierarchies: dict = field(default_factory=dict)   # topic_id -> Hierarchy
    missing: list = field(default_factory=list)       # covered topics with no usable draft
    confidence_floor: float = 0.3

    @property
    def version(self) -> Optional[str]:
        """Stamp of everything that decides an item's subareas, or None when no
        draft loaded (assignment off: nothing becomes due for it)."""
        if not self.hierarchies:
            return None
        payload = {
            "prompt_version": prompts.SUBAREA_PROMPT_VERSION,
            "assign_floor": SUBAREA_ASSIGN_FLOOR,
            "confidence_floor": self.confidence_floor,
            "grant_excluded": sorted(GRANT_EXCLUDED_TOPICS),
            "drafts": {t: h.sha256 for t, h in sorted(self.hierarchies.items())},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"{prompts.SUBAREA_PROMPT_VERSION}@{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def load_context(taxonomy: dict, *, draft_dir: Path = DRAFT_DIR,
                 coverage_path: Path = COVERAGE_PATH) -> SubareaContext:
    """Load every covered topic's approved draft. A missing, unapproved, empty or
    unreadable draft is recorded in ``missing`` and skipped, never fatal (unlike
    ``assign_subtopics._load_hierarchy_draft``, which exits the process).

    A covered topic that is not in the taxonomy is skipped without a report:
    the manifest mirrors the draft files, which outlive retired topics, and the
    scorer can never put such a topic in ``topic_scores``.
    """
    covered = json.loads(Path(coverage_path).read_text())["draft_covered_topics"]
    by_id = {t["id"]: t for t in taxonomy.get("topics", [])}
    ctx = SubareaContext(confidence_floor=ast._get_threshold("confidence_floor"))
    for topic_id in covered:
        if topic_id not in by_id:
            log.debug("subareas: covered topic %s is not in the taxonomy; skipped", topic_id)
            continue
        path = Path(draft_dir) / f"hierarchy_draft_{topic_id}.json"
        try:
            draft = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("subareas: no usable draft for %s (%s)", topic_id, exc)
            ctx.missing.append(topic_id)
            continue
        defs = [{"id": s["id"], "label": s.get("label", ""),
                 "description": s.get("description", "")}
                for s in draft.get("subtopics", []) if s.get("id")]
        if draft.get("review_status") not in _APPROVED or not defs:
            log.warning("subareas: draft for %s is unapproved or empty", topic_id)
            ctx.missing.append(topic_id)
            continue
        entry = by_id[topic_id]
        ctx.hierarchies[topic_id] = Hierarchy(
            topic_meta={"id": topic_id, "label": entry.get("label", ""),
                        "description": entry.get("description", "")},
            subtopic_defs=defs,
            valid_ids=frozenset(d["id"] for d in defs),
            draft=draft,
            sha256=ast._hierarchy_canonical_sha256(draft),
        )
    return ctx


def assignable_topics(kind: str, topic_scores: list, ctx: SubareaContext) -> list[str]:
    """Topics of ``topic_scores`` that get a classifier call, in stored order."""
    out = []
    for entry in topic_scores or []:
        topic_id = entry.get("topic_id")
        if topic_id not in ctx.hierarchies:
            continue
        if kind == "grant" and topic_id in GRANT_EXCLUDED_TOPICS:
            continue
        if float(entry.get("score") or 0) >= SUBAREA_ASSIGN_FLOOR:
            out.append(topic_id)
    return out


def stale_topics(item: dict, ctx: SubareaContext) -> list[str]:
    """Assignable topics whose stored assignment is not current."""
    topics = assignable_topics(item.get("kind", ""), item.get("topic_scores"), ctx)
    if item.get("subarea_prompt_version") != prompts.SUBAREA_PROMPT_VERSION:
        return topics
    versions = item.get("subarea_versions") or {}
    return [t for t in topics if versions.get(t) != ctx.hierarchies[t].sha256]


def is_current(item: dict, ctx: SubareaContext) -> bool:
    """True when re-assigning would change nothing (no stale topic, and no
    subarea fields or versions left on a topic that is no longer assignable)."""
    if stale_topics(item, ctx):
        return False
    keep = set(assignable_topics(item.get("kind", ""), item.get("topic_scores"), ctx))
    if set(item.get("subarea_versions") or {}) - keep:
        return False
    return not any(any(f in e for f in SUBTOPIC_FIELDS)
                   for e in item.get("topic_scores") or [] if e.get("topic_id") not in keep)


@dataclass
class AssignResult:
    item: dict
    calls: int = 0
    errors: list = field(default_factory=list)   # [(topic_id, message)]


def _strip(entry: dict) -> None:
    for f in SUBTOPIC_FIELDS:
        entry.pop(f, None)


def assign(item: dict, *, title: str, client, ctx: SubareaContext,
           topics: Optional[list] = None) -> AssignResult:
    """Classify ``item`` (an AREASIGNAL row) into subareas; returns a new item.

    ``topics`` limits the calls (default: every assignable topic, as for a
    freshly scored row); other assignable topics keep what they have. A failed
    call drops that topic's version, so it stays stale and is retried, and
    strips its fields unless they came from the current draft; the error is
    returned, never raised. Calls go through ``client``
    (the run's ``MeteredBedrockClient``), which meters them.
    """
    out = copy.deepcopy(item)
    kind = out.get("kind", "")
    keep = set(assignable_topics(kind, out.get("topic_scores"), ctx))
    todo = set(keep if topics is None else topics) & keep
    versions = {t: v for t, v in (out.get("subarea_versions") or {}).items() if t in keep}
    activity = {"pmid": f"{kind}:{out.get('text_key', '')}", "title": title,
                "synopsis": out.get("synopsis") or title}
    result = AssignResult(item=out)
    for entry in out.get("topic_scores") or []:
        topic_id = entry.get("topic_id")
        if topic_id not in keep:
            _strip(entry)
            continue
        if topic_id not in todo:
            continue
        h = ctx.hierarchies[topic_id]
        result.calls += 1
        try:
            raw, _usage = ast.classify_activity(
                client=client, activity=activity, topic_meta=h.topic_meta,
                subtopic_defs=h.subtopic_defs,
                system_prompt=prompts.subarea_system_prompt(kind),
                model=SUBAREA_MODEL, temperature=SUBAREA_TEMPERATURE,
                additional_model_request_fields=SUBAREA_REQUEST_FIELDS,
            )
        except Exception as exc:  # noqa: BLE001 - one area never aborts the item
            log.warning("subareas: %s %s failed: %s", activity["pmid"], topic_id, exc)
            result.errors.append((topic_id, f"{type(exc).__name__}: {exc}"))
            if versions.pop(topic_id, None) != h.sha256:
                _strip(entry)
            continue
        confidences = ast._filter_valid_assignments(raw, set(h.valid_ids), ctx.confidence_floor)
        _strip(entry)
        if confidences:
            primary, ranked = ast.pick_primary(confidences, h.subtopic_defs, h.draft)
            entry["primary_subtopic_id"] = primary
            entry["subtopic_ids"] = ranked
            entry["subtopic_confidences"] = {
                sid: Decimal(str(round(c, 4))) for sid, c in confidences.items()}
        versions[topic_id] = h.sha256
    out["subarea_versions"] = versions
    out["subarea_prompt_version"] = prompts.SUBAREA_PROMPT_VERSION
    return result
