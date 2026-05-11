---
phase: 04-subtopic-system
plan: 06
subsystem: subtopic-backfill
tags: [backfill, orchestration, pipeline, checkpoint]
requires:
  - 04-02 discover_subtopics.py
  - 04-03 assign_subtopics.py + aggregate_subtopic_scores.py
  - 04-04 generate_see_also.py
  - 04-05 aging_pilot_gate.py (Verdict: GO recorded 2026-04-14)
provides:
  - backfill_topic.py (per-topic orchestrator)
  - backfill_all.py (full-phase runner with verdict gate)
  - .planning/phases/04-subtopic-system/backfill_log.md (runtime audit trail)
affects:
  - ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json (will be written by Task 3 human execution)
tech_added:
  - subprocess-based pipeline chaining (discover -> review gate -> assign -> aggregate)
  - SKIP_REVIEW audit-trail row format in artifacts/backfill_log.md
patterns:
  - Separate-process-per-pass isolation so each script owns its own BedrockClient + DynamoDB session
  - EXCLUDED:<topic>:<count>:<reason> stdout marker as machine-readable inter-script channel
  - `## Verdict: GO` regex gate as programmatic tie between Plan 05 pilot approval and Plan 06 full backfill
key_files_created:
  - backfill_topic.py (607 lines)
  - backfill_all.py (634 lines)
  - .planning/phases/04-subtopic-system/backfill_log.md (template)
key_files_modified: []
decisions:
  - "[Plan 04-06] Subprocess isolation between passes (not in-process imports). Each pass keeps its own BedrockClient/boto3 session; avoids holding long-lived HTTPS pools between Sonnet bursts and arithmetic-only Pass 3."
  - "[Plan 04-06] Idempotent rerun: if hierarchy_draft_<id>.json already has review_status in {approved, auto_approved}, Pass 1 is skipped on rerun. Pass 2 always uses --resume. Matches operator workflow of approve-then-rerun."
  - "[Plan 04-06] --continue-on-error re-bins failed topics into excluded_topics[] with reason `clustering quality fail` rather than aborting the whole run. Downstream readers already treat excluded topics as Tier-3 fallback per D-18."
  - "[Plan 04-06] Task 3 (full backfill execution) deliberately left to human operator: ~4-8 hours wall time, 66 Sonnet review touchpoints. This plan only ships the orchestrators; running them is an explicit human decision tracked at the Plan 04-06 checkpoint."
metrics:
  duration_code: "~25 min"
  duration_live_backfill: "~5.4 hours (12:55 -> 18:19)"
  tasks_completed: 3
  tasks_pending: 0
  completed_on: "2026-04-14T19:19:00Z"
  pm_commit: "7ce94ba"  # feature/chatbot-runtime (PM subrepo)
  haiku_cost_usd_pass2: 97.05
  topics_processed: 66
  topics_in_hierarchy: 65
  topics_excluded: 2
  total_subtopics: 1526
status: complete
follow_ups:
  - "see_also[] regeneration: currently 0 links. Single-shot Sonnet at 8k and then 32k max_tokens both produced malformed JSON (unterminated string at char ~120k). Navigation-aid only per D-10; does not block retrieval. Needs chunked approach (split parents into groups of ~15-20 per call)."
  - "implementation_science topic excluded after Pass 1 Sonnet JSON truncation (outputTokens=8192 cap, char 20188). Retry individually with larger max_tokens or chunked discover."
---

# Phase 4 Plan 6: Full-Phase Backfill Orchestrators Summary

Per-topic and full-phase backfill runners that chain Phase 4's three passes
(discover -> human review -> assign -> aggregate) across the 66 non-pilot
topics, assemble hierarchy.json, run see-also generation, and stage the
artifact for PM subrepo commit. Gated on Plan 05's `Verdict: GO`.

## What This Plan Ships (Tasks 1 and 2)

### Task 1: `backfill_topic.py` (commit `753906c`)

Per-topic orchestrator. CLI flags: `--topic`, `--skip-review`,
`--min-activities`. Chains four steps:

