# Stage Records and Quality Gates — Substrate Guide

Phase 9 shipped two pieces of substrate that future stages should integrate with:

- **`utils.stage_records`** — content-addressed completion records (`STAGE#` items in DynamoDB).
- **`gates/`** — registered quality gates that run between produce and publish.

`pipeline_hierarchy.publish` is the reference integration. This document tells the next stage author how to follow that pattern.

Source-of-truth for design rationale: [`RECITERAI-SPEC.md` §5 (Decision 4) + §7 (Decision 6)](RECITERAI-SPEC.md). This guide is the operational follow-up.

---

## When to integrate

Integrate when your stage:

- Is expensive to run (LLM calls, bulk DynamoDB writes, S3 uploads) and could be safely skipped when inputs haven't changed.
- Has a notion of "this run vs that run" — i.e. produces an observable artifact, not just side effects on a long-running process.
- Has quality invariants that should hold before downstream consumers read the output (schema validity, PII absence, editorial rules).

If your stage is a one-shot operator script with no skip semantics and no quality invariants, the substrate is overhead — don't bother.

---

## Integration in five steps

The reference is `pipeline_hierarchy/publish.py`. Follow the same shape.

### 1. Pick a stage name and scope

- **Stage name** is a short snake_case identifier — e.g. `publish_hierarchy`, `score_publications`, `discover_subtopics`. The `publish_` and `score_` prefixes group related stages.
- **Scope** distinguishes runs under the same stage. Use `"GLOBAL"` for whole-pipeline stages. Per-topic stages (subtopic discovery, assignment) use `"topic:<topic_id>"`.

The `STAGE#` items land at `PK = STAGE#{stage_name}#{scope}`, `SK = RUN#{started_at}`.

### 2. Define your `input_hash` schema

Build a dict of everything that should invalidate the skip cache when it changes. Common fields:

```python
from utils.stage_records import compute_input_hash
from utils.bedrock_client import MODEL_IDS_BY_STAGE
import pipeline_hierarchy

input_hash = compute_input_hash("score_publications", {
    "pmid_set_sha256": <sha of sorted PMIDs being scored>,
    "taxonomy_version": "taxonomy_v2",
    "prompts_sha256": <sha of the relevant prompt module bytes>,
    "model_ids": [MODEL_IDS_BY_STAGE["screening"], MODEL_IDS_BY_STAGE["scoring"]],
    "stage_code_version": pipeline_hierarchy.__version__,  # or your stage's package version
})
```

**Rules of thumb**:

- Include the model IDs you'll invoke. A model swap MUST invalidate the cache.
- Include a code-version sentinel. A bundler bug fix should invalidate the cache without relying on file-mtime heuristics.
- Hash large inputs (PMIDs, prompts, schemas) — don't put their bytes in the dict directly.
- Exclude timestamps that re-stamp on every run. The publish stage explicitly drops `generated_at` from the hierarchy bytes before hashing for exactly this reason (see G-29 in the spec).

`compute_input_hash` is namespaced by stage name internally — two stages with structurally-identical input dicts won't collide.

### 3. Check the skip cache

```python
from utils.dynamodb_helpers import get_table
from utils.stage_records import should_skip, write_skipped
import time

table = get_table()
t_start = time.monotonic()
started_at = _now_iso()

skip, prior = should_skip(
    table,
    stage="score_publications",
    scope="GLOBAL",
    input_hash=input_hash,
)
if skip:
    duration_ms = int((time.monotonic() - t_start) * 1000)
    write_skipped(
        table,
        stage="score_publications",
        scope="GLOBAL",
        input_hash=input_hash,
        skip_reason=f"input_hash unchanged since {prior['started_at']}",
        started_at=started_at,
        duration_ms=duration_ms,
    )
    return  # exit 0
```

**Critical invariant**: a skip MUST emit a `STAGE#` row. The runner pins `cost_estimate_usd = SKIP_COST_USD` ($0.0000003 — one DDB GetItem) so dashboards split real-work cost from skip-detection cost. A skip that emits no row is the same as no skip at all.

`should_skip` only matches `complete` rows. Prior `failed` or `skipped` rows do NOT short-circuit a re-run.

### 4. Run gates (if applicable)

```python
from gates.registry import any_blocked, run_gates
# Import gate modules so @register_gate decorators fire.
import gates.schema_validation  # noqa: F401
import gates.parent_prefix       # noqa: F401
# ... etc.

gate_results = run_gates(stage="score_publications", **gate_kwargs)
if any_blocked(gate_results) and not force:
    write_failed(table, ..., failure_details={"failing_gates": [...]})
    return  # exit 3 by convention
```

See "Writing a gate" below for how to add new gates.

### 5. Run, then write `complete`

```python
# Do the work.
output_pointer = run_my_stage(...)

duration_ms = int((time.monotonic() - t_start) * 1000)
write_complete(
    table,
    stage="score_publications",
    scope="GLOBAL",
    input_hash=input_hash,
    started_at=started_at,
    duration_ms=duration_ms,
    cost_estimate_usd=Decimal("4.20"),
    output_pointer=output_pointer,
    records_written=12345,
    model_ids_snapshot=sorted({MODEL_IDS_BY_STAGE["screening"], MODEL_IDS_BY_STAGE["scoring"]}),
    force_reason=force_reason if force else None,
)
```

