"""Operator CLI for the Phase 6 spotlight pipeline.

Mirrors backfill_all.py argparse + dispatch shape. Implements --dry-run,
--dry-run-full, --publish, --regen-only, --review-queue, --approve,
--reject, --reset-history. Pipeline composition runs through spotlight/
modules from Plans 06-02..06-06.

Operator workflow flags:
  --dry-run         Stage 1+2 preview (pool + selection); NO Bedrock, NO
                    publish. Cheapest preview.
  --dry-run-full    Full pipeline incl. Bedrock generation/critic; writes
                    ./out/spotlight-{date}.json locally; NO S3 upload.
  --publish         Validate + upload to s3://wcmc-reciterai-artifacts/
                    spotlight/{v{date},latest}/.
  --regen-only      Re-roll one lede; reads prior pool from latest/
                    spotlight.json and routes the candidate into the
                    review queue for operator approval.
  --review-queue    List pending review entries for the most-recent (or
                    --publish-id) publish run.
  --publish-id      Constrain --review-queue / --approve / --reject to a
                    specific publish run (defaults to latest manifest).
  --approve <sub>   Mark a flagged review entry as approved (status:
                    pending → approved).
  --reject <sub>    Mark a flagged review entry as rejected (status:
                    pending → rejected).
  --reset-history   RETIRED (#191). Durable subtopic ids now keep rotation
                    history across a recompute; refuses and points at
                    scripts/migrate_spotlight_history_pk.py instead.

Conventions per CLAUDE.md:
  - Lazy boto3 / Bedrock client construction (no AWS calls at import).
  - All credential values flow from env vars exported by ~/.zshrc; this
    module never logs, prints, or branches on credential VALUES.
  - No model ID literals in this file (Bedrock model IDs are imported by
    spotlight.lede_generator / spotlight.critic from utils.bedrock_client).
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

OUT_DIR = Path("./out")
SCHEMA_PATH = Path("./docs/spotlight.schema.json")

ARTIFACTS_BUCKET_NAME = "wcmc-reciterai-artifacts"
LATEST_SPOTLIGHT_KEY = "spotlight/latest/spotlight.json"
LATEST_MANIFEST_KEY = "spotlight/latest/manifest.json"


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Operator CLI for the Phase 6 ReCiter AI spotlight pipeline. "
            "Orchestrates pool ranker -> rotation selector -> lede "
            "generator -> critic -> sensitive gate -> assembler -> "
            "publish, with operator workflow flags for dry-runs, "
            "publishing to s3://wcmc-reciterai-artifacts/spotlight/, and "
            "review-queue management."
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print pool ranking + selected subtopics (up to 25) + near-clone "
            "report. NO Bedrock LLM critic, NO publish. Cheapest preview."
        ),
    )
    parser.add_argument(
        "--dry-run-full",
        action="store_true",
        help=(
            "Full pipeline incl. Bedrock generation/critic; no publish; "
            "writes ./out/spotlight-{date}.json"
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Validate + upload to s3://wcmc-reciterai-artifacts/"
            "spotlight/{v{date},latest}/"
        ),
    )
    parser.add_argument(
        "--regen-only",
        metavar="SUBTOPIC_ID",
        help=(
            "Re-roll one lede; reads prior pool from latest/spotlight.json "
            "in s3://wcmc-reciterai-artifacts and routes the new candidate "
            "into the review queue for operator approval."
        ),
    )
    parser.add_argument(
        "--review-queue",
        action="store_true",
        help=(
            "List pending review entries (defaults to most-recent "
            "publish_id from s3://wcmc-reciterai-artifacts/spotlight/"
            "latest/manifest.json)."
        ),
    )
    parser.add_argument(
        "--publish-id",
        metavar="PUBLISH_ID",
        help=(
            "Constrain --review-queue / --approve / --reject to a specific "
            "publish run. Defaults to the most-recent publish_id from "
            "latest/manifest.json."
        ),
    )
    parser.add_argument(
        "--approve",
        metavar="SUBTOPIC_ID",
        help="Mark a flagged review entry as approved (pending -> approved).",
    )
    parser.add_argument(
        "--reject",
        metavar="SUBTOPIC_ID",
        help="Mark a flagged review entry as rejected (pending -> rejected).",
    )
    parser.add_argument(
        "--reset-history",
        action="store_true",
        help=(
            "RETIRED (#191 brick D). Durable subtopic ids keep rotation history "
            "across an annual recompute, so wholesale truncation is no longer "
            "needed; refuses and points at scripts/migrate_spotlight_history_pk.py."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="WARNING-level logging (default INFO).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers (lazy AWS client construction)
# ---------------------------------------------------------------------------


def _load_hierarchy() -> dict:
    """Assemble the hierarchy in-memory from per-topic augmented drafts.

    Calls `pipeline_hierarchy.bundler.bundle()` directly rather than reading
    a disk artifact: the publish path no longer writes `hierarchy_full.json`
    to a stable consumer location, so reading from disk risked silent drift
    against a stale local copy. Strict mode is off so a subtopic missing UI
    fields surfaces as empty strings (the spotlight assembler already has
    label-based fallbacks) rather than raising mid-pipeline.

    Containerised runs (the monthly `reciterai-spotlight` Fargate task) have
    NO local augmented drafts at all — `.planning` is gitignored AND
    dockerignored, which is why the first automated publish died here
    (2026-08-07; the six prior hand-runs all had a full checkout). When the
    drafts are absent entirely, fall back to the published
    `latest/hierarchy.json` — the schema-validated, exclusion-enforced
    artifact those same drafts were bundled into. Local drafts still win
    when present: cold-run stage 9 must consume the JUST-BUILT drafts,
    which precede that run's own `publish_hierarchy`.
    """
    from pipeline_hierarchy.bundler import bundle

    try:
        return bundle(strict=False)
    except FileNotFoundError:
        import hashlib

        from utils.s3_client import S3HierarchyClient

        # latest/ holds ONLY manifest.json (a pointer); the artifact lives at
        # {version}/hierarchy.json — learned from a NoSuchKey on the first
        # containerised attempt (2026-08-07).
        client = S3HierarchyClient()
        manifest = json.loads(client.get_object_bytes("latest/manifest.json"))
        version = manifest["version"]
        logging.getLogger(__name__).info(
            "no local hierarchy_augmented_*.json (containerised run) — "
            "falling back to published %s/hierarchy.json", version,
        )
        raw = client.get_object_bytes(f"{version}/hierarchy.json")
        expected = manifest.get("sha256")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError(
                f"published hierarchy failed its manifest integrity check: "
                f"{version}/hierarchy.json does not hash to {expected}"
            )
        return json.loads(raw)


def _build_parent_lookup(hierarchy: dict) -> dict[str, str]:
    """Flatten hierarchy.json topics -> {subtopic_id: parent_topic_id}.

    Used by ``pool_ranker.rank_pool`` to canonicalize parent_topic in
    PoolEntry, which the rotation selector's diversity gate keys on.
    Without this map, the regex fallback in ``pool_ranker._parent_of``
    silently returns the subtopic_id itself for IDs that don't follow the
    legacy ``<parent>_NNN`` shape — defeating the diversity constraint.
    """
    out: dict[str, str] = {}
    topics = hierarchy.get("topics", {})
    for topic_id, topic_block in topics.items():
        subtopics = topic_block.get("subtopics", []) if isinstance(topic_block, dict) else []
        for sub in subtopics:
            sid = sub.get("id")
            if sid:
                out[sid] = topic_id
    return out


def _build_short_descriptions(hierarchy: dict, pool: list) -> dict[str, str]:
    """Map each pooled subtopic_id to its short_description for #91 dedup.

    The near-clone scan only ever needs the ~50 subtopics in the rotation
    pool, not the full ~1,500-subtopic taxonomy — so this subsets the
    hierarchy to the pool. A pooled subtopic with no short_description in the
    hierarchy maps to "" (``theme_dedup.find_near_clones`` gives it an empty
    adjacency entry — it can never be gated out of selection).
    """
    short_by_sid: dict[str, str] = {}
    topics = hierarchy.get("topics", {})
    for topic_block in topics.values():
        if not isinstance(topic_block, dict):
            continue
        for sub in topic_block.get("subtopics", []):
            sid = sub.get("id")
            if sid:
                short_by_sid[sid] = sub.get("short_description", "")
    return {e.subtopic_id: short_by_sid.get(e.subtopic_id, "") for e in pool}


def _build_subtopic_metadata(hierarchy: dict) -> dict:
    """Flatten hierarchy.json topics -> {subtopic_id: SubtopicMeta}.

    SubtopicMeta carries the slim Plan 06-04 NamedTuple fields plus the
    D-19 UI fields (display_name, short_description) that the assembler
    falls back to via getattr.
    """
    from spotlight.sensitive_gate import SubtopicMeta

    out: dict = {}
    topics = hierarchy.get("topics", {})
    for topic_id, topic_block in topics.items():
        subtopics = topic_block.get("subtopics", []) if isinstance(topic_block, dict) else []
        parent_label = topic_block.get("display_name", topic_id) if isinstance(topic_block, dict) else topic_id
        for sub in subtopics:
            sid = sub.get("id")
            if not sid:
                continue
            label = sub.get("label", sid)
            description = sub.get("description", "")
            display_name = sub.get("display_name", label)
            short_description = sub.get("short_description", "")
            # Construct the slim NamedTuple, then attach the D-19 UI
            # fields as additional attributes via subclass-style trick:
            # NamedTuple fields are immutable, so we wrap in a small
            # adapter class that exposes the same .label / .description /
            # .parent_topic_label plus .display_name / .short_description.
            meta = _RichSubtopicMeta(
                subtopic_id=sid,
                label=label,
                description=description,
                parent_topic_label=parent_label,
                display_name=display_name,
                short_description=short_description,
            )
            out[sid] = meta
    return out


@dataclass(slots=True)
class _RichSubtopicMeta:
    """Adapter exposing slim SubtopicMeta fields plus D-19 UI fields.

    Plan 06-04's ``SubtopicMeta`` is a NamedTuple with four fields. The
    assembler uses ``getattr(meta, "display_name", meta.label)`` for the
    UI fallback, so we expose ``display_name`` / ``short_description`` as
    plain attributes here. Sensitive gate's ``is_sensitive`` reads
    ``label``, ``description``, and ``parent_topic_label`` directly, so
    those names match the NamedTuple contract.
    """

    subtopic_id: str
    label: str
    description: str
    parent_topic_label: str
    display_name: str
    short_description: str


def _resolve_publish_id(explicit: str | None) -> str:
    """Return the publish_id for review-queue operations.

    If ``explicit`` is provided, returns it. Otherwise reads
    ``s3://wcmc-reciterai-artifacts/spotlight/latest/manifest.json`` and
    extracts ``manifest['version']`` (e.g. ``v2026-05-07``).
    """
    if explicit:
        return explicit
    import boto3

    s3 = boto3.client("s3", region_name="us-east-1")
    resp = s3.get_object(Bucket=ARTIFACTS_BUCKET_NAME, Key=LATEST_MANIFEST_KEY)
    payload = json.loads(resp["Body"].read())
    return payload["version"]


# ---------------------------------------------------------------------------
# Pipeline dispatchers (one per CLI flag)
# ---------------------------------------------------------------------------


def _print_near_clone_report(near_clones, selections) -> None:
    """Print the #91 near-clone neighborhood for --dry-run threshold tuning.

    Lists, per selected subtopic, the pooled near-clones the selector did not
    also select; then the full ranked near-pair list. A pair at or above the
    threshold is tagged CLONE (the gate acts on it); CLONE/both-selected means
    Pass 2 was forced to admit both because the pool was too thin. Pairs below
    the threshold are near-misses. This is the surface for calibrating
    ``spotlight_theme_similarity_max`` (#91 plan §7).
    """
    from spotlight.theme_dedup import RANKED_PAIR_FLOOR_MARGIN

    threshold = near_clones.threshold
    ranked = near_clones.ranked_pairs
    adjacency = near_clones.adjacency
    selected_sids = {s.entry.subtopic_id for s in selections}

    print(
        f"\nNear-clone theme scan "
        f"(spotlight_theme_similarity_max={threshold:.3f}):"
    )
    if not ranked:
        print(
            "  no near-clone or near-miss pairs among the pooled subtopics "
            "(nothing to gate, or embeddings were unavailable — see the log "
            "above)."
        )
        return

    cosine_by_pair = {frozenset((a, b)): cos for a, b, cos in ranked}

    suppressed_any = False
    for s in selections:
        sid = s.entry.subtopic_id
        suppressed = sorted(adjacency.get(sid, set()) - selected_sids)
        if not suppressed:
            continue
        suppressed_any = True
        detail = ", ".join(
            f"{other} (cos={cosine_by_pair[frozenset((sid, other))]:.3f})"
            for other in suppressed
        )
        print(f"  {sid} suppressed near-clone(s): {detail}")
    if not suppressed_any:
        print("  no near-clones suppressed; every selected subtopic is distinct.")

    print(
        f"  ranked near-pairs (cosine >= "
        f"{threshold - RANKED_PAIR_FLOOR_MARGIN:.3f}):"
    )
    for a, b, cos in ranked:
        if cos >= threshold:
            both_selected = a in selected_sids and b in selected_sids
            tag = "CLONE/both-selected" if both_selected else "CLONE"
        else:
            tag = "near-miss"
        print(f"    [{tag}] cos={cos:.3f}  {a} ~ {b}")


def _top_publishable(
    publishable: list,
    target: int,
    *,
    scholar_penalty_lambda: float = 0.0,
    scholar_lead_depth: int = 3,
) -> list:
    """Return the best ``target`` (selection, validated_lede) pairs for publish.

    Default (``scholar_penalty_lambda <= 0``, #167): the best ``target`` by
    selection score, descending, tie-broken by subtopic_id for determinism.
    Ledes are generated and gated over the full candidate pool; this is the
    final step that truncates the cleared set to the published count, so the
    publish reflects selection-score merit rather than whichever ledes happened
    to clear the critic first.

    Scholar-coverage mode (``scholar_penalty_lambda > 0``; see
    ``docs/spotlight-scholar-coverage-selection.md``): a greedy pick that
    downweights a candidate by how many of its lead authors are ALREADY on the
    page, so one prolific lab cannot front several of the published cards. For
    each pick the effective score is

        sel_score - scholar_penalty_lambda * median_sel * load

    where ``load`` is the summed page-count of the candidate's lead authors
    (first/last ``person_identifier`` of its top ``scholar_lead_depth``
    impact-ranked papers) and ``median_sel`` is the median sel_score of the
    cleared set — so the knob self-normalizes as scores drift. The penalty is
    marginal + escalating (a person's 2nd card is cheap, their 4th expensive)
    and SOFT: a genuinely top-tier card can still out-score the penalty and
    repeat a star; nothing is hard-excluded. The cleared candidates are already
    parent-distinct and near-clone-free (gated in ``select_with_diversity``), so
    the publish step inherits both gates.

    ``target`` is a ceiling — a thin pool publishes however many cleared. Does
    not mutate ``publishable``.
    """
    ranked = sorted(
        publishable,
        key=lambda pv: (-pv[0].sel_score, pv[0].entry.subtopic_id),
    )
    if scholar_penalty_lambda <= 0.0 or not ranked:
        return ranked[:target]

    median_sel = statistics.median([pv[0].sel_score for pv in ranked]) or 1.0

    def _lead_authors(sel) -> set[str]:
        out: set[str] = set()
        for paper in getattr(sel.entry, "papers", ())[:scholar_lead_depth]:
            for author in (paper.first_author, paper.last_author):
                pid = (getattr(author, "person_identifier", "") or "").strip()
                if pid:
                    out.add(pid)
        return out

    # feat keyed by object identity: the (sel, vlede) tuples are stable for the
    # life of this call and the vlede half is not reliably hashable.
    feat = {id(pv): _lead_authors(pv[0]) for pv in ranked}
    board: dict[str, int] = {}
    chosen: list = []
    remaining = list(ranked)  # sel_score-DESC, subtopic_id-ASC
    while len(chosen) < target and remaining:
        def _effective(pv):
            load = sum(board.get(a, 0) for a in feat[id(pv)])
            return (
                pv[0].sel_score - scholar_penalty_lambda * median_sel * load,
                pv[0].sel_score,  # tiebreak: raw merit, then encounter order
            )

        # ``remaining`` stays sel_score-DESC / subtopic_id-ASC, so max() returns
        # the first candidate at the best effective score — sel_score then
        # subtopic_id are the deterministic tiebreakers.
        best = max(remaining, key=_effective)
        chosen.append(best)
        for a in feat[id(best)]:
            board[a] = board.get(a, 0) + 1
        remaining = [pv for pv in remaining if pv is not best]
    return chosen


def _try_generate_lede(
    meta, papers, publish_id, parent_topic, excluded_openers, stage_table=None
):
    """Run the lede critic loop for one subtopic, converting a per-subtopic
    failure into a skip (return None) rather than aborting the whole publish.

    A full publish makes ~75 Bedrock calls; a single transient failure (e.g.
    `ServiceUnavailableException` under burst load) that exhausts the client's
    retries would otherwise kill the entire ~20-minute run. We over-select
    (SELECTION_TARGET candidates for PUBLISH_TARGET slots), so a skipped
    subtopic simply doesn't publish; the SELECTION_FLOOR check downstream still
    guards against mass failure. `ValueError` (subtopic with <2 author-resolved
    papers) was already a skip; this extends the same tolerance to transient
    generation errors.
    """
    from spotlight.critic import run_critic_loop
    try:
        return run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=publish_id,
            parent_topic=parent_topic,
            excluded_openers=excluded_openers,
            stage_table=stage_table,
        )
    except ValueError as e:
        # Lede generator rejects subtopics without >=2 author-identified papers.
        logger.warning("Skipping subtopic %s: %s", meta.subtopic_id, e)
        return None
    except Exception as e:  # noqa: BLE001 — over-selected + floor-guarded: log + skip
        logger.error(
            "Lede generation failed for subtopic %s (%s: %s); skipping",
            meta.subtopic_id, type(e).__name__, e,
        )
        return None


def _reused_validated_lede(meta, parent_topic: str, pr, current_pmids) -> "object":
    """#191 brick E lede-skip: wrap a reused prior lede as a PASS-status
    ValidatedLede so the assembler contract holds (every selected subtopic_id must
    be a status=='pass' entry; assembler.build_artifact raises otherwise).

    ``papers_used`` MUST be the CURRENT run's grounding PMIDs (== ``pr.grounded_pmids``
    by gate 2) so the assembler writes the correct ``lede_grounded_pmids`` and the
    schema's subset-of-papers invariant holds. ``attempts`` is empty (no OPUS/critic
    call was made). ``parent_topic`` is threaded from the loop's ``sel.entry``."""
    from spotlight.critic import ValidatedLede

    return ValidatedLede(
        subtopic_id=meta.subtopic_id,
        parent_topic=parent_topic,
        lede=pr.lede,
        status="pass",
        attempts=(),
        papers_used=tuple(sorted(current_pmids)),
    )


def _report_run_cost(acc) -> None:
    """Print + log the measured Bedrock spend for this run.

    Covers every ``BedrockClient.call()`` made during the run (spotlight lede =
    Opus, critic = Haiku). The Titan embedding calls in the #91 near-clone scan
    use a separate API path and are not captured — they are negligible (<$0.01).
    """
    s = acc.summary()
    by_model = ", ".join(f"{m}=${c}" for m, c in s["by_model"].items()) or "none"
    msg = (
        f"Bedrock spend this run: ${s['total_usd']} over {s['call_count']} "
        f"call(s) ({s['total_input_tokens']:,} in / {s['total_output_tokens']:,} "
        f"out tok); by model: {by_model}"
    )
    logger.info(msg)
    print(f"\n{msg}")


def _write_run_ledger(
    acc, *, publish_id: str, started_at: str, run_id: str, duration_ms: int,
    n_published: int
) -> None:
    """Best-effort: persist a timestamped ``STAGE#spotlight_publish#GLOBAL`` run
    record (SK ``RUN#{started_at}``) carrying this run's cost, so every publish
    leaves a queryable cost + history row. ``output_pointer`` references the
    immutable per-run archive (``spotlight/runs/{run_id}/spotlight.json``) so the
    row links to THIS run's exact full output, not the date-keyed prefix that a
    later same-day publish overwrites. Any failure is logged and swallowed —
    a ledger write must never undo a completed publish.
    """
    try:
        import boto3

        from spotlight.pool_ranker import TABLE_NAME
        from utils.stage_records import write_complete

        table = boto3.resource("dynamodb").Table(TABLE_NAME)
        write_complete(
            table,
            stage="spotlight_publish",
            scope="GLOBAL",
            input_hash=publish_id,
            started_at=started_at,
            duration_ms=duration_ms,
            cost_observed_usd=acc.total_usd,
            output_pointer=f"spotlight/runs/{run_id}/spotlight.json",
            records_written=n_published,
            model_ids_snapshot=sorted(acc.by_model.keys()),
        )
        logger.info(
            "Run-ledger row written: STAGE#spotlight_publish#GLOBAL RUN#%s "
            "(cost=$%s, %d published)",
            started_at, acc.total_usd, n_published,
        )
    except Exception as e:  # noqa: BLE001 — ledger best-effort; publish already done
        logger.warning(
            "Run-ledger write skipped (publish succeeded): %s: %s",
            type(e).__name__, e,
        )


def _check_or_flag_schema_coherent(schema_path: Path | None = None) -> bool:
    """#233 pre-flight: if the B1 OR rule is enabled, the published schema MUST
    allow an empty ``Author.personIdentifier`` (the OR rule emits pid-less
    external co-leads). Return False — after printing why — when the flag is on
    but the schema still requires a non-empty pid, so a flag flip without the
    schema relax fails fast here (before the pool/Bedrock spend) instead of at
    the late publish.py schema gate. Returns True when coherent or the flag is
    off.
    """
    from spotlight.author_resolver import _faculty_or_enabled

    if not _faculty_or_enabled():
        return True
    path = Path(schema_path) if schema_path is not None else SCHEMA_PATH
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — any read/parse error must fail loud
        print(
            f"[#233 pre-flight] spotlight_b1_faculty_or_enabled is true but the "
            f"schema at {path} could not be read to verify it permits an empty "
            f"personIdentifier: {exc}"
        )
        return False
    min_len = (
        schema.get("$defs", {})
        .get("Author", {})
        .get("properties", {})
        .get("personIdentifier", {})
        .get("minLength", 0)
    )
    if min_len and min_len >= 1:
        print(
            "[#233 pre-flight] spotlight_b1_faculty_or_enabled is true but "
            f"{path} still requires Author.personIdentifier minLength>={min_len}. "
            "The OR rule emits pid-less external co-leads, which that constraint "
            "rejects at publish — relax it (remove minLength) before enabling the "
            "flag, or set the flag false. Aborting before spend."
        )
        return False
    return True


def _run_pipeline(dry_run: bool, dry_run_full: bool, publish: bool) -> int:
    """Main pipeline orchestrator.

    Stages (per RESEARCH §"Pipeline composition"):
      1. Pool ranker  (pool_ranker.rank_pool)
      2. Rotation selector (rotation_selector.fetch_history +
         select_with_diversity)
      3. Lede generator + critic (critic.run_critic_loop)
      4. Sensitive gate (sensitive_gate.is_sensitive)
      5. Assembler (assembler.build_artifact)
      6. Publish (publish.publish_artifact) — only on --publish
    """
    # #233 pre-flight: fail fast (before the pool/Bedrock spend) if the B1 OR
    # rule is enabled but the schema still rejects an empty personIdentifier.
    if not _check_or_flag_schema_coherent():
        return 1
    from spotlight.pool_ranker import rank_pool
    from spotlight.rotation_selector import (
        PUBLISH_TARGET,
        SELECTION_FLOOR,
        SELECTION_TARGET,
        fetch_history,
        select_with_diversity,
    )
    from spotlight.author_resolver import resolve_authors
    from spotlight.theme_dedup import NearClones, find_near_clones
    from utils.bedrock_client import set_cost_accumulator
    from utils.env_check import load_thresholds
    from utils.iso_clock import now_iso
    from utils.llm_cost import CostAccumulator

    # Per-run Bedrock cost capture + run history. Every BedrockClient.call() in
    # this run (lede=Opus, critic=Haiku) records its token usage into cost_acc;
    # the total is printed at the end and, on --publish, written to a timestamped
    # STAGE#spotlight_publish run-ledger row. Best-effort — capture never aborts
    # a Bedrock call (see utils.bedrock_client.set_cost_accumulator).
    cost_acc = CostAccumulator()
    run_started_at = now_iso()
    run_monotonic_start = time.monotonic()
    set_cost_accumulator(cost_acc)

    # Hierarchy is loaded up-front so the pool ranker can canonicalize
    # parent_topic for the rotation selector's diversity gate (without
    # this, parent_topic falls back to subtopic_id for slug-style IDs and
    # diversity becomes a no-op).
    hierarchy = _load_hierarchy()
    parent_lookup = _build_parent_lookup(hierarchy)

    # Stage 1+2: always run.
    pool = rank_pool(parent_lookup=parent_lookup, author_resolver=resolve_authors)
    history = fetch_history(
        client=None,
        subtopic_ids=[e.subtopic_id for e in pool],
    )

    # #91: near-clone theme dedup. Embed the pool's short_descriptions and
    # hand the rotation selector a near-clone adjacency so it never places
    # two near-duplicate subtopics in one publish. Best-effort — any
    # embedding failure (Bedrock throttle/timeout/5xx, or a missing
    # bedrock:InvokeModel grant) degrades to parent-only selection and the
    # publish still proceeds (#91 plan §9.6).
    theme_threshold = float(load_thresholds()["spotlight_theme_similarity_max"])
    pool_descriptions = _build_short_descriptions(hierarchy, pool)
    try:
        near_clones = find_near_clones(pool_descriptions, theme_threshold)
    except Exception as exc:
        # Deliberately broad: theme dedup is best-effort and must never block
        # the monthly publish. Any failure -> empty adjacency -> the selector
        # runs exactly today's parent-only selection.
        logger.warning(
            "theme dedup unavailable: %s; falling back to parent-only "
            "selection",
            exc,
        )
        near_clones = NearClones(
            adjacency={}, ranked_pairs=[], threshold=theme_threshold
        )

    # #164: strengthen the near-clone gate with an article-overlap signal.
    # The cosine gate above misses equivalent subtopics that are worded
    # differently (the spaceflight / disparities home-page duplicates). Two
    # pooled subtopics that share most of their author-resolved papers are the
    # same theme regardless of wording, so union an overlap adjacency
    # (min-cardinality D-23, containment-exempt) into the gate. Additive —
    # it only ever adds edges, never removes a cosine one.
    from pipeline_hierarchy.subtopic_dedup import (
        overlap_adjacency,
        theme_cap_adjacency,
        union_adjacency,
    )

    thresholds = load_thresholds()
    overlap_min = float(thresholds["spotlight_clone_overlap_min"])
    containment_ratio = float(thresholds["hierarchy_dedup_containment_ratio"])
    theme_cap_patterns = thresholds.get("spotlight_theme_cap_patterns", [])
    pool_ids = [e.subtopic_id for e in pool]
    pool_pmid_sets = {e.subtopic_id: set(e.full_pmids) for e in pool}
    overlap_adj = overlap_adjacency(
        pool_pmid_sets,
        article_overlap_min=overlap_min,
        containment_ratio=containment_ratio,
    )
    # #164: editorial cross-cutting-theme cap (e.g. cap "disparit"ies at one
    # featured card) — distinct facets the similarity signals score as distinct.
    theme_adj = theme_cap_adjacency(pool_ids, theme_cap_patterns)
    combined_adjacency = union_adjacency(
        near_clones.adjacency, overlap_adj, theme_adj
    )
    overlap_only_edges = sum(
        len(overlap_adj.get(sid, set()) - near_clones.adjacency.get(sid, set()))
        for sid in combined_adjacency
    ) // 2
    logger.info(
        "near-clone gate: %d cosine edge(s) + %d overlap-only edge(s) "
        "(overlap_min=%.2f)",
        sum(len(v) for v in near_clones.adjacency.values()) // 2,
        overlap_only_edges,
        overlap_min,
    )

    # #164 "clean over count": publish the clone-free, parent-distinct set up
    # to SELECTION_TARGET (25). n is the CEILING; SELECTION_FLOOR is the only
    # point Pass 2 force-admits near-clones — so a thin pool publishes fewer
    # CLEAN subtopics rather than padding the count with duplicates. SPS samples
    # 8 of whatever we publish, so 18-22 distinct is still plenty of variety.
    distinct_parents = len({e.parent_topic for e in pool})
    selections = select_with_diversity(
        pool,
        history,
        n=SELECTION_TARGET,
        n_floor=SELECTION_FLOOR,
        near_clones=combined_adjacency,
    )
    logger.info(
        "selection: %d clean (ceiling=%d, floor=%d, %d distinct parents in pool)",
        len(selections),
        SELECTION_TARGET,
        SELECTION_FLOOR,
        distinct_parents,
    )

    print(f"\nPool ranker: {len(pool)} subtopics ranked.")
    print(
        f"Rotation selector: {len(selections)} selections "
        f"(clean up to {SELECTION_TARGET}, floor {SELECTION_FLOOR}, "
        f"{distinct_parents} distinct parents in pool)."
    )
    for s in selections:
        print(
            f"  - {s.entry.subtopic_id} (parent={s.entry.parent_topic}, "
            f"sel_score={s.sel_score:.2f}, last_shown={s.last_shown_at})"
        )

    if dry_run:
        # Cheapest preview — no Bedrock generation, no DynamoDB writes, no
        # publish. The near-clone scan above does make ~50 cheap,
        # non-generative Titan embedding calls; the report below is the
        # calibration surface for spotlight_theme_similarity_max (#91 §7).
        _print_near_clone_report(near_clones, selections)
        # #164 calibration surface for spotlight_clone_overlap_min: the pairs
        # the overlap signal gates that cosine did not.
        extra = sorted(
            {
                tuple(sorted((sid, other)))
                for sid, nbrs in overlap_adj.items()
                for other in nbrs - near_clones.adjacency.get(sid, set())
            }
        )
        print(f"\nArticle-overlap near-clone edges (>= {overlap_min}): {len(extra)}")
        for a, b in extra[:40]:
            ov = round(
                len(pool_pmid_sets[a] & pool_pmid_sets[b])
                / max(1, min(len(pool_pmid_sets[a]), len(pool_pmid_sets[b]))),
                3,
            )
            print(f"  {a} ~ {b}  (overlap={ov})")
        return 0

    # Stage 3+4+5+6: --dry-run-full or --publish.
    from spotlight.assembler import build_artifact
    from spotlight.review_queue import write_review_entry
    from spotlight.sensitive_gate import is_sensitive, load_sensitive_tags

    subtopic_metadata = _build_subtopic_metadata(hierarchy)

    publish_id = f"v{date.today().isoformat()}"

    # Stage 3: per-selection critic loop. Sequential by design so each
    # generation can see which institutional-voice openers earlier spotlights
    # already chose, capping reuse within a publish (wcmc-its/ReciterAI#2 §3;
    # #167 relaxes strict uniqueness to OPENER_REUSE_CAP). Only openers that
    # have already hit the cap are excluded — the generator may keep reusing an
    # opener until it reaches OPENER_REUSE_CAP uses.
    from spotlight.critic import (
        OPENER_RE,
        OPENER_REUSE_CAP,
        effective_excluded_openers,
    )

    # #191 brick E lede-skip: build the reuse map ONCE (gated OFF by default inside
    # load_lede_skip_map; read-only — one store Scan + one S3 GET; returns {} on any
    # error → full generation). Gate-2 (grounding-PMID set equality) is applied
    # per-selection inside the loop via lede_reuse_for. Flag off ⇒ {} ⇒ this path is
    # byte-identical to today.
    from spotlight.lede_skip import (
        current_grounding_pmids,
        lede_reuse_for,
        load_lede_skip_map,
    )

    lede_skip_map = load_lede_skip_map()
    if lede_skip_map:
        logger.info(
            "#191 brick E lede-skip: %d subtopic(s) eligible to reuse a prior lede "
            "(pending the in-loop grounding-PMID check)",
            len(lede_skip_map),
        )

    # Phase 12 D-08: the CRITIC_REJECT# audit row + reason-code drift WARN in
    # run_critic_loop only fire when a stage_table is threaded through. Only
    # --dry-run-full / --publish reach here (plain --dry-run returns above), so
    # AWS is available; mirror the score_publications stage_table pattern.
    from utils.dynamodb_helpers import get_table, TABLE_NAME

    stage_table = get_table(TABLE_NAME)

    validated_ledes = []
    opener_counts: Counter[str] = Counter()
    ledes_reused = 0
    for sel in selections:
        meta = subtopic_metadata.get(sel.entry.subtopic_id)
        if meta is None:
            logger.warning(
                "Subtopic metadata missing for %s; skipping",
                sel.entry.subtopic_id,
            )
            continue
        # #219: exclude openers at the reuse cap, but never so many that the
        # generator is left with no allowed opener (which forces a refusal that
        # then gets persisted as the lede). effective_excluded_openers keeps at
        # least OPENER_MIN_AVAILABLE openers usable, releasing the least-used
        # at-cap openers when the pool is larger than the opener budget allows.
        at_cap = effective_excluded_openers(opener_counts)
        # #191 brick E lede-skip (dual gate): when this subtopic's prior lede is
        # eligible (gate 1, in the map) AND the current run's top-3 grounding PMIDs
        # match the prior lede's (gate 2), carry the prior lede forward — no OPUS, no
        # critic ($0). A reused lede is a real PASS, so it flows through Stage-3.5
        # duplicate-opener / Stage-4 sensitive / Stage-4.5 publish-select unchanged.
        pr = lede_reuse_for(lede_skip_map, sel.entry.subtopic_id, list(sel.entry.papers))
        if pr is not None:
            cur = current_grounding_pmids(list(sel.entry.papers))
            vlede = _reused_validated_lede(meta, sel.entry.parent_topic, pr, cur)
            ledes_reused += 1
            # A reused lede still counts toward OPENER_REUSE_CAP so the page can't
            # exceed the cap via reuse.
            m = OPENER_RE.search(vlede.lede)
            if m:
                opener_counts[m.group(0)] += 1
            validated_ledes.append((sel, vlede))
            logger.info(
                "Lede reused (skip): subtopic_id=%s durable_id=%s overlap=%.2f "
                "(#191 brick E — prior lede carried forward, no OPUS call)",
                sel.entry.subtopic_id,
                pr.durable_id,
                pr.overlap_score,
            )
            continue
        # Per-subtopic failures (no eligible papers, or a transient Bedrock
        # error under burst load) skip this subtopic instead of aborting the
        # whole publish — see _try_generate_lede.
        vlede = _try_generate_lede(
            meta,
            list(sel.entry.papers),
            publish_id,
            sel.entry.parent_topic,
            at_cap,
            stage_table=stage_table,
        )
        if vlede is None:
            continue
        if vlede.status == "pass":
            m = OPENER_RE.search(vlede.lede)
            if m:
                opener_counts[m.group(0)] += 1
        validated_ledes.append((sel, vlede))

    # Stage 3.5: artifact-level critic — cap institutional-voice opener reuse
    # across the publish at OPENER_REUSE_CAP (wcmc-its/ReciterAI#2 §3; #167).
    # The first OPENER_REUSE_CAP uses keep their slots; occurrences beyond the
    # cap are routed to the review queue and dropped from the publishable set.
    # Operator can `--regen-only` them to re-roll.
    from spotlight.critic import find_duplicate_openers

    pass_ledes = [
        (i, sel, vlede) for i, (sel, vlede) in enumerate(validated_ledes)
        if vlede.status == "pass"
    ]
    pass_lede_text = [v.lede for _, _, v in pass_ledes]
    duplicate_indices = find_duplicate_openers(
        pass_lede_text, max_per_opener=OPENER_REUSE_CAP
    )
    flagged_global_idx = set()
    for local_i, opener in duplicate_indices.items():
        global_i, sel, vlede = pass_ledes[local_i]
        write_review_entry(
            client=None,
            entry={
                "publish_id": publish_id,
                "subtopic_id": sel.entry.subtopic_id,
                "parent_topic": sel.entry.parent_topic,
                "lede_text": vlede.lede,
                "flag_reason": "critic",
                "papers_used": list(vlede.papers_used),
                "regen_count": 0,
            },
        )
        logger.info(
            "Duplicate opener flagged: subtopic_id=%s opener=%r",
            sel.entry.subtopic_id,
            opener,
        )
        flagged_global_idx.add(global_i)

    # Stage 4: sensitive gate. Loaded once; applied to passing ledes only.
    sensitive_tags = load_sensitive_tags()
    publishable: list = []
    for global_i, (sel, vlede) in enumerate(validated_ledes):
        if vlede.status != "pass":
            # Already routed to review queue by run_critic_loop.
            continue
        if global_i in flagged_global_idx:
            # Already routed to review queue by the duplicate-opener check.
            continue
        meta = subtopic_metadata[sel.entry.subtopic_id]
        flagged, matched_pattern = is_sensitive(meta, sensitive_tags)
        if flagged:
            # Sensitive — route to review queue, drop from publishable set.
            write_review_entry(
                client=None,
                entry={
                    "publish_id": publish_id,
                    "subtopic_id": sel.entry.subtopic_id,
                    "parent_topic": sel.entry.parent_topic,
                    "lede_text": vlede.lede,
                    "flag_reason": "sensitive_tag",
                    "papers_used": list(vlede.papers_used),
                    "regen_count": 0,
                    "sensitive_tag_matched": matched_pattern,
                },
            )
            logger.info(
                "Sensitive flag: subtopic_id=%s pattern=%s",
                sel.entry.subtopic_id,
                matched_pattern,
            )
            continue
        publishable.append((sel, vlede))

    # Stage 4.5: select the published set from the cleared candidates (#167).
    # Ship-all (2026-06-10): PUBLISH_TARGET == SELECTION_TARGET, so this ships
    # EVERY cleared candidate (up to ~25) — SPS random-samples 8 of them on the
    # home page. The scholar-coverage downweight + #167 sel_score truncation
    # below are therefore INERT at this setting (nothing to truncate); they
    # re-activate automatically if PUBLISH_TARGET is ever lowered below the
    # cleared count. lambda=0 would restore the pure sel_score order.
    scholar_lambda = float(thresholds.get("spotlight_scholar_penalty_lambda", 0.0))
    scholar_depth = int(thresholds.get("spotlight_scholar_lead_depth", 3))
    if len(publishable) > PUBLISH_TARGET:
        logger.info(
            "Publishable %d > target %d; keeping the top %d "
            "(scholar_penalty_lambda=%.3f, lead_depth=%d)",
            len(publishable), PUBLISH_TARGET, PUBLISH_TARGET,
            scholar_lambda, scholar_depth,
        )
    publishable = _top_publishable(
        publishable,
        PUBLISH_TARGET,
        scholar_penalty_lambda=scholar_lambda,
        scholar_lead_depth=scholar_depth,
    )

    # All Bedrock work (ledes + critic + sensitive gate) is done by here; report
    # the measured spend on both the --dry-run-full and --publish paths.
    _report_run_cost(cost_acc)

    # #191 brick E lede-skip: surface the $0 reuse count alongside the spend, on
    # both the --dry-run-full and --publish paths (mirrors the relabel summary).
    if ledes_reused:
        msg = (
            f"Ledes reused (skip): {ledes_reused} "
            f"(#191 brick E — prior lede carried forward, no OPUS call)"
        )
        logger.info(msg)
        print(msg)

    # Stage 5: assemble.
    # #191 brick D3: evaluate the durable-id gate at process entry and load
    # the slug -> durable map ONCE before build_artifact (which stays pure).
    # Gate off -> no snapshot scan -> the artifact is byte-identical to today.
    # Only --dry-run-full / --publish reach here (plain --dry-run returns
    # above), so AWS is available on this path.
    propagate = bool(thresholds.get("propagate_durable_ids", False))
    durable_map: dict[str, str] = {}
    if propagate:
        from pipeline_hierarchy.subtopic_reconcile import load_id_store_snapshot
        from utils.dynamodb_helpers import get_table, TABLE_NAME

        snapshot = load_id_store_snapshot(get_table(TABLE_NAME))
        durable_map = {
            row["slug_id"]: d for d, row in snapshot.items() if row.get("slug_id")
        }

    selected_vledes = [vlede for _, vlede in publishable]
    artifact = build_artifact(
        selected=selected_vledes,
        pool=pool,
        subtopic_metadata=subtopic_metadata,
        durable_map=durable_map,
        propagate_durable_ids=propagate,
    )

    # --dry-run-full: write local artifact, no S3.
    if dry_run_full:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"spotlight-{date.today().isoformat()}.json"
        out_path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nDRY-RUN-FULL artifact written: {out_path}")
        print(f"  spotlights: {len(artifact['spotlights'])}")
        print(f"  pool_snapshot: {len(artifact['pool_snapshot'])}")
        return 0

    # Stage 6: publish.
    from spotlight.publish import publish_artifact

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    publishable_selections = [sel for sel, _ in publishable]
    # S3-safe run id (the ledger SK timestamp with ':' -> '-'); names the
    # immutable per-run archive prefix and the ledger's output_pointer.
    run_id = run_started_at.replace(":", "-")
    rc = publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=publishable_selections,
        dry_run=False,
        run_id=run_id,
    )
    # A timestamped run-ledger row with this run's cost — only on a clean publish.
    if rc == 0:
        _write_run_ledger(
            cost_acc,
            publish_id=publish_id,
            started_at=run_started_at,
            run_id=run_id,
            duration_ms=int((time.monotonic() - run_monotonic_start) * 1000),
            n_published=len(publishable_selections),
        )
    return rc


