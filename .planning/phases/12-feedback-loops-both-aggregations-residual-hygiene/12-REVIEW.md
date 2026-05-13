---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
reviewed: 2026-05-12T22:30:00Z
depth: standard
files_reviewed: 46
files_reviewed_list:
  - gates/reconciliation.py
  - gates/__init__.py
  - tests/test_aggregate_inclusive.py
  - tests/test_aggregate_idempotent.py
  - tests/test_reconciliation_gate.py
  - aggregate_subtopic_scores.py
  - docs/topic-subtopic-assignment.md
  - count_by_cwid.py
  - rollup_by_cwid.py
  - build_cwid_json.py
  - tests/test_rollup_incremental_parity.py
  - pipeline_drift/evaluator.py
  - tests/test_pipeline_drift_evaluator.py
  - pipeline_feedback/__init__.py
  - pipeline_feedback/finding_records.py
  - pipeline_feedback/sweep.py
  - pipeline_feedback/prompts/uncovered_pmid_sonnet_v0.md
  - pipeline_feedback/cli.py
  - pipeline_feedback/__main__.py
  - pipeline_feedback/markdown_render.py
  - tests/test_feedback_finding_records.py
  - tests/test_feedback_sweep.py
  - tests/test_feedback_cli.py
  - tests/test_feedback_render.py
  - pipeline_cold/run.py
  - tests/test_pipeline_cold_run.py
  - utils/event_records.py
  - tests/test_critic_reject_event.py
  - tests/test_critic_reject_producer.py
  - spotlight/critic.py
  - tests/test_hierarchy_reproducibility.py
  - tests/test_cold_path_e2e.py
  - tests/fixtures/cold_path_corpus/README.md
  - tests/fixtures/cold_path_corpus/taxonomy.json
  - tests/fixtures/cold_path_corpus/pubs.json
  - docs/sensitive-topic-exclusion.md
  - GETTING_STARTED.md
  - config/thresholds.schema.json
  - config/thresholds.md
  - tests/test_thresholds_schema.py
  - tests/test_thresholds_keys.py
  - tests/test_env_check_thresholds.py
  - config/thresholds.json
  - utils/env_check.py
  - utils/stage_records.py
  - assign_subtopics.py
findings:
  critical: 3
  warning: 11
  info: 6
  total: 20
status: issues_found
---

# Phase 12: Code Review Report

**Reviewed:** 2026-05-12T22:30:00Z
**Depth:** standard
**Files Reviewed:** 46
**Status:** issues_found

## Summary

Phase 12 ships a substantial cross-cutting change: reconciliation gate (D-18), both-aggregations (exclusive/inclusive), per-CWID rollup CSV rename with dual-write, drift evaluator extension (D-34), feedback sweep + CLI, CRITIC_REJECT# producer, and a bounded E2E test. The code is largely well-structured, but **three load-bearing defects** make it past the test suite because the tests themselves are degenerate in the exact way the defect would manifest:

1. The D-33 reconciliation invariant in `aggregate_subtopic_scores.run()` (line 517-520) is called with `faculty_scores` passed as BOTH `faculty_map` AND `subtopic_score_partition_data`. This is `x == x` — a tautology. The reconciliation can never detect real divergence in production. The tests "validating" D-33 happy-path do the same thing (passing the same dict twice), so the suite green-lights the broken call.

2. `rollup_by_cwid.aggregate_from_breakdowns` reads `row["primary_subtopic_id"]` from the subtopic CSV (line 152), but the new INCLUSIVE CSV (`faculty_subtopic_counts_inclusive.csv`) has a header column named `subtopic_id`, NOT `primary_subtopic_id`. If the rollup is ever pointed at the inclusive CSV (via `--subtopic-csv` flag), it will KeyError on first row. Same defect lives in `build_cwid_json.py:51`. The resolver currently always points to exclusive, masking the bug.