---

## Writing a gate

A gate is a function that takes keyword arguments and returns a `GateResult`. Decorate it to register.

```python
# gates/coverage.py
from gates.registry import GateResult, SEVERITY_WARN, register_gate

@register_gate(stage="discover_subtopics", severity=SEVERITY_WARN)
def coverage_gate(*, per_topic_coverage: dict, **_) -> GateResult:
    """Coverage MUST be ≥ 85% per topic (D-01)."""
    failing = {tid: pct for tid, pct in per_topic_coverage.items() if pct < 0.85}
    if failing:
        return GateResult(
            name="coverage",
            passed=False,
            severity=SEVERITY_WARN,
            summary=f"{len(failing)} topic(s) below 85% coverage target",
            details={"failing_topics": failing},
        )
    return GateResult(
        name="coverage",
        passed=True,
        severity=SEVERITY_WARN,
        summary="all topics meet 85% coverage target",
    )
```

**Conventions**:

- Use `**_` in the signature to swallow unknown kwargs. The runner passes everything verbatim.
- Always return a `GateResult` — never raise. Defensive `try/except` around any operation that could throw (file I/O, JSON parsing, network).
- For `block` severity: failure halts the integrating stage.
- For `warn` severity: failure surfaces but does NOT halt. Use for post-action checks (e.g. `schema_roundtrip` after S3 upload) and quality signals that operators should see but shouldn't block on.
- Keep `details` payload bounded — gate results land in `failure_details` of `STAGE#` records, which have DynamoDB's 400KB item limit. Truncate large lists; report counts separately. See `gates/parent_prefix.py` for the truncation pattern.

**Cap `details` at 50 entries** unless you have a specific reason to go larger.

### Severities

| Severity | Halts stage? | When to use |
|---|---|---|
| `block` | Yes (unless --force) | Correctness invariants: schema validity, PII absence, contract violations. |
| `warn` | No | Post-action sanity checks (S3 round-trip), advisory quality signals (coverage, rejection rate). |

A `warn` failure still appears in `gate_summaries` on the `STAGE#` row, so dashboards can surface them — they're just not exit-blocking.

---

## Running gates ad-hoc

The `python -m gates` CLI runs registered gates against an already-published artifact (or any input you can construct). Useful for:

- Verifying a publish would pass without running the whole pipeline.
- Re-checking an older versioned prefix after registering a new gate.
- Debugging false-positive risk in a new gate against historical data.

Examples:

```bash
# Run all publish-stage gates against the local v2026-05-12 artifact.
python -m gates --stage publish --hierarchy v2026-05-12

# Same, but pull the artifact from S3 instead of out/.
python -m gates --stage publish --hierarchy v2026-05-12 --from-s3

# Run the post-upload round-trip check on a published version.
python -m gates --stage publish_post --version v2026-05-12

# List every registered gate.
python -m gates --list

# Override a block failure (with required audit note).
python -m gates --stage publish --hierarchy v2026-05-12 \
    --force --force-reason "deliberate test against injected violation"
```

Exit codes: `0` all-pass-or-force, `2` argument error, `3` block-severity failure, `4` `--force` without `--force-reason`.

The CLI does **not** write `STAGE#` records. Use it for inspection; integrating stages handle their own persistence.

---

## Test isolation

Gates register at import time via `@register_gate`. If a test imports a gate module and then a later test in the same session expects an empty registry, the prior registration leaks.

The pattern is in `tests/test_gates_registry.py`:

```python
@pytest.fixture(autouse=True)
def _isolate_registry():
    snap = registry._snapshot_registry_for_tests()
    registry._reset_registry_for_tests()
    yield
    registry._restore_registry_for_tests(snap)
```

`_reset_registry_for_tests`, `_snapshot_registry_for_tests`, `_restore_registry_for_tests` are intentionally underscored — they're test-only and production code MUST NOT call them.

Most test files do NOT need this fixture. Use it only when your test asserts on registry state directly (e.g. "registering my gate adds exactly one entry"). For test files that exercise gates as black boxes (call the gate function directly, assert on its return value), the global registry state doesn't matter.

---

## What this substrate doesn't do (yet)

- **No GSI on `input_hash`.** Phase 9 queries by PK and filters in Python. Fine for the publish stage's single PK with a handful of rows. Phase 10 should add a GSI when more stages integrate and per-PK row counts grow.
- **No drift evaluator.** Spec §2's `DRIFT#` records and Slack/GitHub alerting are Phase 10. Today's substrate writes `STAGE#` rows but nothing periodically inspects them.
- **No diff.json.** Spec §6's structured change signaling is Phase 11. Today's consumers still rely on `manifest.sha256` as the only signal.
- **No cross-stage cost telemetry dashboard.** The `cost_estimate_usd` field is populated on every row; aggregating it is a Phase 12 hygiene item.

If your new stage needs any of these, raise it as a phase-scoping question — don't fork the substrate.
