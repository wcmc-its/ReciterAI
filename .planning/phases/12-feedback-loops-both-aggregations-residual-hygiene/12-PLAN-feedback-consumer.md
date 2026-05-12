---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: feedback-consumer
type: execute
wave: 2
depends_on:
  - feedback-producer
  - drift-extension
  - thresholds-substrate
files_modified:
  - pipeline_feedback/__init__.py
  - pipeline_feedback/__main__.py
  - pipeline_feedback/cli.py
  - pipeline_feedback/sweep.py
  - pipeline_feedback/finding_records.py
  - pipeline_feedback/markdown_render.py
  - pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md
  - pipeline_cold/run.py
  - tests/test_feedback_sweep.py
  - tests/test_feedback_finding_records.py
  - tests/test_feedback_render.py
  - tests/test_feedback_cli.py
autonomous: false
requirements:
  - "spec-§9-consumer"
  - D-01
  - D-02
  - D-03
  - D-04
  - D-05
  - D-06
  - D-07
  - D-08
  - D-09
  - D-11
  - D-30
  - D-32
tags: [feedback, sweep, finding-records, cli, sonnet, markdown-render]
must_haves:
  truths:
    - "pipeline_feedback/ is a new Python package with sweep + render entry points"
    - "Operator CLI: `python -m pipeline_feedback sweep --since DAYS` runs as a diagnosis tool"
    - "Cold-path stage: `pipeline_cold/run.py` registers a non-gating feedback_sweep stage between rollup and backfill_spotlight; its subprocess returns 0 even with findings (D-02)"
    - "Sweep produces three typed finding records: CANDIDATE_TOPIC#{slug} from Sonnet uncovered-PMID pass, RECLUSTER_RECOMMENDATION#{topic_id} from per-topic LOW_CONFIDENCE persistence, SPOTLIGHT_DIAGNOSTIC#{subtopic_id} (D-08 re-keyed) from CRITIC_REJECT# distribution per subtopic"
    - "Each sweep generates one source_sweep_run_id UUID; all findings of that sweep carry the same id; idempotent overwrite per (PK, run_id) — no cross-sweep merging (D-05)"
    - "Uncovered-PMID input is capped at feedback_sweep_max_pmids (worst-fitting first by ascending top_topic_score per D-06); overflow surfaces truncated=true + total_unprocessed_remaining=N"
    - "Recluster trigger reads materialized per_topic_low_confidence from DRIFT#evaluation rows (Plan drift-extension); applies recluster_persistence_days duration threshold (D-07)"
    - "SPOTLIGHT_DIAGNOSTIC#{subtopic_id} carries reason_code distribution (per D-30 vocabulary) + underlying_rejects list (suffix shape {publish_id}#{subtopic_id}#{pmid_set_hash} per D-08) capped at feedback_diagnostic_max_underlying (D-32) for drill-down; per-faculty drill-down available via author_cwids in underlying CRITIC_REJECT# rows"
    - "render <run_id> CLI emits deterministic markdown (same rows → same bytes; no generated_at in body; timestamp in filename only)"
    - "_fetch_rows_by_run_id is fully implemented via boto3 table.scan(FilterExpression=Attr('source_sweep_run_id').eq(run_id)) with pagination (analog: review/validator.py:_query_pending_rows per PATTERNS.md)"
  artifacts:
    - path: "pipeline_feedback/__init__.py"
      provides: "Package marker"
      contains: ""
    - path: "pipeline_feedback/__main__.py"
      provides: "python -m pipeline_feedback entry dispatching to cli.main"
      contains: "from pipeline_feedback.cli import main"
    - path: "pipeline_feedback/cli.py"
      provides: "argparse subcommands: sweep + render; exit codes documented in module docstring; _fetch_rows_by_run_id implemented (no stub)"
      contains: "add_subparsers"
    - path: "pipeline_feedback/sweep.py"
      provides: "run_sweep(*, table, since, max_pmids, run_id, triggered_by) → FeedbackSweepRun"
      contains: "run_sweep"
    - path: "pipeline_feedback/finding_records.py"
      provides: "build_candidate_topic_record + build_recluster_recommendation_record + build_spotlight_diagnostic_record (keyed by subtopic_id per D-08)"
      contains: "build_candidate_topic_record"
    - path: "pipeline_feedback/markdown_render.py"
      provides: "render_sweep_markdown(run_id, rows) → bytes — deterministic"
      contains: "def render_sweep_markdown"
    - path: "pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md"
      provides: "Sonnet prompt for candidate-topic discovery from uncovered PMIDs"
      contains: "uncovered"
    - path: "pipeline_cold/run.py"
      provides: "Non-gating feedback_sweep stage registered between rollup and backfill_spotlight"
      contains: "feedback_sweep"
  key_links:
    - from: "pipeline_feedback/sweep.py"
      to: "DRIFT#evaluation rows (Plan drift-extension's per_topic_low_confidence field)"
      via: "read per_topic_low_confidence dict via item.get('per_topic_low_confidence', {})"
      pattern: "per_topic_low_confidence"
    - from: "pipeline_feedback/sweep.py"
      to: "CRITIC_REJECT# rows (Plan feedback-producer, per-publish/per-subtopic keyed)"
      via: "query for diagnostic aggregation, count distinct (publish_id, pmid_set_hash) pairs per subtopic_id (D-09 semantic), aggregate reason_code distribution"
      pattern: "CRITIC_REJECT"
    - from: "pipeline_feedback/sweep.py"
      to: "UNCOVERED_PMID# rows (Phase 10 producer)"
      via: "query rows newer than since-watermark, sort by top_topic_score asc, cap at max_pmids"
      pattern: "UNCOVERED_PMID"
    - from: "pipeline_cold/run.py default_cold_stages"
      to: "pipeline_feedback __main__ sweep subprocess"
      via: "ColdStage(name='feedback_sweep', command=[python, -m, pipeline_feedback, sweep, ...], description='Non-gating Sonnet sweep ...')"
      pattern: "feedback_sweep"
---

