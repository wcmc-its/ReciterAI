---
phase: 06-spotlight-pipeline
plan: 02
subsystem: spotlight
tags: [dynamodb, boto3, pytest, dataclasses, python]

# Dependency graph
requires:
  - phase: 06-spotlight-pipeline (Plan 06-01)
    provides: ARTIFACTS_BUCKET constant, IAM/bucket policy templates, DynamoDB
      partition schema doc, hierarchy → artifacts migration runbook
  - phase: 01-foundation
    provides: TOPIC# enrichment in DynamoDB (impact_score, year, primary_subtopic_id)
provides:
  - "spotlight/ Python package with frozen dataclasses (Author, Paper, PoolEntry)"
  - "spotlight/pool_ranker.rank_pool() — top-50 subtopics by 24-month impact_score sum"
  - "Deterministic ordering invariant via tuple sort key (-score, subtopic_id)"
  - "9-test pytest suite with mocked DynamoDB (no AWS calls)"
affects: [06-03 rotation-selector, 06-05 lede-generator, 06-06 assembler]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Frozen dataclass with __post_init__ validation (Author.position)"
    - "tuple[T, ...] field for hashable container (PoolEntry.papers)"
    - "Lazy module-level boto3 singleton via _get_default_client() pattern"
    - "Synthetic StubDynamoClient with get_paginator('scan') for unit tests"

key-files:
  created:
    - "spotlight/__init__.py"
    - "spotlight/types.py"
    - "spotlight/pool_ranker.py"
    - "test_spotlight_pool_ranker.py"
  modified: []

key-decisions:
  - "Tuple sort key (-score, subtopic_id) is the sole determinism mechanism — no random.seed, no within-window decay (CONTEXT supersedes SPOT-01 wording)"
  - "PMID dedup per subtopic so a single popular publication tagged in N TOPIC# rows counts ONCE — prevents inflation"
  - "Empty-string author fallback in _extract_paper instead of raising — Plan 06-05 lede generator filters out papers with no author identity"
  - "_parent_of uses regex r'_\\d{3}$' as v1 hierarchy convention; Plan 06-03 may override if hierarchy.json canonicalizes parents differently"

patterns-established:
  - "Lazy boto3 init: module-level singleton + _get_default_client() helper, mirrors utils/bedrock_client.py"
  - "Test seam via injected client kwarg (rank_pool(client=stub)) — no AWS calls in tests"

requirements-completed: [SPOT-01, SPOT-02]

# Metrics
duration: ~30min
completed: 2026-05-07
---

# Phase 6 Plan 02: Pool Ranker Summary

**Top-50 subtopic ranker over the last 24 months of TOPIC# enrichment with deterministic tiebreakers, lazy boto3, and a 9-test pytest suite — upstream input for every other Phase 6 stage.**

## Performance

- **Duration:** ~30 min (including a misplaced-commit recovery on the wrong branch)
- **Started:** 2026-05-07T19:00:00Z (approximate)
- **Completed:** 2026-05-07T19:25:06Z
- **Tasks:** 2
- **Files created:** 4 (1 package marker + 3 modules + 1 test file)
- **Lines added:** 545 (1 + 72 + 212 + 260)

## Accomplishments
- `spotlight/` package skeleton with three frozen dataclasses (Author, Paper, PoolEntry)
- `rank_pool()` reads TOPIC# DynamoDB partitions, applies 24-month hard cutoff, sums `impact_score` per `primary_subtopic_id`, and returns top-50 with deterministic ordering
- Pure-Python `_parent_of()` helper for v1 hierarchy convention (`<parent>_NNN` → `<parent>`)
- Lazy boto3 client (no AWS calls at module import time) — verified by an explicit invariant test
- 9-test pytest suite covering all the behaviors enumerated in the plan's `<behavior>` block
- All acceptance criteria pass: deterministic sort literal grep-asserted, no `cwid_` literal, no top-level boto3 client, no randomness, SPOT-01/02 traceability strings present

