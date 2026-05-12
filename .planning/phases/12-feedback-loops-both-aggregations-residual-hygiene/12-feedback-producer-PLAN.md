---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: feedback-producer
type: execute
wave: 1
depends_on: []
files_modified:
  - spotlight/critic.py
  - utils/event_records.py
  - tests/test_critic_reject_event.py
  - tests/test_critic_reject_producer.py
autonomous: true
requirements:
  - "spec-§9-producer"
  - D-08
  - D-09
  - D-10
  - D-30
  - D-31
tags: [spotlight, critic, event-producer, enum]
must_haves:
  truths:
    - "spotlight/critic.py writes CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash} alongside (not replacing) the existing SPOTLIGHT_REVIEW# write"
    - "Per CONTEXT D-08 re-framing, PK is per-(publish, subtopic, pmid_set) — NOT per-cwid. publish_id and meta.subtopic_id are already in scope at the write site; no cwid threading is required."
    - "Row body carries author_cwids: list[str] — distinct first_author.person_identifier and last_author.person_identifier values across selected_papers — for future per-faculty drill-down (D-32 + D-08 body field)"
    - "CritReasonCode StrEnum is the closed vocabulary; LLM verdict values pass through verbatim"
    - "Deterministic gate failures (no LLM verdict reached) emit reason_code=PRE_LLM_GATE with the specific pre-LLM constraint preserved in pre_llm_constraint"
    - "Vocabulary drift (LLM returns unknown code) emits reason_code='unknown' + raw_failed_constraint + a warning via pipeline_common.alert; never crashes the producer"
    - "CRITIC_REJECT# row carries reason_code + pmid_set + author_cwids + regen_count; never lede_text (PII boundary)"
    - "Idempotent: same (publish_id, subtopic_id, pmid_set) regenerated retries overwrite in place via pmid_set_hash keying"
  artifacts:
    - path: "spotlight/critic.py"
      provides: "CritReasonCode StrEnum + CRITIC_REJECT# write at line ~571 alongside existing SPOTLIGHT_REVIEW# write"
      contains: "class CritReasonCode"
    - path: "utils/event_records.py"
      provides: "build_critic_reject_record + write_critic_reject (per-publish/per-subtopic keyed; author_cwids in body)"
      contains: "CRITIC_REJECT"
    - path: "tests/test_critic_reject_event.py"
      provides: "Builder shape + Decimal coercion + pmid_set_hash + author_cwids derivation + idempotency tests"
      contains: "build_critic_reject_record"
    - path: "tests/test_critic_reject_producer.py"
      provides: "End-to-end producer test: critic exhaustion triggers CRITIC_REJECT# write alongside SPOTLIGHT_REVIEW#; existing SPOTLIGHT_REVIEW# call asserted unchanged"
      contains: "write_critic_reject"
  key_links:
    - from: "spotlight/critic.py:571"
      to: "utils/event_records.write_critic_reject"
      via: "additive write after existing write_review_entry call"
      pattern: "write_critic_reject"
    - from: "spotlight/critic.py CritReasonCode"
      to: "LLMVerdict.failed_constraint"
      via: "enum validation, unknown→'unknown' bucket with raw preserved"
      pattern: "CritReasonCode"
---

<objective>
Add the missing spec §9 producer: a `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}` DDB write at the existing critic-loop exhaustion site in `spotlight/critic.py`. Define a closed-vocabulary `CritReasonCode` StrEnum (D-31) using the LLM's existing verdict spelling (D-30). Distinguish post-LLM failures (one of four LLM constraints) from pre-LLM deterministic-gate failures (`PRE_LLM_GATE` + preserved underlying code). Handle vocabulary drift gracefully via a warning + `unknown` bucket. This producer is what the wave-2 feedback-consumer's `SPOTLIGHT_DIAGNOSTIC#{subtopic_id}` aggregator reads.