3. `count_by_cwid.py:136` writes the SAME dict into both the exclusive and inclusive CSV (the script only scans `primary_subtopic_id`). The inclusive CSV is therefore byte-identical to the exclusive CSV in production, making `test_inclusive_csv_distinct_from_exclusive` pass only because it calls `write_subtopic_csvs(exclusive, inclusive)` from outside — the script's own production path never exercises the distinct-content branch. The comment on line 131-135 acknowledges this, but it ships as a silent feature gap, not a clearly-failing assertion.

Additional defects: duplicate `load_thresholds` implementations in `utils/env_check.py` and `utils/event_records.py` (two of the new pipeline modules import from different ones); module-import-time config loading in `assign_subtopics.py` that crashes if `thresholds.json` is absent at import; `pipeline_cold/run.py` propagates negative subprocess returncodes incorrectly; PII-boundary fields are inconsistently documented vs. asserted.

## Critical Issues

### CR-01: D-33 reconciliation invariant is a tautology in production

**File:** `aggregate_subtopic_scores.py:517-520`
**Issue:** The in-stream D-33 reconciliation check is called with the same dict passed as both `faculty_map` and `subtopic_score_partition_data`:

```python
_assert_d33_reconciliation(
    faculty_map=faculty_scores,
    subtopic_score_partition_data=faculty_scores,   # SAME DICT
)
```

The function compares every (pid, sid) pair across the two dicts. When they are the same object, the comparison reduces to `x == x` — it cannot detect any real divergence between the faculty-map write and the SUBTOPIC_SCORE# partition write. The whole point of D-33 (per the module docstring) was to "verify per-CWID per-subtopic equality between faculty.subtopic_scores map and the new SUBTOPIC_SCORE# partition." That comparison cannot happen with this call shape.

This same tautology is repeated in two tests (`test_d33_invariant_passes_on_aligned_writes`, `test_cold_path_d33_reconciliation_passes`), so the suite green-lights the broken call. The "anti-tautology" test (`test_d33_invariant_raises_on_divergence`) constructs a SEPARATE `corrupted_partition` dict and demonstrates the invariant CAN catch divergence when given two different inputs — but the production call site never gives it two different inputs.

**Fix:** Materialize the partition-derivation dict by reading what `_write_subtopic_score_partitions` actually wrote (e.g., capture the Items emitted, project back to `{pid: {sid: score}}`), then pass THAT as `subtopic_score_partition_data`. Example:

```python
# After _write_subtopic_score_partitions(...)
written_partition: dict[str, dict[str, float]] = defaultdict(dict)
# If _write_subtopic_score_partitions returns the items, iterate them:
for item in items_written_to_subtopic_score_partition:
    sid = item["subtopic_id"]
    for pid, score in item["faculty_scores"].items():
        written_partition[pid][sid] = float(score)

_assert_d33_reconciliation(
    faculty_map=faculty_scores,
    subtopic_score_partition_data=dict(written_partition),
)
```

This requires `_write_subtopic_score_partitions` to return the items (or to expose a builder helper that produces them without I/O) so the run-loop can cross-check the two derivations independently.

---

### CR-02: rollup_by_cwid + build_cwid_json crash if pointed at inclusive CSV

**File:** `rollup_by_cwid.py:152`, `build_cwid_json.py:51`
**Issue:** Both scripts read the subtopic CSV with `row["primary_subtopic_id"]`:

```python
# rollup_by_cwid.py:152
n_subtopics[cwid].add(row["primary_subtopic_id"])

# build_cwid_json.py:51
per_cwid[row["personIdentifier"]]["subtopics"][row["primary_subtopic_id"]] = int(row["n_activities"])
```

But Phase 12 D-13 introduces a SECOND subtopic CSV (`faculty_subtopic_counts_inclusive.csv`) whose header is `subtopic_id`, NOT `primary_subtopic_id` (see `count_by_cwid.py:69`):

```python
w.writerow(["personIdentifier", "subtopic_id", "n_activities"])
```

`_resolve_subtopic_csv` currently prefers the canonical exclusive name, so production never hits this bug. But:

