"""#191 brick E: reconcile skip-logic for the cold-path relabel stage.

When a freshly re-clustered subtopic auto-matches a prior durable id with HIGH
membership overlap (low drift), its prior published ``display_name`` /
``short_description`` are already correct — regenerating them via Sonnet is wasted
spend (relabel is the dominant ~$108 cold-run Bedrock line, ``docs/cost-model.md``)
*and* a chance to churn a good label. This module decides, BEFORE the relabel stage
runs, which subtopics to skip and what prior values to reuse.

Design: ``docs/subtopic-lifecycle-and-evolution.md`` §6 ("Schedule the incremental
cold path") + §Phase-2; issue #204; plan
``.planning/issues/0009-issue-191-brick-e-reconcile-skip.md``.

**Determinism & the order-invariance gate.** The skip verdict starts from the
existing brick-B reconcile's Stage-1 ``overlap`` auto-match, computed one stage early
against the SAME durable-id store snapshot the publish-step-10 reconcile reads. We
act ONLY on ``reason == "overlap"`` (pure set math, Bedrock-free) at or above a skip
threshold strictly higher than the auto-match bar.

But a bare ``overlap`` verdict is NOT by itself bit-identical to step 10. The pre-pass
forces the LLM off + a stub embedder (no live AWS), while step 10 runs Stages 2/3 — and
``precompute`` processes clusters by their claim-agnostic best overlap, with a greedy
claimed-set. So a cluster ordered early via a *different* prior can stage-2/3-claim a
prior in the live run that this LLM-disabled pre-pass leaves free, then auto-matched to
a *later* cluster — diverging the two runs and binding the wrong sibling's label to a
durable id step 10 never stamps (the #204 review caught this; the earlier "auto-match
is always processed first" argument conflated a cluster's order-key with the contested
prior's overlap and was wrong).

So a reuse is honored ONLY when the match is provably order-INVARIANT:
  (1) the matched prior is the cluster's **claim-agnostic best** overlap (the cluster is
      never diverted from a stronger prior), AND
  (2) **no other in-topic cluster's** overlap with that prior reaches the ambiguous band
      — so no Stage-2/3 claim on it is possible in either run.
Under (1)+(2) the prior is claimable only by this cluster, and it is, in both runs.
Conservative by design (it withholds reuse for contested/ambiguous matches — e.g. near a
split — which simply relabel): over-relabel is safe; a wrong reuse is not.

**Fail-soft everywhere.** A missing prior value, version mismatch, empty store, or
any I/O error yields NO skip for that cluster (or none at all) → it relabels as
today. Over-relabel is safe; a wrong reuse is not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline_hierarchy.bundler import (
    DEFAULT_AUGMENTED_DIR,
    DEFAULT_THRESHOLDS_PATH,
    build_membership,
    bundle,
)
from pipeline_hierarchy.overlap import best_overlap, min_card_overlap
from pipeline_hierarchy.subtopic_id_store import SubtopicMatch
from pipeline_hierarchy.subtopic_reconcile import (
    ReconcileThresholds,
    SubtopicReconciler,
    load_id_store_snapshot,
)

_log = logging.getLogger(__name__)

# config/thresholds.json (+ .schema.json) keys. Skip is OFF by default.
SKIP_ENABLED_KEY = "subtopic_reconcile_skip_relabel_enabled"
SKIP_OVERLAP_MIN_KEY = "subtopic_reconcile_skip_relabel_overlap_min"


@dataclass(frozen=True)
class PriorReuse:
    """A relabel skip decision: reuse the prior published UI text for one subtopic,
    carried forward from the durable id it auto-matched. ``overlap_score`` is the
    Stage-1 membership overlap (for audit/logging)."""

    durable_id: str
    display_name: str
    short_description: str
    overlap_score: float


def _stub_embed(texts):
    """Zero vectors → centroid never clears its cosine threshold; keeps the skip
    pre-pass offline. The AMBIGUOUS band (the only Stage-2 caller) is never skipped,
    so this stub's verdicts are discarded — it exists only to avoid a live Titan call."""
    return [[0.0, 0.0] for _ in texts]


def _stub_arbiter(**_kwargs):
    return {"verdict": "distinct", "durable_id": None}