**Per CONTEXT D-08 re-framing (CR-2026-05-12):** keying is per-(publish, subtopic, pmid_set), NOT per-cwid. The producer reads `publish_id` and `meta.subtopic_id` already in scope at `spotlight/critic.py:571` — no cwid parameter needs to be threaded through `run_critic_loop`. Body carries `author_cwids: list[str]` derived from `selected_papers` for future per-faculty drill-down (D-32).

Purpose: today's critic exhaustion path writes SPOTLIGHT_REVIEW# (human queue). It produces no machine-readable event for cross-publish aggregation. Phase 12 D-08 adds the typed event alongside; SPOTLIGHT_REVIEW# stays untouched.

Output: spotlight/critic.py with CritReasonCode + dual-write; utils/event_records.py with build_critic_reject_record + write_critic_reject; producer + builder tests.
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

<interfaces>
<!-- Existing critic verdict shape, verified by RESEARCH F-3 and PATTERNS.md spotlight/critic.py section -->

spotlight/critic.py existing LLMVerdict (lines 177-205):
```python
@dataclass(frozen=True)
class LLMVerdict:
    passed: bool
    failed_constraint: str  # "active_verb" | "anchored_in_synopses" | "no_faculty_named" | "institutional_voice" | "" (passing)
    reason: str
```

Existing DeterministicVerdict (also in spotlight/critic.py): exposes `failed_constraints: list[str]` for pre-LLM gate failures.