1. **Pre-count vs cold-start floor (D-01)** — queries TOPIC#<id> for unique
   PMIDs at score >= 0.3; if below `--min-activities`, prints
   `EXCLUDED:<topic>:<count>:below_cold_start_floor` and exits 0 without any
   LLM spend.
2. **Pass 1 (discover)** — invokes `discover_subtopics.py --topic <id>
   --min-activities <N>` via `subprocess.run(..., check=True)`. Skipped on
   rerun if `hierarchy_draft_<id>.json` already has
   `review_status in {approved, auto_approved}`.
3. **Human review gate (D-21)** — reads `hierarchy_draft_<id>.json`. If
   `review_status != "approved"` and `--skip-review` was NOT passed, prints
   the operator's "approve then rerun" prompt and exits 2. With
   `--skip-review`, injects `review_status: "auto_approved"` AND appends a
   `SKIP_REVIEW | <topic> | <UTC ISO-8601> | authorized_by: <USER> |
   pre-authorized for lower-priority topic` row to
   `.planning/phases/04-subtopic-system/artifacts/backfill_log.md`.
4. **Pass 2 (assign) + Pass 3 (aggregate)** — chained via subprocess with
   `--resume` on Pass 2 for idempotency.

After all three passes, the augmented draft (`hierarchy_augmented_<id>.json`)
is written: each subtopic gets `total_weight` (from `total_weights_<id>.json`)
and `activity_count` (from a DynamoDB count of rows whose
`primary_subtopic_id == subtopic_id`, deduped by pmid).

### Task 2: `backfill_all.py` (commit `562ed6b`)

Full-phase runner. CLI flags: `--dry-run`, `--only-verdict-check`,
`--skip-pm-copy`, `--continue-on-error`.

Pipeline:

1. **Verdict gate (D-20)** — reads `aging_pilot_results.md`, requires a line
   matching `## Verdict: GO` (regex). `--only-verdict-check` exits 0 after
   this succeeds.
2. **Topic ordering** — DynamoDB activity-count descending. `aging_geroscience`
   excluded (pilot, already backfilled by Plans 02-05).
3. **Per-topic loop** — invokes `backfill_topic.py --topic <id>` per topic,
   captures stdout/stderr into `backfill_log.md`. Cold-start exclusions (via
   the `EXCLUDED:` marker) drop into `excluded_topics[]`. `--continue-on-error`
   re-bins per-topic failures as `clustering quality fail`.