## Task Commits

Each task was committed atomically on branch `worktree-agent-a10c9d9283dc40489`:

1. **Task 1: spotlight package skeleton + dataclasses** — `8e819ed` (feat)
2. **Task 2 RED: failing pool_ranker tests** — `383b943` (test)
3. **Task 2 GREEN: pool_ranker.rank_pool() implementation** — `cbb4625` (feat)

No REFACTOR commit — implementation was clean on first GREEN pass; no separate cleanup needed.

## Files Created/Modified

- `spotlight/__init__.py` — package marker (1 line)
- `spotlight/types.py` — Author, Paper, PoolEntry frozen dataclasses (72 lines)
- `spotlight/pool_ranker.py` — rank_pool(), _extract_paper(), _parent_of(), _get_default_client() (212 lines)
- `test_spotlight_pool_ranker.py` — 9 pytest cases with StubDynamoClient (260 lines)

## Test Fixture Shape

The synthetic test fixture mirrors the DynamoDB low-level item shape used by `import_enrichment.py`:

```python
{
    "pmid": {"S": "..."},
    "primary_subtopic_id": {"S": "aging_001"},
    "impact_score": {"N": "30.0"},
    "year": {"N": "2025"},
    "title": {"S": "..."},
    "journal": {"S": "..."},
    "impact_justification": {"S": ""},
    "synopsis": {"S": ""},
    "first_author_person_identifier": {"S": ""},
    "first_author_display_name": {"S": ""},
    "last_author_person_identifier": {"S": ""},
    "last_author_display_name": {"S": ""},
}
```

A `StubDynamoClient` exposes `get_paginator("scan")` returning a `_StubPaginator.paginate()` that yields one or more pages of `{"Items": [...]}`. No AWS calls at any point.

## Decisions Made

- **24-month hard cutoff, no within-window decay.** RESEARCH §Open Questions §3 noted that REQUIREMENTS.md SPOT-01 mentions "Σ (impactScore × recency_weight(year))" but CONTEXT supersedes — implementation follows CONTEXT.
- **PMID dedup per subtopic.** Without this, a single highly-cited publication tagged in 5 TOPIC# rows would inflate that subtopic's `pool_score` 5×. Test 6 enforces this invariant.
- **Empty-string author fallback (no raise).** Plan 06-02's `Paper` dataclass intentionally omits author validation so legacy TOPIC# rows without the Phase 6 author-fanout fields still flow through. Filtering happens downstream in Plan 06-05.
- **Tuple sort key is the only determinism mechanism.** No randomness, no `random.seed`, no per-run state. Acceptance criterion grep-asserts the `sorted(.*key=lambda.*-` literal stays on a single line.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking format] Inlined `sorted(...)` call to satisfy grep-based acceptance check**
- **Found during:** Task 2 acceptance verification
- **Issue:** Initial implementation broke `sorted(...)` across multiple lines (4-line tuple-style for readability). Acceptance criterion #4 uses `grep -cE 'sorted\(.*key=lambda.*-'` which requires both `sorted(` and `key=lambda...-` on the same line.
- **Fix:** Collapsed to a single line — `ranked = sorted(by_subtopic.items(), key=lambda kv: (-kv[1]["score"], kv[0]))`. Behavior unchanged.
- **Files modified:** `spotlight/pool_ranker.py`
- **Verification:** `grep -cE 'sorted\(.*key=lambda.*-' spotlight/pool_ranker.py` returned `1`; pytest still 9/9 passing
- **Committed in:** `cbb4625` (fix folded into the GREEN commit)