- `rollup_by_cwid.py` accepts `--subtopic-csv <path>` as a CLI override. An operator passing the inclusive CSV path will KeyError on the first row.
- The deprecation-window resolver path is fragile: in any future cycle where only the inclusive CSV exists (or where rename ordering goes wrong), the code crashes with an opaque KeyError instead of a clear "wrong CSV shape" message.

**Fix:** Either reject the inclusive CSV explicitly with a clear error, or accept both header names:

```python
# In aggregate_from_breakdowns, defensively pick the column:
sub_id_key = "primary_subtopic_id" if "primary_subtopic_id" in row else "subtopic_id"
n_subtopics[cwid].add(row[sub_id_key])
```

Better: define a constant for the expected column name in both producer and consumer, and validate the header up front. The current "two CSVs with two different schemas under the same purpose" is a documented contract gap; surface it.

---

### CR-03: count_by_cwid writes inclusive CSV with exclusive data

**File:** `count_by_cwid.py:136`
**Issue:** The script's `__main__` block calls `write_subtopic_csvs(dict(subtopic_counts), dict(subtopic_counts))` — passing the SAME dict (derived from `primary_subtopic_id` only) for both the exclusive and inclusive CSVs. The result: `faculty_subtopic_counts_inclusive.csv` produced by this script is byte-identical to `faculty_subtopic_counts_exclusive.csv`, which silently breaks the D-13 contract that the inclusive CSV reflect all above-floor `subtopic_ids[]`.

Lines 131-135 contain an explanatory comment acknowledging this, but the workaround is to ship the wrong file rather than emit no file. Downstream consumers reading the inclusive CSV will get inclusive-name semantics with exclusive-content data and never know the difference. This is a data correctness defect dressed as a deprecation step.

The test `test_inclusive_csv_distinct_from_exclusive` calls `write_subtopic_csvs(exclusive_counts, inclusive_counts, dest_dir=tmp_path)` with TWO different dicts and asserts they produce different bytes — that exercises the helper in isolation but never exercises the calling code path that production uses.

**Fix:** Either (a) compute the real inclusive counts by scanning `subtopic_ids` (a list field on activity rows) and aggregating across all entries, or (b) do NOT emit `faculty_subtopic_counts_inclusive.csv` from this script and instead document that it must come from `aggregate_subtopic_scores._aggregate_inclusive` against the activity rows. The current behaviour creates a file with a contractually-wrong name.

```python
# Option (a) — scan subtopic_ids list:
inclusive_counts: dict[tuple[str, str], int] = defaultdict(int)
for page in pages:
    for item in page.get("Items", []):
        pk = item.get("PK", {}).get("S", "")
        if not pk.startswith("TOPIC#"):
            continue
        cwid_raw = item.get("faculty_uid", {}).get("S", "")
        if not cwid_raw:
            continue
        cwid = cwid_raw[len("cwid_"):] if cwid_raw.startswith("cwid_") else cwid_raw
        for sid in item.get("subtopic_ids", {}).get("L", []):
            sid_val = sid.get("S")
            if sid_val:
                inclusive_counts[(cwid, sid_val)] += 1

write_subtopic_csvs(dict(subtopic_counts), dict(inclusive_counts))
```

## Warnings

### WR-01: Duplicate `load_thresholds` implementations diverge by module

**File:** `utils/env_check.py:56-78`, `utils/event_records.py:46-54`
**Issue:** Two implementations of `load_thresholds` with the same name and similar behaviour now exist. `pipeline_drift/evaluator.py:406` imports from `utils.event_records`; `pipeline_feedback/sweep.py:34` imports from `utils.env_check`. The two differ:
- `env_check.load_thresholds` raises `FileNotFoundError` with a guidance message before reading.
- `event_records.load_thresholds` calls `open(target)` directly — if missing, raises `FileNotFoundError` with the bare path.

These are minor today but will drift apart over time (schema validation, env-overlay support, caching). A future bugfix in one won't propagate to the other, and consumers won't know which copy they're getting.

**Fix:** Pick one canonical location (probably `utils/env_check.py` since it owns the schema validation), and re-export from the other for backwards compatibility:

```python
# utils/event_records.py
from utils.env_check import load_thresholds  # noqa: F401  # back-compat re-export
```

Then update all importers to point at the canonical one, and remove the duplicate body.

---

### WR-02: assign_subtopics imports crash if thresholds.json missing

**File:** `assign_subtopics.py:95-99`
**Issue:** Module-level constants are loaded at import time:

```python
from utils.env_check import load_thresholds as _load_thresholds_cfg
_CFG = _load_thresholds_cfg()
SCORE_FLOOR = float(_CFG["score_floor"])
DEFAULT_CONFIDENCE_FLOOR = float(_CFG["confidence_floor"])
TIE_EPSILON = float(_CFG["tie_epsilon"])
```

If `config/thresholds.json` is absent or malformed at import time, `assign_subtopics` cannot be imported at all. This blocks unrelated test runs and operator dry-runs. The test suite works around this by reloading the module with patched `load_thresholds` (see `test_assign_subtopics_constants_pick_up_config_values`), but the production constraint is real.

This pattern also makes it impossible to test the module without a thresholds.json file present in the repo, and turns any thresholds.json schema regression into an import-time crash rather than a runtime check.

**Fix:** Either (a) lazy-load at first use:

```python
_CFG: dict | None = None
def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = load_thresholds()
    return _CFG

# Usage:
score_floor = float(_cfg()["score_floor"])
```

Or (b) make the constants `Optional[float]` with `None` sentinels until used, raising a clear "thresholds.json required" error when first read.

---

### WR-03: pipeline_cold.run returncode propagation breaks for signal terminations

**File:** `pipeline_cold/run.py:399`
**Issue:**

```python
return outcome.returncode or 1
```

`subprocess.run` returns `returncode` as an `int`. On POSIX, if the child died via a signal, returncode is negative (e.g., `-9` for SIGKILL). In Python, negative integers are truthy, so `outcome.returncode or 1` returns the negative number unchanged. The CLI exit code then propagates as a negative value, which most shells will mask to `256 + returncode` (e.g., -9 → 247), losing the signal-vs-exit distinction. Also, callers that check `rc == 0` for success will see "not zero" but lose any meaningful diagnostic.

The truthy-check `or 1` was probably intended to catch `returncode is None`, but `subprocess.run(check=False)` always sets it to an int.

**Fix:** Map signal exits to a stable failure code, e.g.:

```python
rc = outcome.returncode
if rc is None or rc < 0:
    return 1  # subprocess killed by signal — treat as generic failure
return rc or 1
```

---

### WR-04: pipeline_feedback/cli.py._run_render: kw-default get_table is bypassed

**File:** `pipeline_feedback/cli.py:79-91`
**Issue:**

```python
def _run_render(args, *, get_table=_default_get_table) -> int:
    try:
        table = get_table()
    except Exception as exc:
        ...
```

The function declares `get_table` as a keyword-only parameter, but main() calls `_run_render(args)` without passing it (line 153 `return _run_render(args)`). This means in production the default closure-over-`_default_get_table` runs. Fine. But the test `test_render_subcommand_reads_run_id_rows` does:

```python
def _injected_run_render(args, *, get_table=lambda: mock_table):
    return original_run_render(args, get_table=get_table)

with patch.object(cli_mod, "_run_render", side_effect=_injected_run_render):
    rc = cli_mod.main(["render", "run-abc"])
```

The test patches `_run_render` itself (replacing it with a wrapper that calls the original), which means the call path actually exercised is the inner wrapper, not main's `_run_render(args)`. Test coverage of the actual main → _run_render → _default_get_table chain is weak.

**Fix:** Test via dependency-injection at module level (e.g., monkeypatch `cli._default_get_table`) instead of wrapping the function under test. This catches regressions in the actual call path.

---

### WR-05: pipeline_feedback/sweep._invoke_sonnet silently returns [] on parse failure

**File:** `pipeline_feedback/sweep.py:236-238`
**Issue:**

```python
try:
    parsed = json.loads(text)
    return parsed.get("candidate_topics", [])
except (json.JSONDecodeError, AttributeError):
    logger.warning("feedback_sweep: Sonnet response was not valid JSON; skipping candidate topics")
    return []
```