def _run_review_queue(publish_id: str | None) -> int:
    """List pending review entries for the resolved publish_id."""
    from spotlight.review_queue import list_pending

    pid = _resolve_publish_id(publish_id)
    pending = list_pending(client=None, publish_id=pid)

    print(f"\nReview queue (publish_id={pid}): {len(pending)} pending entries\n")
    for entry in pending:
        sid = entry.get("subtopic_id", "?")
        flag_reason = entry.get("flag_reason", "?")
        regen_count = entry.get("regen_count", 0)
        lede_text = entry.get("lede_text", "")
        created_at = entry.get("created_at", "?")
        truncated = lede_text[:80] + ("..." if len(lede_text) > 80 else "")
        print(
            f"  - subtopic_id={sid} flag={flag_reason} "
            f"regen_count={regen_count} created_at={created_at}\n"
            f"      lede: {truncated}"
        )
    return 0


def _run_approve(subtopic_id: str, publish_id: str | None) -> int:
    """Approve one pending review entry (pending -> approved)."""
    from botocore.exceptions import ClientError

    from spotlight.review_queue import set_status

    pid = _resolve_publish_id(publish_id)
    try:
        set_status(
            client=None,
            publish_id=pid,
            subtopic_id=subtopic_id,
            target_status="approved",
            reviewer="cli",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            print(
                f"No pending entry found for {pid}/{subtopic_id} "
                "(already approved/rejected, or doesn't exist)."
            )
            return 1
        raise
    print(f"Approved: {pid}/{subtopic_id}")
    return 0


def _run_reject(subtopic_id: str, publish_id: str | None) -> int:
    """Reject one pending review entry (pending -> rejected)."""
    from botocore.exceptions import ClientError

    from spotlight.review_queue import set_status

    pid = _resolve_publish_id(publish_id)
    try:
        set_status(
            client=None,
            publish_id=pid,
            subtopic_id=subtopic_id,
            target_status="rejected",
            reviewer="cli",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            print(
                f"No pending entry found for {pid}/{subtopic_id} "
                "(already approved/rejected, or doesn't exist)."
            )
            return 1
        raise
    print(f"Rejected: {pid}/{subtopic_id}")
    return 0


def _live_paper_fields(dynamo_client, pmid, subtopic_id):
    """Return (impact_score, impact_justification, synopsis) from the live
    TOPIC# row for (pmid, subtopic_id), or None if no such row exists.

    ``assembler._paper_to_json`` strips these fields from the published
    artifact as pipeline-internal, so a regen must read the grounding text
    back from the substrate (PmidIndex GSI: pmid HASH, PK RANGE) rather than
    ground the lede — and the critic's anchored_in_synopses check — on
    placeholder text.
    """
    from utils.dynamodb_helpers import TABLE_NAME

    start_key = None
    while True:
        kwargs = {
            "TableName": TABLE_NAME,
            "IndexName": "PmidIndex",
            "KeyConditionExpression": "pmid = :p AND begins_with(PK, :t)",
            "ExpressionAttributeValues": {
                ":p": {"S": str(pmid)},
                ":t": {"S": "TOPIC#"},
            },
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = dynamo_client.query(**kwargs)
        for item in resp.get("Items", []):
            if item.get("primary_subtopic_id", {}).get("S") == subtopic_id:
                return (
                    float(item.get("impact_score", {}).get("N", "0")),
                    item.get("impact_justification", {}).get("S", ""),
                    item.get("synopsis", {}).get("S", ""),
                )
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return None


def _run_regen_only(subtopic_id: str) -> int:
    """Re-roll a single lede; route candidate into the review queue.

    Reads prior pool from s3://wcmc-reciterai-artifacts/spotlight/latest/
    spotlight.json, finds the subtopic's papers + parent_topic, runs the
    full critic loop, and writes the candidate as a pending review entry
    (operator approves later via --approve).
    """
    import boto3

    from spotlight.critic import run_critic_loop
    from spotlight.review_queue import write_review_entry
    from utils.dynamodb_helpers import get_table, TABLE_NAME

    s3 = boto3.client("s3", region_name="us-east-1")
    resp = s3.get_object(Bucket=ARTIFACTS_BUCKET_NAME, Key=LATEST_SPOTLIGHT_KEY)
    prior_artifact = json.loads(resp["Body"].read())

    # Locate the subtopic in the prior artifact.
    target = None
    for spot in prior_artifact.get("spotlights", []):
        if spot.get("subtopic_id") == subtopic_id:
            target = spot
            break
    if target is None:
        print(
            f"Subtopic {subtopic_id} not found in prior latest spotlight "
            "artifact; nothing to regenerate."
        )
        return 1

    hierarchy = _load_hierarchy()
    subtopic_metadata = _build_subtopic_metadata(hierarchy)
    meta = subtopic_metadata.get(subtopic_id)
    if meta is None:
        print(f"Subtopic {subtopic_id} not in current hierarchy; abort.")
        return 1

    # Reconstruct the Paper list from the prior artifact's paper payload,
    # re-hydrating the substrate-only fields (synopsis / impact) the assembler
    # strips from the published artifact from the live TOPIC# rows. Grounding
    # the regen on those real fields — not the subtopic description repeated
    # per paper — is the whole point of the fix path (a paper that no longer
    # has a TOPIC# row under this subtopic is dropped from the grounding set).
    from spotlight.types import Author, Paper

    dynamo_client = boto3.client("dynamodb", region_name="us-east-1")
    papers: list[Paper] = []
    for p in target.get("papers", []):
        live = _live_paper_fields(dynamo_client, p["pmid"], subtopic_id)
        if live is None:
            logger.warning(
                "Regen: PMID %s has no live TOPIC# row under %s; dropping "
                "from the grounding set.",
                p["pmid"],
                subtopic_id,
            )
            continue
        impact_score, impact_justification, synopsis = live
        fa = p.get("first_author", {})
        la = p.get("last_author", {})
        papers.append(
            Paper(
                pmid=p["pmid"],
                title=p.get("title", ""),
                journal=p.get("journal", ""),
                year=int(p.get("year", 0)),
                impact_score=impact_score,
                impact_justification=impact_justification,
                synopsis=synopsis,
                first_author=Author(
                    person_identifier=fa.get("personIdentifier", ""),
                    display_name=fa.get("displayName", ""),
                    position="first",
                ),
                last_author=Author(
                    person_identifier=la.get("personIdentifier", ""),
                    display_name=la.get("displayName", ""),
                    position="last",
                ),
            )
        )

    publish_id = f"v{date.today().isoformat()}"
    parent_topic = target.get("parent_topic", meta.parent_topic_label)
    vlede = run_critic_loop(
        meta=meta,
        papers=papers,
        publish_id=publish_id,
        parent_topic=parent_topic,
        stage_table=get_table(TABLE_NAME),
    )

    # Route candidate into the review queue regardless of pass/fail status:
    # the regen workflow is a human-in-the-loop tool, not an auto-publish.
    write_review_entry(
        client=None,
        entry={
            "publish_id": publish_id,
            "subtopic_id": subtopic_id,
            "parent_topic": parent_topic,
            "lede_text": vlede.lede,
            "flag_reason": "critic" if vlede.status == "needs_review" else "critic",
            "papers_used": list(vlede.papers_used),
            "regen_count": 0,
        },
    )
    print(
        f"Regenerated lede for {subtopic_id} (publish_id={publish_id}); "
        f"routed into review queue. Use --approve {subtopic_id} "
        f"--publish-id {publish_id} to accept."
    )
    return 0


def _run_reset_history() -> int:
    """Tombstoned (#191 brick D). Refuses the truncate and redirects to the re-key.

    Durable subtopic ids (the ``SUBTOPIC_ID#``/``SUBTOPIC_SLUG#`` store) keep spotlight
    rotation history stable across an annual recompute, so the old "slugs rotated, so
    wipe everything" workflow is obsolete. The cutover tool is now
    ``scripts/migrate_spotlight_history_pk.py``, which re-keys history onto durable ids
    without deleting any state. This stub deletes nothing — it only points the way, so an
    operator's muscle memory can't truncate live rotation history.
    """
    print(
        "--reset-history is RETIRED (#191). Durable subtopic ids now keep spotlight "
        "rotation history across an annual recompute, so truncating it is no longer "
        "necessary or safe.\n"
        "To migrate existing slug-keyed history onto durable ids (no state is deleted), "
        "run:\n"
        "    PYTHONPATH=. python scripts/migrate_spotlight_history_pk.py --dry-run\n"
        "then re-run without --dry-run."
    )
    return 2


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Dispatch to the helper for whichever flag the operator selected.

    Mutually exclusive precedence (review-queue ops > regen > reset >
    pipeline). Default is help + exit 1.
    """
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    elif args.quiet:
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.review_queue:
        return _run_review_queue(args.publish_id)
    if args.approve:
        return _run_approve(args.approve, args.publish_id)
    if args.reject:
        return _run_reject(args.reject, args.publish_id)
    if args.regen_only:
        return _run_regen_only(args.regen_only)
    if args.reset_history:
        return _run_reset_history()
    if args.dry_run or args.dry_run_full or args.publish:
        return _run_pipeline(args.dry_run, args.dry_run_full, args.publish)

    print(
        "No action selected. Run with --help to see operator workflow flags.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(run(_parse_args()))
