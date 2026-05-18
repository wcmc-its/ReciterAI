# ReciterAI — operational policies

Binding operational policies and decision disciplines for ReciterAI. Each entry is
the **rule**, its **trigger or a concrete example**, and a **pointer** to where the
full rationale lives — this file is the index, not the rationale.

Keep it high-signal. Prune entries that have become habitual or no longer apply;
this is not an append-only log. Growth past ~10–12 entries is a signal to prune,
not to keep adding.

Longer-form general lessons live in
`~/Dropbox/Projects/ReciterAI - Planning/process-notes.md`; this file is the
repo-side, collaborator-visible index.

---

## 1. The CDK migration is committed — `infra/` is frozen substrate

`infra/` (single-file IaC — `eventbridge.json`, `lambda_iam_policy.json`,
`deploy_cron.sh`, the two `deploy_*_state_machine.sh`) is transitional. All three
D-10 triggers have fired; migration to `ReCiter-CDK` (Shape 1, cross-repo —
decided 2026-05-15) is committed.

*Trigger to schedule it:* `ReCiter-CDK#11` closes with a named, deployable hosting
path. Until then the migration is deliberately deferred (D-INFRA).

*Source:* `infra/README.md` § "D-10 migration trigger"; `.planning/issues/0006-d10-cdk-migration-phase.md`.

## 2. No new work goes to `infra/`

Binding from 2026-05-15. New cron rules, IAM, secrets, or compute land in
`ReCiter-CDK`, not `infra/`. `infra/` owns only the existing hot-path / spotlight /
drift substrate, frozen as the rollback path until policy 1 completes.

*Exception (closed):* #80 Phase 2 onboarding declared its infra in `infra/`
alongside the hot path (D-INFRA — they migrate as one batch). A deliberate
carve-out; with onboarding deployed 2026-05-18, `infra/` should receive nothing
further.

*Source:* `.planning/issues/0006-d10-cdk-migration-phase.md` § "Hybrid-state owner rules"; `infra/README.md` § "#80 Phase 2 update".

## 3. In-code scope expansion must bubble up the same day

A forward-declaration in a comment or docstring ("step N+1 will also handle X, Y,
Z") that widens a future phase's scope is silent scope expansion unless it also
lands in a tracked artifact (issue body, planning doc) the same day.

*Watch-for:* treat scope-defining comments left by a prior phase as a request for
review, not a spec.

*Source:* `~/Dropbox/Projects/ReciterAI - Planning/process-notes.md` § "Scope expansion via in-code comment" (evidence: #37 step 3 was 5× larger in code than in its issue body).

## 4. Read existing code and docs before designing against them

Before drafting a plan for code that touches existing systems — writers, readers,
schemas, modules, and their READMEs / contract docs / config comments — read them
first. Reading almost always collapses planned scope; designing in the abstract
overstates it and risks compatibility breaks.

*Example:* #37 — three surfaces, each collapsed after the read. The 2026-05-18
onboarding deploy — the "hierarchy version mismatch" dissolved once the bundled
drafts were compared against the live hierarchy, and the per-Lambda VPC question
resolved after reading `assign_fanout.py` and the build-script comments.

*Source:* `~/Dropbox/Projects/ReciterAI - Planning/process-notes.md` § "Read existing code before designing infrastructure".

## 5. Blast-radius-proportional operations need a cap or pause before deploy

Any operation whose blast radius scales with system state — issues filed ∝ flagged
CWIDs, model spend ∝ publication count, rows written ∝ corpus size — needs an
explicit cap or pause-for-confirmation built in *before* it ships, not bolted on
after.

*Example:* the daily-job cost guard had one (300-PMID ceiling); the onboarding
detector did not (uncapped issue-per-flagged-CWID → a ~1,558-issue cold-start
flood, caught only at deploy — #106); the 7b onboarding smoke does (operator pause
before real Bedrock spend).

*Watch-for:* when planning an operation, ask "what is this proportional to, and
what bounds it?" If nothing bounds it, that is the gap.

*Source:* surfaced during the #80 Phase 2 onboarding deploy, 2026-05-18; cf. #106.