A Sonnet response that fails JSON-parse silently degrades to "zero candidates." Operators see the sweep complete with `candidate_topics=0` and no surface signal that Bedrock returned garbage. Combined with `_run_uncovered_pmid_sweep` returning early `if not candidate_topics_raw: return []` (line 275-276), a parse failure is observationally identical to a no-uncovered-PMIDs run.

**Fix:** Emit a structured event (e.g., a `FEEDBACK_SWEEP_PARSE_FAILURE#` row, or at minimum bump the alert dispatcher) so a degraded sweep is distinguishable from a clean one. The sweep summary should also carry a `sonnet_parse_status` field.

---

### WR-06: pipeline_feedback/sweep._run_diagnostic_aggregation uses datetime.now() internally

**File:** `pipeline_feedback/sweep.py:425`
**Issue:**

```python
now = datetime.now(timezone.utc)
diag_since = now - timedelta(days=persistence_days)
effective_since = max(since, diag_since)
```

`now` is captured inside the helper, not threaded from the caller. This makes the test `test_diagnostic_window_bounded` flaky over time (the "in_window" timestamps are fixed in 2026, but `datetime.now()` returns the real wall clock — when run after 2026-08-12, the "in_window" `2026-04-15` timestamp drifts out of the 90-day window). The test passes today only because the calendar matches.

It also means in production a critic_reject_persistence_days=90 effectively starts from `datetime.now()`, not from any deterministic moment, so reruns at different times produce different `effective_since` and therefore different diagnostic outputs given the same input rows. That's a hidden non-determinism in a layer that the rest of Phase 12 went out of its way to make deterministic (D-14, G-29, G-36).

**Fix:** Accept `now` as a parameter with a `datetime.now(timezone.utc)` default; thread it through `run_sweep`:

```python
def _run_diagnostic_aggregation(*, table, cfg, since, run_id, triggered_by, now=None):
    now = now or datetime.now(timezone.utc)
    ...
```

And let the test pin `now` directly.

---

### WR-07: `_resolve_subtopic_csv` returns canonical path when neither file exists

**File:** `rollup_by_cwid.py:113-114`, `build_cwid_json.py:39`
**Issue:**

```python
# Neither exists; return the canonical name so downstream raises a clear FileNotFoundError.
return DEFAULT_SUBTOPIC_CSV
```

The comment says "downstream raises a clear FileNotFoundError," but downstream is `open(subtopic_csv)` which raises `FileNotFoundError: faculty_subtopic_counts_exclusive.csv` — that's not "clear," it just blames one file when both candidates are missing. An operator who has `cwid_subtopic_counts.csv` from a year ago (the legacy filename) and forgot to rename will see "exclusive.csv not found" and not realize the resolver is checking that path.

**Fix:** Raise a `FileNotFoundError` in the resolver itself with a message listing both candidate paths:

```python
raise FileNotFoundError(
    f"Subtopic CSV not found. Checked: {DEFAULT_SUBTOPIC_CSV} (canonical) "
    f"and {LEGACY_SUBTOPIC_CSV} (legacy). Generate via count_by_cwid.py."
)
```

---

### WR-08: D-33 invariant only iterates over union of person_identifiers — silent on partition-only entries

**File:** `aggregate_subtopic_scores.py:371-387`
**Issue:** `_assert_d33_reconciliation` builds `all_pids = set(faculty_map.keys()) | set(subtopic_score_partition_data.keys())`. For a pid present only in the partition (faculty-map missing), the code reads `fm_scores = faculty_map.get(pid, {})` and compares each partition value against 0.0. That works.

But the violation reporting caps at 50 with `if len(violations) >= 50: break` — and the break is checked AFTER each subtopic AND after each pid, so it can exit mid-subtopic for one pid leaving the next pid's violations uncounted. The reported violation count is thus systematically a floor, not an actual count. The error message says `{len(violations)} pairs disagree` but that's just the cap, not the truth.

