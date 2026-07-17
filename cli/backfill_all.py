"""
Full-phase backfill runner (Plan 04-06, Task 2).

Orchestrates `backfill_topic.py` across all 66 non-Aging topics in
taxonomy_v2.json, in activity-count-descending order, then:
  - assembles `out/hierarchy_full.json` from per-topic
    `hierarchy_augmented_<id>.json` files (ephemeral local artifact —
    `out/` is gitignored)
  - invokes `generate_see_also.py` against the full hierarchy
  - merges the bidirectional see_also[] back into hierarchy_full.json
  - copies the final artifact to
    `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json`
  - prints the commit-to-subrepo commands the operator runs (against the
    `feature/chatbot-runtime` branch inside the PM worktree; NOT from the
    parent repo, per CLAUDE.md).

Verdict gate (D-20):
  Reads `.planning/phases/04-subtopic-system/aging_pilot_results.md` and
  refuses to run unless it contains a line matching `## Verdict: GO`. This
  is the programmatic tie to Plan 05's pilot approval. Use
  `--only-verdict-check` to probe the gate without touching any data.

Per-topic logging:
  Captures each `backfill_topic.py` subprocess's stdout/stderr to a rolling
  per-topic table written to `.planning/phases/04-subtopic-system/backfill_log.md`
  (format: | topic_id | activity_count | cluster_count | coverage_pct |
  passes | sonnet_$ | haiku_$ | status |). Cost columns are populated from
  the underlying scripts' printed usage counters when available.

Failure handling:
  - Default: abort on the first topic failure (so operators see the
    problem early instead of silently losing coverage).
  - `--continue-on-error`: record the failure, push the topic into
    `excluded_topics[]` with a `clustering quality fail` reason, and keep
    going. Downstream callers treat excluded topics as Tier-3 fallback
    per D-18.

Dry-run:
  `--dry-run` prints the topic ordering + estimated cost without calling
  any pass or touching DynamoDB. Useful for sanity-checking ordering and
  budget before committing ~4-8 hours of wall time.

Usage:
    # Full run — expect ~4-8 hours wall time at 66 topics x ~4 min each.
    python backfill_all.py

    # Gate check only — returns 0 if Verdict: GO present, 2 otherwise.
    python backfill_all.py --only-verdict-check

    # Plan without spending money.
    python backfill_all.py --dry-run

    # Emit hierarchy_full.json to out/ but skip the PM copy (useful when
    # testing hierarchy assembly separately from the PM subrepo commit).
    python backfill_all.py --skip-pm-copy

    # Tolerate per-topic failures; record and continue.
    python backfill_all.py --continue-on-error

Exit codes:
    0  success
    1  unexpected error (hierarchy assembly, file I/O)
    2  verdict gate not GO (or, under --continue-on-error=false, a per-topic
       backfill failure — the per-topic failure exit code is propagated)

Reference: .planning/phases/04-subtopic-system/04-06-PLAN.md Task 2.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.env_check import load_thresholds  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


# --- Constants ---
REPO_ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_FILE = REPO_ROOT / "taxonomy_v2.json"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "04-subtopic-system"
AGING_PILOT_RESULTS = PHASE_DIR / "aging_pilot_results.md"
# Ephemeral working artifact — gitignored under `out/`. The canonical
# hierarchy artifact lives in S3 (`wcmc-reciterai-hierarchy/{version,latest}/`);
# this on-disk copy is only a multi-step pipeline intermediate (assemble →
# see-also merge → validate → publish → PM copy) and must not be mistaken
# for a tracked planning artifact.
HIERARCHY_FULL_OUT = REPO_ROOT / "out" / "hierarchy_full.json"
SEE_ALSO_FULL_OUT = REPO_ROOT / "out" / "see_also_full.json"
PER_TOPIC_LOG = PHASE_DIR / "backfill_log.md"

# Pilot topic — already processed by Plans 02/03/04/05, skip here.
PILOT_TOPIC_ID = "aging_geroscience"

# Cost model (rough — matches CONTEXT.md §Cost model baseline).
EST_SONNET_PASS1_PER_TOPIC_USD = 0.25
EST_HAIKU_PASS2_PER_ACTIVITY_USD = 0.001

# Lifted to config/thresholds.json `score_floor` (G-18) — same gate the
# rest of the pipeline applies when filtering qualifying activities.
SCORE_FLOOR = load_thresholds()["score_floor"]

# PM worktree destination
PM_WORKTREE = REPO_ROOT / "ReCiter-Publication-Manager"
PM_HIERARCHY_JSON = PM_WORKTREE / "controllers" / "chatbot" / "hierarchy.json"

HIERARCHY_SCHEMA_PATH = REPO_ROOT / "docs" / "hierarchy.schema.json"
HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"


# ---------------------------------------------------------------------------
# Verdict gate
# ---------------------------------------------------------------------------

_VERDICT_GO_RE = re.compile(r"^##\s*Verdict:\s*GO", re.MULTILINE | re.IGNORECASE)


def _check_verdict_go() -> bool:
    """
    Return True iff aging_pilot_results.md contains `## Verdict: GO`.

    This is the programmatic tie to Plan 05's pilot approval (D-20).
    """
    if not AGING_PILOT_RESULTS.exists():
        logger.error(f"Pilot results file missing: {AGING_PILOT_RESULTS}")
        return False
    text = AGING_PILOT_RESULTS.read_text()
    if _VERDICT_GO_RE.search(text):
        logger.info(f"Verdict: GO confirmed in {AGING_PILOT_RESULTS}")
        return True
    logger.error(
        f"Pilot results file does not contain a `## Verdict: GO` line. "
        f"Review {AGING_PILOT_RESULTS} before proceeding."
    )
    return False


# ---------------------------------------------------------------------------
# Taxonomy + activity counts
# ---------------------------------------------------------------------------

def _load_taxonomy_ids() -> list:
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    return [t["id"] for t in data.get("topics", [])]


def _compute_topic_ordering() -> list:
    """Return [(topic_id, activity_count), ...] sorted desc, pilot excluded."""
    from cli.backfill_topic import _count_qualifying_activities

    ids = _load_taxonomy_ids()
    ordered = []
    for tid in ids:
        if tid == PILOT_TOPIC_ID:
            continue
        count = _count_qualifying_activities(tid)
        ordered.append((tid, count))
    ordered.sort(key=lambda kv: -kv[1])
    return ordered


# ---------------------------------------------------------------------------
# Per-topic runner (wraps backfill_topic.py)
# ---------------------------------------------------------------------------

def _ensure_per_topic_log_header() -> None:
    PHASE_DIR.mkdir(parents=True, exist_ok=True)
    if not PER_TOPIC_LOG.exists():
        PER_TOPIC_LOG.write_text(
            "# Per-topic Backfill Log\n\n"
            "| topic_id | activity_count | cluster_count | coverage_pct | "
            "passes | sonnet_$ | haiku_$ | status |\n"
            "| -------- | -------------- | ------------- | ------------ | "
            "------ | -------- | ------- | ------ |\n"
        )


def _run_topic(
    topic_id: str,
    continue_on_error: bool,
    skip_review: bool = False,
) -> tuple[int, str, str]:
    """Run `backfill_topic.py --topic <id>` and capture output.

    Returns (returncode, stdout, stderr). Does NOT raise on non-zero return;
    caller decides based on --continue-on-error.

    If `skip_review` is True, propagates `--skip-review` into the child
    invocation so the human-review gate is bypassed (an audit row is
    still written to backfill_log.md by backfill_topic.py).
    """
    cmd = [
        sys.executable,
        str(REPO_ROOT / "cli" / "backfill_topic.py"),
        "--topic",
        topic_id,
    ]
    if skip_review:
        cmd.append("--skip-review")
    logger.info(f"[topic {topic_id}] invoking: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


_EXCLUDED_RE = re.compile(
    r"^EXCLUDED:(?P<topic>[^:]+):(?P<count>\d+):(?P<reason>\S+)",
    re.MULTILINE,
)


def _parse_excluded_marker(stdout: str) -> dict | None:
    """Scan backfill_topic.py stdout for the EXCLUDED:<topic>:<count>:<reason> line."""
    m = _EXCLUDED_RE.search(stdout)
    if not m:
        return None
    return {
        "id": m.group("topic"),
        "activity_count": int(m.group("count")),
        "reason": m.group("reason"),
    }


# ---------------------------------------------------------------------------
# Hierarchy assembly
# ---------------------------------------------------------------------------

def _augmented_draft_path(topic_id: str) -> Path:
    return PHASE_DIR / f"hierarchy_augmented_{topic_id}.json"


def _assemble_hierarchy(
    all_topic_ids: list,
    excluded_topics: list,
) -> dict:
    """Build hierarchy_full.json from per-topic augmented drafts.

    Any topic id in `all_topic_ids` that has no augmented draft AND is not
    already in `excluded_topics` lands in `excluded_topics[]` with reason
    "below cold-start floor" and activity_count 0 (unknown). This is the
    conservative default so every taxonomy id is accounted for.
    """
    generated_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    out: dict = {
        "version": "subtopic_v1",
        "generated_at": generated_at,
        "taxonomy_version": "taxonomy_v2",
        "excluded_topics": list(excluded_topics),
        "topics": {},
        "see_also": [],
    }

    excluded_ids = {e["id"] for e in out["excluded_topics"]}

    for tid in all_topic_ids:
        if tid in excluded_ids:
            continue
        aug_path = _augmented_draft_path(tid)
        if not aug_path.exists():
            out["excluded_topics"].append({
                "id": tid,
                "reason": "augmented draft missing after backfill",
                "activity_count": 0,
            })
            continue
        with open(aug_path) as f:
            aug = json.load(f)

        # SubtopicDef shape per hierarchy-schema.md
        subtopics_out = []
        for s in aug.get("subtopics", []):
            subtopics_out.append({
                "id": s["id"],
                "label": s.get("label", ""),
                "description": s.get("description", ""),
                "display_name": s.get("display_name") or s.get("label", ""),
                "short_description": s.get("short_description", ""),
                "activity_count": int(s.get("activity_count", 0) or 0),
                "total_weight": float(s.get("total_weight", 0.0) or 0.0),
            })
        out["topics"][tid] = {"subtopics": subtopics_out}

    return out


def _write_hierarchy_full(hierarchy: dict) -> Path:
    HIERARCHY_FULL_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(HIERARCHY_FULL_OUT, "w") as f:
        json.dump(hierarchy, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {HIERARCHY_FULL_OUT}")
    return HIERARCHY_FULL_OUT


def _run_see_also() -> list:
    """Invoke generate_see_also.py and return the merged see_also[] list."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "cli" / "generate_see_also.py"),
        "--input",
        str(HIERARCHY_FULL_OUT),
        "--output",
        str(SEE_ALSO_FULL_OUT),
    ]
    logger.info(f"[see-also] invoking: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    with open(SEE_ALSO_FULL_OUT) as f:
        data = json.load(f)
    return data.get("see_also", []) or []


def _merge_see_also_into_hierarchy(see_also: list) -> None:
    with open(HIERARCHY_FULL_OUT) as f:
        h = json.load(f)
    h["see_also"] = see_also
    with open(HIERARCHY_FULL_OUT, "w") as f:
        json.dump(h, f, indent=2, ensure_ascii=False)
    logger.info(f"Merged {len(see_also)} see_also links into {HIERARCHY_FULL_OUT}")


# ---------------------------------------------------------------------------
# PM worktree copy + commit instructions
# ---------------------------------------------------------------------------

def _copy_to_pm() -> Path:
    PM_HIERARCHY_JSON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HIERARCHY_FULL_OUT, PM_HIERARCHY_JSON)
    logger.info(f"Copied hierarchy to {PM_HIERARCHY_JSON}")
    return PM_HIERARCHY_JSON