**2. [Recovery — not a code deviation] First Task 1 commit landed on `main` instead of the worktree branch**
- **Found during:** Task 1 commit verification
- **Issue:** Initial Bash invocations resolved to the project root (`/Users/paulalbert/Dropbox/GitHub/ReciterAI -ReCiter-Integration`) which is the main checkout, not the worktree at `.claude/worktrees/agent-a10c9d9283dc40489`. The first commit `aff1098` landed on `main`.
- **Fix:** `git reset --hard d81faf0` on the main checkout to drop the misplaced commit (only my own commit was affected; verified via `git log d81faf0..HEAD`). Re-created the same files in the worktree and re-committed there as `8e819ed`. The two commits are functionally identical.
- **Verification:** `git log --oneline -3` on `main` shows the merge commit at HEAD with no orphan commit; the worktree branch holds the canonical Task 1 commit.
- **Committed in:** N/A (recovery, not a logic change)

**Total deviations:** 1 code deviation (single-line sort literal) + 1 recovery (branch placement). Both auto-fixed.

**Impact on plan:** None. All planned behavior shipped; no scope creep.

## Issues Encountered

- **Branch placement.** The orchestrator placed the agent on a worktree branch but Bash tool calls landed in the main checkout. Mitigated by always passing absolute paths under `.claude/worktrees/agent-a10c9d9283dc40489/...` for subsequent operations.

## TOPIC# Author-Fanout Field Audit (per plan `<output>` directive)

The plan's `<output>` block instructs me to verify that the TOPIC# author-payload attribute names referenced in `_extract_paper` (`first_author_person_identifier`, `first_author_display_name`, `last_author_person_identifier`, `last_author_display_name`) match what `import_enrichment.py` actually writes.

**Finding:** `import_enrichment.py:155-162` writes only `synopsis`, `impact_score`, `impact_justification`, `title`, `journal`, `year`, and `author_position` (a single string for the row's own `personIdentifier`). It does NOT write the four `first_author_*` / `last_author_*` fan-out fields that `_extract_paper` reads.

**Consequence:** Today, `_extract_paper` always sees empty strings for the four author-fanout attributes on production TOPIC# rows, so every Paper emitted by `rank_pool()` has empty-string Author identities. This is a known design choice — the empty-string fallback flows downstream to Plan 06-05's lede generator, which filters out papers with no author identity.

**Follow-up tracked:** A future plan (likely 06-05 or a new 06-XX enrichment-extension plan) must either (a) extend `import_enrichment.py` to fan out first/last author identifiers from `analysis_summary_author` and back-fill TOPIC# rows, or (b) have the lede generator join author identities from `analysis_summary_author` at runtime. The pool ranker itself does not need to change either way — it is structurally agnostic to whether author fields are populated.

This is **not** a Plan 06-02 bug because the pool ranker's contract (rank by `impact_score` per `primary_subtopic_id`) does not depend on author fields. Author fields are passenger data carried through to the assembler.

## User Setup Required

None — no external service configuration. Tests run offline.

## Next Phase Readiness

- **Plan 06-03 (rotation_selector)** can now `from spotlight.pool_ranker import rank_pool` and `from spotlight.types import PoolEntry` and treat both as stable contracts.
- **PoolEntry is hashable** so the rotation selector can use it directly as a dict key for SPOTLIGHT_HISTORY# lookups.
- **Determinism** is enforced upstream — downstream stages do not need to re-sort.

## Self-Check: PASSED

Files verified to exist:
- `spotlight/__init__.py` — FOUND
- `spotlight/types.py` — FOUND
- `spotlight/pool_ranker.py` — FOUND
- `test_spotlight_pool_ranker.py` — FOUND

Commits verified to exist on branch:
- `8e819ed` — FOUND (feat: spotlight package skeleton)
- `383b943` — FOUND (test: failing pool_ranker tests)
- `cbb4625` — FOUND (feat: pool_ranker GREEN)

All 9 pytest cases pass: `python3 -m pytest test_spotlight_pool_ranker.py -x -q` exits 0.

---
*Phase: 06-spotlight-pipeline*
*Completed: 2026-05-07*
