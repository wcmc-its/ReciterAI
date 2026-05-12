# Phase 12: Feedback Loops, Both Aggregations, Residual Hygiene — Research

**Researched:** 2026-05-12
**Domain:** ReciterAI cold-path substrate extension (DDB event records + Sonnet sweep consumer + dual-aggregation arithmetic + thresholds-config hygiene)
**Confidence:** HIGH for code shapes; HIGH for codebase verification; MEDIUM for tunable defaults (no prior production telemetry visible in summaries).

## Summary

CONTEXT.md is dense with 29 locked decisions. The codebase verification turns up **three findings that change planning, but do not invalidate the decisions** — they refine them. (1) `SUBTOPIC_SCORE#{topic}#{subtopic}` partitions described in D-12 as "existing" **do not exist today**; the current aggregator writes into a `subtopic_scores.<topic_id>` map on `FACULTY#<pid>` records. D-12's intent (parallel partitions for the two aggregations) is achievable, but it's new work, not "add an inclusive sibling to the existing exclusive partition." (2) `pipeline_drift/evaluator.py` persists only `low_confidence_max_topic` (single string) + `low_confidence_max_count` (single int) per daily row — **NOT a per-topic count dict**, so D-07's "consumer reads prior N days of DRIFT# rows and applies persistence threshold per topic" needs either a drift-evaluator schema extension OR a different consumer strategy (re-scan raw `LOW_CONFIDENCE_ASSIGNMENT#` rows directly). (3) The critic verdict shape today exposes two stable closed-vocabulary enums (`DeterministicVerdict.failed_constraints` and `LLMVerdict.failed_constraint`) that already function as reason codes; D-10's proposed `LEDE_INCOHERENT / WEAK_EVIDENCE / OFF_TOPIC_PUBS / INSUFFICIENT_NARRATIVE` enum does not map 1:1 to any field in the code, so the planner must decide between adopting the existing enums verbatim or layering a coarse mapping on top.

Everything else CONTEXT names — `gates/registry.py` pattern, `utils/event_records.py` builder pattern, `utils/stage_records.py` extension shape, `review/cli.py` subcommand model, `config/thresholds.json`, `jsonschema>=4.23.0` in `requirements.txt`, G-29's `generated_at` removal already shipped, the absence of an IAM section in `GETTING_STARTED.md` — checks out exactly as CONTEXT describes.

