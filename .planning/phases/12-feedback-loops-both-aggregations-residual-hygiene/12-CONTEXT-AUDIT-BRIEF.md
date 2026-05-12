---
phase: 12
slug: feedback-loops-both-aggregations-residual-hygiene
created: 2026-05-12
purpose: One-pass CONTEXT.md vs code reconciliation, post-CR-2026-05-12
status: brief (audit pending)
---

# CONTEXT Audit Brief — Phase 12

## Why this audit exists

Phase 12 planning surfaced five conflicts between CONTEXT.md and code in a single round (F-1 partitions, F-2 drift schema, F-3 reason codes, F-4 confidence floor, W-4 cwid keying). The pattern: CONTEXT was authored from spec analysis, not code inspection. Single-decision corrections compound — each patch destabilizes neighboring decisions that may carry the same misframing.

Rather than continue the "react as conflicts surface" pattern, this audit verifies every CONTEXT decision against actual code/config in one coherent pass, BEFORE plan revision resumes.

This brief is committed as a precedent for how subsequent phases handle CONTEXT audits. Future phases facing the same "did we audit CONTEXT, what did we check, what did we miss" question can read this file instead of reconstructing institutional memory from chat history.

## Audit scope

Every locked decision D-01..D-34 in `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md`.

Also: every claim in the `<canonical_refs>`, `<code_context>`, `<specifics>`, and `<deferred>` blocks. CONTEXT claims aren't limited to the `<decisions>` block.

## Categorization taxonomy

Each CONTEXT claim must be tagged with exactly one:

| Category | Definition |
|----------|-----------|
| **VERIFIED** | Claim accurately describes code/config state as of audit date. Include file:line citation in the audit output. |
| **DRIFT** | Claim describes a real concept but the specifics (numbers, line numbers, file paths, function shapes) are wrong. Audit output shows CONTEXT claim verbatim AND the actual code reality with file:line. |
| **SPECULATIVE** | Claim describes a workflow, pattern, or behavior not grounded in current code — typically inherited from spec or gap analysis without code verification. Audit output traces the original source if locatable (spec section, gap number, prior CONTEXT round). |
| **ARCHITECTURAL MISMATCH** | Claim assumes an architecture that doesn't match the code (like D-08's per-cwid keying against a per-subtopic critic loop). Distinct from DRIFT in that no factual correction makes the claim consistent — the framing itself needs revisiting. |
| **UNVERIFIABLE** | Claim depends on consumer behavior outside this repo (e.g., "SPS reads X", "faculty profile pages aggregate Y"). Cannot be confirmed or refuted from code inspection alone. Flag for external confirmation. |

## Required output format

`CONTEXT-AUDIT.md` in the phase directory, structured as one section per CONTEXT claim:

```markdown
### D-NN — [short claim summary]

**Category:** VERIFIED | DRIFT | SPECULATIVE | ARCHITECTURAL MISMATCH | UNVERIFIABLE

**Source-of-claim:** spec §X | G-NN | code inspection 2026-05-12 | prior CONTEXT round YYYY-MM-DD | Claude's discretion | UNTRACED (best guess: ...)

**Evidence:**
- [For VERIFIED: file:line citation showing the claim matches code/config.]
- [For DRIFT: CONTEXT claim verbatim; actual code reality with file:line; the delta.]
- [For SPECULATIVE: trace the framing to its origin if locatable; describe what code reality is instead.]
- [For ARCHITECTURAL MISMATCH: what CONTEXT assumes; what the code actually does; why no simple correction reconciles them.]
- [For UNVERIFIABLE: what's claimed; what would need to be inspected outside this repo to confirm.]

**Recommended action:** [VERIFIED — keep as-is | DRIFT — specific correction | SPECULATIVE — replace with code-grounded claim | ARCHITECTURAL MISMATCH — re-frame; planner cannot decide locally | UNVERIFIABLE — flag for operator confirmation]
```

**Required: every entry carries source-of-claim, not just SPECULATIVE ones.** Knowing the source of a VERIFIED entry lets future spot-checks happen quickly — they re-read the cited file:line rather than re-doing the audit.

## Numeric-value verification (explicit mechanical step)

For every numeric value cited in CONTEXT (thresholds, line numbers, counts, percentages, version strings):

1. Locate the value in CONTEXT (grep for digits).
2. Find the corresponding code/config location (CONTEXT usually cites file:line — verify both that the file exists and that the line carries the cited value).
3. Categorize:
   - Same value, same location → VERIFIED.
   - Different value, same location → DRIFT (cite both values).
   - Same value, different/missing location → DRIFT (cite correct location).
   - Value not findable in any code path → SPECULATIVE.

