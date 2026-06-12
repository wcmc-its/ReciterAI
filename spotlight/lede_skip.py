"""#191 brick E lede-skip: reuse a prior published lede for the cold STEP-9
(editorial-lede) stage when a subtopic is unchanged.

Lede generation (``spotlight/lede_generator.generate_lede``, OPUS + the Haiku
critic loop) is the dominant Bedrock line of the cold spotlight backfill. When a
subtopic's durable identity AND its grounding papers are both unchanged
run-to-run, the prior published lede is already correct — regenerating it is
wasted spend (and a chance to churn a good lede). This module decides, BEFORE the
Stage-3 loop runs, which subtopics may reuse their prior lede.

**Dual gate.** A reuse is honored ONLY when BOTH hold:

  GATE 1 — order-invariant overlap verdict (built at startup, carried in the map).
    Identical to the merged relabel-skip: the brick-B Stage-1 ``reason=="overlap"``
    auto-match at or above ``subtopic_reconcile_skip_relabel_overlap_min`` (the
    REUSED threshold key), passing the #204-review order-invariance safety gate
    (matched prior is the cluster's claim-agnostic best overlap AND no other
    in-topic cluster reaches ``ambiguous_min``). Computed by the SHARED core
    ``pipeline_hierarchy.relabel_skip.compute_overlap_reuse_map`` so the gate
    semantics are byte-identical across both skip layers. This proves the durable
    subtopic identity + editorial scope are stable.

  GATE 2 — grounding-PMID set equality (evaluated in-loop, per subtopic, at
    generation time). The load-bearing addition over relabel-skip: a lede is
    grounded in its top-3 papers' synopsis + impact_justification (daily-changing),
    so membership stability alone is NOT sufficient. The current run's top-3
    grounding PMIDs (== what ``generate_lede`` would itself ground on, via the
    SHARED ``lede_generator._filter_and_clamp_papers`` clamp) must EXACTLY equal the
    prior lede's ``lede_grounded_pmids``. If the grounding set gained/lost/swapped
    any PMID, REGENERATE.

Gate 1 lives in the STARTUP map build (one DDB Scan + one S3 GET) — it resolves
the prior lede TEXT and prior grounded PMIDs and stashes them in the map value, but
does NOT decide reuse, because the current run's papers (hence the current grounding
set) are only known per-selection inside the loop. Gate 2 is ``lede_reuse_for``,
called in the Stage-3 loop where the selection's papers are available.

**Correctness.** A lede's content is a pure function of (a) the subtopic's editorial
identity/scope and (b) its top-3 grounding papers' synopsis + impact_justification.
Gate 1 proves (a) is stable (the same order-invariance proof relabel-skip relies on
to safely reuse display_name/short_description). Gate 2 proves (b)'s PMID set is
stable. The gate is membership-equality, not content-equality, by deliberate design
(the spec REJECTS hashing paper content as too strict; a synopsis reword on an
unchanged top-3 set is editorially immaterial for a 22-38-word lede).

**Fail-soft everywhere.** A missing prior value, empty ``lede_grounded_pmids``,
absent durable id, empty store, version mismatch, or any I/O error yields NO reuse
(→ full generation). Over-generate is safe; a wrong reuse is not. With the flag OFF
(default) the map is ``{}`` and every subtopic generates — byte-identical to today.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline_hierarchy.bundler import (
    DEFAULT_AUGMENTED_DIR,
    DEFAULT_THRESHOLDS_PATH,
    build_membership,
    bundle,
)
from pipeline_hierarchy.relabel_skip import (
    SKIP_OVERLAP_MIN_KEY,
    compute_overlap_reuse_map,
)
from pipeline_hierarchy.subtopic_reconcile import (
    ReconcileThresholds,
    load_id_store_snapshot,
)
from spotlight.lede_generator import MIN_PAPERS, _filter_and_clamp_papers

_log = logging.getLogger(__name__)

# config/thresholds.json (+ .schema.json) keys. Lede-skip is OFF by default.
# SKIP_OVERLAP_MIN_KEY is REUSED from relabel-skip (the issue: lede-skip reuses the
# same overlap verdict map) — there is no second overlap threshold.
SKIP_LEDE_ENABLED_KEY = "subtopic_reconcile_skip_lede_enabled"

# The artifacts bucket + key carrying the prior published spotlight artifact. The
# prior lede is the reuse source. Mirrors cli.backfill_spotlight's constants — NOTE
# this is wcmc-reciterai-artifacts, NOT the S3HierarchyClient default
# (wcmc-reciterai-hierarchy) that relabel-skip reads.
ARTIFACTS_BUCKET_NAME = "wcmc-reciterai-artifacts"
LATEST_SPOTLIGHT_KEY = "spotlight/latest/spotlight.json"


@dataclass(frozen=True)
class PriorLede:
    """A lede-reuse candidate: the prior published lede text + its grounded PMIDs
    (normalized to a ``frozenset[str]``) + audit fields. ``grounded_pmids`` is the
    gate-2 comparison target; ``overlap_score`` is the Stage-1 membership overlap
    (audit only)."""

    durable_id: str
    prior_slug: str
    lede: str
    grounded_pmids: frozenset[str]
    overlap_score: float


def load_skip_lede_config(thresholds_path=DEFAULT_THRESHOLDS_PATH) -> tuple[bool, float]:
    """Read ``(enabled, skip_overlap_min)`` from config.

    Mirrors ``relabel_skip.load_skip_config`` exactly but keyed on
    ``SKIP_LEDE_ENABLED_KEY``: absent ``enabled`` → OFF (fail-soft to the safe
    default). When enabled, the REUSED overlap-min key is REQUIRED and range-checked
    — fail-loud. The overlap bar is the SAME
    ``subtopic_reconcile_skip_relabel_overlap_min`` value relabel-skip uses."""
    with open(thresholds_path) as f:
        cfg = json.load(f)
    enabled = bool(cfg.get(SKIP_LEDE_ENABLED_KEY, False))
    if not enabled:
        return False, 1.0
    if SKIP_OVERLAP_MIN_KEY not in cfg:
        raise ValueError(
            f"{thresholds_path}: {SKIP_LEDE_ENABLED_KEY} is true but "
            f"{SKIP_OVERLAP_MIN_KEY} is missing"
        )
    skip_min = float(cfg[SKIP_OVERLAP_MIN_KEY])
    if not 0.0 <= skip_min <= 1.0:
        raise ValueError(
            f"{thresholds_path}: {SKIP_OVERLAP_MIN_KEY}={skip_min} outside [0, 1]"
        )
    return True, skip_min


def build_prior_lede_index(prior_spotlight: dict) -> dict[str, dict]:
    """Index a prior published ``spotlight.json`` by ``spotlights[].subtopic_id``
    (the SLUG — ``durable_id`` is NOT reliably present with ``propagate_durable_ids``
    off) → ``{"lede": str, "lede_grounded_pmids": [str, ...]}``.

    Entries with an empty ``subtopic_id``, an empty ``lede``, or empty
    ``lede_grounded_pmids`` are DROPPED — they can never satisfy gate 2 (so carrying
    them would only cost a per-slug fail-soft check downstream). Mirrors
    ``relabel_skip.build_prior_hierarchy_index``."""
    index: dict[str, dict] = {}
    for s in prior_spotlight.get("spotlights") or []:
        sid = s.get("subtopic_id")
        lede = (s.get("lede") or "").strip()
        grounded = s.get("lede_grounded_pmids") or []
        if not sid or not lede or not grounded:
            continue
        index[sid] = {"lede": lede, "lede_grounded_pmids": list(grounded)}
    return index


def compute_lede_skip_map(
    *,
    membership: dict,
    labels: dict,
    snapshot: dict,
    prior_lede_index: dict,
    reconcile_thresholds: ReconcileThresholds,
    skip_overlap_min: float,
    embed: Optional[Callable] = None,
    arbiter: Optional[Callable] = None,
) -> dict[str, "PriorLede"]:
    """Pure gate-1 decision: ``{current_slug -> PriorLede}``.

    Calls the SHARED ``compute_overlap_reuse_map`` core (same order-invariance gate
    as relabel-skip) to get ``{current_slug -> (durable_id, prior_slug,
    overlap_score)}``, then joins ``prior_lede_index[prior_slug]`` for the prior lede
    text + its grounded PMIDs. A slug whose prior entry is absent (or whose lede /
    grounded PMIDs are empty — those are already dropped by ``build_prior_lede_index``)
    is OMITTED (fail-soft → generate). Gate 2 is NOT applied here (the current run's
    papers are unknown at startup); the map carries the prior grounding set for the
    in-loop comparison."""
    reuse_map = compute_overlap_reuse_map(
        membership=membership,
        labels=labels,
        snapshot=snapshot,
        reconcile_thresholds=reconcile_thresholds,
        skip_overlap_min=skip_overlap_min,
        embed=embed,
        arbiter=arbiter,
    )
    skip_map: dict[str, PriorLede] = {}
    for slug, (durable_id, prior_slug, score) in reuse_map.items():
        prior = prior_lede_index.get(prior_slug)
        if not prior:
            continue  # matched id absent from prior artifact → regenerate (fail-soft)
        lede = (prior.get("lede") or "").strip()
        grounded = prior.get("lede_grounded_pmids") or []
        if not lede or not grounded:
            continue  # no usable prior lede / grounding → regenerate
        skip_map[slug] = PriorLede(
            durable_id=durable_id,
            prior_slug=prior_slug,
            lede=lede,
            grounded_pmids=frozenset(str(p) for p in grounded),
            overlap_score=score,
        )
    return skip_map


def current_grounding_pmids(papers: list) -> frozenset[str]:
    """Gate-2 helper: the EXACT top-MAX_PAPERS-by-impact PMID set ``generate_lede``
    would itself ground on, via the SHARED ``_filter_and_clamp_papers`` clamp (so the
    gate can never diverge from the generator). PMIDs normalized to ``str``."""
    return frozenset(str(p.pmid) for p in _filter_and_clamp_papers(papers))


def lede_reuse_for(
    skip_map: dict[str, "PriorLede"], subtopic_id: str, papers: list
) -> Optional["PriorLede"]:
    """In-loop gate-2 evaluator. Return the ``PriorLede`` for ``subtopic_id`` ONLY
    when the current run's top-3 grounding PMIDs (non-empty, ``>= MIN_PAPERS``) EXACTLY
    equal the prior lede's grounded set; else None (absent / grounding drifted →
    generate). Pure, no I/O."""
    pr = skip_map.get(subtopic_id)
    if pr is None:
        return None
    cur = current_grounding_pmids(papers)
    if not cur or len(cur) < MIN_PAPERS:
        return None
    if cur != pr.grounded_pmids:
        return None
    return pr


def _load_prior_spotlight_index(s3_client: Any) -> dict[str, dict]:
    """Read ``spotlight/latest/spotlight.json`` from the artifacts bucket and index it
    by slug. The latest spotlight.json IS the prior artifact directly (mirrors
    ``_run_regen_only``, which reads ``LATEST_SPOTLIGHT_KEY`` — not a versioned key).
    Returns ``{}`` when the artifact carries no usable ledes."""
    prior = json.loads(s3_client.get_object_bytes(LATEST_SPOTLIGHT_KEY))
    return build_prior_lede_index(prior)


def load_lede_skip_map(
    *,
    table: Any = None,
    s3_client: Any = None,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    thresholds_path=DEFAULT_THRESHOLDS_PATH,
) -> dict[str, "PriorLede"]:
    """I/O shell: gate on config, assemble inputs, return the lede-skip map.

    Returns ``{}`` (→ full generation) when the flag is OFF, the durable-id store is
    empty (first reconciled run), there is no prior published spotlight with usable
    ledes, or ANY error occurs. Read-only (a Scan of the store + ONE S3 GET); never
    raises; never writes. ``table`` / ``s3_client`` are injectable for offline tests.

    ``s3_client`` must point at the ``wcmc-reciterai-artifacts`` bucket (the spotlight
    artifact lives there, NOT in the S3HierarchyClient default hierarchy bucket); the
    default constructed below passes ``bucket=ARTIFACTS_BUCKET_NAME``."""
    try:
        enabled, skip_min = load_skip_lede_config(thresholds_path)
    except Exception as exc:  # noqa: BLE001 — config blip must not break generation
        _log.warning("lede skip-config load failed (%s); full generation", exc)
        return {}
    if not enabled:
        return {}
    try:
        # Non-strict bundle: the subtopic ID set + labels. The seed_pmids (read from
        # the augmented files) are byte-identical to what publish step 10 will see, so
        # the overlap verdicts match the real reconcile.
        hierarchy = bundle(augmented_dir=augmented_dir, strict=False)
        membership = build_membership(
            hierarchy,
            hierarchy_version="(lede-skip-prepass)",
            augmented_dir=augmented_dir,
        )
        labels = {
            s["id"]: s.get("label", "")
            for tval in hierarchy["topics"].values()
            for s in tval["subtopics"]
            if s.get("id")
        }
        if table is None:
            from utils.dynamodb_helpers import get_table

            table = get_table()
        snapshot = load_id_store_snapshot(table)
        if not snapshot:
            _log.info("lede skip: durable-id store empty; full generation")
            return {}
        if s3_client is None:
            from utils.s3_client import S3HierarchyClient

            s3_client = S3HierarchyClient(bucket=ARTIFACTS_BUCKET_NAME)
        prior_lede_index = _load_prior_spotlight_index(s3_client)
        if not prior_lede_index:
            _log.info("lede skip: no prior published ledes; full generation")
            return {}
        recon_thresholds = ReconcileThresholds.from_config(thresholds_path)
        skip_map = compute_lede_skip_map(
            membership=membership,
            labels=labels,
            snapshot=snapshot,
            prior_lede_index=prior_lede_index,
            reconcile_thresholds=recon_thresholds,
            skip_overlap_min=skip_min,
        )
        _log.info(
            "lede skip: %d/%d subtopics eligible to reuse prior lede "
            "(overlap >= %.2f; gate 2 grounding-PMID check applied in-loop)",
            len(skip_map),
            len(membership.get("subtopics") or {}),
            skip_min,
        )
        return skip_map
    except Exception as exc:  # noqa: BLE001 — any failure → safe full generation
        _log.warning("lede skip-map build failed (%s); full generation", exc)
        return {}