Existing critic-loop exhaustion site (lines 554-577 — the SPOTLIGHT_REVIEW# write is the anchor):
```python
review_entry = {...}
if last_llm_verdict is not None:
    review_entry["critic_verdict"] = {...}
write_review_entry(dynamo_client, review_entry)   # EXISTING — UNCHANGED
logger.info("Critic exhausted: subtopic_id=%s regen_count=%d -> review queue", meta.subtopic_id, MAX_RETRIES)
```

At this site, `publish_id` and `meta.subtopic_id` are already in scope (`meta` carries subtopic_id per the existing log line; publish_id is the spotlight run's publish identifier already available to write_review_entry — read the existing call to confirm the exact variable name). `selected_papers` (the rejected pmid_set's source) is also in scope — used to derive both `pmids` and `author_cwids`.

CritReasonCode enum values (D-31, exact spelling preserves comparability with SPOTLIGHT_REVIEW#):
```python
from enum import StrEnum
class CritReasonCode(StrEnum):
    ACTIVE_VERB = "active_verb"
    ANCHORED_IN_SYNOPSES = "anchored_in_synopses"
    NO_FACULTY_NAMED = "no_faculty_named"
    INSTITUTIONAL_VOICE = "institutional_voice"
    PRE_LLM_GATE = "pre_llm_gate"
```

**PK shape (D-08 re-framed):** `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}`, SK="GLOBAL", record_type="CRITIC_REJECT", source_stage="spotlight.critic"

**Body field author_cwids (D-08):** derived from selected_papers per CONTEXT canonical_refs `spotlight/types.py:39-57` (Paper/Author shape):
```python
author_cwids = sorted(
    {p.first_author.person_identifier for p in selected_papers if p.first_author and p.first_author.person_identifier}
    | {p.last_author.person_identifier for p in selected_papers if p.last_author and p.last_author.person_identifier}
)
```

pmid_set_hash computation (per PATTERNS.md):
```python
import hashlib
pmid_set_hash = hashlib.sha256(",".join(sorted(pmids)).encode()).hexdigest()[:16]
```

PII boundary (Pattern H from PATTERNS.md): CRITIC_REJECT# carries reason_code + pmid_set + author_cwids + regen_count + reason (short, no lede text). DOES NOT carry lede_text. SPOTLIGHT_REVIEW# is the place where lede_text lives.

Existing utils/event_records.py builder pattern (lines 61-98 for build_uncovered_pmid_record + write_uncovered_pmid):
- All keyword-only args (`*,`)
- `created_at: str | None = None` with `_now_iso()` fallback
- Stable PK + constant SK="GLOBAL"
- Float→Decimal via `Decimal(str(v))`
- `record_type` + `source_stage` fields
- Thin writer composes builder + `table.put_item`

pipeline_common.alert signature (Phase 10): `alert(message: str, *, severity: str)` — severity ∈ {"info", "warning", "error", "critical"}
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add build_critic_reject_record + write_critic_reject to utils/event_records.py (per-publish/per-subtopic keyed, author_cwids in body)</name>
  <files>utils/event_records.py, tests/test_critic_reject_event.py</files>
  <read_first>
    - utils/event_records.py lines 1-110 (full file — mirror imports, _now_iso helper, build_uncovered_pmid_record + write_uncovered_pmid pattern)
    - tests/test_uncovered_pmid_event.py (full file — analog producer-side test shape)
    - tests/test_event_records.py lines 1-80 (builder-shape test idioms)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "Pattern A: DDB event-record idempotency" + Pattern B Decimal coercion
  </read_first>
  <behavior>
    - tests/test_critic_reject_event.py::test_record_shape_required_fields — call build_critic_reject_record with publish_id="2026-05-12-001", subtopic_id="aging_geroscience", pmids=["123","456"], author_cwids=["abc1","def2"], reason_code="active_verb", regen_count=3, reason="LLM said no", created_at="2026-05-12T12:00:00Z"; assert PK starts with "CRITIC_REJECT#2026-05-12-001#aging_geroscience#" AND PK.count("#") == 3 (per D-08 four-segment shape), SK == "GLOBAL", record_type == "CRITIC_REJECT", source_stage == "spotlight.critic", reason_code == "active_verb", regen_count == 3, record["author_cwids"] == ["abc1","def2"]
    - tests/test_critic_reject_event.py::test_pmid_set_hash_is_stable_and_order_invariant — build the record twice with pmids=["a","b","c"] and pmids=["c","b","a"]; assert PKs are identical (sorted before hashing per the interfaces block)
    - tests/test_critic_reject_event.py::test_pmid_set_hash_changes_with_set — different pmid lists produce different PK suffixes
    - tests/test_critic_reject_event.py::test_pk_changes_with_publish_id — same subtopic/pmid_set but different publish_id produces a distinct PK (D-08: same subtopic re-failing with the same pmid_set across publish cycles = N rows, one per publish_id)
    - tests/test_critic_reject_event.py::test_pk_changes_with_subtopic_id — same publish_id/pmid_set but different subtopic_id produces a distinct PK
    - tests/test_critic_reject_event.py::test_author_cwids_sorted_and_deduplicated — pass author_cwids=["c","a","b","a"]; assert record["author_cwids"] == ["a","b","c"] (sorted, deduped)
    - tests/test_critic_reject_event.py::test_pmid_set_field_carries_full_list — record["pmid_set"] is the sorted full list, not just the hash
    - tests/test_critic_reject_event.py::test_unknown_reason_code_path — call with reason_code="unknown" + raw_failed_constraint="something_new_from_llm"; assert record["reason_code"] == "unknown" and record["raw_failed_constraint"] == "something_new_from_llm"
    - tests/test_critic_reject_event.py::test_pre_llm_gate_path — call with reason_code="pre_llm_gate" + pre_llm_constraint="active_verb"; assert both fields present on record
    - tests/test_critic_reject_event.py::test_no_lede_text_field — D-30 / Pattern H PII boundary: assert "lede_text" NOT in record and no key contains the word "lede" (record carries no lede content)
    - tests/test_critic_reject_event.py::test_writer_calls_put_item — `write_critic_reject(table=MagicMock(), ...)` builds and calls `table.put_item(Item=record)` exactly once
    - tests/test_critic_reject_event.py::test_idempotent_overwrite_same_triplet — two writes with identical (publish_id, subtopic_id, pmid_set) build identical PK; second write would overwrite (DDB-level overwrite is implicit)
  </behavior>
  <action>
    In `utils/event_records.py`, ADD (do NOT modify existing builders) the following after the existing `write_low_confidence_assignment` or at the end of the file:

    ```python
    import hashlib  # add to existing imports at top if not already

    def _compute_pmid_set_hash(pmids: list[str]) -> str:
        """Stable, order-invariant hash for a PMID set.

        Sort first, comma-join, sha256, take 16 hex chars. Same set → same hash
        regardless of input ordering, giving the CRITIC_REJECT# producer natural
        dedup across critic retries within a single spotlight regen run.
        """
        joined = ",".join(sorted(str(p) for p in pmids))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


    def build_critic_reject_record(
        *,
        publish_id: str,
        subtopic_id: str,
        pmids: list[str],
        author_cwids: list[str],
        reason_code: str,
        regen_count: int,
        reason: str = "",
        raw_failed_constraint: str | None = None,
        pre_llm_constraint: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Pure builder for a CRITIC_REJECT# row.

        Phase 12 D-08 (CR-2026-05-12 re-framing): keying is per-(publish_id,
        subtopic_id, pmid_set), NOT per-cwid. Spotlight artifacts have
        authorship spanning many CWIDs; there is no single 'spotlight cwid'.
        Per-faculty drill-down is preserved via the author_cwids body field
        (distinct first/last-author person_identifiers across the rejected
        pmid_set), allowing the wave-2 consumer to surface affected faculty
        without forcing per-CWID aggregation.

        Idempotent via pmid_set_hash → same (publish_id, subtopic_id,
        pmid_set) overwrites within a regen run (D-09).

        Carries no lede text (PII boundary, Pattern H). SPOTLIGHT_REVIEW#
        remains the artifact carrying lede content for human reviewers.

        reason_code is one of CritReasonCode (defined in spotlight/critic.py):
        - "active_verb" | "anchored_in_synopses" | "no_faculty_named"
        - "institutional_voice"     (post-LLM rejections, verbatim from LLM)
        - "pre_llm_gate"             (deterministic gate failed; pre_llm_constraint carries the specific code)
        - "unknown"                  (vocabulary drift; raw_failed_constraint preserves the raw LLM string)
        """
        sorted_pmids = sorted(str(p) for p in pmids)
        pmid_set_hash = _compute_pmid_set_hash(sorted_pmids)
        # author_cwids: sort+dedup at the builder boundary so callers can pass raw lists
        sorted_cwids = sorted({str(c) for c in (author_cwids or []) if c})
        item: dict[str, Any] = {
            "PK": f"CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}",
            "SK": "GLOBAL",
            "record_type": "CRITIC_REJECT",
            "publish_id": str(publish_id),
            "subtopic_id": str(subtopic_id),
            "pmid_set": sorted_pmids,
            "pmid_set_hash": pmid_set_hash,
            "author_cwids": sorted_cwids,
            "reason_code": str(reason_code),
            "regen_count": int(regen_count),
            "reason": str(reason),
            "created_at": created_at or _now_iso(),
            "source_stage": "spotlight.critic",
        }
        if raw_failed_constraint is not None:
            item["raw_failed_constraint"] = str(raw_failed_constraint)
        if pre_llm_constraint is not None:
            item["pre_llm_constraint"] = str(pre_llm_constraint)
        return item


    def write_critic_reject(table: Any, **kwargs: Any) -> dict[str, Any]:
        """Build a CRITIC_REJECT# row and persist via table.put_item."""
        item = build_critic_reject_record(**kwargs)
        table.put_item(Item=item)
        return item
    ```

    Write `tests/test_critic_reject_event.py` mirroring `tests/test_uncovered_pmid_event.py` structure: module docstring listing every test, `from __future__ import annotations`, MagicMock for the table in the writer test. Every test pins `created_at="2026-05-12T12:00:00Z"` for determinism. The `test_no_lede_text_field` test must assert both `"lede_text" not in record` and `not any("lede" in k.lower() for k in record.keys())` — this is the PII boundary; the assertion is load-bearing.

    Commit message: `feat(12-feedback): add build_critic_reject_record + write_critic_reject event producer (per-publish/per-subtopic keyed)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_critic_reject_event.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "def build_critic_reject_record" utils/event_records.py` returns 1
    - `grep -c "def write_critic_reject" utils/event_records.py` returns 1
    - `grep -c "def _compute_pmid_set_hash" utils/event_records.py` returns 1
    - `grep -c "CRITIC_REJECT" utils/event_records.py` returns count >= 3 (PK fstring + record_type literal + docstring reference)
    - `grep -c "author_cwids" utils/event_records.py` returns count >= 2 (param + body field assignment)
    - `grep -c "publish_id" utils/event_records.py` returns count >= 2 (param + body)
    - `grep -c "lede" utils/event_records.py` returns 0 (PII boundary: no lede content in this module's builders)
    - `pytest tests/test_critic_reject_event.py -x` exits 0
    - `python -c "from utils.event_records import build_critic_reject_record; r=build_critic_reject_record(publish_id='p1', subtopic_id='s1', pmids=['1','2'], author_cwids=['c1'], reason_code='active_verb', regen_count=3); assert r['PK'].startswith('CRITIC_REJECT#p1#s1#') and r['PK'].count('#') == 3 and r['SK']=='GLOBAL' and r['record_type']=='CRITIC_REJECT' and 'lede_text' not in r and r['author_cwids']==['c1']"` exits 0
    - `python -c "from utils.event_records import build_critic_reject_record; a=build_critic_reject_record(publish_id='p1', subtopic_id='s1', pmids=['c','a','b'], author_cwids=['x'], reason_code='x', regen_count=1); b=build_critic_reject_record(publish_id='p1', subtopic_id='s1', pmids=['b','a','c'], author_cwids=['x'], reason_code='x', regen_count=1); assert a['PK']==b['PK']"` exits 0
    - `pytest tests/test_event_records.py tests/test_uncovered_pmid_event.py tests/test_low_confidence_event.py -x` exits 0 (no regression on existing event-record tests)
  </acceptance_criteria>
  <done>Builder + thin writer exist, conform to event-records pattern; PK is per-(publish_id, subtopic_id, pmid_set_hash); author_cwids in body sorted+deduped; PII boundary asserted; tests green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Define CritReasonCode StrEnum + dual-write at critic exhaustion site (uses publish_id + meta.subtopic_id already in scope; derives author_cwids from selected_papers)</name>
  <files>spotlight/critic.py, tests/test_critic_reject_producer.py</files>
  <read_first>
    - spotlight/critic.py full file (need to see: LLMVerdict dataclass ~line 177, DeterministicVerdict, the regen loop, exact line of the existing `write_review_entry(dynamo_client, review_entry)` call — PATTERNS.md cites ~571). Confirm `publish_id`, `meta.subtopic_id`, and `selected_papers` are all in scope at the write site.
    - spotlight/types.py lines 39-57 (Paper + Author dataclass: `first_author.person_identifier`, `last_author.person_identifier` shape)
    - spotlight/review_queue.py:134 (write_review_entry signature; CRITIC_REJECT# must NOT use this; it uses utils.event_records.write_critic_reject)
    - utils/event_records.py (just added — write_critic_reject + build_critic_reject_record from Task 1)
    - pipeline_common/alert.py (alert() signature and severity values)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md "spotlight/critic.py CRITIC_REJECT# additive write" section
  </read_first>
  <behavior>
    - tests/test_critic_reject_producer.py::test_critreasoncode_enum_values — CritReasonCode is a StrEnum with exactly these five members (test by name AND value): ACTIVE_VERB="active_verb", ANCHORED_IN_SYNOPSES="anchored_in_synopses", NO_FACULTY_NAMED="no_faculty_named", INSTITUTIONAL_VOICE="institutional_voice", PRE_LLM_GATE="pre_llm_gate"
    - tests/test_critic_reject_producer.py::test_exhaustion_writes_both_review_and_critic_reject — mock the critic-loop's regen path to fail MAX_RETRIES+1 times with last_llm_verdict.failed_constraint="active_verb"; capture all `put_item` calls; assert SPOTLIGHT_REVIEW# write happens AND a CRITIC_REJECT# write happens; assert order is review-first (matches PATTERNS.md "additive after the existing call")
    - tests/test_critic_reject_producer.py::test_review_queue_write_unchanged — assert the existing `write_review_entry` call receives the SAME review_entry payload it did before Phase 12 (the additive CRITIC_REJECT# write does not mutate the SPOTLIGHT_REVIEW# arguments). Strong-form regression test: capture the call args dict, compare against a golden value built from the test's input fixtures with NO Phase-12-specific fields injected.
    - tests/test_critic_reject_producer.py::test_pk_uses_publish_id_and_subtopic_id_in_scope — capture the put_item Item for CRITIC_REJECT#; assert `Item["PK"].startswith(f"CRITIC_REJECT#{expected_publish_id}#{expected_subtopic_id}#")` AND `Item["PK"].count("#") == 3`. Demonstrates D-08 PK shape using values already in scope (no cwid parameter threaded).
    - tests/test_critic_reject_producer.py::test_author_cwids_derived_from_selected_papers — feed selected_papers with first_author.person_identifier ∈ {"a","b"} and last_author.person_identifier ∈ {"b","c"} across the rejected pmid_set; assert captured Item["author_cwids"] == ["a","b","c"] (sorted, deduped). Empty / None person_identifiers are silently dropped.
    - tests/test_critic_reject_producer.py::test_post_llm_known_constraint_passes_through — last_llm_verdict.failed_constraint="institutional_voice"; resulting CRITIC_REJECT# row has reason_code="institutional_voice", raw_failed_constraint NOT present, pre_llm_constraint NOT present
    - tests/test_critic_reject_producer.py::test_post_llm_unknown_constraint_emits_warning_and_unknown_bucket — last_llm_verdict.failed_constraint="some_new_code_we_havent_seen"; resulting row has reason_code="unknown" + raw_failed_constraint="some_new_code_we_havent_seen"; assert pipeline_common.alert was called once with severity="warning"
    - tests/test_critic_reject_producer.py::test_pre_llm_gate_path — simulate path where last_llm_verdict is None and last_deterministic_verdict.failed_constraints == ["active_verb"]; resulting row has reason_code="pre_llm_gate" + pre_llm_constraint="active_verb"; raw_failed_constraint NOT present
    - tests/test_critic_reject_producer.py::test_pre_llm_gate_no_deterministic_detail — last_llm_verdict is None and deterministic verdict has empty failed_constraints; row still written with reason_code="pre_llm_gate" + pre_llm_constraint="unknown"
    - tests/test_critic_reject_producer.py::test_critic_reject_carries_no_lede — captured Item dict for the CRITIC_REJECT# put_item call contains neither "lede_text" key nor any key containing the substring "lede"
    - tests/test_critic_reject_producer.py::test_review_entry_still_carries_lede — existing SPOTLIGHT_REVIEW# write_review_entry call STILL receives lede_text in its review_entry payload (no regression — only the additive CRITIC_REJECT# write is new)
    - tests/test_critic_reject_producer.py::test_passing_critic_does_not_write_critic_reject — when the critic eventually passes within MAX_RETRIES, neither SPOTLIGHT_REVIEW# nor CRITIC_REJECT# is written
  </behavior>
  <action>
    In `spotlight/critic.py`:

    1. Immediately after the `LLMVerdict` dataclass definition (around line 205), add the StrEnum:

    ```python
    from enum import StrEnum

    class CritReasonCode(StrEnum):
        """Closed vocabulary for CRITIC_REJECT#.reason_code (Phase 12 D-31).

        Values match the LLM critic prompt's `failed_constraint` output verbatim
        so SPOTLIGHT_REVIEW# rows (carrying critic_verdict.failed_constraint)
        and CRITIC_REJECT# rows (carrying reason_code) speak the same vocabulary.

        PRE_LLM_GATE is reserved for deterministic-gate failures that never
        reached the LLM; the specific deterministic code is preserved separately
        in the row's `pre_llm_constraint` field.
        """

        ACTIVE_VERB = "active_verb"
        ANCHORED_IN_SYNOPSES = "anchored_in_synopses"
        NO_FACULTY_NAMED = "no_faculty_named"
        INSTITUTIONAL_VOICE = "institutional_voice"
        PRE_LLM_GATE = "pre_llm_gate"
    ```

    2. Locate the existing exhaustion-path block (anchor: the line `write_review_entry(dynamo_client, review_entry)`). Add — AFTER that existing call (additive, never replacing) — a CRITIC_REJECT# write. **No cwid parameter needs to be added to `run_critic_loop`** — per CONTEXT D-08 re-framing, the PK is per-(publish_id, subtopic_id, pmid_set), and `publish_id`, `meta.subtopic_id`, and `selected_papers` are already in scope at this site. The earlier draft's W-4 "cwid threading hazard" is resolved by the architecture change, not by adding the parameter.

    ```python
    write_review_entry(dynamo_client, review_entry)   # EXISTING — UNCHANGED

    # Phase 12 D-08: additive CRITIC_REJECT# write for per-(publish,subtopic,pmid_set) aggregation
    from utils.event_records import write_critic_reject
    from pipeline_common.alert import alert

    if last_llm_verdict is not None:
        raw = last_llm_verdict.failed_constraint
        try:
            reason_code = CritReasonCode(raw).value
            extra: dict[str, str] = {}
        except ValueError:
            reason_code = "unknown"
            extra = {"raw_failed_constraint": raw}
            alert(
                f"CRITIC_REJECT# vocabulary drift: LLM returned '{raw}' "
                f"which is not in CritReasonCode. Row written with reason_code='unknown'.",
                severity="warning",
            )
    else:
        # Deterministic-only failure — never reached the LLM
        pre_llm = "unknown"
        if last_deterministic_verdict is not None and last_deterministic_verdict.failed_constraints:
            pre_llm = last_deterministic_verdict.failed_constraints[0]
        reason_code = CritReasonCode.PRE_LLM_GATE.value
        extra = {"pre_llm_constraint": pre_llm}

    # author_cwids derivation (D-08 + D-32): distinct first/last-author person_identifiers across selected_papers
    # See spotlight/types.py:39-57 for Paper/Author shape.
    author_cwids = sorted(
        {p.first_author.person_identifier for p in selected_papers
         if p.first_author and p.first_author.person_identifier}
        | {p.last_author.person_identifier for p in selected_papers
           if p.last_author and p.last_author.person_identifier}
    )

    write_critic_reject(
        dynamo_client,
        publish_id=publish_id,
        subtopic_id=meta.subtopic_id,
        pmids=[p.pmid for p in selected_papers],
        author_cwids=author_cwids,
        reason_code=reason_code,
        regen_count=MAX_RETRIES,
        reason=(last_llm_verdict.reason if last_llm_verdict is not None else ""),
        **extra,
    )
    ```

    3. **Variable-name verification (do this in read_first, do NOT guess):** confirm the exact identifiers used in the existing exhaustion block. The CONTEXT canonical_refs line for `spotlight/critic.py:571` names `publish_id`, `meta.subtopic_id`, and `selected_papers`. If the actual local names differ (e.g. `pub_id`, `current_meta`, `candidate_papers`), use the actual names and add a one-line comment noting the rename. **Do NOT invent a variable. Do NOT thread a cwid parameter.** If `publish_id` is genuinely not in scope (canonical_refs is wrong), surface this as a CHECKPOINT — it would invalidate the D-08 re-framing premise and need orchestrator-level resolution.

    4. If `last_deterministic_verdict` is not currently captured in the regen loop, add the minimal change needed to track it: store the last DeterministicVerdict.failed_constraints alongside last_llm_verdict so the pre-LLM-gate path has the data it needs.

    Write `tests/test_critic_reject_producer.py` using the analog from PATTERNS.md "Gate test with injected violation" pattern — build a known-failing critic state, run the function-under-test, capture put_item args. Mock `dynamo_client` (or whatever the existing param is named), patch `pipeline_common.alert.alert` to capture severity calls. Use the `MagicMock` table pattern from `tests/test_event_records.py`.

    The `test_review_queue_write_unchanged` test is the explicit guarantee the additive change is in fact additive. Build a synthetic `review_entry` golden value from the test's input fixtures and assert `write_review_entry.call_args[0][1] == expected_review_entry` byte-for-byte (or via dict equality).

    Commit message: `feat(12-feedback): add CritReasonCode enum + CRITIC_REJECT# dual-write at critic exhaustion (per-publish/per-subtopic key + author_cwids body)`.
  </action>
  <verify>
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && pytest tests/test_critic_reject_producer.py tests/test_critic_reject_event.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "class CritReasonCode" spotlight/critic.py` returns 1
    - `grep -E 'ACTIVE_VERB = "active_verb"|ANCHORED_IN_SYNOPSES = "anchored_in_synopses"|NO_FACULTY_NAMED = "no_faculty_named"|INSTITUTIONAL_VOICE = "institutional_voice"|PRE_LLM_GATE = "pre_llm_gate"' spotlight/critic.py | wc -l` returns 5 (all five enum members with exact spelling)
    - `grep -c "write_critic_reject" spotlight/critic.py` returns count >= 1
    - `grep -c "write_review_entry" spotlight/critic.py` returns count >= 1 (existing call preserved, not replaced)
    - `grep -c "author_cwids" spotlight/critic.py` returns count >= 1 (derivation site)
    - `grep -c "publish_id" spotlight/critic.py` returns count >= 1 (used at the write site; not introduced as a new parameter)
    - `python -c "from spotlight.critic import CritReasonCode; assert CritReasonCode('active_verb') == CritReasonCode.ACTIVE_VERB; assert CritReasonCode('pre_llm_gate') == CritReasonCode.PRE_LLM_GATE"` exits 0
    - `python -c "from spotlight.critic import CritReasonCode; from enum import StrEnum; assert issubclass(CritReasonCode, StrEnum)"` exits 0
    - `python -c "from spotlight.critic import CritReasonCode; assert len(list(CritReasonCode)) == 5"` exits 0
    - `pytest tests/test_critic_reject_producer.py -x` exits 0
    - `pytest spotlight/ tests/test_critic_reject_event.py tests/test_critic_reject_producer.py -x` exits 0 (no regression in spotlight test suite)
  </acceptance_criteria>
  <done>CritReasonCode StrEnum exists at exact spelling; CRITIC_REJECT# write co-exists with the unchanged SPOTLIGHT_REVIEW# write; PK is per-(publish_id, subtopic_id, pmid_set_hash) using values already in scope (no cwid threading); author_cwids derived at the write site; vocabulary drift emits a warning + 'unknown' bucket without crashing; pre-LLM gate path is handled; PII boundary respected.</done>
</task>

</tasks>

<verification>
- Both tasks' acceptance criteria green
- `pytest tests/test_critic_reject_event.py tests/test_critic_reject_producer.py -x` exits 0
- `grep -c "lede" utils/event_records.py` returns 0 (PII boundary)
- `grep -c "write_review_entry" spotlight/critic.py` returns count >= 1 (SPOTLIGHT_REVIEW# write preserved)
- `python -c "from utils.event_records import build_critic_reject_record; r=build_critic_reject_record(publish_id='p',subtopic_id='s',pmids=['1'],author_cwids=['c'],reason_code='active_verb',regen_count=1); assert r['PK'].count('#')==3"` exits 0 (PK shape verified per D-08)
- No regression: `pytest tests/test_event_records.py tests/test_uncovered_pmid_event.py tests/test_low_confidence_event.py -x` exits 0
</verification>

<success_criteria>
The spec §9 producer exists with the D-08 re-framed shape: PK is per-(publish_id, subtopic_id, pmid_set_hash); author_cwids in body preserves per-faculty drill-down for D-32. Every critic exhaustion writes both SPOTLIGHT_REVIEW# (queue) and CRITIC_REJECT# (event). The wave-2 feedback consumer's SPOTLIGHT_DIAGNOSTIC#{subtopic_id} aggregator now has a source of truth to read from. The earlier W-4 cwid-threading hazard is resolved by architecture (the parameter doesn't need to be threaded), not by adding the threaded parameter.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-feedback-producer-SUMMARY.md`.
</output>