**Fix:** Decouple "collect" from "report." Walk all pairs, count total violations, then cap the reported sample:

```python
total_violations = 0
sample: list[str] = []
for pid in sorted(all_pids):
    ...
    if abs(fm_val - sp_val) > FLOAT_EPS:
        total_violations += 1
        if len(sample) < 50:
            sample.append(...)
if total_violations:
    raise RuntimeError(
        f"D-33 reconciliation violated: {total_violations} (cwid, subtopic) pairs disagree "
        f"(showing first {len(sample)}). First: {sample[0]}"
    )
```

This makes the count accurate (operators triaging will want to know if it's 51 or 5000) without unbounded output.

---

### WR-09: `_aggregate_inclusive`'s `confidence_floor` parameter is dead code

**File:** `aggregate_subtopic_scores.py:193-203`
**Issue:** The function signature is `_aggregate_inclusive(rows, *, confidence_floor: float = 0.0)`, and the docstring says "Confidence floor is NOT re-applied here ... The confidence_floor parameter is on the signature for type safety and future-proofing; today it is documentation, not behavior."

A parameter that callers can pass any value to but that has no effect is a footgun. Tests pass `confidence_floor=0.0` everywhere; a real bug where a non-zero value should have filtered subtopic_ids would not be caught.

**Fix:** Remove the parameter entirely. If a future need for re-application arises, add it then with a real implementation and a test that fails when the floor is ignored. The "future-proofing" rationale doesn't survive a code review.

---

### WR-10: `_in_window` and `_filter_in_window` accept any timestamp parse failure silently

**File:** `pipeline_drift/evaluator.py:113-127`, `pipeline_feedback/sweep.py:55-61`
**Issue:** Both helpers swallow `ValueError` (and `AttributeError` in sweep) on timestamp parse failures, returning `False` (sweep) or just skipping the row (evaluator). A bulk pipeline that started writing timestamps in a slightly-off format would silently lose every row from drift evaluation and feedback sweep. No alert, no count.

The evaluator at least logs nothing; the sweep helper has no logging either. The only signal is "drift returned zero rows" — observationally identical to a healthy quiet day.

**Fix:** Track a `parse_failures` counter; if it exceeds a threshold (e.g., 10% of rows or 100 rows), raise or emit a `WARN` alert via `pipeline_common.alert.dispatch`. At minimum, log a `WARNING` once with the offending value so an operator grepping logs can find it.

---

### WR-11: `pipeline_cold/run.py` records_written semantics misleading for cold_run row

**File:** `pipeline_cold/run.py:417`
**Issue:** `records_written=len(outcomes)` writes the number of stages run to the `records_written` field on `STAGE#cold_run#GLOBAL`. Every other STAGE# row in the codebase uses `records_written` to mean "rows the stage actually wrote to its data store" (see `rollup_by_cwid.py:427`, `stage_records.py:178-182` doc). For cold_run, this field is "stage count" — a different semantic dressed in the same field name.

A dashboard that SUMs `records_written` across stages will conflate "data row count" with "stage count" and produce nonsense aggregates.

**Fix:** Either use a different field (e.g., `stage_count`) for cold_run, or set `records_written=None` (the builder skips the field when None) and rely on the existing `stage_names` list for stage cardinality.

## Info

### IN-01: Inconsistent module docstring style across pipeline_feedback

**File:** `pipeline_feedback/__init__.py`
**Issue:** Single-line docstring `"""Feedback-event consumer — Phase 12 §9."""` while neighbouring modules have multi-paragraph docstrings linking D-numbers. The package-level docstring is the right place to list module index for the package (sweep, cli, finding_records, markdown_render) so newcomers can navigate.

**Fix:** Expand to a 5-10 line module index. Example:

```python
"""Phase 12 §9 feedback-event consumer.

Submodules:
- finding_records: pure builders + writers for CANDIDATE_TOPIC#, RECLUSTER_RECOMMENDATION#, SPOTLIGHT_DIAGNOSTIC#.
- sweep:           main run_sweep() orchestrator (D-01..D-09, D-32, D-34).
- cli:             python -m pipeline_feedback sweep|render entry point.
- markdown_render: deterministic markdown rendering for sweep findings (D-03).
"""
```

---

### IN-02: `pipeline_cold/run.py` carries unused `dataclass(frozen=False)` for ColdStage

**File:** `pipeline_cold/run.py:75-86`
**Issue:** `ColdStage` is a `@dataclass` (mutable) but is never mutated after construction. Making it frozen documents the intent and catches accidental mutation. Same for `StageOutcome` (line 151).

**Fix:** `@dataclass(frozen=True)` on both.

---

### IN-03: `pipeline_feedback/cli.py` imports `boto3.dynamodb.conditions.Attr` at module scope

**File:** `pipeline_feedback/cli.py:31`
**Issue:** Top-level `from boto3.dynamodb.conditions import Attr` forces a `boto3` import at module load. Most other modules in this phase lazy-import boto3 inside the function (`pipeline_feedback/sweep.py:165`, `pipeline_drift/evaluator.py:86`). The inconsistency complicates test fixtures that try to monkeypatch boto3.

**Fix:** Move the import into `_fetch_rows_by_run_id`:

```python
def _fetch_rows_by_run_id(table, run_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Attr
    ...
```

---

### IN-04: `_now_iso` helper duplicated across modules

**File:** `gates/reconciliation.py` (none), `aggregate_subtopic_scores.py:124-126`, `pipeline_drift/evaluator.py:38-39`, `pipeline_feedback/sweep.py:45-46`, `pipeline_feedback/finding_records.py:25-26`, `pipeline_cold/run.py:66-67`, `rollup_by_cwid.py:85-86`, `utils/event_records.py:42-43`, `utils/stage_records.py:53-54`
**Issue:** The same `_now_iso()` definition appears in nine modules:

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
```

A future change to the timestamp format (microseconds, suffix, etc.) requires touching all nine. Documented as Pattern E in CLAUDE.md per the docstring in `aggregate_subtopic_scores.py:125`, but the pattern is "every module reimplements" — which is the opposite of a pattern.

**Fix:** Lift to `utils/time_helpers.py` (or similar) and import from there. Mechanical refactor.

---

### IN-05: `_resolve_subtopic_csv` defined twice with the same logic

**File:** `rollup_by_cwid.py:89-114`, `build_cwid_json.py:21-39`
**Issue:** Both modules implement `_resolve_subtopic_csv` with identical logic and identical comments. Maintenance footprint.

**Fix:** Move to a shared utility (e.g., `utils/csv_paths.py` or extend `utils/env_check.py`), import from both.

---

### IN-06: Test fixture `pubs.json` carries impossible "intended_subtopic" mapping silently

**File:** `tests/test_cold_path_e2e.py:131-162`
**Issue:** The E2E test builds SCORE# rows from `pubs.json` mapping each pub to `intended_subtopic`. If a pub in `pubs.json` carries an `intended_subtopic` value that doesn't match any subtopic in `taxonomy.json` (e.g., typo, stale data, hand-edit drift), the SCORE# row will be built with a primary_subtopic_id that the aggregator silently includes anyway (no taxonomy validation in `_aggregate_exclusive`). The test then passes despite the broken mapping.

**Fix:** Add a fixture-validation helper that asserts every `intended_subtopic` in pubs.json appears in taxonomy.json before building rows. This is one assertion in the test setup; failure would surface as a clear "fixture drift" error rather than a downstream null-shaped result.

```python
def _validate_fixture(taxonomy: dict, pubs: list[dict]) -> None:
    valid_ids = {
        s["id"]
        for t in taxonomy.get("topics", [])
        for s in t.get("subtopics", [])
    }
    for pub in pubs:
        intended = pub.get("intended_subtopic", "")
        assert intended in valid_ids, (
            f"Fixture drift: pub {pub.get('pmid')} → intended_subtopic={intended!r} "
            f"not in taxonomy.json subtopics"
        )
```

---

_Reviewed: 2026-05-12T22:30:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
