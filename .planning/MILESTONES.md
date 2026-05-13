# Milestones

## v1.0 — SPS-Feeding Service (Shipped: 2026-05-13)

**Goal:** ReciterAI fully owns the production of all data SPS consumes — no logic that produces ReciterAI artifacts lives in SPS.

**Phases shipped:** 7, 9, 10, 11, 12 (Phase 8 — Tools / Axis 2 — intentionally deferred, blocked on decision-deferred issues #5/#6/#7/#8).

**Key accomplishments:**

- **Phase 7 — Hierarchy Publisher.** `pipeline_hierarchy/` package owns artifact production + S3 publish to `s3://wcmc-reciterai-hierarchy/{version,latest}/`. SPS stopgap deleted; SPS issue #180 closed.
- **Phase 9 — Substrate: Stages & Gates.** Content-addressed `STAGE#` records (`utils/stage_records.py`) and a registered quality-gate framework (`gates/registry.py` with `parent_prefix`, `pii_scan`, `schema_validation`, `schema_roundtrip`) plus `python -m gates` CLI. `pipeline_hierarchy.publish` wired to both substrates.
- **Phase 10 — Hot/Cold Path Split.** Cold path (`pipeline_cold.run`) walks score → assign → discover → relabel → count → rollup → feedback_sweep → backfill_spotlight → publish_hierarchy with a single `STAGE#cold_run#GLOBAL` umbrella row stamped with `initiated_by`.
- **Phase 11 — Versioning, Review State, Diff Signaling.** `hierarchy_version` stamped on every read/write; `REVIEW#` rows as machine-readable cold-path gate (`python -m review approve` CLI with pre-write validator); rotation state keyed by `(cwid, hierarchy_version)`; `STAGE#hierarchy_version_cutover#GLOBAL` written on every cold cutover; `diff.json` shipped alongside `hierarchy.json` per version. UAT-1/UAT-2/UAT-3 all passed end-to-end against live DDB + S3 (v2026-05-13 published).
- **Phase 12 — Feedback Loops, Both Aggregations, Residual Hygiene.** Both aggregation contracts (exclusive + inclusive `faculty_subtopic_counts_*.csv`), feedback consumer (Sonnet sweep over UNCOVERED_PMID# / LOW_CONFIDENCE_ASSIGNMENT# / CRITIC_REJECT# events), feedback producer (`CritReasonCode` StrEnum + `CRITIC_REJECT#{publish_id}#{subtopic_id}#{pmid_set_hash}` dual-write at critic exhaustion), drift evaluator extension, thresholds substrate, hierarchy reproducibility, and the bounded G-37 cold-path E2E test.

**Pre-existing cold-path defects fixed during UAT-3 (none from Phase 11 work itself):**

- `backfill_topic` review-gate rejected `auto_approved` drafts (Phase 4 legacy) — commit `8022ab8`
- `assign_subtopics` PEP-562 `__getattr__` NameError on in-module bare-name access (Phase 12 WR-02 regression) — commit `58c2510`
- `pipeline_cold` didn't pass `--skip-all-reviews` to backfill_all (Phase 10 omission) — commit `aba4975`
- `discover` + `relabel` cold-stages referenced non-existent CLI flags (Phase 10 dead scaffolding) — commits `bb7ca87` + `02fbfc9`
- `count_by_cwid` stage missing before rollup (Phase 12 D-13 omission) — commit `bb90ce7`
- `pipeline_feedback.sweep` hardcoded invalid Bedrock model id (Phase 12) — commit `d1c9b3d`

**Known deferred items at close:** 2 forward-looking Phase 10 design questions (Slack channel + env-var name, Bedrock Batch wait mechanism) — both for the not-yet-built hot-path Step Functions wiring; documented in `STATE.md` Deferred Items.

**Follow-up issues opened:** #15 (rolling synopsis + impact-score generation for new publications).

**Tag:** `v1.0`

---