def load_skip_config(thresholds_path=DEFAULT_THRESHOLDS_PATH) -> tuple[bool, float]:
    """Read ``(enabled, skip_overlap_min)`` from config.

    Absent ``enabled`` → OFF (fail-soft to the safe default, so an older config can
    never accidentally enable skipping). When enabled, the overlap-min key is REQUIRED
    and range-checked — fail-loud, mirroring ``ReconcileThresholds.from_config``."""
    with open(thresholds_path) as f:
        cfg = json.load(f)
    enabled = bool(cfg.get(SKIP_ENABLED_KEY, False))
    if not enabled:
        return False, 1.0
    if SKIP_OVERLAP_MIN_KEY not in cfg:
        raise ValueError(
            f"{thresholds_path}: {SKIP_ENABLED_KEY} is true but "
            f"{SKIP_OVERLAP_MIN_KEY} is missing"
        )
    skip_min = float(cfg[SKIP_OVERLAP_MIN_KEY])
    if not 0.0 <= skip_min <= 1.0:
        raise ValueError(
            f"{thresholds_path}: {SKIP_OVERLAP_MIN_KEY}={skip_min} outside [0, 1]"
        )
    return True, skip_min


def build_prior_hierarchy_index(prior_hierarchy: dict) -> dict[str, dict]:
    """Index a prior published ``hierarchy.json`` by subtopic slug → its UI text.

    ``topics`` is a dict (``generator._sort_topics`` keeps it keyed by topic_id);
    each topic's ``subtopics`` is a list of subtopic dicts carrying ``display_name``
    and ``short_description``."""
    index: dict[str, dict] = {}
    for topic_id, tval in (prior_hierarchy.get("topics") or {}).items():
        for s in (tval or {}).get("subtopics", []):
            sid = s.get("id")
            if not sid:
                continue
            index[sid] = {
                "display_name": s.get("display_name", ""),
                "short_description": s.get("short_description", ""),
                "topic_id": topic_id,
            }
    return index


def compute_relabel_skip_map(
    *,
    membership: dict,
    labels: dict,
    snapshot: dict,
    prior_index: dict,
    reconcile_thresholds: ReconcileThresholds,
    skip_overlap_min: float,
    embed: Optional[Callable] = None,
    arbiter: Optional[Callable] = None,
) -> dict[str, "PriorReuse"]:
    """Pure decision: ``{slug -> PriorReuse}`` for subtopics safe to skip relabel.

    Runs the brick-B reconciler against ``snapshot`` (LLM forced off, stub embedder)
    and keeps a ``reason == "overlap"`` auto-match (``score >= skip_overlap_min``) ONLY
    when it is provably ORDER-INVARIANT vs the live publish-step-10 reconcile (see the
    module docstring): the matched prior is the cluster's claim-agnostic best overlap,
    AND no other in-topic cluster's overlap with that prior reaches ``ambiguous_min``.
    The reuse value is joined from the prior published UI text via the matched durable
    id's PRIOR slug (``snapshot[durable_id]["slug_id"]`` — the slug as published last
    run, even if this run's discovered slug drifted). Fail-soft everywhere: a cluster
    that is contested, diverted, or has no resolvable non-empty prior value is omitted
    (it relabels). Parent-prefix re-validation is the CALLER's job — ``relabel_topic``
    holds the parent label and drops a reuse that would trip the rule under a new parent."""
    subs = membership.get("subtopics") or {}
    if not snapshot or not subs:
        return {}
    # LLM off for the skip pre-pass: we only read deterministic Stage-1 verdicts.
    thresholds = replace(reconcile_thresholds, llm_arbiter_enabled=False)
    reconciler = SubtopicReconciler(
        snapshot,
        thresholds=thresholds,
        embed=embed or _stub_embed,
        arbiter=arbiter or _stub_arbiter,
        verdict_cache={},
    )
    reconciler.precompute(membership=membership, labels=labels)

    # Group priors + this run's clusters by topic, for the order-invariance gate.
    # (An id only ever continues within its own topic; matching the reconciler.)
    priors_by_topic: dict = {}
    for did, row in snapshot.items():
        priors_by_topic.setdefault(row.get("topic_id"), {})[did] = row.get("seed_pmids") or set()
    new_pmids: dict = {}
    new_by_topic: dict = {}
    for slug, entry in subs.items():
        pmids = {int(p) for p in (entry.get("seed_pmids") or [])}
        new_pmids[slug] = pmids
        new_by_topic.setdefault(entry.get("topic_id"), {})[slug] = pmids
    ambiguous_min = thresholds.ambiguous_min

    skip_map: dict[str, PriorReuse] = {}
    for slug in subs:
        verdict = reconciler.match(slug=slug)
        # Reuse ONLY a deterministic Stage-1 overlap auto-match at/above the bar.
        if not (isinstance(verdict, SubtopicMatch) and verdict.reason == "overlap"):
            continue
        if verdict.score < skip_overlap_min:
            continue
        matched_id = verdict.durable_id
        topic_id = subs[slug].get("topic_id")
        prior_row = snapshot.get(matched_id) or {}
        prior_pmids = prior_row.get("seed_pmids") or set()

        # --- order-invariance safety gate (#204 review) ---
        # (1) the matched prior must be this cluster's claim-AGNOSTIC best overlap, so a
        #     stronger prior being claimed away cannot divert the cluster elsewhere; and
        # (2) no OTHER in-topic cluster's overlap with this prior may reach the ambiguous
        #     band, so neither a Stage-1 nor a Stage-2/3 claim on it is possible by anyone
        #     else — making the assignment identical in the pre-pass and in step 10.
        best_did, _best_ov = best_overlap(new_pmids[slug], priors_by_topic.get(topic_id, {}))
        if best_did != matched_id:
            continue
        contested = any(
            other_slug != slug and min_card_overlap(other_pmids, prior_pmids) >= ambiguous_min
            for other_slug, other_pmids in new_by_topic.get(topic_id, {}).items()
        )
        if contested:
            continue
        # --- end safety gate ---

        prior_slug = prior_row.get("slug_id")
        if not prior_slug:
            continue
        prior = prior_index.get(prior_slug)
        if not prior:
            continue  # matched id absent from the prior artifact → relabel (fail-soft)
        display_name = (prior.get("display_name") or "").strip()
        short_description = (prior.get("short_description") or "").strip()
        if not (display_name and short_description):
            continue  # prior had no usable UI text → relabel
        skip_map[slug] = PriorReuse(
            durable_id=matched_id,
            display_name=display_name,
            short_description=short_description,
            overlap_score=verdict.score,
        )
    return skip_map


