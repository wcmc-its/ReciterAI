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
  --reset-history   Truncate SPOTLIGHT_HISTORY# (use after annual
                    hierarchy recompute when subtopic IDs rotate per D-06).
                    Prompts before deletion.

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
import sys
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
            "Print pool ranking + selected 10 + draft ledes. NO Bedrock "
            "LLM critic, NO publish. Cheapest preview."
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
            "Truncate SPOTLIGHT_HISTORY# (use after annual hierarchy "
            "recompute when subtopic IDs rotate per D-06). Prompts for "
            "confirmation before any DynamoDB delete."
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
    """
    from pipeline_hierarchy.bundler import bundle

    return bundle(strict=False)


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


class _RichSubtopicMeta:
    """Adapter exposing slim SubtopicMeta fields plus D-19 UI fields.

    Plan 06-04's ``SubtopicMeta`` is a NamedTuple with four fields. The
    assembler uses ``getattr(meta, "display_name", meta.label)`` for the
    UI fallback, so we expose ``display_name`` / ``short_description`` as
    plain attributes here. Sensitive gate's ``is_sensitive`` reads
    ``label``, ``description``, and ``parent_topic_label`` directly, so
    those names match the NamedTuple contract.
    """

    __slots__ = (
        "subtopic_id",
        "label",
        "description",
        "parent_topic_label",
        "display_name",
        "short_description",
    )

    def __init__(
        self,
        subtopic_id: str,
        label: str,
        description: str,
        parent_topic_label: str,
        display_name: str,
        short_description: str,
    ) -> None:
        self.subtopic_id = subtopic_id
        self.label = label
        self.description = description
        self.parent_topic_label = parent_topic_label
        self.display_name = display_name
        self.short_description = short_description


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
    from spotlight.pool_ranker import rank_pool
    from spotlight.rotation_selector import fetch_history, select_with_diversity
    from spotlight.author_resolver import resolve_authors
    from spotlight.theme_dedup import NearClones, find_near_clones
    from utils.env_check import load_thresholds

    # Hierarchy is loaded up-front so the pool ranker can canonicalize
    # parent_topic for the rotation selector's diversity gate (without
    # this, parent_topic falls back to subtopic_id for slug-style IDs and
    # diversity becomes a no-op).
    hierarchy = _load_hierarchy()
    parent_lookup = _build_parent_lookup(hierarchy)

    # Stage 1+2: always run.
    # Phase 11 D-04: fetch_history requires hierarchy_version to build the
    # new PK shape (SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}).
    # backfill_spotlight uses publish_id == v{today} as hierarchy_version
    # (same convention as publish.py OQ-2 resolution).
    spotlight_hierarchy_version = f"v{date.today().isoformat()}"
    pool = rank_pool(parent_lookup=parent_lookup, author_resolver=resolve_authors)
    history = fetch_history(
        client=None,
        subtopic_ids=[e.subtopic_id for e in pool],
        hierarchy_version=spotlight_hierarchy_version,
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

    selections = select_with_diversity(
        pool, history, near_clones=near_clones.adjacency
    )

    print(f"\nPool ranker: {len(pool)} subtopics ranked.")
    print(f"Rotation selector: {len(selections)} selections.")
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
        return 0

    # Stage 3+4+5+6: --dry-run-full or --publish.
    from spotlight.assembler import build_artifact
    from spotlight.critic import run_critic_loop
    from spotlight.review_queue import write_review_entry
    from spotlight.sensitive_gate import is_sensitive, load_sensitive_tags

    subtopic_metadata = _build_subtopic_metadata(hierarchy)

    publish_id = f"v{date.today().isoformat()}"

    # Stage 3: per-selection critic loop. Sequential by design so each
    # generation can see which institutional-voice openers earlier
    # spotlights already chose, avoiding within-publish repetition
    # (wcmc-its/ReciterAI#2 §3).
    from spotlight.critic import OPENER_RE

    validated_ledes = []
    used_openers: list[str] = []
    for sel in selections:
        meta = subtopic_metadata.get(sel.entry.subtopic_id)
        if meta is None:
            logger.warning(
                "Subtopic metadata missing for %s; skipping",
                sel.entry.subtopic_id,
            )
            continue
        try:
            vlede = run_critic_loop(
                meta=meta,
                papers=list(sel.entry.papers),
                publish_id=publish_id,
                parent_topic=sel.entry.parent_topic,
                excluded_openers=tuple(used_openers),
            )
            if vlede.status == "pass":
                m = OPENER_RE.search(vlede.lede)
                if m and m.group(0) not in used_openers:
                    used_openers.append(m.group(0))
        except ValueError as e:
            # Lede generator rejects subtopics without ≥2 author-identified
            # papers. Log + skip rather than abort the run; the slot drops
            # out of the spotlight set for this publish_id.
            logger.warning(
                "Skipping subtopic %s: %s",
                sel.entry.subtopic_id,
                e,
            )
            continue
        validated_ledes.append((sel, vlede))

    # Stage 3.5: artifact-level critic — fail-closed on duplicate
    # institutional-voice openers across the publish. Per
    # wcmc-its/ReciterAI#2 §3. The first occurrence keeps its slot; later
    # duplicates are routed to the review queue and dropped from the
    # publishable set. Operator can `--regen-only` them to re-roll.
    from spotlight.critic import find_duplicate_openers

    pass_ledes = [
        (i, sel, vlede) for i, (sel, vlede) in enumerate(validated_ledes)
        if vlede.status == "pass"
    ]
    pass_lede_text = [v.lede for _, _, v in pass_ledes]
    duplicate_indices = find_duplicate_openers(pass_lede_text)
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

    # Stage 5: assemble.
    selected_vledes = [vlede for _, vlede in publishable]
    artifact = build_artifact(
        selected=selected_vledes,
        pool=pool,
        subtopic_metadata=subtopic_metadata,
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
    return publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=publishable_selections,
        dry_run=False,
    )


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

    # Reconstruct the Paper list from the prior artifact's paper payload.
    from spotlight.types import Author, Paper

    papers: list[Paper] = []
    for p in target.get("papers", []):
        fa = p.get("first_author", {})
        la = p.get("last_author", {})
        papers.append(
            Paper(
                pmid=p["pmid"],
                title=p.get("title", ""),
                journal=p.get("journal", ""),
                year=int(p.get("year", 0)),
                impact_score=0.0,
                impact_justification="",
                synopsis=meta.description,
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
    """Truncate SPOTLIGHT_HISTORY# partition. Confirms before deleting.

    Mirrors import_enrichment.py:_flush_batch retry pattern for
    BatchWriteItem deletes. Used only after annual hierarchy recompute
    when subtopic IDs rotate (D-06).
    """
    import boto3

    confirm = input(
        "WARNING: this will delete every SPOTLIGHT_HISTORY# row in the "
        "reciterai DynamoDB table. Type 'yes' to confirm: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return 1

    client = boto3.client("dynamodb", region_name="us-east-1")
    table = "reciterai"
    deleted = 0

    paginator = client.get_paginator("scan")
    pages = paginator.paginate(
        TableName=table,
        FilterExpression="begins_with(PK, :prefix)",
        ExpressionAttributeValues={":prefix": {"S": "SPOTLIGHT_HISTORY#"}},
        ProjectionExpression="PK, SK",
    )

    batch: list = []
    for page in pages:
        for item in page.get("Items", []):
            batch.append(
                {"DeleteRequest": {"Key": {"PK": item["PK"], "SK": item["SK"]}}}
            )
            if len(batch) == 25:
                _flush_delete_batch(client, table, batch)
                deleted += len(batch)
                batch = []
    if batch:
        _flush_delete_batch(client, table, batch)
        deleted += len(batch)

    print(f"Deleted {deleted} SPOTLIGHT_HISTORY# rows.")
    return 0


def _flush_delete_batch(client, table: str, batch: list) -> None:
    """One-shot retry on UnprocessedItems (mirror of import_enrichment.py)."""
    import time

    resp = client.batch_write_item(RequestItems={table: batch})
    unprocessed = resp.get("UnprocessedItems", {}).get(table, [])
    if unprocessed:
        time.sleep(0.1)
        client.batch_write_item(RequestItems={table: unprocessed})


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
