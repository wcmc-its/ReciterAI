# Phase 5: Hierarchy Publishing Contract - Context

**Gathered:** 2026-05-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Establish `hierarchy.json` as a versioned, schema-validated, contract-documented S3 artifact that any current or future downstream consumer (SPS, PM, future analytics) integrates against — replacing the current ad-hoc cross-repo file-copy pattern that caused the SPS data-path mismatch (SPS expected hierarchy structure in DynamoDB; the upstream pipeline only ever wrote it as a co-located JSON file in PM's repo).

**In scope:**
- `hierarchy.schema.json` — JSON Schema generated from `hierarchy-schema.md` (D-19), co-published with every artifact upload.
- `docs/hierarchy-contract.md` — single consumer-facing contract document covering URL pattern, schema location, publish cadence, integration pattern, breaking-change policy, CHANGELOG.
- `backfill_all.py --publish` — schema-validate + S3 PutObject to `s3://wcmc-reciterai-hierarchy/v{ISO-date}/` and `latest/`; writes `manifest.json` carrying version, taxonomy_version, schema_version, sha256(hierarchy.json), generated_at; fails build on validation errors. ALSO retains the existing PM-worktree `shutil-copy` step (backwards-compat).
- `docs/sps-integration-handoff.md` + reference fetch script — architecture brief and working reference implementation SPS coding agent can adapt.
- STATE.md and `hierarchy-schema.md` D-19 cross-references to the new contract doc.

**Out of scope (deferred to other phases or other repos):**
- SPS-side ETL implementation (separate session in `wcmc-its/Scholars-Profile-System`).
- PM migration off the worktree-file pattern (deferred — current copy-via-`backfill_all.py` keeps working; phase boundary explicitly preserves this).
- DynamoDB load step for hierarchy data (rejected — fights the 400KB DDB item limit; the artifact pattern solves the consumer problem more cleanly without forcing a partition redesign).
- CloudFront / public-read access (rejected — IAM-gated is sufficient and avoids the institutional-data classification question).
- GH Actions or Lambda automation for publish (rejected for this phase — annual + ad-hoc cadence is fine to run from operator's machine; can revisit in a future infra phase).
- Slack/email notifications on publish (rejected — manifest.json sha256 is sufficient signal for one current consumer).

</domain>

<decisions>
## Implementation Decisions

### S3 substrate & access
- **D-01 (Bucket):** Bucket name is `wcmc-reciterai-hierarchy` in `us-east-1` (co-located with the existing DynamoDB `reciterai-chatbot` table and Bedrock calls per `BedrockClient` defaults). Matches `wcmc-its/*` repo naming convention.
- **D-02 (Access model):** IAM-gated private bucket. Each consumer (SPS Lambda, future apps) gets a tightly-scoped IAM role with `GetObject` on this bucket prefix. No public-read, no CloudFront. Avoids the institutional-data classification question entirely.
- **D-03 (Provisioning):** User (Paul) provisions the bucket via AWS console. Pipeline IAM role gains an inline `PutObject` policy on the bucket. Plan should produce the exact IAM JSON snippet so the console paste-step is unambiguous.

### Versioning scheme
- **D-04 (Version prefix):** `v{ISO-date}/` — e.g. `v2026-05-06/`. Human-readable, lexicographically sortable, captures publish moment. Handles ad-hoc re-publishes (today's relabel pass is a real example) cleanly because every publish gets a unique prefix. Does NOT incorporate `taxonomy_version` in the prefix path; semantic version info lives in `manifest.json` instead.
- **D-05 (Latest pointer):** `latest/hierarchy.json`, `latest/hierarchy.schema.json`, `latest/manifest.json` are PutObject-overwritten on every publish. Simple, atomic-per-object. Brief consumer-fetch race window is acceptable at the phase's actual cadence (annual + occasional ad-hoc; nothing realistic fetches mid-second).
- **D-06 (Retention):** Keep all `v{date}/` versions indefinitely. ~13 MB/year storage cost is negligible. Full historical rollback. Don't add S3 Lifecycle rules in this phase — premature.
- **D-07 (Schema versioning):** `hierarchy.schema.json` carries its OWN `schema_version` semver (e.g. `"1.0.0"`), independent of `taxonomy_version` and the publish date. `manifest.json` surfaces all three so consumers can detect "schema didn't change, only data did" vs "schema bumped — review your validator." Bump rules: additive=minor, breaking=major.

### Contract scope & promises
- **D-08 (Cadence promise):** Contract guarantees an annual full recompute. Ad-hoc re-publishes (label fixes, contract revisions, schema additions) are explicitly permitted and treated as first-class. Consumers should poll `latest/manifest.json` on at least a weekly cadence and react to `sha256(hierarchy.json)` changes.
- **D-09 (Deprecation window):** 30 days advance notice for breaking schema changes. Aligns with SPS's existing 30-day schema-change protocol (`SPS/CLAUDE.md` design-spec policy). Schema changes get queued in CHANGELOG with a target activation date and the new schema is published at the existing version until the window elapses.
- **D-10 (Schema-change communication):** A `## Changelog` section in `docs/hierarchy-contract.md` is the canonical record. Every schema change has an entry: date, schema_version delta, additive-vs-breaking, migration notes if breaking. Consumers watch the CHANGELOG and the `schema_version` field in `manifest.json`. No email, no Slack — `manifest.json` sha256 is the trigger; CHANGELOG is the explanation.
- **D-11 (Backwards-compatibility default):** **Additive fields are always non-breaking.** Adding a new field (as `display_name` was added in D-19) is a minor schema bump. Consumers MUST tolerate unknown fields silently. Codifies the precedent that just played out and makes future additions risk-free.

### Operational integration
- **D-12 (Notification mechanism):** None beyond CloudWatch logs and `manifest.json`. Pipeline emits a structured log line on each publish (version, schema_version, sha256, manifest path). Consumers polling `latest/manifest.json` detect changes via sha256 mismatch. No Slack webhook, no email distribution. Reasonable to add Slack later if a second consumer surfaces and human heads-up becomes valuable.
- **D-13 (PM worktree copy retention):** `--publish` does BOTH: PutObject to S3 AND `shutil-copy` to `ReCiter-Publication-Manager/controllers/chatbot/hierarchy.json` on the existing `feature/chatbot-runtime` branch. PM keeps reading from disk per its current pattern. Zero risk to PM today; PM migration to S3-fetch is a future-deferred phase. The publish step does NOT have a flag to disable the PM copy in this phase — keeping it always-on simplifies operator mental model.
- **D-14 (SPS handoff depth):** Phase produces `docs/sps-integration-handoff.md` (architecture brief — fetch latest/manifest.json, sha256 compare, GetObject hierarchy.json + schema, validate, project to MySQL Subtopic table) PLUS a working reference fetch script (`docs/sps-etl-reference.py` or `.ts` — pick the language that matches SPS's ETL Lambda runtime). SPS coding agent adapts the reference; doesn't have to architect from scratch. Phase does NOT open a draft PR in the SPS repo.
- **D-15 (Run environment):** Publish step runs locally on the operator's machine, same place `backfill_all.py` runs today. Uses existing AWS creds via `~/.zshrc` shell init. Zero new infra, no GitHub Actions secrets, no Lambda. Right answer for an annual-cadence-with-ad-hoc-re-publishes pattern with one current operator.

### Schema-validation strictness (locked from prior conversation)
- **D-16 (Validation strictness):** `--publish` MUST fail and refuse to upload if `hierarchy_full.json` does not validate against `hierarchy.schema.json`. No "warn and publish anyway" mode. The whole point of co-publishing the schema is to make schema drift impossible; a soft-fail mode would defeat that.

### Claude's Discretion
- JSON Schema library choice (Python `jsonschema` is the default; researcher should confirm).
- Exact path of `hierarchy.schema.json` within this repo's tree (`docs/hierarchy.schema.json` is the obvious candidate).
- Manifest field ordering and any additional metadata fields beyond the locked-in core (version/taxonomy_version/schema_version/sha256/generated_at).
- Whether `manifest.json` includes a `previous_version` back-pointer for changelog discoverability — researcher to recommend.
- Idempotency mechanism on re-runs of the same publish-date (overwrite is acceptable; abort-if-exists is also defensible — researcher to pick).
- Specific reference-script language (Python vs TypeScript) — match SPS's ETL Lambda runtime; researcher to check.
- IAM JSON snippet shape — researcher to produce `docs/aws-bucket-policy.json` and the inline PutObject policy snippet for paste-into-console.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This phase's authoritative schema source
- `.planning/phases/04-subtopic-system/hierarchy-schema.md` — Source of truth for the SubtopicDef shape, top-level HierarchyJson interface, and all D-rules including D-19 (display_name / short_description). The JSON Schema generated in this phase MUST match this document character-for-character; the document remains canonical even after the schema ships.

### Phase 5's downstream-consumer brief (already drafted)
- `.planning/phases/04-subtopic-system/sps-integration-brief.md` — The SPS-side rendering brief written 2026-05-06. Phase 5's `docs/sps-integration-handoff.md` extends this with the ETL-fetch architecture and reference script. The rendering rules in the existing brief carry forward unchanged.

### Phase 4 D-decisions that constrain this phase
- `.planning/phases/04-subtopic-system/04-CONTEXT.md` — Phase 4 implementation decisions. Specifically: D-15 (`label`/`description` are synthesis-injected verbatim, must remain required strings in the schema); D-06 (subtopic IDs unstable across recomputes — contract MUST tell consumers not to persist them); pipeline architecture (three-pass cadence) that produces the artifact this phase publishes.

### Existing pipeline code that the publish step extends
- `backfill_all.py` — Specifically the `--assemble-only` path added 2026-05-06 (lines around `_run_assemble_only()`), the `_assemble_hierarchy()` helper, the `_write_hierarchy_full()` helper, the `_copy_to_pm()` helper. The `--publish` flag adds a step that runs after `_write_hierarchy_full()`.
- `prompts/subtopic_discovery.py` — Reference for the existing prompt-module pattern; `hierarchy.schema.json` ships with similar "import-safe, no side effects" discipline.
- `utils/bedrock_client.py` — Existing AWS-credentials pattern (lazy boto3 init, `~/.zshrc`-sourced creds). The S3 client should follow the same lazy-init convention.

### Project-level
- `.planning/PROJECT.md` — Project constraints (Bedrock-only, AWS reside in library account, etc.).
- `.planning/REQUIREMENTS.md` — v1 requirements; this phase will add HPC-* requirements during planning.
- `CLAUDE.md` (project root) — Naming/security/workflow rules. Specifically: never hardcode credentials, never read `~/.zshrc`, AWS via env vars; commits never include AI attribution.

### SPS-side context (read-only reference for this phase)
- `~/Dropbox/GitHub/Scholars-Profile-System/CLAUDE.md` — SPS tech stack, ADR-006 (LOCKED runtime: MySQL only, no runtime DDB read path), schema-change protocol (30-day notice).
- `~/Dropbox/GitHub/Scholars-Profile-System/docs/ADR-001-runtime-dal-vs-etl-transform.md` — Documents the ETL-transform-layer pattern that `docs/sps-integration-handoff.md` integrates with.
- `~/Dropbox/GitHub/Scholars-Profile-System/.planning/PROJECT.md` § Key Decisions — ADR-006 row gives the canonical rationale for the MySQL-only runtime that the SPS ETL must respect.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`backfill_all.py:_assemble_hierarchy()` and `_write_hierarchy_full()`** — Already build and write `hierarchy_full.json`. The publish step extends the post-write path; no rework of assembly logic.
- **`backfill_all.py:_copy_to_pm()`** — Already implements the PM worktree copy. D-13 keeps this in the publish flow unchanged.
- **`utils/bedrock_client.py` (lazy boto3 init pattern)** — Reference for the publish step's S3 client. Same `__init__` posture: no AWS calls at import time, lazy client creation, `~/.zshrc`-sourced creds.
- **Existing `manifest.json`-style writes in the codebase** — None currently; researcher should confirm. The publish step is the first manifest-style artifact.

### Established Patterns
- **JSON-only artifacts written via `json.dump(..., indent=2, ensure_ascii=False)`** — Established convention in `_write_hierarchy_full()`. Schema and manifest follow the same pattern.
- **Argparse flag style on CLI scripts** — `backfill_all.py` already has `--assemble-only`, `--skip-pm-copy`, `--continue-on-error`, `--dry-run`. `--publish` slots in alongside; researcher should confirm interaction with `--assemble-only` (probably composable: `--assemble-only --publish` re-assembles AND publishes).
- **Verdict-gate bypass** — `--assemble-only` already bypasses the D-20 verdict gate because it's a non-LLM operation. `--publish` should do the same; it's also non-LLM.

### Integration Points
- **`backfill_all.py:run()`** — The `_run_assemble_only()` function is where `--publish` integrates. Sequence: assemble → validate against schema → S3 PutObject → PM worktree copy → exit.
- **AWS IAM role the pipeline runs under** — Currently has DDB + Bedrock permissions. Phase 5 adds S3:PutObject scoped to `arn:aws:s3:::wcmc-reciterai-hierarchy/*`. Plan should produce the exact JSON snippet.
- **`docs/` directory at the repo root** — Currently does not exist. Phase 5 creates it. Future-proofing question for the researcher: is there a `docs/` discipline already in PM or other sister repos worth mirroring?

</code_context>

<specifics>
## Specific Ideas

- **The contract document MUST read like a published API contract.** Section structure (suggested by user's recommendation acceptance): URL pattern, schema, cadence, breaking-change policy, CHANGELOG, integration pattern, FAQ. Future agents should be able to read just this one file and integrate without conversation.
- **The reference fetch script in `docs/sps-etl-reference.{py,ts}` MUST be runnable as-shipped** — not pseudocode. Goal: SPS coding agent copies it, swaps in their MySQL connection, adds their schema-mapping, ships. If the fetch logic is pseudocode the handoff fails.
- **`manifest.json` field ordering should be stable and deterministic.** Consumers may compute their own sha256 over the manifest for change detection; field-order drift would produce false positives.
- **Today's relabel pass is the reference example** for "what does an ad-hoc re-publish look like." The CHANGELOG entry for the FIRST publish should describe the relabel as the inaugural data-only re-publish event.

</specifics>

<deferred>
## Deferred Ideas

These came up in the course of designing Phase 5 but explicitly belong in other phases:

- **PM migration off the worktree-file pattern.** PM currently `import`s `hierarchy.json` from disk via the chatbot runtime. A future phase migrates PM to fetch from S3 at build time. Out of scope for Phase 5 per ROADMAP. Phase 5's D-13 keeps the worktree copy alive specifically to preserve PM's current pattern.
- **SPS-side ETL implementation.** Phase 5 produces the handoff brief and reference script; the actual ETL Lambda lives in `wcmc-its/Scholars-Profile-System`. Will be picked up in a separate session in that repo, after Phase 5 ships.
- **DynamoDB hierarchy load step (rejected).** Considered as Option A in the architecture discussion. Rejected because (a) hierarchy_full.json is 1.1 MB, exceeds DDB single-item limit; (b) artifact pattern solves the consumer problem more cleanly; (c) adds infrastructure for a problem that doesn't exist. Document in `docs/hierarchy-contract.md` FAQ so future engineers don't re-litigate.
- **Multi-environment artifacts (dev/staging/prod prefixes).** Currently a single bucket with a single set of versions. If Phase 5+ ever needs separate dev/prod hierarchy artifacts, that's a future scope expansion. Keep mental note; don't build for it now.
- **CloudFront / public-read access.** Rejected for Phase 5; revisit if a non-IAM-authenticated consumer surfaces (unlikely given WCM-internal usage).
- **GitHub Actions or Lambda automation for publish.** Rejected for Phase 5; revisit if multiple operators need to publish or annual cadence shifts to weekly+.
- **Slack/email publish notifications.** Rejected for Phase 5; revisit if/when a second consumer surfaces and human heads-up becomes valuable.
- **Schema-change-triggered consumer notification.** Currently CHANGELOG + manifest sha256 only. If a future consumer needs push notification on schema bumps, that's a future scope.

</deferred>

---

*Phase: 05-hierarchy-publishing-contract*
*Context gathered: 2026-05-06*