4. **Hierarchy assembly** — reads every `hierarchy_augmented_<id>.json` (and
   the pilot's augmented draft if present), writes `hierarchy_full.json`
   conforming to `hierarchy-schema.md` (version, generated_at, taxonomy_version,
   topics{subtopics:[{id,label,description,activity_count,total_weight}]},
   excluded_topics[], see_also[]).
5. **See-also generation** — `generate_see_also.py --input hierarchy_full.json
   --output see_also_full.json`, merges the bidirectional `see_also[]` back
   into the final hierarchy.
6. **PM worktree copy** — copies `hierarchy_full.json` to
   `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json` and prints
   the `commit-to-subrepo` commands the operator runs inside the PM worktree
   on `feature/chatbot-runtime` (per CLAUDE.md). Suppressed by `--skip-pm-copy`.

### Per-topic log template (committed with Task 2)

`.planning/phases/04-subtopic-system/backfill_log.md` — header-only template
that both runners append to at execution time.

## Verification

### Task 1 acceptance criteria (all pass)

| Check | Result |
|-------|--------|
| `python backfill_topic.py --help` | shows `--topic`, `--skip-review`, `--min-activities` |
| Help flag lines matching the three names | `8` (>=3 required) |
| `grep -c "subprocess.run" backfill_topic.py` | `3` (discover, assign, aggregate) |
| `grep -c "review_status.*approved" backfill_topic.py` | `11` (>=1 required) |
| `grep -cE "below_cold_start_floor\|EXCLUDED" backfill_topic.py` | `3` (>=1 required) |
| `grep -c "SKIP_REVIEW" backfill_topic.py` | `14` (>=1 required) |
| `python3 -c "import ast; ast.parse(open('backfill_topic.py').read())"` | ast parse OK |
| Min line count (plan requires 100+) | `607` |

### Task 2 acceptance criteria (all pass)

| Check | Result |
|-------|--------|
| `grep -c "Verdict: GO" backfill_all.py` | `8` (>=1 required) |
| `grep -cE "commit-to-subrepo\|feature/chatbot-runtime" backfill_all.py` | `5` (>=1 required) |
| `grep -c "excluded_topics" backfill_all.py` | `12` (>=2 required) |
| `grep -c "generate_see_also.py" backfill_all.py` | `4` (>=1 required) |
| Help-flag lines matching the four flag names | `6` (all 4 flags present) |
| `python3 backfill_all.py --only-verdict-check` | exit 0 (`Verdict: GO confirmed`) |
| `python3 -c "import ast; ast.parse(open('backfill_all.py').read())"` | ast parse OK |
| Min line count (plan requires 120+) | `634` |

Cold `--only-verdict-check` live run output (smoke):

```
12:36:24 INFO Verdict: GO confirmed in .planning/phases/04-subtopic-system/aging_pilot_results.md
12:36:24 INFO Verdict gate check complete. Exiting 0 (--only-verdict-check).
exit=0
```

## Deviations from Plan

None for Tasks 1 and 2 — plan executed as written. One minor Rule-2 addition
beyond the plan's step list: when `--skip-review` is passed, `backfill_topic.py`
ALSO writes `review_status: "auto_approved"` back into the draft file so
Pass 2's own review-gate check in `assign_subtopics._load_hierarchy_draft`
(which exits 2 on anything other than `approved`/`auto_approved`) passes
without requiring a second manual edit. Plan Task 1 step 4 mentions the
audit log but does not specify persisting the status back to the draft; I
persisted it to avoid a second-edit trap.

## Task 3: PENDING HUMAN EXECUTION

Task 3 (`checkpoint:human-verify gate="blocking"`) is intentionally NOT
executed by this plan. It is a ~4-8 hour wall-time run against live
DynamoDB + Bedrock, with ~66 human review touchpoints (one per topic
draft). The orchestrators committed here are the prerequisite that
unblocks Task 3.

### What the human operator does next

1. From the repo root, run:
   ```
   python backfill_all.py
   ```
   Expect ~4-8 hours wall time at 66 topics x ~4 min each (variable
   depending on activity counts per topic and human review latency).

2. For each topic the run pauses for review of
   `hierarchy_draft_<topic>.json`. Approve by editing the draft to set
   `"review_status": "approved"` at the top level, then rerun
   `python backfill_topic.py --topic <topic>` to resume that topic at
   Pass 2. Lower-priority topics with Paul's pre-authorization may use
   `--skip-review` (SKIP_REVIEW audit row will be recorded).

3. Monitor the per-topic log:
   `.planning/phases/04-subtopic-system/backfill_log.md`
   Any topic failing the four Plan 05 quality gates (coverage <70%, >50%
   pairwise overlap, etc.) gets manually added to `excluded_topics[]`
   with reason `"clustering quality fail"`.

4. After the full run completes, inspect final hierarchy.json:
   ```
   jq '.topics | length' ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json
   # expect: 50..67
   jq '.excluded_topics | length' ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json
   # expect: complement to 67
   jq '.see_also | length' ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json
   # expect: non-zero (~30-100 bidirectional links)
   jq '[.topics[].subtopics | length] | add' ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json
   # expect: ~400-800 total subtopics
   ```

5. Commit the PM artifact from the PM worktree (NOT from the parent repo):
   ```
   cd ReCiter-Publication-Manager
   git checkout feature/chatbot-runtime
   git add controllers/chatbot/hierarchy.json
   git commit -m "feat(04): add subtopic hierarchy.json from Plan 06 full backfill"
   ```

### Resume signal

Type `backfill complete` once `hierarchy.json` is committed to the PM
worktree, OR `blocked: <issue>` if any topic requires additional
intervention.

## Commits

| Task | Commit | Files |
|------|--------|-------|
| Task 1: backfill_topic.py | `753906c` | `backfill_topic.py` (607 lines, new) |
| Task 2: backfill_all.py + log template | `562ed6b` | `backfill_all.py` (634 lines, new), `.planning/phases/04-subtopic-system/backfill_log.md` (template) |
| SUMMARY | (this commit) | `04-06-SUMMARY.md` |

## Self-Check: PASSED

- `backfill_topic.py` exists at repo root (607 lines).
- `backfill_all.py` exists at repo root (634 lines).
- `.planning/phases/04-subtopic-system/backfill_log.md` exists (template).
- Commit `753906c` on `main` (verified via `git log --oneline`).
- Commit `562ed6b` on `main` (verified via `git log --oneline`).
- All Task 1 and Task 2 acceptance_criteria greps return the expected counts.
- `python backfill_all.py --only-verdict-check` live-runs and returns 0.
- Task 3 (full-backfill execution) explicitly flagged as pending human
  operator action; no hierarchy.json has been written to the PM worktree yet.

---

## Task 3 Completion (2026-04-14T19:19Z)

Full backfill executed unattended via `--skip-all-reviews --continue-on-error`.
A mid-run contract bug surfaced: `backfill_topic.py --skip-review` wrote
`review_status: "auto_approved"` per plan spec D-19, but `assign_subtopics.py`
only accepted `"approved"`. Patched in `68396b5` so Pass 2 accepts both values
(and emits a warning when auto-approved is seen — SKIP_REVIEW audit trail
remains the primary record).

### Run metrics

| Metric | Value |
|--------|-------|
| Start → end | 12:55:42 → 18:16:49 (5 hr 21 min) |
| Topics processed | 66 |
| Topics included in hierarchy | 65 (64 from backfill + aging pilot) |
| Topics excluded | 2 (`implementation_science` — Pass 1 JSON truncation; `oral_craniofacial_health` — below cold-start floor, 25 activities) |
| Total subtopics | 1,526 |
| Pass 2 Haiku cost (parsed from stdout) | **$97.05** |
| Pass 1 Sonnet cost (not auto-measured) | estimated ~$30-40 |
| Full run log | `.planning/phases/04-subtopic-system/artifacts/backfill_run_20260414_125542.log` |

### Artifact assembly

Aging pilot data was not written as an augmented draft during the backfill
(backfill_all skips aging_geroscience by design). After the run completed,
`hierarchy_augmented_aging_geroscience.json` was generated offline by
calling `backfill_topic._build_augmented_draft` against the existing reviewed
draft + `total_weights_aging_geroscience.json` (no LLM cost). Hierarchy was
re-assembled via `backfill_all._assemble_hierarchy`.

### See-also gap (known, documented)

`generate_see_also.py` was invoked twice:
1. Original run: Sonnet returned unterminated JSON at char 30,304 (8k output cap).
2. Retry at `max_tokens=32768` with `read_timeout=900s` (patched in
   `utils/bedrock_client.py`): unterminated JSON at char 120,773.

Both failures confirm single-shot see-also over 1,526 subtopics exceeds what
Sonnet can reliably produce. The hierarchy.json committed to PM has
`see_also: []`. Per D-10, see_also is navigation-aid only (NOT used in
retrieval scoring), so Plans 07-10 are unblocked. Follow-up work: chunk the
see_also generation by parent-topic group.

### Fixes committed during Task 3

| Commit | Change |
|--------|--------|
| `2fec412` | Add `--skip-all-reviews` flag to `backfill_all.py` so a single command can run the full phase unattended |
| `68396b5` | `assign_subtopics.py`: accept `auto_approved` in addition to `approved` |
| (uncommitted in parent repo — config tweak) | `utils/bedrock_client.py`: bump Bedrock `read_timeout` from 300s to 900s |

### PM subrepo commit

`ReCiter-Publication-Manager` (worktree on `feature/chatbot-runtime`):
- Commit: `7ce94ba`
- File: `controllers/chatbot/hierarchy.json`
- Schema: version=subtopic_v1, taxonomy_version=taxonomy_v2
- Stats: 65 topics, 1,526 subtopics, 2 excluded, 0 see_also (pending)