**Primary recommendation:** Before writing the plan, the planner must (a) decide the partition-vs-faculty-map shape question for §8 (proposal: ship D-12's two new partitions and keep the existing faculty-map write as the SPS contract — they serve different reader audiences), (b) decide D-07's input source (proposal: extend the `DRIFT#evaluation` row with a `per_topic_low_confidence: {topic_id: count}` dict on the same backwards-compatible additive pattern Phase 11 used for `run_id`), and (c) commit to `reason_code` source (proposal: use `failed_constraint` from the existing LLM verdict directly — the four codes are already documented, stable, and stored on the existing SPOTLIGHT_REVIEW# row).

## User Constraints (from CONTEXT.md)

### Locked Decisions

D-01: Hybrid trigger. Two entry points, one code path: operator CLI `python -m feedback sweep --since DAYS` and a non-gating stage inside `pipeline_cold.run.main()`. Both write findings with the same shape; the row carries `triggered_by ∈ {operator, cold_run}` and a `source_sweep_run_id` (UUID generated at sweep start). Diagnosis (operator-CLI) vs documentation (cold-path-stage) are different events; neither blocks the other.

D-02: Cold-path invocation is non-gating. A sweep finding "12 candidate new topics" during a cold run does NOT block that cold run from completing its already-approved taxonomy regeneration. The findings flow into the *next* cold run's taxonomy regen input pile.

D-03: DDB-primary output, deterministic markdown render. Source of truth lives in typed DDB records. `python -m feedback render <run_id>` emits a deterministic markdown view from the rows — same row in, same bytes out, no `generated_at` inside the markdown body.

D-04: Three typed finding records, no unified discriminator:
- `CANDIDATE_TOPIC#{slug}` — from UNCOVERED_PMID# pool; carries proposed_label, source_pmids[], Sonnet rationale, source_sweep_run_id.
- `RECLUSTER_RECOMMENDATION#{topic_id}` — from persistent LOW_CONFIDENCE_ASSIGNMENT# counts under a single topic; carries topic_id, evaluation_history, source_sweep_run_id.
- `SPOTLIGHT_DIAGNOSTIC#{cwid}` — from CRITIC_REJECT# counts per CWID over a window; carries cwid, reason_code distribution, source_sweep_run_id.

D-05: Idempotent overwrite per `source_sweep_run_id`, no cross-sweep merging.

D-06: Since-last-sweep input with cap. Sweep reads every UNCOVERED_PMID# row since prior sweep's `started_at`, capped at `feedback_sweep_max_pmids`. When cap hit, output carries `truncated: true` + `total_unprocessed_remaining: N`. Worst-fitting PMIDs go first (sort by `top_topic_score` ascending).

D-07: Recluster trigger is duration, not evaluation count. `RECLUSTER_RECOMMENDATION#{topic_id}` written when LOW_CONFIDENCE_ASSIGNMENT# counts under a single topic exceed `drift_low_confidence_topic_max` for `recluster_persistence_days` consecutive days. Day-window phrasing decouples from evaluator cadence.

D-08: CRITIC_REJECT# producer is additive to existing SPOTLIGHT_REVIEW# write. Two writes at the same code site, two purposes, both idempotent.

D-09: `pmid_set_hash` keying gives natural producer-side dedup. SPOTLIGHT_DIAGNOSTIC# count semantics are "distinct rejected pmid_sets over the window," not "total rejection events." That distinction documented at producer site and in record docstring.

D-10: `reason_code` is a controlled vocabulary. Proposed starting set: `LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`. Planner refines after reading current critic verdict shape. SPOTLIGHT_DIAGNOSTIC# carries reason-code distribution, not just a count.

D-11: Two tunables for diagnostic — `critic_reject_persistence_days` + `critic_reject_cwid_max`, both in `config/thresholds.json`.

D-12: Parallel DDB partitions + parallel CSV files. New partition `SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic}` alongside existing `SUBTOPIC_SCORE#{topic}#{subtopic}`. New CSV `faculty_subtopic_counts_inclusive.csv` alongside renamed `cwid_subtopic_counts.csv` → `faculty_subtopic_counts_exclusive.csv`.

D-13: CSV rename is contract change requiring SPS-consumer audit.

D-14: Two-partition writes idempotent at partition level, not transactional. Re-run against same `run_id` cleanly overwrites either partition. TransactWriteItems rejected (size limits + complexity).

D-15: Inclusive weighting is uniform full `article_score` per above-floor assignment. Confidence-weighted secondaries rejected. Per-pub 1/N normalization rejected.

D-16: Confidence floor still gates `subtopic_ids[]` membership. Inclusive aggregation is "uniform full weight for every *above-floor* assignment," not "every conceivable assignment."

D-17: Arithmetic invariant documented at the writes:
- `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) ≥ sum(SUBTOPIC_SCORE#X#*)` — equality iff every paper in topic X has exactly one above-floor subtopic assignment.
- `sum(SUBTOPIC_SCORE_INCLUSIVE#X#*) − sum(SUBTOPIC_SCORE#X#*)` = total article_score contributed by secondary assignments.
Also documented in `docs/topic-subtopic-assignment.md`.

D-18: Reconciliation gate fails the publish stage, not aggregation. Registered in `gates/registry.py`.

D-19: Five §11 items inside main phase work (G-1, G-18, G-24, G-34, G-36). G-37 (E2E test) as separately-gated final task.

D-20: G-37 gating is SHA-able: starts only after pytest passes on every other Phase 12 deliverable in CI on main.

D-21: G-37 is one bounded E2E test. "Stripped-down fixture corpus through cold path from `generate_taxonomy.py` to `pipeline_hierarchy/publish.py`; assert published manifest validates against schema; assert byte-identical `hierarchy.json` on second run."

D-22: G-37 carry-over case filed as GitHub issue on day one.

D-23: G-18 lifts every operationally-tunable number into `config/thresholds.json` — single file. Includes `tie_epsilon` (from `assign_subtopics.py:98`, 0.001), `confidence_floor` (from `assign_subtopics.py:1022` DEFAULT_CONFIDENCE_FLOOR), plus every new Phase 12 tunable. CLI flags still override per-invocation.

D-24: `confidence_floor` key has no `_default` suffix.

D-25: `confidence_floor` (G-18) ≠ `low_confidence_floor` (existing in thresholds.json). Both 0.35 today but represent different decisions; preserve both keys with distinct names.

D-26: Every key in `thresholds.json` documented in sibling `config/thresholds.md`. Markdown lists every key with semantics, default, and a one-sentence "if you raise this…" note.

D-27: `thresholds.json` is schema-validated at startup via `env_check.py`. Add `config/thresholds.schema.json`.

D-28: CLI tunable overrides logged at stage startup and written into STAGE# records.

D-29: Plan reflects parallel-fan dependency, not linear sequence. Four mechanical/writing items parallel; G-36 and G-37 are sequenced long-poles.

### Claude's Discretion

- Exact `reason_code` enum values in `spotlight/critic.py` (D-10). Contract is closed enum; planner reads current critic verdict shape and picks discriminators.
- Module layout for the feedback sweep (`feedback/`, `pipeline_feedback/`, or folded into `pipeline_cold/`). Planner picks based on Phase 10/11 conventions.
- Whether `env_check.py` schema-validation (D-27) uses jsonschema or hand-rolled. Planner picks based on existing patterns.
- Initial defaults for `feedback_sweep_max_pmids`, `recluster_persistence_days`, `critic_reject_persistence_days`, `critic_reject_cwid_max`.

### Deferred Ideas (OUT OF SCOPE)

- GitHub-issue dispatcher reading from finding records.
- Longitudinal candidate-topic tracking across sweeps.
- Confidence-weighted secondary aggregation (`SUBTOPIC_SCORE_WEIGHTED#`).
- Per-topic `confidence_floor` override.
- Drift evaluator extension for CRITIC_REJECT counts.
- Per-topic `REVIEW#` granularity.
- Separate `algorithm.json` config file.
- Auto-promotion of `CANDIDATE_TOPIC#` to taxonomy.
- A test that asserts `thresholds.md` documents every key in `thresholds.schema.json` (nice-to-have).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| §8 | Both aggregations side-by-side | Existing `aggregate_subtopic_scores.py:_aggregate` reads `primary_subtopic_id` only (verified line 128); inclusive needs new function reading `subtopic_ids[]` from activity rows. **Note: the `SUBTOPIC_SCORE#{topic}#{subtopic}` DDB partition in D-12 does not currently exist** — see Finding F-1. |
| §9 producer | `CRITIC_REJECT#{cwid}#{pmid_set_hash}` | Existing critic write site at `spotlight/critic.py:571` writes SPOTLIGHT_REVIEW# (verified); D-08 adds CRITIC_REJECT# alongside. `LLMVerdict.failed_constraint` provides the existing closed-vocabulary enum (Finding F-3). |
| §9 consumer | Feedback-sweep producing three finding-record types | `pipeline_drift/evaluator.py` exists and counts in-window UNCOVERED_PMID# and LOW_CONFIDENCE_ASSIGNMENT# rows; persists only single max-topic+count per day, not per-topic dict (Finding F-2). |
| G-1 | ReciterDB column refs in `utils/env_check.py` | Existing `env_check.py` already does the runtime check (verified columns `external_id`, `synopsis`, `nameFirst`, etc.). G-1 means moving the *hardcoded list* of expected columns into a central data structure so the check is data-driven and schema renames at upstream don't surprise the pipeline mid-run. |
| G-18 | Magic numbers → `config/thresholds.json` | `TIE_EPSILON = 0.001` at `assign_subtopics.py:98`; `DEFAULT_CONFIDENCE_FLOOR = 0.3` at `assign_subtopics.py:95` (**note: actual value is 0.3, not 0.35 as CONTEXT D-23 states** — see Finding F-4). |
| G-24 | Document sensitive-topic exclusion list | Sensitive patterns live in DynamoDB (`SPOTLIGHT_CONFIG#sensitive_tags`), NOT in source; gate is `spotlight/sensitive_gate.py`. G-24 documents the rationale, the DDB-resident pattern, and the SPOT-08 fail-closed invariant. |
| G-34 | IAM policy reference in `GETTING_STARTED.md` | `docs/aws-iam-pipeline-policy.json` and `docs/aws-iam-pipeline-policy-artifacts.json` both exist; `GETTING_STARTED.md` has no IAM section today (verified). |
| G-36 | Reproducibility test for `pipeline_hierarchy/` | `tests/test_hierarchy_reproducibility.py` ALREADY EXISTS with four tests covering bundle/build/generate byte-stability (verified). G-36 either hardens the existing tests or extends scope. |
| G-37 | One bounded E2E test | No existing E2E test for the full cold path; existing fixtures in `tests/fixtures/` are JSON-schema-validation fixtures only, not corpus fixtures. New fixture corpus required. |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| CRITIC_REJECT# emission | Cold path (spotlight regen) | — | Lives at the critic-loop exit site (`spotlight/critic.py:571`); fired during spotlight runs which are cold-path stages today. |
| Feedback sweep (Sonnet uncovered-PMID pass) | Cold path stage + Operator CLI | — | Hybrid trigger per D-01; both code paths share the same module. Operator CLI is a diagnosis tool; cold-path stage runs as a non-gating phase within `pipeline_cold.run.main()`. |
| Inclusive subtopic aggregation | Cold path (aggregator stage) | — | Joins exclusive aggregation as a sibling operation in `aggregate_subtopic_scores.py`. Both produce DDB rows + CSV files; downstream consumed by SPS over DDB and by operators over CSV. |
| Reconciliation gate | Gate registered against `publish` stage | — | Per Phase 9 pattern in `gates/registry.py`; gate fails the publish stage, not aggregation (D-18). |
| `thresholds.json` schema-validation | Pipeline preflight (`utils/env_check.py`) | — | Fail-early at startup; same pattern as Phase 9 `schema_validation` gate but at process bootstrap. |
| STAGE# tunable-audit field | Substrate (`utils/stage_records.py`) | — | Additive backwards-compatible field; same pattern as Phase 11 D-13 `run_id`. |
| CRITIC_REJECT# / finding-record DDB rows | DynamoDB single-table | — | Same `reciterai-chatbot` table; PK-prefixed record types. |
| Feedback markdown rendering | Operator CLI | — | `python -m feedback render <run_id>` outputs deterministic markdown; same pattern as `python -m review` (Phase 11). |

## Standard Stack

### Core (already in repo, reused by Phase 12)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| boto3 | >=1.42.0 | DynamoDB + S3 client [VERIFIED: requirements.txt] | Existing standard across all phases. |
| jsonschema | >=4.23.0 | JSON schema validation [VERIFIED: requirements.txt] | Already used for hierarchy.schema.json round-trip gate; planner should use this for D-27 thresholds-schema validation. |
| pyyaml | >=6.0.1 | YAML parsing [VERIFIED: requirements.txt] | Used by `review/cli.py`; relevant if any feedback CLI subcommand needs YAML input. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `argparse` | stdlib | CLI subcommand routing | Mirror `review/cli.py` pattern for `python -m feedback`. |
| `dataclasses` | stdlib | Record builders | `event_records.py` and `stage_records.py` use plain dicts via pure builder functions; Phase 12 follows that pattern, NOT dataclass builders. |
| `decimal.Decimal` | stdlib | DDB numeric coercion | All DDB float values must be Decimal. Existing helper pattern: `Decimal(str(float_val))`. |
| `uuid.uuid4()` | stdlib | `source_sweep_run_id` minting | Same pattern as `pipeline_cold/run.py:339` generates `run_id`. |

### Alternatives Considered (rejected by CONTEXT or by code state)

| Instead of | Could Use | Why Rejected |
|------------|-----------|--------------|
| Per-record DDB partition for both aggregations | Extended fields on one record | D-12 rejected; reproduces G-19 |
| Hand-rolled JSON schema check | Use existing `jsonschema` | Already in dep tree; planner should use it for D-27 |
| TransactWriteItems for two-partition consistency | Per-partition idempotent rewrite | D-14 rejected (size limits) |

**Installation:** No new deps required. Phase 12 reuses the existing dep set.

## Architecture Patterns

### System Architecture Diagram

```
                       Operator CLI                    Cold Path
                  ┌──────────────────────┐    ┌────────────────────────────┐
                  │ python -m feedback   │    │ pipeline_cold.run.main()   │
                  │   sweep --since N    │    │   (existing 7-stage walker)│
                  │   render <run_id>    │    │                            │
                  └──────────┬───────────┘    │                            │
                             │                │  stages: score → assign →  │
                             │                │  discover → relabel →      │
                             │                │  rollup → backfill_spotlight│
                             │                │  → publish_hierarchy       │
                             │                │  + (NEW) feedback_sweep    │
                             │                │    [non-gating, between    │
                             │                │     rollup and publish]    │
                             ▼                └──────────────┬─────────────┘
                  ┌──────────────────────┐                   │
                  │ feedback.sweep.run() │ ◄─────────────────┘
                  │  (shared entry point)│
                  └──────────┬───────────┘
                             │
              reads ◄────────┼────────► writes
                             │
   ┌──────────────────────┐  │  ┌──────────────────────────────────┐
   │ UNCOVERED_PMID# rows │  │  │ CANDIDATE_TOPIC#{slug}           │
   │ (Phase 10 producer)  │──┤  │   from Sonnet sweep over         │
   │                      │  │  │   uncovered PMIDs                │
   └──────────────────────┘  │  ├──────────────────────────────────┤
                             │  │ RECLUSTER_RECOMMENDATION#{topic} │
   ┌──────────────────────┐  │  │   from per-topic LOW_CONFIDENCE  │
   │ LOW_CONFIDENCE_      │──┤  │   persistence over N days        │
   │   ASSIGNMENT# rows   │  │  ├──────────────────────────────────┤
   │ (Phase 10 producer)  │  │  │ SPOTLIGHT_DIAGNOSTIC#{cwid}      │
   └──────────────────────┘  │  │   from CRITIC_REJECT# counts     │
                             │  │   per cwid over N days           │
   ┌──────────────────────┐  │  └──────────────────────────────────┘
   │ CRITIC_REJECT#       │──┘
   │ (NEW, Phase 12)      │
   │ produced by          │
   │ spotlight/critic.py  │
   └──────────────────────┘
   (additive write next to existing SPOTLIGHT_REVIEW#)


                        Aggregation pipeline (§8)
   ┌──────────────────────────────────────────────────────────────────┐
   │ aggregate_subtopic_scores.py                                     │
   │   _aggregate_exclusive(rows) → primary-subtopic-only weights     │
   │   _aggregate_inclusive(rows) → above-floor subtopic_ids[] weights│
   │                                                                  │
   │   writes:                                                        │
   │     1. faculty.subtopic_scores.<topic_id> map (existing path,    │
   │        SPS consumer contract — must be preserved)                │
   │     2. SUBTOPIC_SCORE#{topic}#{subtopic} partition (NEW)         │
   │     3. SUBTOPIC_SCORE_INCLUSIVE#{topic}#{subtopic} partition (NEW)│
   │     4. faculty_subtopic_counts_exclusive.csv (NEW, rename)       │
   │     5. faculty_subtopic_counts_inclusive.csv (NEW)               │
   └──────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
                      ┌──────────────────────────┐
                      │ reconciliation gate      │
                      │  registered against      │
                      │  publish stage           │
                      │  (gates/registry.py)     │
                      └──────────┬───────────────┘
                                 │ on fail: block publish stage,
                                 │ leave aggregation DDB rows readable
                                 ▼
                      ┌──────────────────────────┐
                      │ publish stage            │
                      │  (existing                │
                      │   pipeline_hierarchy/    │
                      │   publish.py)            │
                      └──────────────────────────┘
```

### Recommended Project Structure

```
feedback/                                    # NEW module (recommendation: see Module Layout below)
├── __init__.py
├── __main__.py                              # dispatch to feedback.cli.main
├── cli.py                                   # subcommand parser + dispatch
├── sweep.py                                 # core sweep logic (shared by CLI + cold stage)
├── findings.py                              # three typed record builders
├── render.py                                # deterministic markdown renderer
└── prompts/
    └── uncovered_pmid_sonnet_v0.md          # the Sonnet prompt for candidate-topic discovery

config/
├── thresholds.json                          # EXTEND
├── thresholds.md                            # NEW (sibling doc per D-26)
└── thresholds.schema.json                   # NEW (per D-27)

utils/
├── event_records.py                         # ADD build_critic_reject_record + write_critic_reject
├── stage_records.py                         # ADD optional tunable_inputs field on builders (D-28)
└── env_check.py                             # ADD thresholds.schema.json validation + data-driven column check (G-1)

gates/
└── reconciliation.py                        # NEW gate, registered against publish stage (D-18)

spotlight/
└── critic.py                                # ADD CRITIC_REJECT# write at line ~571 alongside existing SPOTLIGHT_REVIEW# write (D-08)

aggregate_subtopic_scores.py                 # ADD _aggregate_inclusive function + parallel partition writers + CSV emitter

docs/
├── topic-subtopic-assignment.md             # ADD D-17 invariant one-liner
└── sensitive-topic-exclusion.md             # NEW for G-24 (or inline section in spotlight-contract.md)

GETTING_STARTED.md                           # ADD IAM Policy section for G-34
```

### Pattern 1: Builder + Thin Writer (existing — `utils/event_records.py`)

**What:** Pure builder function returns a dict; thin writer composes builder + `table.put_item`. Idempotent PK + constant `SK = "GLOBAL"`. Decimal coercion for floats. `created_at` ISO timestamp. `source_stage` field.

**When to use:** Every new DDB event record in Phase 12 (`CRITIC_REJECT#`, `CANDIDATE_TOPIC#`, `RECLUSTER_RECOMMENDATION#`, `SPOTLIGHT_DIAGNOSTIC#`).

**Example (verbatim template from existing code):**
```python
# Source: utils/event_records.py lines 61-98
def build_uncovered_pmid_record(
    *,
    pmid: str,
    taxonomy_version: str,
    top_topics: list[tuple[str, float]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for an UNCOVERED_PMID# row.

    `top_topics` is the top-3 (topic_id, score) tuples ordered from
    highest to lowest score. Float scores are converted to Decimal to
    satisfy DynamoDB's numeric type rule.
    """
    top_sorted = sorted(top_topics, key=lambda t: (-float(t[1]), t[0]))[:3]
    top_topic_score = (
        Decimal(str(top_sorted[0][1])) if top_sorted else Decimal("0")
    )
    return {
        "PK": f"UNCOVERED_PMID#{pmid}",
        "SK": "GLOBAL",
        "record_type": "UNCOVERED_PMID",
        "pmid": str(pmid),
        "taxonomy_version": taxonomy_version,
        "top_topic_score": top_topic_score,
        "top_topics": [
            {"topic_id": tid, "score": Decimal(str(score))}
            for tid, score in top_sorted
        ],
        "created_at": created_at or _now_iso(),
        "source_stage": "score_publications",
    }


def write_uncovered_pmid(table: Any, **kwargs: Any) -> dict[str, Any]:
    """Build an UNCOVERED_PMID# row and persist via table.put_item."""
    item = build_uncovered_pmid_record(**kwargs)
    table.put_item(Item=item)
    return item
```

### Pattern 2: Gate Registration (existing — `gates/registry.py`)

**What:** `@register_gate(stage=..., severity=...)` decorator pushes a pure-function gate onto `_REGISTRY`. Integrating stage calls `run_gates(stage="publish", **kwargs)` and `any_blocked(results)`.

**When to use:** D-18's reconciliation gate.

**Example:**
```python
# Source: gates/parent_prefix.py lines 38-94 (the live registered gate)
@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="parent_prefix")
def parent_prefix_gate(
    *,
    hierarchy: dict[str, Any],
    topic_labels: dict[str, str] | None = None,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    **_: object,
) -> GateResult:
    """Reject hierarchy bundles where any subtopic `display_name` starts
    with a parent-topic word."""
    # ... checks ...
    if violations:
        return GateResult(
            name="parent_prefix",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=f"{len(violations)} subtopic display_name(s) start with a parent-topic word",
            details={"violation_count": len(violations), "violations": violations[:50]},
        )
    return GateResult(
        name="parent_prefix",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="no parent-prefix violations across the bundle",
    )
```

The Phase 12 reconciliation gate registers against `stage="publish"`, severity `SEVERITY_BLOCK`. It receives the aggregation outputs (or queries them from DDB) and asserts D-17's invariant.

### Pattern 3: CLI Subcommand Dispatch (existing — `review/cli.py`)

**What:** `argparse.ArgumentParser` with `add_subparsers(dest="command", required=True)`. `main()` parses and dispatches.

**When to use:** `python -m feedback` CLI with `sweep` and `render` subcommands.

**Example (verbatim template from existing code):**
```python
# Source: review/cli.py lines 233-278
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review",
        description="ReciterAI artifact review CLI — approve or validate hierarchy artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_approve = sub.add_parser(
        "approve",
        help="Open $EDITOR on a pre-populated YAML, validate, and write REVIEW# to DDB.",
    )
    p_approve.add_argument("--artifact", required=True, choices=["hierarchy"])
    p_approve.add_argument("--version", required=True)

    p_validate = sub.add_parser(
        "validate",
        help="Validate a draft review YAML without writing to DDB.",
    )
    p_validate.add_argument("path")

    args = parser.parse_args(argv)
    if args.command == "approve":
        return _run_approve(args)
    return _run_validate(args)
```

And `review/__main__.py`:
```python
# Source: review/__main__.py
import sys
from review.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

### Pattern 4: Cold-Stage Subprocess Registration (existing — `pipeline_cold/run.py`)

**What:** Each cold stage is a `ColdStage(name, command, description)` invoked via `subprocess.run`. The orchestrator threads `RECITERAI_COLD_RUN_ID` and `RECITERAI_HIERARCHY_VERSION` env vars into each subprocess.

**When to use:** Registering the new feedback-sweep stage in `default_cold_stages()`.

**Example:**
```python
# Source: pipeline_cold/run.py lines 90-131
def default_cold_stages() -> list[ColdStage]:
    """Canonical cold-stage list per CONTEXT stage-assignment table."""
    return [
        ColdStage(name="score", command=[sys.executable, "-m", "score_publications"], ...),
        ColdStage(name="assign", command=[sys.executable, "backfill_all.py", "--skip-pm-copy"], ...),
        # ... other stages ...
        ColdStage(name="rollup", command=[sys.executable, "-m", "rollup_by_cwid"], ...),
        ColdStage(name="backfill_spotlight", ...),
        ColdStage(name="publish_hierarchy", command=[sys.executable, "-m", "pipeline_hierarchy.publish"], ...),
    ]
```

Phase 12 inserts a `feedback_sweep` stage. **Recommended position: between `rollup` and `backfill_spotlight`** — by then assignment+rollup have written their UNCOVERED/LOW_CONFIDENCE rows, and backfill_spotlight may itself produce CRITIC_REJECT# rows that we explicitly don't want this sweep to consume (those flow into the *next* sweep, per D-05/D-06 since-last-sweep semantics).

**Crucial:** D-02 says the sweep is non-gating. The current `run_stage` exits with `outcome.status = "failed"` on non-zero subprocess return code and the orchestrator then aborts. The feedback-sweep stage's subprocess must NEVER return non-zero on findings — it returns 0 for "sweep ran and wrote rows" regardless of whether findings were emitted. Operational failures (DDB error, Bedrock 4xx) DO return non-zero, but findings do not.

### Pattern 5: STAGE# Builder Backwards-Compatible Extension (existing — Phase 11 D-13 `run_id`)

**What:** Add optional kwargs to `build_complete_record`/`build_skipped_record`/`build_failed_record`; only include in dict when value is non-None.

**When to use:** D-28's tunable-audit fields.

**Example:**
```python
# Source: utils/stage_records.py lines 217-219 (the Phase 11 D-13 extension)
if run_id is not None:
    item["run_id"] = run_id
return item
```

Phase 12 adds e.g. `tunable_inputs: dict[str, Any] | None = None`. Set at the stage's startup with `{"confidence_floor": 0.35, "confidence_floor_source": "config|cli|default"}`.

### Anti-Patterns to Avoid

- **Hand-rolling thresholds-schema validation when `jsonschema>=4.23.0` is already in `requirements.txt`.** Use `jsonschema.validate(instance=cfg, schema=schema)` with a `jsonschema.Draft202012Validator` for actionable error messages.
- **Folding CRITIC_REJECT# severity into `pipeline_drift/evaluator.py`.** Explicitly rejected by D-04 rationale; the critic-reject signal is per-CWID, not corpus-scoped.
- **Computing the inclusive aggregation in TypeScript on the SPS side.** `aggregate_subtopic_scores.py` docstring already warns: "Never recompute articleScore in TypeScript (Pitfall P-10)." The inclusive aggregation must land in Python alongside the exclusive one.
- **Using `created_at` on finding records as the dedup key.** Per D-05, dedup is by `source_sweep_run_id`. `created_at` is metadata, not identity.
- **Returning non-zero from the feedback-sweep subprocess based on whether findings were emitted.** That converts findings into stage failures and violates D-02.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON schema validation | Hand-rolled type-checker | `jsonschema.Draft202012Validator` (already in deps) | Already in use for `hierarchy.schema.json`; consistent error format; battle-tested edge cases (additionalProperties, oneOf, const). |
| DDB idempotent overwrite | Conditional update with versioning | Existing PK-stable + constant-SK pattern from `event_records.py` | The pattern is proven across UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#, DRIFT#evaluation. Repeating it for finding records preserves operator mental model. |
| Decimal coercion of Sonnet response scores | Manual float conversion | `Decimal(str(float_val))` helper at the call site | Standard pattern across all event-record builders; ensures DDB-correct numeric type without precision drift. |
| Gate-failure-blocks-stage logic | Custom orchestration | `gates.registry.run_gates(stage="publish") + any_blocked()` | Phase 9 substrate already implements this with severity routing and `STAGE#` row integration via `failure_details`. |
| Subcommand dispatch | Custom flag-based mode switch | `argparse.add_subparsers(required=True)` exactly like `review/cli.py` | Operator muscle memory from Phase 11; both have `sweep`/`render` vs `approve`/`validate` symmetry. |
| Markdown rendering with timestamps | Embed `generated_at` in body | Timestamp in filename, deterministic body | D-03 + Phase 11 G-29 lesson; supports byte-identical re-render assertion. |
| Cold-stage failure tracking | Per-stage `try/except` in orchestrator | Existing `run_stage → StageOutcome → write_failed` pattern | Already in `pipeline_cold/run.py`. The new feedback-sweep stage is just another `ColdStage`. |

**Key insight:** Phase 12 is heavy on substrate *re-use*. Every new piece — finding records, gate, CLI, stage registration, tunable audit — has a working template in Phases 9–11. The risk is inventing parallel patterns; the discipline is to follow the existing ones verbatim.

## Runtime State Inventory

This phase is additive (new records, new CSVs, new gate, new CLI) and one rename (CSV file). Runtime-state risk is concentrated in the rename.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | DDB rows for the existing record types written by Phase 10 (UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#, DRIFT#evaluation) — Phase 12 reads these, does not migrate them. Existing `cwid_subtopic_counts.csv` artifact is a build-output, not stored data. | None for existing data. New writes (CRITIC_REJECT#, three finding-record types, two SUBTOPIC_SCORE# partitions) follow idempotent overwrite — D-14 guarantees clean re-run. |
| Live service config | EventBridge cron rules from Phase 10 (`infra/eventbridge.json` — drift-daily) are not changed by Phase 12. SPS does not have a Phase 12-specific config. | None. The drift evaluator runs unchanged. |
| OS-registered state | None. No Windows/launchd/systemd state. | None. |
| Secrets/env vars | `RECITERAI_COLD_RUN_ID` (existing, set by `pipeline_cold/run.py`), `RECITERAI_HIERARCHY_VERSION` (existing). Phase 12 may need to expose `RECITERAI_FEEDBACK_SWEEP_RUN_ID` to subprocess steps but the recommendation is to keep the sweep run_id internal to the sweep stage. | None for existing. Decide internal-vs-env for `source_sweep_run_id` (recommendation: internal). |
| Build artifacts / installed packages | `cwid_subtopic_counts.csv` exists at repo root as a checked-in artifact (`-rw-r--r--`, 59KB). Verified by `ls`. Renaming this file (D-13) drops the old name. | (1) Audit consumers of `cwid_subtopic_counts.csv` before renaming — three readers found: `rollup_by_cwid.py` (line 61), `build_cwid_json.py` (line 12), and `tests/test_rollup_incremental_parity.py` (line 68). The first two write the file's contents into `cwid_rollup.csv` / `cwid_topics_subtopics.json`. The third is a parity test. (2) Audit SPS — if SPS reads the CSV, dual-name emission for one cold-run cycle per D-13. (3) The renamed `count_by_cwid.py` (line 61) is the producer. |

**SPS-consumer audit conclusion:** Three in-repo readers found. **No evidence in this repo that SPS reads this CSV** — SPS-side reads would happen against DDB (`FACULTY#<pid>.subtopic_scores` map), not against a local CSV. Recommend a one-line check with the SPS team to confirm before committing the rename. If they don't read it, direct rename is cheap. If they do, dual-name emission for one cycle.

## Common Pitfalls

### Pitfall 1: Confusing the two confidence-floor keys (D-25)
**What goes wrong:** `confidence_floor` (gates `subtopic_ids[]` membership at assign-time) and `low_confidence_floor` (gates LOW_CONFIDENCE_ASSIGNMENT# event emission) both default to 0.35 in CONTEXT but live in different code paths. A future planner could collapse them in `thresholds.json` cleanup.
**Why it happens:** Same numerical value invites the simplification.
**How to avoid:** Both keys MUST appear in `thresholds.json` with distinct names. `thresholds.md` should explicitly note "yes both are 0.35 today; no they are not the same decision." A test asserting the two keys exist separately is cheap insurance.
**Warning signs:** A diff that touches one key but not the other while collapsing them in code or docs.

**Pitfall 1.5 (variant):** The actual code today has `DEFAULT_CONFIDENCE_FLOOR = 0.3` at `assign_subtopics.py:95`, NOT 0.35 as CONTEXT D-23 states. Phase 12 must reconcile — see Finding F-4.

### Pitfall 2: Re-running the sweep and producing duplicate-but-different rows
**What goes wrong:** A sweep crashes after writing 3 of 5 RECLUSTER_RECOMMENDATION# rows. The retry uses a new `source_sweep_run_id` and writes 5 fresh rows; the original 3 stick around as zombies.
**Why it happens:** D-05's "idempotent overwrite per `source_sweep_run_id`" relies on the PK being stable across retries of the SAME sweep run; a new sweep run_id is a new logical sweep.
**How to avoid:** Within a single sweep invocation, generate the `source_sweep_run_id` ONCE at sweep start and pass it to every finding writer. Crash-resume should reuse the same `source_sweep_run_id` (consider persisting the in-progress run_id to a watermark row, or accept that crashed sweeps require operator re-invocation with `--run-id <prior_uuid>`).
**Warning signs:** Two sweeps run within minutes of each other, each writing overlapping finding sets.

### Pitfall 3: Counting CRITIC_REJECT# rows as rejection events when they are rejected-pmid-sets
**What goes wrong:** `SPOTLIGHT_DIAGNOSTIC#{cwid}` reports "10 rejections" but the actual signal is "10 distinct rejected pmid_sets," which is much higher-confidence-of-problem than the same CWID's lede being retried 10 times against the same papers.
**Why it happens:** D-09's pmid_set_hash keying creates producer-side dedup; downstream consumers may not realize this.
**How to avoid:** SPOTLIGHT_DIAGNOSTIC#'s field name MUST be `distinct_rejected_pmid_sets_count`, not `rejection_count`. Operator-facing markdown MUST repeat the distinction. Producer-site comment in `spotlight/critic.py` MUST explain the semantics so future code-readers don't introduce a "total events" counter.
**Warning signs:** A consumer (dashboard, alert, prompt) that says "X rejections" without qualification.

### Pitfall 4: G-37 fixture corpus that is not actually small
**What goes wrong:** "Stripped-down fixture corpus" creeps into 50+ pubs because the cold path's stages each have minimum thresholds (e.g., spotlight_dirty_subtopic_min: 3, spotlight_dirty_pubs_per_subtopic_min: 5). The test takes 15+ minutes per pytest run and gets skipped in CI.
**Why it happens:** Each stage's threshold tuning is independent; satisfying all of them with a small corpus requires careful selection.
**How to avoid:** Plan the fixture-corpus shape BEFORE writing the test (per D-21 framing). Specifically pick: 2 topics × 3 subtopics × 5 pubs = 30 pubs as the minimum that lets every cold stage execute without short-circuiting on its threshold. Document the shape choice in the test file's docstring.
**Warning signs:** Test that's slower than 60s; fixture corpus that adds more pubs than it removes to "satisfy the next stage."

### Pitfall 5: D-07's recluster trigger reading from a row schema that does not carry per-topic counts
**What goes wrong:** D-07 says "consumer queries 'how many evaluation rows fall in that window' at read time." If the consumer reads `DRIFT#evaluation` rows, each carries only `low_confidence_max_topic` (one) and `low_confidence_max_count` (one). A topic that was the runner-up every day for 14 days produces zero matches under that schema, even though it materially has persistent low-confidence.
**Why it happens:** Phase 10's drift evaluator persists only the single max-topic per day; the per-topic dict is computed in memory and discarded.
**How to avoid:** Either (a) extend the `DriftEvaluation.to_dynamodb_item()` schema additively with a `per_topic_low_confidence: dict[topic_id, count]` field (backwards-compatible; same pattern as Phase 11 `run_id` extension), OR (b) have the feedback sweep re-scan `LOW_CONFIDENCE_ASSIGNMENT#` rows directly with a `topic_id`-grouped count over the persistence window. Recommendation: (a) — cheaper at read time, single source of truth, reuses substrate. Note this means Phase 12 DOES extend the drift evaluator in a small way despite CONTEXT saying "does not extend it" — the additive `per_topic_low_confidence` field is the minimum extension needed to make D-07 implementable.
**Warning signs:** A consumer doing per-topic counts by querying `DRIFT#evaluation` and getting empty results for topics that are not the daily max.

### Pitfall 6: Module name collision with `feedback` as a Python keyword-adjacent term
**What goes wrong:** `feedback` is not a Python keyword but it shadows readability when imports look like `from feedback import sweep`. Operators reading the code may confuse it with a generic English term.
**Why it happens:** Phase 10/11 chose `pipeline_*` prefixes for cold-path-adjacent modules (`pipeline_cold`, `pipeline_drift`, `pipeline_hot`, `pipeline_spotlight`) and `review/` for the operator-CLI module. The feedback sweep is in both worlds (cold stage + operator CLI).
**How to avoid:** Module-layout recommendation: `pipeline_feedback/` — it pattern-matches the `pipeline_drift/` convention (the drift evaluator is also operator-readable Python with daily cron entry) and avoids the naked-word problem. The CLI entry is `python -m pipeline_feedback sweep` / `python -m pipeline_feedback render`. (`review/` lacks the `pipeline_` prefix because it is purely operator-facing; the feedback sweep has a cold-path stage too, so `pipeline_*` is more honest.)
**Warning signs:** Discussions in PR review about whether the module name is too generic.

## Code Examples

### Adding the CRITIC_REJECT# builder (mirror of `build_uncovered_pmid_record`)

```python
# To add to utils/event_records.py
def build_critic_reject_record(
    *,
    cwid: str,
    pmid_set: list[str],
    reason_code: str,
    parent_topic: str,
    subtopic_id: str,
    publish_id: str,
    regen_count: int,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pure builder for a CRITIC_REJECT# row.

    pmid_set_hash is computed from the sorted-tuple of PMIDs; producer-
    side dedup means SPOTLIGHT_DIAGNOSTIC# count semantics are "distinct
    rejected pmid_sets over the window," not "total rejection events."
    See spec §9 and D-09.
    """
    pmid_sorted = sorted(str(p) for p in pmid_set)
    pmid_set_hash = hashlib.sha256(",".join(pmid_sorted).encode("utf-8")).hexdigest()[:16]
    return {
        "PK": f"CRITIC_REJECT#{cwid}#{pmid_set_hash}",
        "SK": "GLOBAL",
        "record_type": "CRITIC_REJECT",
        "cwid": str(cwid),
        "pmid_set": pmid_sorted,
        "pmid_set_hash": pmid_set_hash,
        "reason_code": str(reason_code),
        "parent_topic": str(parent_topic),
        "subtopic_id": str(subtopic_id),
        "publish_id": str(publish_id),
        "regen_count": int(regen_count),
        "created_at": created_at or _now_iso(),
        "source_stage": "spotlight.critic",
    }
```

### Adding the additive CRITIC_REJECT# write at `spotlight/critic.py:571`

```python
# At spotlight/critic.py just before or after the existing review_queue.write_review_entry call:
write_review_entry(dynamo_client, review_entry)  # EXISTING — UNCHANGED

# NEW additive write — feeds SPOTLIGHT_DIAGNOSTIC# consumer
if last_llm_verdict is not None:
    from utils.event_records import write_critic_reject
    # Extract cwid from publish_id (publish_id format: "publish_{cwid}_{ts}" — confirm convention)
    cwid = publish_id.split("_")[1] if "_" in publish_id else publish_id
    write_critic_reject(
        table,
        cwid=cwid,
        pmid_set=[p.pmid for p in last_papers],
        reason_code=last_llm_verdict.failed_constraint,  # one of: active_verb,
                                                         # anchored_in_synopses, no_faculty_named,
                                                         # institutional_voice (D-10: confirm enum)
        parent_topic=parent_topic,
        subtopic_id=meta.subtopic_id,
        publish_id=publish_id,
        regen_count=MAX_RETRIES,
    )
```

(`publish_id` → `cwid` extraction is a planner-decided detail; the publish_id format is set in `pipeline_spotlight/orchestrator.py`. Confirm before coding.)

### Adding the inclusive aggregation function

```python
# To add to aggregate_subtopic_scores.py alongside _aggregate (which becomes _aggregate_exclusive)
def _aggregate_inclusive(rows: list, confidence_floor: float) -> tuple[dict, dict]:
    """
    Aggregate article_score per (person_identifier, subtopic_id) for every
    above-floor subtopic in the row's subtopic_ids[].

    D-15: uniform full article_score per above-floor assignment.
    D-16: confidence_floor gates membership in subtopic_ids[] — papers below
          the floor for subtopic Y do not appear in the row's subtopic_ids[]
          and therefore do not contribute to inclusive aggregation under Y.

    Returns (faculty_scores_inclusive, subtopic_total_weights_inclusive).
    """
    faculty_scores: dict = defaultdict(lambda: defaultdict(float))
    subtopic_total_weights: dict = defaultdict(float)

    for row in rows:
        subtopic_ids = row.get("subtopic_ids") or []
        if not subtopic_ids:
            continue

        # The same row-validity checks as _aggregate_exclusive
        faculty_uid = row.get("faculty_uid") or ""
        if not faculty_uid:
            continue
        person_identifier = _strip_faculty_prefix(faculty_uid)
        if not person_identifier:
            continue
        try:
            relevance_score = float(row.get("score") or 0)
            impact_score = float(row.get("impact_score") or 0)
        except (TypeError, ValueError):
            continue

        article_score = (impact_score / 100) ** 1.2 * relevance_score ** 1.4

        # D-15: uniform full article_score per above-floor assignment.
        # Note: subtopic_ids[] was written by assign_subtopics.py:632 which
        # already filtered by confidence_floor (assign_subtopics.py:592). We
        # do NOT re-filter here — the inclusive aggregation trusts the writer.
        # The confidence_floor parameter is kept on the signature so a future
        # divergence shows up at the type level, not silently.
        for sid in subtopic_ids:
            faculty_scores[person_identifier][sid] += article_score
            subtopic_total_weights[sid] += article_score

    return (
        {pid: dict(m) for pid, m in faculty_scores.items()},
        dict(subtopic_total_weights),
    )
```

### Reconciliation gate (D-18)

```python
# gates/reconciliation.py (NEW)
from gates.registry import GateResult, SEVERITY_BLOCK, register_gate

@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="reconciliation")
def reconciliation_gate(
    *,
    exclusive_totals: dict[str, dict[str, float]],   # {topic_id: {subtopic_id: total}}
    inclusive_totals: dict[str, dict[str, float]],   # {topic_id: {subtopic_id: total}}
    **_: object,
) -> GateResult:
    """D-17 / D-18: assert the arithmetic invariant between the exclusive
    and inclusive aggregations.

    For every topic X:
      sum(inclusive[X]) >= sum(exclusive[X])     — inequality, not equality
      sum(inclusive[X]) >= sum(exclusive[X]) iff every paper in X has
        exactly one above-floor subtopic assignment.

    On violation, this gate blocks the publish stage (D-18). Aggregation
    is allowed to complete so operators can read the partitions to diagnose.
    """
    violations: list[dict] = []
    for topic_id, exclusive_subs in exclusive_totals.items():
        excl_sum = sum(exclusive_subs.values())
        inclusive_subs = inclusive_totals.get(topic_id, {})
        incl_sum = sum(inclusive_subs.values())
        if incl_sum < excl_sum - 1e-9:  # float tolerance
            violations.append({
                "topic_id": topic_id,
                "exclusive_sum": excl_sum,
                "inclusive_sum": incl_sum,
                "diff": excl_sum - incl_sum,
            })

    if violations:
        return GateResult(
            name="reconciliation",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=f"{len(violations)} topic(s) violate inclusive ≥ exclusive invariant",
            details={"violations": violations[:50]},
        )
    return GateResult(
        name="reconciliation",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="exclusive ≤ inclusive holds for all topics",
    )
```

The gate must be IMPORTED at the publish stage so the `@register_gate` decorator runs — either `gates/__init__.py` adds `from gates import reconciliation` or `pipeline_hierarchy/publish.py` does it at module import time.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hand-rolled JSON validation per script | jsonschema (4.23.0) for `hierarchy.schema.json` | Phase 9 (schema_validation gate) | D-27 can reuse the same library + pattern. |
| `hierarchy.json` carries `generated_at` | `generated_at` lives only in `manifest.json`; hierarchy is byte-stable | Phase 11 G-29 (verified: `pipeline_hierarchy/generator.py:74`) | G-36 reproducibility tests already shipped (`tests/test_hierarchy_reproducibility.py`). |
| Float values written to DDB | Decimal coercion at builder boundary | Phase 10 D-07 | Phase 12 builders must follow. |
| Per-stage cost field `cost_estimate_usd` | `cost_observed_usd` (skips emit Decimal('0')) | Phase 10 D-09 | Already in `stage_records.py`. |

**Deprecated/outdated:**
- The pattern of computing `articleScore` in TypeScript on SPS side is explicitly forbidden (Pitfall P-10 in `aggregate_subtopic_scores.py:7`). Phase 12 must not add a "hint" toward TS-side recomputation.
- "Inferring aggregation choice from CSV column name" (the failure mode G-19 addressed). Phase 12's two-file rename + DDB-partition split is exactly the fix; do not regress by adding mode flags.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `feedback_sweep_max_pmids = 200` is a sensible default | Tunable Defaults below | Sweep silently drops findings; mitigated by `truncated: true` field in output row. |
| A2 | `recluster_persistence_days = 7` is a sensible default | Tunable Defaults below | Too short → noisy recluster recommendations; too long → drift goes unnoticed. |
| A3 | `critic_reject_persistence_days = 14` aligns with drift window | Tunable Defaults below | Existing `drift_window_days = 14` in `thresholds.json`; same window is operator-coherent. |
| A4 | `critic_reject_cwid_max = 3` (distinct pmid_sets) is the right severity threshold | Tunable Defaults below | One CWID with 3 distinct rejected pmid_sets in 14 days is strong evidence of a ranking issue; lower would over-fire, higher would miss real signal. |
| A5 | The four `LLMVerdict.failed_constraint` values map directly to D-10's reason_code enum | Code Examples / F-3 | If the planner picks D-10's literal enum names instead, a translation function is needed at the producer site. Both options have merit; the planner decides. |
| A6 | SPS does not currently consume `cwid_subtopic_counts.csv` | Runtime State Inventory | Direct rename without dual-emission breaks the SPS pipeline. Mitigation: ask SPS team before committing the rename. |
| A7 | `publish_id` has cwid embedded in a parseable position | Code Examples (CRITIC_REJECT# write) | If the format is opaque, we need a separate `cwid` parameter passed into `run_critic_loop` (currently it accepts `publish_id` only). Confirm against `pipeline_spotlight/orchestrator.py`. |

## Open Questions (RESOLVED)

1. **Does the SPS pipeline read `cwid_subtopic_counts.csv` today?** (D-13 audit)
   **RESOLVED (2026-05-12):** Audit deferred per CONTEXT D-13 safe-path default. Plan-aggregations Task 3 ships dual-name emission (both `cwid_subtopic_counts.csv` and `faculty_subtopic_counts_exclusive.csv`) for one cold-run cycle; old name dropped in a follow-up phase. Operator sign-off on SPS audit recorded in SUMMARY at phase close.
   - What we know: Three in-repo readers — `rollup_by_cwid.py:61`, `build_cwid_json.py:12`, `tests/test_rollup_incremental_parity.py:68`. No evidence of external (SPS) reads.
   - What's unclear: SPS source is not in this workspace; cannot grep it directly.
   - Recommendation: One-line ping to SPS team. Default to dual-name emission for one cold-run cycle to be safe (cheap insurance, removable in a follow-up phase).

2. **Does the existing critic-write site at line 571 have access to `cwid`?**
   **RESOLVED (2026-05-12) — escalated to user, awaiting decision:** Code inspection confirmed: `publish_id = f"v{date.today().isoformat()}"` (`backfill_spotlight.py:362`); `SubtopicMeta` carries only `subtopic_id/label/description/parent_topic_label` (`spotlight/sensitive_gate.py:38`); the critic loop is fundamentally per-subtopic, not per-cwid; `SPOTLIGHT_REVIEW#` is keyed on `(publish_id, subtopic_id)`. CONTEXT D-08's `CRITIC_REJECT#{cwid}#{pmid_set_hash}` keying does not map cleanly to the call site. Three options surfaced to user for resolution before planner revises: (α) re-key to `CRITIC_REJECT#{subtopic_id}#{pmid_set_hash}` and `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}`; (β) fan-out per author CWID in `selected_papers` (write amplification); (γ) defer CRITIC_REJECT# producer to a later phase. Resolution pending in CONTEXT amendment.

3. **Should the `feedback_sweep` cold stage write a STAGE# row?**
   **RESOLVED (2026-05-12):** Yes for both code paths. Stage name `feedback_sweep`, scope `GLOBAL`, `source_sweep_run_id` in row body for correlation. Plan-feedback-consumer Task 3 implements this following Phase 11 substrate pattern.

4. **D-07's persistence-window read source — extend `DRIFT#evaluation` or re-scan raw events?**
   **RESOLVED (2026-05-12):** Extend additively with sparse `per_topic_low_confidence` dict. CONTEXT D-04 prohibition replaced (CR-2026-05-12) with precise additive-extension language; CONTEXT D-34 added to lock sparse representation. Plan-drift-extension Task 1 implements; plan-feedback-consumer Task 2 consumes.

5. **`assign_subtopics.py:95` shows `DEFAULT_CONFIDENCE_FLOOR = 0.3`, but CONTEXT D-23 says 0.35.**
   **RESOLVED (2026-05-12):** CONTEXT D-23 + D-25 corrected (CR-2026-05-12): the two floors are distinct concepts (`confidence_floor` = assignment-time, `low_confidence_floor` = event-emission) AND distinct current values (0.3 vs 0.35). Phase 12 G-18 preserves 0.3 as the operational default. Whether 0.3 is the right target is tracked separately in `.planning/issues/0001-confidence-floor-target.md` and is NOT in Phase 12 scope.

6. **Module name: `feedback/`, `pipeline_feedback/`, or fold into `pipeline_cold/`?**
   **RESOLVED (2026-05-12):** `pipeline_feedback/`. Matches Phase 10 cold-path-adjacent module convention; the cold-stage role is real and the `pipeline_*` prefix is honest about that. CLI entry is `python -m pipeline_feedback sweep`/`render`. Plan-feedback-consumer uses this name throughout.

## Environment Availability

Phase 12 depends on external services and tools. The cold-path orchestrator already verifies most of these at startup; Phase 12 inherits.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | All scripts | (assumed per GETTING_STARTED.md) | — | None — hard requirement. |
| boto3 | DDB access | ✓ (in requirements.txt >=1.42.0) | — | None — hard requirement. |
| jsonschema | D-27 thresholds-schema validation | ✓ (in requirements.txt >=4.23.0) | 4.23.0+ | Hand-rolled (NOT recommended). |
| AWS Bedrock | Sonnet sweep over uncovered PMIDs | ✓ (used by score_publications, etc.) | — | If unavailable: sweep stage writes a STAGE# failed row with `error_code=BEDROCK_UNAVAILABLE` and returns 0 (per D-02 non-gating). |
| DynamoDB `reciterai-chatbot` table | All record writes | ✓ (per `utils/dynamodb_helpers.TABLE_NAME`) | — | None — hard requirement for cold path. |
| `gh` CLI | Phase 10's alert dispatcher (not used by Phase 12 sweep findings) | ✓ per Phase 10 SUMMARY | — | Not needed for Phase 12 findings; only relevant if sweep itself fails operationally and dispatches a severity-tagged alert. |
| EventBridge (`drift-daily` cron) | Source of `DRIFT#evaluation` rows that D-07 reads | ✓ per Phase 10 | — | None — D-07 cannot operate without prior drift-evaluator rows. Recommendation: feedback sweep tolerates missing DRIFT# rows by reading `LOW_CONFIDENCE_ASSIGNMENT#` directly as a fallback (already a Pitfall 5 alternative). |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None confirmed missing at this time.

## Validation Architecture

This phase is rich in invariants and is exactly the kind of work that benefits from Nyquist-style sampling.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (verified via existing `tests/test_*.py` files) |
| Config file | None at repo root — pytest uses defaults; `.pytest_cache/` exists, indicating recent successful runs. |
| Quick run command | `pytest tests/test_event_records.py tests/test_gates_registry.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| §8 invariant D-17 | `sum(inclusive[X]) >= sum(exclusive[X])` holds for all topics | unit | `pytest tests/test_reconciliation_gate.py -x` | ❌ Wave 0 |
| §8 invariant D-15 | Inclusive uses uniform full article_score per above-floor assignment | unit | `pytest tests/test_aggregate_inclusive.py -x` | ❌ Wave 0 |
| §8 invariant D-16 | confidence_floor still gates subtopic_ids[] membership (no double filtering) | unit | `pytest tests/test_aggregate_inclusive.py::test_floor_already_filtered_by_writer` | ❌ Wave 0 |
| §8 idempotency D-14 | Re-running aggregator with same `run_id` produces byte-identical DDB writes | unit | `pytest tests/test_aggregate_idempotent.py -x` | ❌ Wave 0 |
| §9 CRITIC_REJECT# producer | Existing critic write site at `:571` is unchanged; new write is additive | unit | `pytest tests/test_critic_reject_event.py::test_review_queue_write_unchanged` | ❌ Wave 0 |
| §9 CRITIC_REJECT# dedup | Same `(cwid, pmid_set)` retries overwrite, do not duplicate | unit | `pytest tests/test_critic_reject_event.py::test_pmid_set_hash_dedup` | ❌ Wave 0 |
| §9 sweep idempotency D-05 | Same `source_sweep_run_id` overwrites; new run_id produces separate record | unit | `pytest tests/test_feedback_sweep.py::test_idempotent_per_run_id` | ❌ Wave 0 |
| §9 sweep cap D-06 | Cap exceeded → `truncated=true` + `total_unprocessed_remaining=N` | unit | `pytest tests/test_feedback_sweep.py::test_cap_emits_truncated` | ❌ Wave 0 |
| §9 sweep ordering D-06 | Worst-fitting (lowest top_topic_score) PMIDs go first | unit | `pytest tests/test_feedback_sweep.py::test_worst_fitting_first` | ❌ Wave 0 |
| §9 recluster trigger D-07 | Persistence-window threshold produces RECLUSTER_RECOMMENDATION# | unit | `pytest tests/test_feedback_sweep.py::test_recluster_trigger` | ❌ Wave 0 |
| §9 diagnostic D-09 | SPOTLIGHT_DIAGNOSTIC# count is distinct rejected pmid_sets, not events | unit | `pytest tests/test_feedback_sweep.py::test_diagnostic_distinct_pmid_sets` | ❌ Wave 0 |
| §9 render D-03 | Same row in → same bytes out; no generated_at in markdown body | unit | `pytest tests/test_feedback_render.py::test_byte_identical_render` | ❌ Wave 0 |
| §11 G-18 thresholds | All existing keys + new keys present in thresholds.json | unit | `pytest tests/test_thresholds_keys.py -x` | ❌ Wave 0 |
| §11 G-18 confidence_floor not collapsed with low_confidence_floor (D-25) | Both keys exist; confidence_floor=0.3, low_confidence_floor=0.35 (per D-25 CR-2026-05-12) | unit | `pytest tests/test_thresholds_keys.py::test_two_floor_keys_distinct` | ❌ Wave 0 |
| §11 D-27 schema validation | Malformed thresholds.json fails env_check.py | unit | `pytest tests/test_thresholds_schema.py -x` | ❌ Wave 0 |
| §11 G-36 reproducibility | `hierarchy.json` byte-stable on second run | unit | `pytest tests/test_hierarchy_reproducibility.py -x` | ✅ (exists; verify still green) |
| §11 G-37 E2E | Stripped-down fixture corpus through cold path | integration | `pytest tests/test_cold_path_e2e.py -x` | ❌ Wave 0 |
| §11 G-37 invariant | Same fixture corpus → byte-identical `hierarchy.json` across two runs | integration | `pytest tests/test_cold_path_e2e.py::test_byte_identical_across_runs` | ❌ Wave 0 |
| D-28 tunable audit | STAGE# row carries `tunable_inputs` when stage is invoked with overrides | unit | `pytest tests/test_stage_records.py::test_tunable_inputs_in_row` | ❌ Wave 0 (extend existing file) |

### Sampling Rate
- **Per task commit:** `pytest tests/test_event_records.py tests/test_gates_registry.py tests/test_feedback_sweep.py -x` (quick — ~5 sec)
- **Per wave merge:** `pytest tests/ -x` (full suite — likely <60s currently)
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_feedback_sweep.py` — covers §9 consumer behavior
- [ ] `tests/test_feedback_render.py` — covers D-03 deterministic markdown
- [ ] `tests/test_critic_reject_event.py` — covers §9 producer
- [ ] `tests/test_aggregate_inclusive.py` — covers D-15/D-16
- [ ] `tests/test_aggregate_idempotent.py` — covers D-14
- [ ] `tests/test_reconciliation_gate.py` — covers D-17/D-18
- [ ] `tests/test_thresholds_keys.py` — covers G-18 + D-25
- [ ] `tests/test_thresholds_schema.py` — covers D-27
- [ ] `tests/test_cold_path_e2e.py` — covers G-37 (the long-pole; planner schedules at end per D-19)
- [ ] `tests/fixtures/cold_path_corpus/` — fixture corpus for the E2E test (planning step required per Pitfall 4)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Phase 12 uses existing AWS IAM identity through the pipeline-runner role; no new auth surface. |
| V3 Session Management | no | No session state. |
| V4 Access Control | yes | New IAM-policy doc reference in `GETTING_STARTED.md` (G-34). The policy file already exists at `docs/aws-iam-pipeline-policy.json`. Phase 12 task: link from runbook, not re-author. |
| V5 Input Validation | yes | `thresholds.schema.json` (D-27) for config; Sonnet sweep outputs are parsed JSON and must be validated before write (per existing `event_records.py` Decimal coercion + type checks). |
| V6 Cryptography | no | DDB encryption-at-rest is account-level; no Phase 12-specific crypto. |

### Known Threat Patterns for Phase 12 surface

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Operator runs `python -m feedback sweep` with wrong AWS profile and writes to prod DDB | Tampering | Pipeline-runner role per G-34 doc; `RECITERAI_ENV` env-var check (verify already in env_check.py pattern) at sweep start. |
| Sonnet response includes malformed JSON | Spoofing/Tampering | Validate Sonnet output against a schema before writing finding records; on parse error, write a STAGE# failed row with `error_code=SONNET_PARSE_ERROR` and skip the offending PMID, not the whole sweep. |
| Sonnet response includes PMID strings that look like injection (`'; DROP TABLE`) | Tampering | DDB is NoSQL — no SQL injection risk. PMIDs are PK key fragments and must be sanitized to `[0-9]+` only before composing PKs. Reuse existing PMID-normalization helper if present; otherwise add a regex check. |
| CRITIC_REJECT# writes leak lede content (which the spotlight contract forbids) | Information Disclosure | The CRITIC_REJECT# record carries `reason_code` + `pmid_set` + `regen_count` ONLY — never lede text. Existing SPOTLIGHT_REVIEW# does carry `lede_text`; Phase 12 must not duplicate that on CRITIC_REJECT#. Document at producer site. |
| `SPOTLIGHT_DIAGNOSTIC#{cwid}` row exposes faculty name implicitly via cwid | Information Disclosure | cwid is faculty-identifying; the gates/pii.py gate explicitly forbids cwid in hierarchy artifacts. SPOTLIGHT_DIAGNOSTIC# rows live in DDB, not S3; they are operator-visible and not published. Phase 12 must NOT include SPOTLIGHT_DIAGNOSTIC# rows in any S3-published artifact, including the deterministic markdown rendering (D-03 markdown is operator-facing only; document at the render site). |

## Findings Specific to Planning

### F-1: `SUBTOPIC_SCORE#{topic}#{subtopic}` partitions described in D-12 do not exist today

**Evidence:** `grep -rn "SUBTOPIC_SCORE" --include="*.py"` returns no matches (verified). The current aggregator (`aggregate_subtopic_scores.py`) writes faculty subtopic scores into a nested `subtopic_scores.<topic_id>` map on `FACULTY#<personIdentifier>` records via `update_faculty_subtopic_scores` (verified at `utils/dynamodb_subtopic_migration.py:90-130`). Spec §8 (verified at `docs/RECITERAI-SPEC.md:375-386`) talks about CSV file names, not DDB partitions.

**Impact:** D-12 reads as "add an inclusive sibling to the existing exclusive partition," but the actual work is creating BOTH partitions, since neither exists. The existing faculty-map write may also need to remain in place (SPS consumer contract). Planner must decide:
- **Option A:** Ship D-12 verbatim — create both partitions; keep the faculty-map write as the SPS contract. Plan task count goes up by one (build the exclusive partition writer too).
- **Option B:** Stop writing the faculty-map; SPS reads from the new partition. Higher risk: SPS migration coordination.

**Recommendation:** **Option A.** The SPS contract is load-bearing and faculty-map → SPS-side `subtopic_scores` is what makes SPS work today. Creating both partitions for §8's "publish both" purpose AND preserving the existing path is honest about the migration: we are adding parallel-but-equivalent reads; SPS migration to read from partitions instead of the map is a separate phase.

### F-2: `DRIFT#evaluation` row schema does NOT carry per-topic LOW_CONFIDENCE counts

**Evidence:** `pipeline_drift/evaluator.py:73-96` `to_dynamodb_item()` persists only `low_confidence_max_topic: str | None` and `low_confidence_max_count: int`. The per-topic dict `per_topic: dict[str, int]` is computed at lines 153-161 in `evaluate()` and discarded.

**Impact:** D-07's "consumer queries 'how many evaluation rows fall in that window' at read time" cannot be implemented without either (a) extending the row schema with `per_topic_low_confidence: dict[topic_id, count]`, or (b) re-scanning raw `LOW_CONFIDENCE_ASSIGNMENT#` rows directly during the sweep.

**Recommendation:** Option (a) — extend the row additively. CONTEXT's "does not extend the drift evaluator" is honest about not changing the count *semantics* or the alert ladder, but the row schema needs the per-topic dict to be queryable. Backwards-compatible (same shape as Phase 11 `run_id` extension). One additional field on `DriftEvaluation`, one additional dict in `to_dynamodb_item()`. Existing tests at `tests/test_pipeline_drift_evaluator.py` must be updated.

### F-3: Critic verdict shape today exposes TWO closed enums; D-10's proposed enum does not map to either

**Evidence:** `spotlight/critic.py:177-205`:
- `DeterministicVerdict.failed_constraints: tuple[str, ...]` — values include `em_dash_present`, `time_bound_language`, `marketing_language`, `dead_word:<word>`, `missing_wcm_scholars_tic`, `length_out_of_band:<word_count>`, `opener_already_used:<opener>`.
- `LLMVerdict.failed_constraint: str` — values include `active_verb`, `anchored_in_synopses`, `no_faculty_named`, `institutional_voice`, plus `parse_error` (line 403).

D-10 proposes `LEDE_INCOHERENT`, `WEAK_EVIDENCE`, `OFF_TOPIC_PUBS`, `INSUFFICIENT_NARRATIVE`. These don't map 1:1 to either existing enum.

The existing SPOTLIGHT_REVIEW# write at `spotlight/critic.py:565-569` only stores LLMVerdict (`failed_constraint` + `reason`); the deterministic verdicts that triggered earlier retries do not get persisted to the queue row.

**Impact:** The planner must decide:
- **Option A:** Use `LLMVerdict.failed_constraint` verbatim as `reason_code` — four codes, already documented in the critic prompt and the dataclass docstring. This means CRITIC_REJECT# only fires on LLM-stage failures (after all 4 attempts exhaust at the LLM stage). Deterministic-only failures (which complete the retry loop but never reach the final LLM check) would not produce a CRITIC_REJECT# row.
- **Option B:** Combine both enums into a single broader vocabulary that covers both stages. Higher fidelity; requires defining 11+ codes.
- **Option C:** Adopt D-10's four-code vocabulary as a coarse rollup of both internal enums via a mapping table. Highest abstraction; planner needs to define the mapping.

**Recommendation:** **Option A.** The CONTEXT D-10 is explicit that the planner "may refine the exact list once they read the current critic verdict shape; the *contract* is that it's a closed enum." Using `failed_constraint` directly satisfies the closed-enum contract, requires no new vocabulary or mapping table, and the four LLM codes ARE the codes operators currently see in `SPOTLIGHT_REVIEW#` rows. This preserves the operator mental model. If deterministic-only failures need their own diagnostic surface, that's a follow-up.

Document this choice in `spotlight/critic.py` reason_code docstring and in `thresholds.md`.

### F-4: `DEFAULT_CONFIDENCE_FLOOR` in code is 0.3, not 0.35

**Evidence:** `assign_subtopics.py:95`:
```python
DEFAULT_CONFIDENCE_FLOOR = 0.3     # Below this, assignments are dropped (D-02)
```

CONTEXT D-23 says "`confidence_floor` (from `assign_subtopics.py:1022` `DEFAULT_CONFIDENCE_FLOOR`, value 0.35)". The line number is right (it's where the CLI flag uses the constant) but the value is wrong — actual default is 0.3.

`low_confidence_floor` in `thresholds.json:3` IS 0.35.

**Impact:** When the planner lifts `confidence_floor` to `thresholds.json`, they must pick a value. Three options:
- **Option A:** Lift the current 0.3 verbatim. Preserves behavior. Surfaces the divergence from `low_confidence_floor` (they ARE different decisions per D-25).
- **Option B:** Bump to 0.35 to match `low_confidence_floor`. Cleaner mental model but is a real behavior change (more assignments dropped at assign-time). Document loudly in SUMMARY.
- **Option C:** Pick a new value altogether.

**Recommendation:** **Option A** — preserve the 0.3 current behavior. D-25's argument is exactly that these two keys represent different decisions; coupling them numerically without telemetry to justify the change is speculative. If a future phase decides 0.35 is right for both, they'll have the data to back it up.

### F-5: G-36 reproducibility tests already exist

**Evidence:** `tests/test_hierarchy_reproducibility.py` exists with four tests (verified): `test_bundle_is_byte_stable_across_two_passes`, `test_build_hierarchy_is_byte_stable_across_two_passes`, `test_generate_is_byte_stable_with_pinned_generated_at`, `test_hierarchy_dict_has_no_generated_at_after_build`. The file docstring explicitly references "Phase 11 D-14 / G-36 prerequisite."

**Impact:** G-36's framing in CONTEXT D-19 (a Phase 12 deliverable) is partially already shipped. The remaining G-36 scope per spec §11.459 ("`pipeline_hierarchy/` tests... reproducibility test (same inputs → same sha256)") is partially covered by `test_generate_is_byte_stable_with_pinned_generated_at` (line 119-132) which already asserts `manifest1["sha256"] == manifest2["sha256"]`.

**Recommendation:** G-36 in Phase 12 means **hardening** the existing tests, not creating them. Concrete actions: (a) audit for missing edge cases (empty bundle, multi-topic edge, see-also presence), (b) add a test that runs the FULL `pipeline_hierarchy.publish.publish_hierarchy()` (not just `generate()`) twice and asserts byte-stability — that's the assertion that buys "publish is deterministic", not just "the generator is." (c) Move from `test_hierarchy_reproducibility.py` testing `generate()` to also testing `publish()` end-to-end at the same boundary.

### F-6: Sensitive-topic exclusion list location for G-24

**Evidence:** Two separate exclusion concepts:
- `config/excluded_topics.json` — frozen taxonomy-level excluded topics (`implementation_science`, `oral_craniofacial_health`). Spec G-32 (resolved `058529c`).
- `SPOTLIGHT_CONFIG#sensitive_tags` DDB record — operator-controlled sensitive-tag patterns, intentionally NOT in source per `spotlight/sensitive_gate.py:8-11`: "Tag patterns live in DynamoDB ONLY — they are operationally sensitive and never appear in source."

**Impact:** G-24 is about the sensitive-tag gate. Documentation cannot leak the patterns themselves (per the security invariant), but can document:
- The rationale (compliance-adjacent; certain research domains shouldn't have public spotlight ledes)
- The mechanism (DDB-resident patterns, fail-closed gate, case-insensitive substring match against label+description+parent_topic_label)
- The operator workflow (how to add/update patterns via DDB CLI)
- The SPOT-08 fail-closed invariant

**Recommendation:** Create `docs/sensitive-topic-exclusion.md` (NEW) or add a section to `docs/spotlight-contract.md`. Reference from `spotlight/sensitive_gate.py:1-17` module docstring. Do NOT include actual patterns. Cross-link from `GETTING_STARTED.md` (alongside G-34's IAM section).

### F-7: Tunable defaults — recommendations

| Tunable | Recommended Default | Justification |
|---------|---------------------|---------------|
| `feedback_sweep_max_pmids` | `200` | Bedrock Sonnet at ~$15/M output tokens; 200 PMIDs × ~500 tokens/response ≈ 100K output tokens ≈ $1.50 per sweep. Sweep cadence is per cold-run + ad-hoc operator, so weekly cap of ~$10 is operator-tolerable. Truncation surfaces as a tunable, so erring low is safe. |
| `recluster_persistence_days` | `7` | Half of `drift_window_days` (14). A topic that's been daily-max for half the drift window is materially persistent. Shorter (3-day) would noise-trigger; longer (14-day, matching drift window) would only fire on extended-persistence cases that should already be alert-worthy. |
| `critic_reject_persistence_days` | `14` | Matches existing `drift_window_days`. Operator coherence: same window for both signals means one mental model. |
| `critic_reject_cwid_max` | `3` | Three distinct rejected pmid_sets for the same cwid in 14 days is materially "stuck." Lower (2) would over-fire on normal regen retries that the producer-side dedup hasn't fully suppressed; higher (5) would miss the early-warning window. |

Document each in `thresholds.md` with the rationale + the "if you raise/lower this…" sentence per D-26.

## Sources

### Primary (HIGH confidence)
- Direct codebase reads — every file path cited above was verified via Read tool against the working copy at `/Users/paulalbert/Dropbox/GitHub/ReciterAI` on 2026-05-12.
- `docs/RECITERAI-SPEC.md` §8 (lines 375-386), §9 (lines 390-407), §11 (lines 459-467) — verified.
- `requirements.txt` — verified jsonschema>=4.23.0, boto3>=1.42.0, pyyaml>=6.0.1.
- Phase 9-11 SUMMARY.md files referenced above.

### Secondary (MEDIUM confidence)
- Tunable default recommendations in F-7 — drawn from cost estimates and operator-coherence reasoning; no production telemetry visible in the SUMMARY files to confirm.

### Tertiary (LOW confidence)
- None. Every claim above is either verified against the codebase or explicitly tagged as a planner recommendation needing user confirmation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every library is already in `requirements.txt`; no version research needed.
- Architecture: HIGH for existing patterns (`gates/registry.py`, `event_records.py`, `stage_records.py`, `review/cli.py`); MEDIUM for Phase 12 architectural decisions (F-1, F-2, F-3 each need a planner call).
- Pitfalls: HIGH — derived directly from code reading + CONTEXT contradictions.

**Research date:** 2026-05-12
**Valid until:** ~2026-06-12 (30 days; codebase moves fast on cold-path substrate but Phase 12 surface is well-bounded).