Known F-4 example for calibration: CONTEXT D-23 says `assign_subtopics.py:1022 DEFAULT_CONFIDENCE_FLOOR, value 0.35`. Reality: `assign_subtopics.py:95 DEFAULT_CONFIDENCE_FLOOR = 0.3`; line 1022 is the CLI default reader. Audit output for D-23: DRIFT, both value (0.35 → 0.3) and line (1022 → 95). This pattern is what the numeric-value verification step is designed to catch systematically.

## Required reading

- `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md` — the audit target (34 decisions + supporting blocks)
- `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md` — already surfaces F-1..F-7 with code citations; cross-reference but don't trust uncritically
- `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md` — file/analog mappings with code excerpts; useful for verifying PATTERNS-claims that also appear in CONTEXT
- Every code file CONTEXT cites in `<canonical_refs>` and `<code_context>` blocks
- `docs/RECITERAI-SPEC.md` §8, §9, §11 — to distinguish "CONTEXT says X because spec says X" from "CONTEXT says X based on prior round's speculation"

## Constraints

- **Do NOT correct CONTEXT.md.** Output is an audit report only. Corrections are applied in a separate revision step by the orchestrator after this audit lands. Single-decision corrections are exactly what this audit is designed to replace.
- **Do NOT spawn nested subagents.** This is a focused read-and-report task.
- **Read CONTEXT.md as text, not as remembered substance.** Treat the document as if you've never read it before. The whole point of a fresh subagent is to escape assumptions the planning context has accumulated.
- **When citing code, always include file:line.** "I think it's around line 500" is not acceptable; either find it exactly or mark the claim UNVERIFIABLE.

## Known conflicts already surfaced (cross-check these are caught)

The audit must, at minimum, surface these as DRIFT/SPECULATIVE/ARCHITECTURAL MISMATCH:

| CONTEXT entry | Known issue | Expected category |
|---------------|-------------|-------------------|
| D-08 | `CRITIC_REJECT#{cwid}#{pmid_set_hash}` keying — critic loop is per-subtopic, no cwid available; `SubtopicMeta` carries no cwid; `publish_id = "v{date}"` has no cwid | ARCHITECTURAL MISMATCH |
| D-11 | `critic_reject_cwid_max` tunable name — misnamed if keying becomes per-subtopic | DRIFT (name-level) |
| D-23 | Already corrected post-CR for `confidence_floor` value (0.35 → 0.3) and line (1022 → 95) — verify the corrected text is accurate | VERIFIED (after CR) |
| D-25 | Already corrected post-CR ("both 0.35" → distinct values 0.3 / 0.35) — verify | VERIFIED (after CR) |
| D-32 | `underlying_rejects` field carrying `{cwid}#{pmid_set_hash}` PK suffixes — depends on D-08 resolution | downstream of D-08 |
| `<code_context>` drift-evaluator line | Already corrected post-CR (replaced prohibition with additive-extension permission) — verify wording is precise | VERIFIED (after CR) |

If the audit doesn't surface D-08 as ARCHITECTURAL MISMATCH independently, the audit itself failed (this is a deliberate test of whether the subagent is reading code rather than echoing CONTEXT).

## What "done" looks like

`CONTEXT-AUDIT.md` exists in the phase directory with:

- Every D-01..D-34 categorized.
- Every claim in `<canonical_refs>`, `<code_context>`, `<specifics>`, `<deferred>` categorized.
- Source-of-claim noted for every entry.
- Numeric-value verification table (every cited number → code location → status).
- A summary section: count by category; list of UNVERIFIABLE entries that need operator-side confirmation; list of ARCHITECTURAL MISMATCHES that need re-framing (D-08 expected, others possible).

The audit does NOT fix CONTEXT. It produces the input needed for a single coherent CONTEXT revision round that follows.

## Subsequent steps (out of scope for the audit)

1. Orchestrator reads CONTEXT-AUDIT.md.
2. Orchestrator applies all corrections in a single edit batch, adding **Source:** inline tags to every D-NN.
3. Orchestrator re-derives `critic_reject_persistence_days` and (renamed) `critic_reject_subtopic_max` for per-subtopic semantics.
4. Orchestrator sends a single revision prompt to the planner covering all plan-checker findings + D-08 keying ripple.

---

*Brief committed before audit subagent runs, per "make the brief queryable" precedent.*