<objective>
Build the wave-2 §9 consumer: a `pipeline_feedback/` package that the operator invokes via `python -m pipeline_feedback sweep --since DAYS` AND that `pipeline_cold.run.main()` invokes as a non-gating stage between rollup and backfill_spotlight. The sweep reads three event sources (UNCOVERED_PMID# directly; LOW_CONFIDENCE_ASSIGNMENT# via the materialized DRIFT#evaluation per_topic_low_confidence dict from Plan drift-extension; CRITIC_REJECT# from Plan feedback-producer) and writes three typed finding records: `CANDIDATE_TOPIC#{slug}`, `RECLUSTER_RECOMMENDATION#{topic_id}`, and **`SPOTLIGHT_DIAGNOSTIC#{subtopic_id}` (re-keyed per CONTEXT D-08 CR-2026-05-12 — was per-cwid; now per-subtopic; per-faculty drill-down via author_cwids in underlying CRITIC_REJECT# rows).**

Purpose: today these signals exist as events but nothing consumes them. Operators must vigilance-monitor. Phase 12's D-04 closes the loop — each signal becomes a queryable backlog row, accessible to operators via the CLI and to future automation via DDB queries.

**Plan-size note (per plan-checker W-6):** This plan has three tasks. Task 3 depends on Task 2's sweep functions importable + green-tested. Between Task 2 and Task 3 the plan emits a `checkpoint:human-verify` so the executor can resume with fresh context if needed.

Output: pipeline_feedback/ package (7 files), cold-stage registration, four test files. Heaviest plan in Phase 12 — explicitly the long-pole for wave 2 alongside G-36.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md
@.planning/phases/11-versioning-review-diff/11-SUMMARY-review-state.md

<interfaces>
<!-- The three sources Plan feedback-consumer reads from -->

UNCOVERED_PMID# rows (Phase 10 producer, verified in utils/event_records.py):
- PK: UNCOVERED_PMID#{pmid}, SK: GLOBAL
- Fields: pmid, taxonomy_version, top_topic_score (Decimal), top_topics (list of {topic_id, score}), created_at, source_stage
- Read by: D-06 sweep input; sort by top_topic_score ascending, cap at feedback_sweep_max_pmids

DRIFT#evaluation rows (Phase 10 + Plan drift-extension D-34 sparse per_topic_low_confidence):
- PK: DRIFT#evaluation, SK: DAY#YYYY-MM-DD
- New field per Plan drift-extension: per_topic_low_confidence: dict[str, int] (sparse — absent ≡ zero)
- Read by: D-07 recluster trigger; iterate prior recluster_persistence_days rows; for each topic, count how many days had non-zero count above drift_low_confidence_topic_max; if count == recluster_persistence_days, emit RECLUSTER_RECOMMENDATION#{topic_id}

CRITIC_REJECT# rows (Plan feedback-producer, re-keyed per CONTEXT D-08):
- PK: **CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}**, SK: GLOBAL
- Fields: publish_id, subtopic_id, pmid_set, pmid_set_hash, **author_cwids: list[str]** (D-08 body field for per-faculty drill-down), reason_code (one of CritReasonCode), regen_count, reason, created_at, source_stage="spotlight.critic"
- Read by: D-09 diagnostic aggregation; per **subtopic_id** count distinct **(publish_id, pmid_set_hash) pairs** within critic_reject_persistence_days window; if >= **critic_reject_subtopic_max** (renamed from critic_reject_cwid_max per D-08/D-11) emit SPOTLIGHT_DIAGNOSTIC#{subtopic_id}

<!-- The three finding-record outputs Plan feedback-consumer writes -->

CANDIDATE_TOPIC#{slug}:
- PK: CANDIDATE_TOPIC#{slug}, SK: GLOBAL
- Fields: slug, proposed_label, source_pmids (list), sonnet_rationale (str), source_sweep_run_id, triggered_by, truncated (bool, if sweep cap was hit), total_unprocessed_remaining (int when truncated), created_at, source_stage="feedback.sweep"

RECLUSTER_RECOMMENDATION#{topic_id}:
- PK: RECLUSTER_RECOMMENDATION#{topic_id}, SK: GLOBAL
- Fields: topic_id, evaluation_history (list of {date: ISO, count: int} for the recluster_persistence_days days that tripped), source_sweep_run_id, triggered_by, created_at, source_stage="feedback.sweep"