def _print_pm_commit_instructions() -> None:
    print(
        "\n=== PM subrepo commit instructions ===\n"
        "The hierarchy.json artifact has been copied into the PM worktree.\n"
        "Now commit it on the feature/chatbot-runtime branch per CLAUDE.md\n"
        "(commit-to-subrepo; do NOT commit from the parent repo):\n"
        "\n"
        "    cd ReCiter-Publication-Manager\n"
        "    git checkout feature/chatbot-runtime\n"
        "    git add controllers/chatbot/hierarchy.json\n"
        "    git commit -m 'feat(04): add subtopic hierarchy.json (Plan 06 full backfill)'\n"
        "\n"
        "Plans 07-10 consume this artifact.\n"
    )


# ---------------------------------------------------------------------------
# Cost parsing (best-effort)
# ---------------------------------------------------------------------------

_COST_RE = re.compile(r"estimated_cost_usd:\s*([0-9.]+)")


def _parse_estimated_cost_usd(stdout: str) -> float:
    m = _COST_RE.search(stdout)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Publish path
# ---------------------------------------------------------------------------

def _run_publish(dry_run: bool) -> int:
    """
    Validate hierarchy_full.json against hierarchy.schema.json, then upload
    to S3 at v{ISO-date}/ and latest/.

    Bypasses the D-20 verdict gate (non-LLM operation, same as --assemble-only).
    PM worktree copy ALWAYS runs after S3 upload (D-13 — no flag to disable).

    Returns process exit code (0 = success, 1 = validation error or upload error).
    """
    from utils.s3_client import S3HierarchyClient
    from jsonschema import Draft202012Validator
    from botocore.exceptions import NoCredentialsError
    from gates.shrink_guard import shrink_guard_gate
    import hashlib
    from datetime import date, datetime, timezone

    # Step 1: Ensure hierarchy_full.json exists (assemble if missing).
    if not HIERARCHY_FULL_OUT.exists():
        logger.info("hierarchy_full.json missing — running assemble-only first.")
        rc = _run_assemble_only(skip_pm_copy=True)
        if rc != 0:
            return rc

    # Step 2: Load schema; error if missing.
    if not HIERARCHY_SCHEMA_PATH.exists():
        logger.error(f"Schema file missing: {HIERARCHY_SCHEMA_PATH}")
        return 1

    with open(HIERARCHY_FULL_OUT) as f:
        hierarchy = json.load(f)
    with open(HIERARCHY_SCHEMA_PATH) as f:
        schema = json.load(f)

    # Step 3: Validate (fail-fast per D-16 — no warn-and-publish).
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(hierarchy), key=lambda e: list(e.absolute_path))
    if errors:
        print("\nSCHEMA VALIDATION FAILED — hierarchy_full.json does not match schema:\n")
        for e in errors:
            path = " > ".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"  [{path}] {e.message}")
        print(f"\nFix hierarchy_full.json or update docs/hierarchy.schema.json before publishing.")
        return 1
    logger.info("Schema validation: PASS")

    # Step 4: Compute manifest bytes (sha256 over in-memory bytes — T-05-02-02).
    hierarchy_bytes = json.dumps(hierarchy, indent=2, ensure_ascii=False).encode("utf-8")
    schema_bytes = json.dumps(schema, indent=2, ensure_ascii=False).encode("utf-8")
    sha256 = hashlib.sha256(hierarchy_bytes).hexdigest()
    version = f"v{date.today().isoformat()}"
    schema_version = schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")

    # Manifest field order is LOCKED (RESEARCH.md §2) — do NOT sort_keys (T-05-02-03).
    manifest = {
        "schema_version": schema_version,
        "taxonomy_version": hierarchy.get("taxonomy_version", "unknown"),
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": sha256,
        "artifact_bytes": len(hierarchy_bytes),
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    # Step 5: Dry-run short-circuit (validate-only; no S3 PutObject calls).
    if dry_run:
        print(f"\n=== DRY RUN — --publish preview ===")
        print(f"  version:          {version}")
        print(f"  schema_version:   {schema_version}")
        print(f"  taxonomy_version: {manifest['taxonomy_version']}")
        print(f"  sha256:           {sha256}")
        print(f"  artifact_bytes:   {len(hierarchy_bytes):,}")
        print(f"  Would upload to:  s3://{HIERARCHY_BUCKET}/{version}/  (3 objects)")
        print(f"                    s3://{HIERARCHY_BUCKET}/latest/     (3 objects, overwrite)")
        print(f"\nManifest preview:\n{json.dumps(manifest, indent=2)}")
        return 0

    # Step 6: Upload to S3 (T-05-02-04 — validation gate precedes all PutObject calls).
    try:
        s3 = S3HierarchyClient(bucket=HIERARCHY_BUCKET)

        # Idempotency check: warn on same-day re-publish (T-05-02-05).
        if s3.key_exists(f"{version}/manifest.json"):
            logger.warning(
                f"{version}/ already exists in s3://{HIERARCHY_BUCKET} "
                f"— overwriting (same-day re-publish)."
            )

        # §4.2 shrink guard — this raw-publish path bypassed the gate that
        # pipeline_hierarchy/publish.py runs, and the schema has no minItems on
        # subtopics, so a truncated hierarchy validates and ships. Block a
        # catastrophic shrink vs the live latest/ BEFORE any PutObject. Baseline
        # is counted off the prior latest/hierarchy.json itself (not the manifest,
        # which this path does not stamp with a subtopic_count), so it works no
        # matter which publisher wrote the prior. Fail-open on a missing/corrupt
        # prior; override with BACKFILL_PUBLISH_FORCE=1.
        new_subtopic_count = sum(
            len(t.get("subtopics", [])) for t in hierarchy.get("topics", {}).values()
        )
        prev_subtopic_count = None
        try:
            if s3.key_exists("latest/hierarchy.json"):
                prev_h = json.loads(s3.get_object_bytes("latest/hierarchy.json"))
                prev_subtopic_count = sum(
                    len(t.get("subtopics", [])) for t in prev_h.get("topics", {}).values()
                )
        except Exception as exc:  # noqa: BLE001 — fail-open, but say so
            logger.warning(
                "shrink guard: could not read prior latest/hierarchy.json (%s); "
                "guard is fail-open this run", exc,
            )
        gate = shrink_guard_gate(
            prev_subtopic_count=prev_subtopic_count, new_subtopic_count=new_subtopic_count
        )
        if gate.blocked and os.environ.get("BACKFILL_PUBLISH_FORCE") != "1":
            print(
                f"\nABORT: hierarchy {gate.summary} — refusing to overwrite latest/. "
                f"Re-run with BACKFILL_PUBLISH_FORCE=1 to override."
            )
            return 1

        # Versioned prefix (D-04).
        s3.put_object(f"{version}/hierarchy.json", hierarchy_bytes)
        s3.put_object(f"{version}/hierarchy.schema.json", schema_bytes)
        s3.put_object(f"{version}/manifest.json", manifest_bytes)

        # latest/ overwrite (D-05).
        s3.put_object("latest/hierarchy.json", hierarchy_bytes)
        s3.put_object("latest/hierarchy.schema.json", schema_bytes)
        s3.put_object("latest/manifest.json", manifest_bytes)

    except NoCredentialsError:
        print(
            "\nABORT: AWS credentials not found. Ensure AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY are exported in your shell (from ~/.zshrc). "
            "Run: source ~/.zshrc && python backfill_all.py --publish"
        )
        return 1

    # Step 7: PM worktree copy (D-13 — ALWAYS runs in --publish mode; no flag to disable).
    if not PM_WORKTREE.exists():
        logger.error(
            f"PM worktree not found at {PM_WORKTREE}. "
            f"Cannot complete --publish without PM copy step."
        )
        return 1
    _copy_to_pm()
    _print_pm_commit_instructions()

    # Step 8: Summary.
    print(
        f"\n=== backfill_all.py --publish complete ===\n"
        f"  version:          {version}\n"
        f"  schema_version:   {schema_version}\n"
        f"  sha256:           {sha256}\n"
        f"  artifact_bytes:   {len(hierarchy_bytes):,}\n"
        f"  S3 (versioned):   s3://{HIERARCHY_BUCKET}/{version}/\n"
        f"  S3 (latest):      s3://{HIERARCHY_BUCKET}/latest/\n"
        f"  PM artifact:      {PM_HIERARCHY_JSON}\n"
    )
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_assemble_only(skip_pm_copy: bool) -> int:
    """
    Re-assemble `hierarchy_full.json` from the existing per-topic augmented
    drafts WITHOUT re-running per-topic Pass 1/2/3 or see-also generation.

    Use case: an out-of-band edit to the augmented drafts (e.g., the D-19
    `relabel_subtopics.py` UI-field backfill) needs to land in the assembled
    artifact before the next annual recompute.

    Preserves `excluded_topics` and `see_also[]` from the prior
    `hierarchy_full.json` so cold-start floor entries and the bidirectional
    see-also network are not silently dropped. Subtopic IDs are stable per
    D-06, so the prior see-also links remain valid.

    Returns: process exit code (0 on success).
    """
    t_start = time.time()

    if not HIERARCHY_FULL_OUT.exists():
        logger.error(
            f"--assemble-only requires a prior {HIERARCHY_FULL_OUT} to inherit "
            f"`excluded_topics` and `see_also[]` from. Run a full backfill first."
        )
        return 1

    with open(HIERARCHY_FULL_OUT) as f:
        prior = json.load(f)
    prior_excluded = list(prior.get("excluded_topics", []) or [])
    prior_see_also = list(prior.get("see_also", []) or [])
    logger.info(
        f"Inherited from prior hierarchy: "
        f"{len(prior_excluded)} excluded_topics, "
        f"{len(prior_see_also)} see_also links."
    )

    all_topic_ids = _load_taxonomy_ids()
    logger.info(
        f"Re-assembling hierarchy_full.json from augmented drafts "
        f"({len(all_topic_ids)} topics in taxonomy)..."
    )
    hierarchy = _assemble_hierarchy(all_topic_ids, prior_excluded)
    hierarchy["see_also"] = prior_see_also
    _write_hierarchy_full(hierarchy)

    if skip_pm_copy:
        logger.info(
            "--skip-pm-copy: PM worktree copy skipped. "
            f"Hierarchy artifact at {HIERARCHY_FULL_OUT}."
        )
    else:
        if not PM_WORKTREE.exists():
            logger.error(
                f"PM worktree not found at {PM_WORKTREE}. "
                f"Either create the worktree or pass --skip-pm-copy."
            )
            return 1
        _copy_to_pm()
        _print_pm_commit_instructions()

    elapsed_s = time.time() - t_start
    final_excluded = len(hierarchy.get("excluded_topics", []) or [])
    final_topics = len(hierarchy.get("topics", {}) or {})
    print(
        f"\n=== backfill_all.py --assemble-only complete ===\n"
        f"  topics in hierarchy: {final_topics}\n"
        f"  excluded_topics:     {final_excluded} (preserved from prior)\n"
        f"  see_also links:      {len(prior_see_also)} (preserved from prior)\n"
        f"  hierarchy.json:      {HIERARCHY_FULL_OUT}\n"
        f"  PM artifact:         {PM_HIERARCHY_JSON if not skip_pm_copy else '(skipped)'}\n"
        f"  elapsed:             {elapsed_s:.1f}s\n"
    )
    return 0


def run(
    dry_run: bool,
    only_verdict_check: bool,
    skip_pm_copy: bool,
    continue_on_error: bool,
    skip_all_reviews: bool = False,
    assemble_only: bool = False,
    publish: bool = False,
) -> int:
    # Assemble-only path: short-circuit before the verdict gate. Assembly is a
    # non-LLM operation; the D-20 verdict gate exists to guard LLM spend.
    if assemble_only:
        return _run_assemble_only(skip_pm_copy=skip_pm_copy)

    # Publish path: validate + upload (non-LLM operation). Bypasses verdict gate.
    if publish:
        return _run_publish(dry_run=dry_run)

    t_start = time.time()

    # 1. Verdict gate (D-20) — always enforce.
    if not _check_verdict_go():
        print(
            "\nABORT: aging_pilot_results.md does not end with "
            "`## Verdict: GO`. Run Plan 05's pilot gate (aging_pilot_gate.py) "
            "and obtain human approval before full backfill."
        )
        return 2
    if only_verdict_check:
        logger.info("Verdict gate check complete. Exiting 0 (--only-verdict-check).")
        return 0

    # 2. Compute topic ordering (excludes pilot).
    logger.info("Computing topic ordering (may take a moment — queries DynamoDB)...")
    ordering = _compute_topic_ordering()
    logger.info(f"{len(ordering)} non-pilot topics to process.")
    for i, (tid, cnt) in enumerate(ordering):
        logger.info(f"  [{i+1:2d}] {tid:<50} {cnt:>6} activities")

    # 3. Dry-run short-circuit.
    if dry_run:
        total_activities = sum(cnt for _, cnt in ordering)
        est_sonnet = EST_SONNET_PASS1_PER_TOPIC_USD * len(ordering)
        est_haiku = EST_HAIKU_PASS2_PER_ACTIVITY_USD * total_activities
        print(
            f"\n=== DRY RUN ===\n"
            f"  topics to process: {len(ordering)}\n"
            f"  total qualifying activities: {total_activities}\n"
            f"  estimated Sonnet (Pass 1) cost: ${est_sonnet:.2f}\n"
            f"  estimated Haiku  (Pass 2) cost: ${est_haiku:.2f}\n"
            f"  estimated total: ${est_sonnet + est_haiku:.2f}\n"
        )
        return 0

    # 4. Per-topic loop.
    _ensure_per_topic_log_header()
    excluded_topics: list = []
    failures: list = []
    total_sonnet_cost = 0.0  # Pass 1 (cannot be measured cleanly from stdout yet)
    total_haiku_cost = 0.0   # Pass 2 (parsed from assign_subtopics stdout)

    for idx, (tid, activity_count) in enumerate(ordering):
        logger.info(
            f"--- [{idx+1}/{len(ordering)}] topic={tid} "
            f"(activities={activity_count}) ---"
        )
        rc, stdout, stderr = _run_topic(
            tid,
            continue_on_error=continue_on_error,
            skip_review=skip_all_reviews,
        )

        # Append subprocess stdout to the per-topic log for auditability.
        with open(PER_TOPIC_LOG, "a") as f:
            f.write(f"\n<!-- === {tid} === rc={rc} -->\n")
            f.write("```\n")
            f.write(stdout[-4000:])
            if stderr.strip():
                f.write("\n-- stderr --\n")
                f.write(stderr[-2000:])
            f.write("\n```\n")

        # Detect cold-start exclusion.
        excluded = _parse_excluded_marker(stdout)
        if excluded is not None:
            excluded_topics.append({
                "id": excluded["id"],
                "reason": "below cold-start floor",
                "activity_count": excluded["activity_count"],
            })
            logger.info(
                f"[{tid}] excluded: {excluded['activity_count']} "
                f"activities below cold-start floor"
            )
            continue

        # Non-zero exit code == failure.
        if rc != 0:
            msg = (
                f"backfill_topic.py --topic {tid} returned rc={rc}. "
                f"stderr tail:\n{stderr[-500:]}"
            )
            failures.append({"topic_id": tid, "rc": rc, "stderr_tail": stderr[-500:]})
            if continue_on_error:
                logger.warning(f"[{tid}] FAILED (continuing): {msg}")
                excluded_topics.append({
                    "id": tid,
                    "reason": "clustering quality fail",
                    "activity_count": activity_count,
                })
                continue
            logger.error(f"[{tid}] FAILED: {msg}")
            print(
                f"\nABORT at topic {tid}. Rerun with --continue-on-error "
                f"to skip failures, or fix the underlying issue and rerun. "
                f"Per-topic logs at {PER_TOPIC_LOG}."
            )
            return rc

        # Best-effort cost parsing from Pass 2 stdout.
        total_haiku_cost += _parse_estimated_cost_usd(stdout)

    # 5. Assemble hierarchy_full.json.
    logger.info("Assembling hierarchy_full.json from per-topic augmented drafts...")
    # Include the pilot topic's augmented draft too if present (it IS in
    # taxonomy_v2 and must appear in the final hierarchy).
    all_topic_ids = _load_taxonomy_ids()
    hierarchy = _assemble_hierarchy(all_topic_ids, excluded_topics)
    _write_hierarchy_full(hierarchy)

    # 6. See-also generation + merge.
    logger.info("Running see-also generation over the full hierarchy...")
    try:
        see_also_links = _run_see_also()
    except subprocess.CalledProcessError as e:
        logger.error(f"generate_see_also.py failed: {e}")
        see_also_links = []
    _merge_see_also_into_hierarchy(see_also_links)

    # 7. Copy to PM worktree unless --skip-pm-copy.
    if skip_pm_copy:
        logger.info(
            "--skip-pm-copy: PM worktree copy skipped. "
            f"Hierarchy artifact at {HIERARCHY_FULL_OUT}."
        )
    else:
        if not PM_WORKTREE.exists():
            logger.error(
                f"PM worktree not found at {PM_WORKTREE}. "
                f"Either create the worktree or pass --skip-pm-copy."
            )
            return 1
        _copy_to_pm()
        _print_pm_commit_instructions()

    # 8. Summary.
    elapsed_s = time.time() - t_start
    print(
        f"\n=== backfill_all.py complete ===\n"
        f"  topics processed:   {len(ordering)}\n"
        f"  excluded:           {len(excluded_topics)}\n"
        f"    (cold-start floor + clustering-quality fail)\n"
        f"  failures:           {len(failures)}\n"
        f"  sonnet cost (est):  ${total_sonnet_cost:.2f}  (Pass 1 — not auto-measured)\n"
        f"  haiku  cost (est):  ${total_haiku_cost:.2f}  (Pass 2 — parsed from stdout)\n"
        f"  see_also links:     {len(see_also_links)}\n"
        f"  hierarchy.json:     {HIERARCHY_FULL_OUT}\n"
        f"  PM artifact:        {PM_HIERARCHY_JSON if not skip_pm_copy else '(skipped)'}\n"
        f"  elapsed:            {elapsed_s:.0f}s ({elapsed_s/60:.1f}m)\n"
    )
    if failures and not continue_on_error:
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Full-phase Subtopic backfill runner. Loops backfill_topic.py "
            "across all non-pilot topics, assembles hierarchy.json, runs "
            "see-also generation, and copies the final artifact to the PM "
            "worktree. Gated on aging_pilot_results.md Verdict: GO."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print topic ordering + estimated cost; do NOT call any pass, "
            "DynamoDB write, or Bedrock call."
        ),
    )
    parser.add_argument(
        "--only-verdict-check",
        action="store_true",
        help=(
            "Check for `## Verdict: GO` in aging_pilot_results.md and exit "
            "(0 if present, 2 otherwise). Useful before committing to a "
            "full run."
        ),
    )
    parser.add_argument(
        "--skip-pm-copy",
        action="store_true",
        help=(
            "Skip the copy of hierarchy.json into the "
            "ReCiter-Publication-Manager worktree. Hierarchy is still "
            "written to out/hierarchy_full.json (ephemeral, gitignored)."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Tolerate per-topic failures: record the topic in "
            "excluded_topics[] with reason `clustering quality fail` and "
            "proceed. Default: abort on first failure."
        ),
    )
    parser.add_argument(
        "--skip-all-reviews",
        action="store_true",
        help=(
            "Bypass the human-review gate for every topic by propagating "
            "`--skip-review` into each backfill_topic.py invocation. Each "
            "topic still records a SKIP_REVIEW audit row in "
            "backfill_log.md. Use for fully-automated one-shot runs where "
            "you accept the risk of un-reviewed cluster labels."
        ),
    )
    parser.add_argument(
        "--assemble-only",
        action="store_true",
        help=(
            "Skip the per-topic Pass 1/2/3 loop AND see-also generation; "
            "only re-assemble hierarchy_full.json from the existing "
            "per-topic augmented drafts. Inherits `excluded_topics` and "
            "`see_also[]` from the prior hierarchy_full.json so cold-start "
            "entries and the see-also network are preserved (subtopic IDs "
            "are stable per D-06). Use after an out-of-band edit to the "
            "augmented drafts (e.g., relabel_subtopics.py for D-19 UI "
            "fields). Does not call any LLM. Bypasses the D-20 verdict "
            "gate. Honors --skip-pm-copy."
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Validate hierarchy_full.json against docs/hierarchy.schema.json, then "
            "upload hierarchy.json + hierarchy.schema.json + manifest.json to "
            "s3://wcmc-reciterai-hierarchy/v{ISO-date}/ AND s3://.../latest/. "
            "If hierarchy_full.json is missing, runs --assemble-only first. "
            "Honors --dry-run (dry-run prints manifest "
            "preview without uploading). Bypasses the D-20 verdict gate "
            "(non-LLM operation). Fails and does not upload if schema "
            "validation fails (D-16)."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(run(
        dry_run=args.dry_run,
        only_verdict_check=args.only_verdict_check,
        skip_pm_copy=args.skip_pm_copy,
        continue_on_error=args.continue_on_error,
        skip_all_reviews=args.skip_all_reviews,
        assemble_only=args.assemble_only,
        publish=args.publish,
    ))