def _load_prior_hierarchy_index(s3_client: Any) -> dict[str, dict]:
    """Resolve the live ``latest/manifest.json`` → its ``version`` →
    ``{version}/hierarchy.json``, indexed by slug. Reuses the same
    ``get_object_bytes`` path ``compute_diff`` uses to read the prior artifact."""
    manifest = json.loads(s3_client.get_object_bytes("latest/manifest.json"))
    prev_version = manifest.get("version")
    if not prev_version:
        return {}
    prior = json.loads(s3_client.get_object_bytes(f"{prev_version}/hierarchy.json"))
    return build_prior_hierarchy_index(prior)


def load_relabel_skip_map(
    *,
    table: Any = None,
    s3_client: Any = None,
    augmented_dir: Path = DEFAULT_AUGMENTED_DIR,
    thresholds_path=DEFAULT_THRESHOLDS_PATH,
) -> dict[str, "PriorReuse"]:
    """I/O shell: gate on config, assemble inputs, return the skip map.

    Returns ``{}`` (→ full relabel) when the flag is OFF, the durable-id store is
    empty (first reconciled run), there is no prior published hierarchy, or ANY error
    occurs. Read-only (a Scan of the store + two S3 GETs); never raises; never
    writes. ``table`` / ``s3_client`` are injectable for offline tests."""
    try:
        enabled, skip_min = load_skip_config(thresholds_path)
    except Exception as exc:  # noqa: BLE001 — config blip must not break relabel
        _log.warning("relabel skip-config load failed (%s); full relabel", exc)
        return {}
    if not enabled:
        return {}
    try:
        # Non-strict bundle: the subtopic ID set + labels (UI fields may be empty
        # pre-relabel; the skip pre-pass needs neither). The seed_pmids (read from the
        # augmented files) are byte-identical to what publish step 10 will see, so the
        # overlap verdicts match the real reconcile.
        hierarchy = bundle(augmented_dir=augmented_dir, strict=False)
        membership = build_membership(
            hierarchy,
            hierarchy_version="(relabel-skip-prepass)",
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
            _log.info("relabel skip: durable-id store empty; full relabel")
            return {}
        if s3_client is None:
            from utils.s3_client import S3HierarchyClient

            s3_client = S3HierarchyClient()
        prior_index = _load_prior_hierarchy_index(s3_client)
        if not prior_index:
            _log.info("relabel skip: no prior published hierarchy; full relabel")
            return {}
        recon_thresholds = ReconcileThresholds.from_config(thresholds_path)
        skip_map = compute_relabel_skip_map(
            membership=membership,
            labels=labels,
            snapshot=snapshot,
            prior_index=prior_index,
            reconcile_thresholds=recon_thresholds,
            skip_overlap_min=skip_min,
        )
        _log.info(
            "relabel skip: %d/%d subtopics eligible to reuse prior UI text "
            "(overlap >= %.2f)",
            len(skip_map),
            len(membership.get("subtopics") or {}),
            skip_min,
        )
        return skip_map
    except Exception as exc:  # noqa: BLE001 — any failure → safe full relabel
        _log.warning("relabel skip-map build failed (%s); full relabel", exc)
        return {}