**SPOTLIGHT_DIAGNOSTIC#{subtopic_id} (D-08 re-keyed from per-cwid to per-subtopic):**
- PK: **SPOTLIGHT_DIAGNOSTIC#{subtopic_id}**, SK: GLOBAL
- Fields: **subtopic_id** (was cwid), reason_code_distribution: dict[reason_code: count], **distinct_pmid_set_count** (int, the D-09 semantics — "distinct (publish_id, pmid_set_hash) pairs over the window for this subtopic" — document this verbatim in the docstring), **underlying_rejects: list[str]** (D-32, capped at feedback_diagnostic_max_underlying — store the **{publish_id}#{subtopic_id}#{pmid_set_hash}** suffixes per D-08 PK shape; on overflow set underlying_rejects_truncated=true + total_underlying=N), source_sweep_run_id, triggered_by, window_days, created_at, source_stage="feedback.sweep"
- **Per-faculty drill-down:** available by reading the underlying CRITIC_REJECT# rows' body `author_cwids` field. The diagnostic row does NOT carry author_cwids directly — the source of truth lives on the per-(publish, subtopic, pmid_set) rejection rows.

<!-- D-30 reason_code vocabulary (matching CritReasonCode from Plan feedback-producer) -->
- "active_verb", "anchored_in_synopses", "no_faculty_named", "institutional_voice" — post-LLM
- "pre_llm_gate" — deterministic gate failed (drill-down via row's pre_llm_constraint field)
- "unknown" — vocabulary drift (drill-down via row's raw_failed_constraint field)

<!-- Tunables read from config/thresholds.json (Plan thresholds-substrate, post-D-08 rename + corrected defaults) -->
- feedback_sweep_max_pmids: 200
- recluster_persistence_days: 7
- **critic_reject_persistence_days: 90** (CORRECTED from 14; 90-day window covers ~3 monthly spotlight cycles per Phase 10 D-10)
- **critic_reject_subtopic_max: 2** (RENAMED from critic_reject_cwid_max; default 2 = "2 of last 3 monthly spotlights for this subtopic had critic-failed pmid_set")
- feedback_diagnostic_max_underlying: 20
- drift_low_confidence_topic_max: 50 (existing)

<!-- Cold-stage placement (per RESEARCH F-7 + PATTERNS.md pipeline_cold/run.py section) -->

Insert ColdStage between `rollup` (line 116-120) and `backfill_spotlight` (line 121-125):
```python
ColdStage(
    name="feedback_sweep",
    command=[sys.executable, "-m", "pipeline_feedback", "sweep"],
    description="Non-gating Sonnet sweep over UNCOVERED_PMID#/LOW_CONFIDENCE_ASSIGNMENT#/CRITIC_REJECT# events → three typed finding records (CANDIDATE_TOPIC#, RECLUSTER_RECOMMENDATION#, SPOTLIGHT_DIAGNOSTIC#{subtopic_id})",
),
```

**Pre-edit assertion (W-7 hardening):** before modifying default_cold_stages, the executor MUST run
```bash
python -c "from pipeline_cold.run import default_cold_stages; names=[s.name for s in default_cold_stages()]; assert 'rollup' in names and 'backfill_spotlight' in names, f'expected rollup+backfill_spotlight in {names}'"
```
If the assertion fails, surface a CHECKPOINT — do NOT silently fall back to a different placement. The cold-stage placement is load-bearing (D-02 non-gating between rollup and backfill_spotlight); a missing anchor stage means the cold-path orchestration shape changed and needs orchestrator review.

The subprocess returns 0 even when findings exist (D-02 — non-gating); only DDB/Bedrock operational failures return non-zero.

<!-- D-03 deterministic markdown -->

render_sweep_markdown(run_id, rows) takes a list of finding-record dicts and returns bytes. Same input → same bytes. NO generated_at in the body. The timestamp lives in the caller's filename (feedback_sweep_{run_id}_{date}.md). Sort records deterministically by (record_type, PK).

<!-- _fetch_rows_by_run_id implementation (replaces W-5 stub) -->

```python
from boto3.dynamodb.conditions import Attr

def _fetch_rows_by_run_id(table, run_id: str) -> list[dict]:
    """Scan the table for rows carrying source_sweep_run_id == run_id.

    Pattern analog: review/validator.py:_query_pending_rows (per PATTERNS.md).
    Pagination: boto3 table.scan returns LastEvaluatedKey when results truncate;
    we loop until absent. Bounded by the number of findings emitted in one
    sweep (3 partitions × ≤ N findings each), so scan is acceptable here —
    a GSI keyed by source_sweep_run_id is a deferred optimization (see CONTEXT
    Deferred Ideas if the sweep volume ever grows past ~10k findings per run).
    """
    rows: list[dict] = []
    scan_kwargs = {"FilterExpression": Attr("source_sweep_run_id").eq(run_id)}
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows
```

</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Build pipeline_feedback/finding_records.py + the three builder tests (SPOTLIGHT_DIAGNOSTIC keyed by subtopic_id per D-08)</name>
  <files>pipeline_feedback/__init__.py, pipeline_feedback/finding_records.py, tests/test_feedback_finding_records.py</files>
  <read_first>
    - utils/event_records.py (full file — model the builder pattern verbatim)
    - tests/test_event_records.py + tests/test_uncovered_pmid_event.py (analog test shape)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "pipeline_feedback/finding_records.py" + Pattern A + Pattern B sections
    - The `<interfaces>` block in this plan (exact field lists for the three records — note SPOTLIGHT_DIAGNOSTIC is keyed by subtopic_id per D-08 re-framing)
  </read_first>
  <behavior>
    - tests/test_feedback_finding_records.py::test_candidate_topic_record_shape — assert PK="CANDIDATE_TOPIC#sars_cov2_long_term_effects", SK="GLOBAL", record_type="CANDIDATE_TOPIC", source_stage="feedback.sweep", source_sweep_run_id present, all required fields present
    - tests/test_feedback_finding_records.py::test_candidate_topic_with_truncation — call with truncated=True, total_unprocessed_remaining=350; assert both fields are on the record
    - tests/test_feedback_finding_records.py::test_recluster_recommendation_shape — assert PK="RECLUSTER_RECOMMENDATION#aging_geroscience", evaluation_history is a list of dicts each with {date, count}, source_sweep_run_id present
    - tests/test_feedback_finding_records.py::test_spotlight_diagnostic_shape_per_subtopic — D-08: assert PK="SPOTLIGHT_DIAGNOSTIC#aging_geroscience" (subtopic_id NOT cwid), record["subtopic_id"]=="aging_geroscience", record carries NO "cwid" field, reason_code_distribution is a dict (e.g. {"active_verb":2,"institutional_voice":1}), distinct_pmid_set_count is int, underlying_rejects is a list of str
    - tests/test_feedback_finding_records.py::test_spotlight_diagnostic_underlying_rejects_suffix_shape — D-08 PK shape: each entry in underlying_rejects matches the regex r"^[^#]+#[^#]+#[a-f0-9]{16}$" (three segments: publish_id#subtopic_id#pmid_set_hash); NOT the prior two-segment cwid#pmid_set_hash shape
    - tests/test_feedback_finding_records.py::test_spotlight_diagnostic_underlying_rejects_truncation — pass 30 underlying reject suffixes with feedback_diagnostic_max_underlying=20; assert len(record["underlying_rejects"])==20, record["underlying_rejects_truncated"] is True, record["total_underlying"]==30
    - tests/test_feedback_finding_records.py::test_spotlight_diagnostic_does_not_carry_author_cwids — D-08 + D-32: per-faculty drill-down lives on underlying CRITIC_REJECT# rows; the diagnostic row does NOT carry author_cwids directly. Assert "author_cwids" not in record.
    - tests/test_feedback_finding_records.py::test_no_lede_text_in_diagnostic — assert "lede_text" not in record and no key contains "lede" (PII boundary Pattern H)
    - tests/test_feedback_finding_records.py::test_idempotent_overwrite_same_pk_for_same_inputs — build twice with identical kwargs; PK identical
    - tests/test_feedback_finding_records.py::test_writers_call_put_item — three writers (write_candidate_topic, write_recluster_recommendation, write_spotlight_diagnostic) each invoke MagicMock(table).put_item exactly once
  </behavior>
  <action>
    Create `pipeline_feedback/__init__.py` (empty file or one-line module docstring `"""Feedback-event consumer — Phase 12 §9."""`).

    Create `pipeline_feedback/finding_records.py` mirroring `utils/event_records.py` structure. Imports: `from __future__ import annotations`, `from datetime import datetime, timezone`, `from decimal import Decimal`, `from typing import Any`. Module-level `_now_iso()` helper copy-pasted from utils/event_records.py:41-42.

    Define three builders (verbatim per the `<interfaces>` block):

    ```python
    def build_candidate_topic_record(
        *,
        slug: str,
        proposed_label: str,
        source_pmids: list[str],
        sonnet_rationale: str,
        source_sweep_run_id: str,
        triggered_by: str,
        truncated: bool = False,
        total_unprocessed_remaining: int | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Pure builder for a CANDIDATE_TOPIC# row — Phase 12 D-04.
        Idempotent PK; source_sweep_run_id lives in body, not key (D-05).
        """
        item: dict[str, Any] = {
            "PK": f"CANDIDATE_TOPIC#{slug}",
            "SK": "GLOBAL",
            "record_type": "CANDIDATE_TOPIC",
            "slug": str(slug),
            "proposed_label": str(proposed_label),
            "source_pmids": [str(p) for p in source_pmids],
            "sonnet_rationale": str(sonnet_rationale),
            "source_sweep_run_id": str(source_sweep_run_id),
            "triggered_by": str(triggered_by),
            "truncated": bool(truncated),
            "created_at": created_at or _now_iso(),
            "source_stage": "feedback.sweep",
        }
        if total_unprocessed_remaining is not None:
            item["total_unprocessed_remaining"] = int(total_unprocessed_remaining)
        return item


    def build_recluster_recommendation_record(
        *,
        topic_id: str,
        evaluation_history: list[dict[str, Any]],
        source_sweep_run_id: str,
        triggered_by: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Pure builder for a RECLUSTER_RECOMMENDATION# row — Phase 12 D-04 + D-07.

        evaluation_history is a list of {"date": "YYYY-MM-DD", "count": int}
        for the recluster_persistence_days days that tripped the
        drift_low_confidence_topic_max threshold under this topic.
        """
        return {
            "PK": f"RECLUSTER_RECOMMENDATION#{topic_id}",
            "SK": "GLOBAL",
            "record_type": "RECLUSTER_RECOMMENDATION",
            "topic_id": str(topic_id),
            "evaluation_history": [
                {"date": str(e["date"]), "count": int(e["count"])}
                for e in evaluation_history
            ],
            "source_sweep_run_id": str(source_sweep_run_id),
            "triggered_by": str(triggered_by),
            "created_at": created_at or _now_iso(),
            "source_stage": "feedback.sweep",
        }


    def build_spotlight_diagnostic_record(
        *,
        subtopic_id: str,
        reason_code_distribution: dict[str, int],
        distinct_pmid_set_count: int,
        underlying_rejects: list[str],
        max_underlying: int,
        window_days: int,
        source_sweep_run_id: str,
        triggered_by: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Pure builder for a SPOTLIGHT_DIAGNOSTIC# row — Phase 12 D-04 + D-08 + D-09 + D-32.

        Phase 12 D-08 (CR-2026-05-12 re-framing): keying is by subtopic_id,
        NOT cwid. Spotlight rejections are per-(publish, subtopic, pmid_set)
        events; rolling them up by subtopic surfaces 'this subtopic is
        structurally failing across publish cycles' as the diagnostic signal.

        distinct_pmid_set_count carries D-09 semantics: 'distinct (publish_id,
        pmid_set_hash) pairs over the window for this subtopic' — NOT 'total
        rejection events.' Document this explicitly so consumers don't conflate
        'ranking is stuck on the same papers across publishes' with 'ranking
        is broken across different papers.'

        underlying_rejects carries {publish_id}#{subtopic_id}#{pmid_set_hash}
        suffixes (D-08 four-segment PK shape) for drill-down (D-32); capped
        at max_underlying; overflow surfaces as underlying_rejects_truncated=true
        + total_underlying=N.

        Per-faculty drill-down is available by reading the underlying
        CRITIC_REJECT# rows' body author_cwids field (D-08 body field).
        The diagnostic row deliberately does NOT carry author_cwids
        directly — the per-(publish, subtopic, pmid_set) rejection row is
        the source of truth.

        DOES NOT carry lede_text — that's a SPOTLIGHT_REVIEW# concern only
        (Pattern H PII boundary).
        """
        total = len(underlying_rejects)
        capped = underlying_rejects[:max_underlying]
        item: dict[str, Any] = {
            "PK": f"SPOTLIGHT_DIAGNOSTIC#{subtopic_id}",
            "SK": "GLOBAL",
            "record_type": "SPOTLIGHT_DIAGNOSTIC",
            "subtopic_id": str(subtopic_id),
            "reason_code_distribution": {str(k): int(v) for k, v in reason_code_distribution.items()},
            "distinct_pmid_set_count": int(distinct_pmid_set_count),
            "underlying_rejects": [str(s) for s in capped],
            "window_days": int(window_days),
            "source_sweep_run_id": str(source_sweep_run_id),
            "triggered_by": str(triggered_by),
            "created_at": created_at or _now_iso(),
            "source_stage": "feedback.sweep",
        }
        if total > max_underlying:
            item["underlying_rejects_truncated"] = True
            item["total_underlying"] = total
        return item


    def write_candidate_topic(table: Any, **kwargs: Any) -> dict[str, Any]:
        item = build_candidate_topic_record(**kwargs); table.put_item(Item=item); return item

    def write_recluster_recommendation(table: Any, **kwargs: Any) -> dict[str, Any]:
        item = build_recluster_recommendation_record(**kwargs); table.put_item(Item=item); return item

    def write_spotlight_diagnostic(table: Any, **kwargs: Any) -> dict[str, Any]:
        item = build_spotlight_diagnostic_record(**kwargs); table.put_item(Item=item); return item
    ```

    Write `tests/test_feedback_finding_records.py` mirroring `tests/test_event_records.py` style. The `test_spotlight_diagnostic_underlying_rejects_suffix_shape` test is load-bearing — it pins the D-08 PK shape against future drift.

    Commit message: `feat(12-feedback): add pipeline_feedback/finding_records — three typed finding-record builders (SPOTLIGHT_DIAGNOSTIC keyed by subtopic_id per D-08)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_feedback_finding_records.py -x</automated>
  </verify>
  <acceptance_criteria>
    - Files `pipeline_feedback/__init__.py` + `pipeline_feedback/finding_records.py` exist
    - `grep -c "def build_candidate_topic_record\|def build_recluster_recommendation_record\|def build_spotlight_diagnostic_record" pipeline_feedback/finding_records.py` returns 3
    - `grep -c "def write_candidate_topic\|def write_recluster_recommendation\|def write_spotlight_diagnostic" pipeline_feedback/finding_records.py` returns 3
    - `grep -c "lede" pipeline_feedback/finding_records.py` returns 0 (PII boundary)
    - `grep -c "source_sweep_run_id" pipeline_feedback/finding_records.py` returns count >= 3 (in every builder)
    - `grep -c "subtopic_id" pipeline_feedback/finding_records.py` returns count >= 3 (D-08 keying in SPOTLIGHT_DIAGNOSTIC builder)
    - `grep -c "SPOTLIGHT_DIAGNOSTIC#{cwid}\|SPOTLIGHT_DIAGNOSTIC#{ cwid" pipeline_feedback/finding_records.py` returns 0 (no per-cwid keying remnants)
    - `pytest tests/test_feedback_finding_records.py -x` exits 0
  </acceptance_criteria>
  <done>Three pure builders + three thin writers, conform to Pattern A; SPOTLIGHT_DIAGNOSTIC keyed by subtopic_id per D-08; underlying_rejects three-segment suffix shape per D-08; underlying_rejects truncation per D-32; PII boundary enforced.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Build pipeline_feedback/sweep.py — core sweep logic + Sonnet prompt (per-subtopic diagnostic aggregation)</name>
  <files>pipeline_feedback/sweep.py, pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md, tests/test_feedback_sweep.py</files>
  <read_first>
    - pipeline_drift/evaluator.py (full file — analog for window-read + dataclass-result + Decimal pattern)
    - pipeline_feedback/finding_records.py (just built in Task 1)
    - utils/env_check.py (load_thresholds helper from Plan thresholds-substrate Task 2)
    - score_publications/*.py — any existing Bedrock Sonnet invocation site to mirror (look for `bedrock-runtime` / `invoke_model` / `messages.create`)
    - tests/test_pipeline_drift_evaluator.py (analog test file shape)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "pipeline_feedback/sweep.py" section
  </read_first>
  <behavior>
    - tests/test_feedback_sweep.py::test_idempotent_per_run_id — call run_sweep twice with the same run_id and same input rows; assert all put_item Item dicts are identical between runs
    - tests/test_feedback_sweep.py::test_cap_emits_truncated — provide 350 UNCOVERED_PMID# rows with max_pmids=200; assert CANDIDATE_TOPIC# records carry truncated=True and total_unprocessed_remaining=150
    - tests/test_feedback_sweep.py::test_worst_fitting_first — provide PMIDs with top_topic_score [0.1, 0.3, 0.05, 0.2]; assert the sweep processed them in [0.05, 0.1, 0.2, 0.3] order (ascending)
    - tests/test_feedback_sweep.py::test_recluster_trigger_at_persistence_threshold — feed 7 DRIFT#evaluation rows where topic_a's per_topic_low_confidence count > 50 every day (drift_low_confidence_topic_max=50, recluster_persistence_days=7); assert RECLUSTER_RECOMMENDATION#topic_a is written with evaluation_history length 7
    - tests/test_feedback_sweep.py::test_recluster_no_trigger_when_below_threshold — same setup but only 6 days non-zero; assert NO RECLUSTER_RECOMMENDATION# is written for topic_a
    - tests/test_feedback_sweep.py::test_recluster_handles_absent_per_topic_field — DRIFT#evaluation rows missing per_topic_low_confidence (pre-D-34 rows); assert sweep does NOT crash, treats absent as zero, does NOT emit RECLUSTER_RECOMMENDATION# from those rows
    - tests/test_feedback_sweep.py::test_diagnostic_distinct_pmid_sets_per_subtopic — D-09 + D-08: feed 5 CRITIC_REJECT# rows for subtopic_a spanning 3 distinct (publish_id, pmid_set_hash) pairs (critic_reject_subtopic_max=2); assert SPOTLIGHT_DIAGNOSTIC#subtopic_a has distinct_pmid_set_count=3 (counts distinct pairs, NOT total rows); reason_code_distribution sums to 5
    - tests/test_feedback_sweep.py::test_diagnostic_below_threshold_no_emit — feed subtopic_b with 1 distinct (publish_id, pmid_set_hash) pair, critic_reject_subtopic_max=2; assert NO SPOTLIGHT_DIAGNOSTIC#subtopic_b is written
    - tests/test_feedback_sweep.py::test_diagnostic_window_bounded — feed CRITIC_REJECT# rows outside the critic_reject_persistence_days=90 window; assert they are NOT counted
    - tests/test_feedback_sweep.py::test_diagnostic_groups_by_subtopic_not_cwid — feed CRITIC_REJECT# rows where author_cwids span many CWIDs but only one subtopic_id; assert exactly ONE SPOTLIGHT_DIAGNOSTIC# row is written (keyed by subtopic_id) regardless of CWID count. Demonstrates the D-08 grain change.
    - tests/test_feedback_sweep.py::test_underlying_rejects_suffix_shape_per_d08 — feed CRITIC_REJECT# rows; capture the resulting SPOTLIGHT_DIAGNOSTIC#'s underlying_rejects; assert each suffix matches r"^[^#]+#[^#]+#[a-f0-9]{16}$" (publish_id#subtopic_id#pmid_set_hash)
    - tests/test_feedback_sweep.py::test_underlying_rejects_capped — feed subtopic_c with 25 distinct rejected (publish, pmid_set) pairs, feedback_diagnostic_max_underlying=20; assert len(underlying_rejects)==20, underlying_rejects_truncated=True, total_underlying=25
    - tests/test_feedback_sweep.py::test_unknown_reason_code_bucket_handled — feed a CRITIC_REJECT# row with reason_code="unknown"; assert it shows up in reason_code_distribution["unknown"]
    - tests/test_feedback_sweep.py::test_sonnet_invocation_only_for_uncovered — mock Bedrock client; assert it is called for the uncovered-PMID path but NOT for the recluster or diagnostic paths (those are pure aggregation, no LLM)
    - tests/test_feedback_sweep.py::test_dataclass_carries_run_id_and_triggered_by — run_sweep returns FeedbackSweepRun with the run_id and triggered_by it was called with; every written finding record carries the same run_id
  </behavior>
  <action>
    Create `pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md` with the Sonnet prompt for candidate-topic discovery. Structure: system-prompt style guidelines (role: "You are a biomedical taxonomist..."), input shape (a list of {pmid, title, abstract, top_topics}), output shape (JSON: {"candidate_topics": [{"slug": "...", "proposed_label": "...", "evidence_pmids": [...], "rationale": "..."}, ...]} — schema documented in the prompt). The exact prompt body is implementation detail; the file's existence + JSON output contract is what matters for the tests (which mock Bedrock). Use the existing score_publications prompts as the tone analog.

    Create `pipeline_feedback/sweep.py` mirroring `pipeline_drift/evaluator.py`:

    ```python
    """Phase 12 §9 feedback sweep — consumes UNCOVERED_PMID#, DRIFT#evaluation
    per_topic_low_confidence (Plan drift-extension), CRITIC_REJECT# (Plan
    feedback-producer, per-publish/per-subtopic keyed per D-08); produces three
    finding records (Plan feedback-consumer Task 1)."""

    from __future__ import annotations
    import json
    import logging
    import uuid
    from dataclasses import dataclass, field
    from datetime import datetime, timedelta, timezone
    from typing import Any, Iterable

    from pipeline_feedback.finding_records import (
        write_candidate_topic, write_recluster_recommendation, write_spotlight_diagnostic,
    )
    from utils.env_check import load_thresholds

    logger = logging.getLogger(__name__)


    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


    @dataclass
    class FeedbackSweepRun:
        source_sweep_run_id: str
        triggered_by: str          # "operator" | "cold_run"
        started_at: str
        since: str
        candidate_topics: list[dict] = field(default_factory=list)
        recluster_recommendations: list[dict] = field(default_factory=list)
        spotlight_diagnostics: list[dict] = field(default_factory=list)


    def run_sweep(
        *,
        table: Any,
        since: datetime,
        triggered_by: str,
        run_id: str | None = None,
        max_pmids: int | None = None,
        bedrock_client: Any = None,                  # injected for testability
        thresholds: dict | None = None,              # injected for testability
    ) -> FeedbackSweepRun:
        """Single entry point for both the operator CLI and the cold-stage subprocess.

        D-01: triggered_by ∈ {"operator", "cold_run"}; both paths share this code.
        D-02: this function never raises on findings; only DDB/Bedrock errors propagate.
        D-05: one run_id per invocation; all writes carry it; idempotent overwrite per PK.
        D-08: SPOTLIGHT_DIAGNOSTIC aggregation is keyed by subtopic_id (per CONTEXT
              CR-2026-05-12 re-framing); per-faculty drill-down via author_cwids on
              underlying CRITIC_REJECT# rows.
        """
        cfg = thresholds or load_thresholds()
        max_pmids = max_pmids if max_pmids is not None else int(cfg["feedback_sweep_max_pmids"])
        run_id = run_id or str(uuid.uuid4())
        started_at = _now_iso()
        result = FeedbackSweepRun(
            source_sweep_run_id=run_id,
            triggered_by=triggered_by,
            started_at=started_at,
            since=since.isoformat(),
        )

        # --- 1. Uncovered-PMID Sonnet sweep (D-06) ---
        result.candidate_topics = _run_uncovered_pmid_sweep(
            table=table, since=since, max_pmids=max_pmids,
            bedrock_client=bedrock_client, run_id=run_id, triggered_by=triggered_by,
        )

        # --- 2. Recluster trigger from DRIFT#evaluation per_topic_low_confidence (D-07 + D-34) ---
        result.recluster_recommendations = _run_recluster_trigger(
            table=table, cfg=cfg, run_id=run_id, triggered_by=triggered_by,
        )

        # --- 3. SPOTLIGHT_DIAGNOSTIC#{subtopic_id} from CRITIC_REJECT# aggregation (D-08 + D-09 + D-30 + D-32) ---
        result.spotlight_diagnostics = _run_diagnostic_aggregation(
            table=table, cfg=cfg, run_id=run_id, triggered_by=triggered_by,
        )

        logger.info("feedback_sweep complete: run_id=%s triggered_by=%s "
                    "candidates=%d reclusters=%d diagnostics=%d",
                    run_id, triggered_by,
                    len(result.candidate_topics),
                    len(result.recluster_recommendations),
                    len(result.spotlight_diagnostics))
        return result
    ```

    Implement the three private helpers (`_run_uncovered_pmid_sweep`, `_run_recluster_trigger`, `_run_diagnostic_aggregation`) per the `<interfaces>` block specifications.

    **For `_run_diagnostic_aggregation` (the D-08 ripple is heaviest here):**
    1. Query CRITIC_REJECT# rows via `table.scan(FilterExpression=...)` filtered to created_at within the critic_reject_persistence_days=90 window.
    2. **Group by subtopic_id** (NOT cwid). For each subtopic, gather all in-window rows.
    3. **Count distinct (publish_id, pmid_set_hash) pairs** per subtopic — `len({(r["publish_id"], r["pmid_set_hash"]) for r in rows_for_subtopic})`. This is the D-09 semantic; the docstring above must say so verbatim.
    4. **Aggregate reason_code distribution** across all rows for the subtopic (one entry per CRITIC_REJECT# row, not per distinct pair — operators want to see whether the same pmid_set re-failed for the same reason or a different reason on retry).
    5. **Build underlying_rejects** as the list of `f"{r['publish_id']}#{r['subtopic_id']}#{r['pmid_set_hash']}"` for distinct (publish_id, pmid_set_hash) pairs (capped at feedback_diagnostic_max_underlying=20). Use the builder's truncation logic.
    6. If `distinct_pmid_set_count >= critic_reject_subtopic_max=2`, call `write_spotlight_diagnostic` keyed by `subtopic_id`.

    For the recluster trigger: read prior `recluster_persistence_days` DRIFT#evaluation rows via `table.query(KeyConditionExpression=Key("PK").eq("DRIFT#evaluation") & Key("SK").begins_with("DAY#"))` then filter to the window in Python. For each row, read `item.get("per_topic_low_confidence", {})` (presence-check per D-34: absent is treated as empty). For each topic, count how many days within the window had count > drift_low_confidence_topic_max. If that count == recluster_persistence_days, emit RECLUSTER_RECOMMENDATION# with the evaluation_history of those days.

    Write `tests/test_feedback_sweep.py` with the 15 behaviors above. Use MagicMock for `table` (capturing both query/scan and put_item calls), MagicMock for `bedrock_client` (returning canned JSON when invoke_model is called). Use the `thresholds=` injected seam to pass test-controlled values (including critic_reject_subtopic_max=2 / critic_reject_persistence_days=90) rather than reading config/thresholds.json from disk in tests.

    Commit message: `feat(12-feedback): pipeline_feedback/sweep.py — Sonnet uncovered-PMID + recluster trigger + per-subtopic spotlight diagnostic aggregation (D-08)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_feedback_sweep.py -x</automated>
  </verify>
  <acceptance_criteria>
    - File `pipeline_feedback/sweep.py` exists with `def run_sweep` exported
    - File `pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md` exists
    - `grep -c "FeedbackSweepRun" pipeline_feedback/sweep.py` returns count >= 2 (dataclass + return type)
    - `grep -c "source_sweep_run_id" pipeline_feedback/sweep.py` returns count >= 3 (mint + thread through three subhelpers)
    - `grep -c "per_topic_low_confidence" pipeline_feedback/sweep.py` returns count >= 1 (reads Plan drift-extension's field)
    - `grep -c "CRITIC_REJECT" pipeline_feedback/sweep.py` returns count >= 1 (reads Plan feedback-producer's rows)
    - `grep -c "subtopic_id" pipeline_feedback/sweep.py` returns count >= 3 (D-08 keying: group by subtopic, write per subtopic, underlying_rejects suffix carries subtopic_id)
    - `grep -c "feedback_sweep_max_pmids\|recluster_persistence_days\|critic_reject_persistence_days\|critic_reject_subtopic_max\|feedback_diagnostic_max_underlying" pipeline_feedback/sweep.py` returns count >= 5 (every tunable read — note rename to critic_reject_subtopic_max)
    - `grep -c "critic_reject_cwid_max" pipeline_feedback/sweep.py` returns 0 (old name fully gone)
    - `pytest tests/test_feedback_sweep.py -x` exits 0
  </acceptance_criteria>
  <done>Sweep entry point runs three sub-passes; reads from the three event sources; writes the three finding records; respects D-05 idempotency, D-06 cap+worst-first, D-07 persistence, D-08 per-subtopic diagnostic grain, D-09 distinct-(publish_id,pmid_set_hash)-pair semantics, D-32 underlying_rejects cap.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 2.5 (W-6): Inter-task checkpoint — sweep functions importable + green before CLI/render/cold-stage</name>
  <what-built>
    Task 1 (finding_records) and Task 2 (sweep + Sonnet prompt) complete; their test files pass. Task 3 builds the CLI + render + cold-stage and explicitly depends on Task 2's sweep module being importable. This plan is large; the inter-task checkpoint exists so the executor can resume Task 3 with fresh context if needed (per plan-checker W-6).
  </what-built>
  <how-to-verify>
    1. Run the two test files for Tasks 1+2:
       ```bash
       cd /Users/paulalbert/Dropbox/GitHub/ReciterAI
       pytest tests/test_feedback_finding_records.py tests/test_feedback_sweep.py -x
       ```
       Expect exit 0.
    2. Confirm sweep is importable as a module:
       ```bash
       cd /Users/paulalbert/Dropbox/GitHub/ReciterAI
       python -c "from pipeline_feedback.sweep import run_sweep, FeedbackSweepRun; print('OK')"
       ```
       Expect `OK` and exit 0.
    3. Confirm the D-08 grain change is reflected in builder output:
       ```bash
       cd /Users/paulalbert/Dropbox/GitHub/ReciterAI
       python -c "from pipeline_feedback.finding_records import build_spotlight_diagnostic_record as b; r=b(subtopic_id='s1', reason_code_distribution={'active_verb':2}, distinct_pmid_set_count=2, underlying_rejects=['p1#s1#abc1234567890def'], max_underlying=20, window_days=90, source_sweep_run_id='r1', triggered_by='operator'); assert r['PK']=='SPOTLIGHT_DIAGNOSTIC#s1' and r['subtopic_id']=='s1' and 'cwid' not in r and 'author_cwids' not in r; print('OK')"
       ```
       Expect `OK`. If 'cwid' or 'author_cwids' appears on the diagnostic record, Task 1 missed the D-08 grain change — return to Task 1 before proceeding.
  </how-to-verify>
  <resume-signal>Type "tasks 1+2 green; proceed to task 3" if all three checks pass. Otherwise describe which check failed.</resume-signal>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Build pipeline_feedback/cli.py + markdown_render.py + register cold-stage in pipeline_cold/run.py</name>
  <files>pipeline_feedback/cli.py, pipeline_feedback/__main__.py, pipeline_feedback/markdown_render.py, pipeline_cold/run.py, tests/test_feedback_cli.py, tests/test_feedback_render.py</files>
  <read_first>
    - review/cli.py + review/__main__.py (full files — verbatim analog)
    - review/validator.py:_query_pending_rows (pagination pattern for the _fetch_rows_by_run_id implementation per PATTERNS.md / W-5 resolution)
    - pipeline_feedback/sweep.py (Task 2 just built — main() must invoke run_sweep)
    - pipeline_cold/run.py FULL file (need: default_cold_stages() at lines 90-131, exact ColdStage tuple structure, the `rollup` and `backfill_spotlight` stages to insert between)
    - pipeline_hierarchy/generator.py lines 70-74 (deterministic output precedent — no generated_at in body)
    - tests/test_hierarchy_reproducibility.py (byte-identical assertion analog)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "pipeline_feedback/cli.py" + "pipeline_cold/run.py stage registration" sections
  </read_first>
  <behavior>
    - tests/test_feedback_cli.py::test_sweep_subcommand_dispatches_to_run_sweep — patch pipeline_feedback.sweep.run_sweep; call `main(["sweep", "--since", "7"])`; assert run_sweep was called with `triggered_by="operator"` and a `since` 7 days before now
    - tests/test_feedback_cli.py::test_sweep_subcommand_default_run_id_minted — call sweep without --run-id; assert run_sweep was called with a non-None run_id (UUID format)
    - tests/test_feedback_cli.py::test_sweep_subcommand_explicit_run_id_threaded — call sweep with --run-id specific-id; assert run_sweep was called with run_id="specific-id"
    - tests/test_feedback_cli.py::test_render_subcommand_reads_run_id_rows — call render with a run_id; assert it queries DDB for rows carrying that source_sweep_run_id, then writes deterministic markdown to stdout (or --output path)
    - tests/test_feedback_cli.py::test_fetch_rows_by_run_id_paginates — W-5 fix: mock table.scan to return LastEvaluatedKey on the first call and absent on the second; assert _fetch_rows_by_run_id concatenates results from both pages; assert scan was called exactly twice with ExclusiveStartKey threaded on the second call
    - tests/test_feedback_cli.py::test_fetch_rows_by_run_id_no_pagination_when_single_page — mock table.scan to return without LastEvaluatedKey; assert scan called exactly once; no ExclusiveStartKey ever supplied
    - tests/test_feedback_cli.py::test_exit_codes_documented_in_module_docstring — read pipeline_feedback/cli.py; assert module docstring contains "Exit codes:" and lines documenting at least 0, 2, 3
    - tests/test_feedback_cli.py::test_argparse_errors_return_exit_code_2 — call `main(["badsubcmd"])`; assert exit code 2
    - tests/test_feedback_cli.py::test_main_invokable_via_dunder_main — `subprocess.run([sys.executable, "-m", "pipeline_feedback", "--help"])` exits 0 with help text containing both "sweep" and "render"
    - tests/test_feedback_render.py::test_render_byte_identical_two_runs — pass the same rows + run_id twice; assert identical bytes (D-03 determinism)
    - tests/test_feedback_render.py::test_render_no_generated_at_in_body — pass rows; assert b"generated_at" NOT in the rendered output (G-29 lesson, D-03)
    - tests/test_feedback_render.py::test_render_sorts_records_deterministically — pass rows in two different orders; assert outputs are byte-identical (sort by PK or record_type+PK internally)
    - tests/test_feedback_render.py::test_render_groups_by_record_type — assert the markdown has section headers for "Candidate Topics", "Recluster Recommendations", "Spotlight Diagnostics" (or analogous H2 sections)
    - tests/test_feedback_render.py::test_render_diagnostic_shows_subtopic_id — D-08: assert rendered diagnostic block surfaces "subtopic_id" not "cwid" in the body text
    - In tests/test_feedback_cli.py: test_cold_stage_registration — `from pipeline_cold.run import default_cold_stages`; call it; assert there is a stage with name="feedback_sweep" AND it appears between the "rollup" stage and the "backfill_spotlight" stage by index AND its command includes "pipeline_feedback" and "sweep"
  </behavior>
  <action>
    **Pre-edit assertion (W-7 hardening — RUN BEFORE EDITING pipeline_cold/run.py):**
    ```bash
    cd /Users/paulalbert/Dropbox/GitHub/ReciterAI
    python -c "from pipeline_cold.run import default_cold_stages; names=[s.name for s in default_cold_stages()]; assert 'rollup' in names and 'backfill_spotlight' in names, f'expected rollup+backfill_spotlight in {names}'"
    ```
    If this exits non-zero, surface a CHECKPOINT — the cold-path orchestration shape changed and needs orchestrator review BEFORE this task proceeds. Do NOT silently fall back to a different placement.

    Create `pipeline_feedback/cli.py` mirroring `review/cli.py` structure exactly. **`_fetch_rows_by_run_id` is fully implemented (no ellipsis stub) per the `<interfaces>` block specification — uses boto3 `table.scan(FilterExpression=Attr("source_sweep_run_id").eq(run_id))` with pagination via `LastEvaluatedKey` / `ExclusiveStartKey`, analog pattern from `review/validator.py:_query_pending_rows` per PATTERNS.md.**

    ```python
    """Phase 12 feedback CLI — `python -m pipeline_feedback {sweep|render}`.

    Use cases:
      - Operator diagnosis: python -m pipeline_feedback sweep --since 7
      - Cold-path stage: invoked by pipeline_cold.run with --triggered-by cold_run
      - Operator render: python -m pipeline_feedback render <run_id>

    'Diagnosis vs documentation' framing (CONTEXT specifics):
      - operator-CLI mode = diagnosis (drift alert → investigate before deciding on a cold run)
      - cold-path-stage mode = documentation (cold run is happening; record findings in this run_id's context)

    Exit codes:
      0 — sweep completed and wrote findings (zero findings is also success)
      2 — argparse error / unknown subcommand
      3 — invalid argument values
      4 — missing DDB env / table not configured
      5 — DDB write failed
      6 — Bedrock unavailable for the Sonnet uncovered-PMID pass
    """

    from __future__ import annotations
    import argparse
    import os
    import sys
    import uuid
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from typing import Optional

    from boto3.dynamodb.conditions import Attr

    from pipeline_feedback.sweep import run_sweep
    from pipeline_feedback.markdown_render import render_sweep_markdown


    def _default_get_table():
        from utils.dynamodb_helpers import get_table
        return get_table()


    def _run_sweep(args, *, get_table=_default_get_table) -> int:
        triggered_by = args.triggered_by or "operator"
        since = datetime.now(timezone.utc) - timedelta(days=args.since)
        run_id = args.run_id or str(uuid.uuid4())
        try:
            table = get_table()
        except Exception as exc:
            print(f"[error] missing DDB env: {exc}", file=sys.stderr); return 4
        try:
            result = run_sweep(
                table=table, since=since,
                triggered_by=triggered_by, run_id=run_id,
                max_pmids=args.max_pmids,
            )
        except Exception as exc:
            print(f"[error] sweep failed: {exc}", file=sys.stderr); return 5
        print(f"sweep complete: run_id={result.source_sweep_run_id} "
              f"candidates={len(result.candidate_topics)} "
              f"reclusters={len(result.recluster_recommendations)} "
              f"diagnostics={len(result.spotlight_diagnostics)}")
        return 0


    def _run_render(args, *, get_table=_default_get_table) -> int:
        try:
            table = get_table()
        except Exception as exc:
            print(f"[error] missing DDB env: {exc}", file=sys.stderr); return 4
        rows = _fetch_rows_by_run_id(table, args.run_id)
        markdown_bytes = render_sweep_markdown(args.run_id, rows)
        if args.output:
            Path(args.output).write_bytes(markdown_bytes)
        else:
            sys.stdout.buffer.write(markdown_bytes)
        return 0


    def _fetch_rows_by_run_id(table, run_id: str) -> list[dict]:
        """Scan the table for finding rows carrying source_sweep_run_id == run_id.

        Pattern analog: review/validator.py:_query_pending_rows (per PATTERNS.md).
        Pagination: boto3 table.scan returns LastEvaluatedKey when results
        truncate; we loop until absent. Bounded by the number of findings emitted
        in one sweep (3 partitions × ≤ N findings each), so scan is acceptable.
        A GSI keyed by source_sweep_run_id is a deferred optimization.
        """
        rows: list[dict] = []
        scan_kwargs: dict = {"FilterExpression": Attr("source_sweep_run_id").eq(run_id)}
        while True:
            resp = table.scan(**scan_kwargs)
            rows.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return rows


    def main(argv: Optional[list[str]] = None) -> int:
        parser = argparse.ArgumentParser(prog="pipeline_feedback", description="Phase 12 feedback CLI.")
        sub = parser.add_subparsers(dest="command", required=True)

        p_sweep = sub.add_parser("sweep", help="Run the feedback sweep.")
        p_sweep.add_argument("--since", type=int, default=7, help="Days back (default 7).")
        p_sweep.add_argument("--run-id", type=str, default=None)
        p_sweep.add_argument("--max-pmids", type=int, default=None, help="Override feedback_sweep_max_pmids.")
        p_sweep.add_argument("--triggered-by", choices=["operator", "cold_run"], default=None)

        p_render = sub.add_parser("render", help="Render a sweep's findings as deterministic markdown.")
        p_render.add_argument("run_id")
        p_render.add_argument("--output", type=str, default=None)

        args = parser.parse_args(argv)
        if args.command == "sweep":
            return _run_sweep(args)
        return _run_render(args)
    ```

    Create `pipeline_feedback/__main__.py` (verbatim three-line analog from review/__main__.py):

    ```python
    """`python -m pipeline_feedback` entry point — dispatches to cli.main."""
    import sys
    from pipeline_feedback.cli import main
    if __name__ == "__main__":
        sys.exit(main())
    ```

    Create `pipeline_feedback/markdown_render.py`:

    ```python
    """Deterministic markdown rendering for feedback-sweep findings.

    Same rows in → same bytes out. No generated_at in the body (G-29 lesson,
    D-03). The caller owns the filename's timestamp (e.g.
    feedback_sweep_{run_id}_{date}.md).
    """

    from __future__ import annotations
    import io
    from typing import Any

    def render_sweep_markdown(run_id: str, rows: list[dict[str, Any]]) -> bytes:
        candidates = sorted(
            (r for r in rows if r.get("record_type") == "CANDIDATE_TOPIC"),
            key=lambda r: r["PK"],
        )
        reclusters = sorted(
            (r for r in rows if r.get("record_type") == "RECLUSTER_RECOMMENDATION"),
            key=lambda r: r["PK"],
        )
        diagnostics = sorted(
            (r for r in rows if r.get("record_type") == "SPOTLIGHT_DIAGNOSTIC"),
            key=lambda r: r["PK"],
        )

        buf = io.StringIO()
        buf.write(f"# Feedback Sweep — run_id `{run_id}`\n\n")
        buf.write(f"## Candidate Topics ({len(candidates)})\n\n")
        for r in candidates:
            buf.write(f"### `{r['slug']}` — {r['proposed_label']}\n")
            buf.write(f"- evidence PMIDs: {', '.join(r['source_pmids'][:10])}\n")
            buf.write(f"- rationale: {r['sonnet_rationale']}\n")
            if r.get("truncated"):
                buf.write(f"- _truncated: {r.get('total_unprocessed_remaining')} PMIDs unprocessed_\n")
            buf.write("\n")

        buf.write(f"## Recluster Recommendations ({len(reclusters)})\n\n")
        for r in reclusters:
            buf.write(f"### `{r['topic_id']}`\n")
            for ev in r["evaluation_history"]:
                buf.write(f"- {ev['date']}: count={ev['count']}\n")
            buf.write("\n")

        buf.write(f"## Spotlight Diagnostics ({len(diagnostics)})\n\n")
        for r in diagnostics:
            # D-08: keyed by subtopic_id (not cwid)
            buf.write(f"### `{r['subtopic_id']}` (subtopic_id)\n")
            buf.write(f"- distinct rejected (publish, pmid_set) pairs: {r['distinct_pmid_set_count']}\n")
            buf.write(f"- reason_code distribution: {sorted(r['reason_code_distribution'].items())}\n")
            buf.write(f"- window_days: {r['window_days']}\n")
            ut = r.get("underlying_rejects_truncated", False)
            tail = f" (truncated; total={r.get('total_underlying')})" if ut else ""
            buf.write(f"- underlying_rejects: {len(r['underlying_rejects'])}{tail}\n")
            buf.write("\n")

        return buf.getvalue().encode("utf-8")
    ```

    In `pipeline_cold/run.py`, modify `default_cold_stages()` to insert the new ColdStage between `rollup` and `backfill_spotlight`. The W-7 pre-edit assertion above must have passed first.

    ```python
    ColdStage(
        name="feedback_sweep",
        command=[sys.executable, "-m", "pipeline_feedback", "sweep",
                 "--triggered-by", "cold_run"],
        description=("Non-gating Sonnet sweep over UNCOVERED_PMID# + "
                     "LOW_CONFIDENCE_ASSIGNMENT# (via DRIFT# materialized counts) "
                     "+ CRITIC_REJECT# events → three typed finding records "
                     "(Phase 12 D-04, D-08 per-subtopic diagnostic). Returns 0 "
                     "even with findings (D-02 non-gating)."),
    ),
    ```

    Place this stage in the list at exactly the index between "rollup" and "backfill_spotlight" — confirmed reachable by the W-7 pre-edit assertion.

    Write `tests/test_feedback_cli.py` and `tests/test_feedback_render.py` with the behaviors above. Use unittest.mock to patch `pipeline_feedback.sweep.run_sweep` and `pipeline_feedback.cli._default_get_table`. The W-5 pagination test injects a side_effect list on the mocked `table.scan` so the function returns two distinct response dicts (first with LastEvaluatedKey, second without).

    Commit message: `feat(12-feedback): CLI + deterministic markdown renderer + cold-stage registration + paginated _fetch_rows_by_run_id`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_feedback_cli.py tests/test_feedback_render.py -x</automated>
  </verify>
  <acceptance_criteria>
    - Files exist: pipeline_feedback/cli.py, pipeline_feedback/__main__.py, pipeline_feedback/markdown_render.py
    - `grep -c "add_subparsers" pipeline_feedback/cli.py` returns count >= 1
    - `grep -E 'add_parser\(\"sweep\"|add_parser\(\"render\"' pipeline_feedback/cli.py` returns count >= 2 (both subcommands)
    - `grep -c "Exit codes:" pipeline_feedback/cli.py` returns count >= 1 (PATTERNS.md Pattern G: module docstring documents exit codes)
    - `grep -c "feedback_sweep" pipeline_cold/run.py` returns count >= 1
    - `grep -c "generated_at" pipeline_feedback/markdown_render.py` returns 0 (D-03 + G-29 determinism)
    - `grep -c "\.\.\." pipeline_feedback/cli.py | head -1` — there must be NO ellipsis-stub remaining in `_fetch_rows_by_run_id` (W-5): inspect the function body manually; the body must contain `table.scan(...)` and a `while True` loop, not `...`
    - `grep -c "LastEvaluatedKey\|ExclusiveStartKey" pipeline_feedback/cli.py` returns count >= 2 (pagination both endpoints)
    - `grep -c "subtopic_id" pipeline_feedback/markdown_render.py` returns count >= 1 (D-08 rendered in diagnostic block)
    - `python -m pipeline_feedback --help` exits 0 (or via subprocess.run; the help text must mention both subcommands — verified in test_main_invokable_via_dunder_main)
    - `python -c "from pipeline_cold.run import default_cold_stages; stages=default_cold_stages(); names=[s.name for s in stages]; assert 'feedback_sweep' in names; idx_f=names.index('feedback_sweep'); idx_r=names.index('rollup'); idx_b=names.index('backfill_spotlight'); assert idx_r < idx_f < idx_b, f'placement wrong: rollup={idx_r} feedback={idx_f} backfill={idx_b}'"` exits 0
    - `pytest tests/test_feedback_cli.py tests/test_feedback_render.py -x` exits 0
    - Full feedback consumer suite: `pytest tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_cli.py tests/test_feedback_render.py -x` exits 0
  </acceptance_criteria>
  <done>CLI exposes sweep + render with documented exit codes; markdown renderer is byte-deterministic and surfaces subtopic_id per D-08; cold stage is registered at the correct position (W-7 pre-flight assertion verified the anchor stages exist); _fetch_rows_by_run_id is fully implemented with pagination (W-5 stub eliminated); the whole pipeline_feedback package works end-to-end.</done>
</task>

</tasks>

<verification>
- All three tasks' acceptance criteria green
- Full feedback suite passes: `pytest tests/test_feedback_finding_records.py tests/test_feedback_sweep.py tests/test_feedback_cli.py tests/test_feedback_render.py -x` exits 0
- No regression: existing cold-path tests still pass: `pytest tests/test_pipeline_cold*.py -x` exits 0
- `python -c "from pipeline_cold.run import default_cold_stages; assert 'feedback_sweep' in [s.name for s in default_cold_stages()]"` exits 0
- W-5 closed: `_fetch_rows_by_run_id` no longer has an ellipsis stub; pagination tested
- W-6 closed: inter-task checkpoint added between Task 2 and Task 3
- W-7 closed: pre-edit assertion documented in Task 3 action; surfaces CHECKPOINT if cold-path anchors are missing
</verification>

<success_criteria>
spec §9 closes the loop: events have a consumer. Each of the three signals (uncovered-PMID, low-confidence persistence, critic-reject distribution per subtopic) becomes a queryable backlog row. Operator gets diagnostic CLI; cold path gets non-gating documentation stage. Single shared code path; idempotent per run_id. SPOTLIGHT_DIAGNOSTIC is keyed by subtopic_id per D-08 re-framing; per-faculty drill-down preserved via author_cwids on underlying CRITIC_REJECT# rows.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-feedback-consumer-SUMMARY.md`.
</output>
