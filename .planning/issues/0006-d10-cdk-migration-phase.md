---
issue: 0006
title: D-10 CDK migration Phase — trigger fired during #37 step 2; PR A is the leading edge
status: drafted 2026-05-15; awaiting operator confirmation, then becomes a ROADMAP Phase
filed: 2026-05-15
filed_by: Path 2 commit pass on #37 step 2 PR A scoping
related_issues: [37, 38]
related_artifacts:
  - infra/README.md  (defines the migration trigger and policy)
  - infra/eventbridge.json  (legacy substrate to be migrated)
  - infra/lambda_iam_policy.json  (legacy substrate to be migrated)
  - scripts/deploy_cron.sh  (legacy substrate to be migrated)
  - ../../../ReCiter-CDK/src/main/java/edu/wcm/reciter/  (migration target)
  - .planning/issues/0005-issue-37-step2-pr-a-plan.md  (the PR that fired the trigger)
---

# D-10 CDK migration Phase

## Why this exists

The migration policy in `infra/README.md` (Phase 10's IaC v1, marker D-10) defined three triggers:

> **Adopt CDK when ReciterAI adds any one of:**
> - (a) a second Step Function, or
> - (b) a second cron rule beyond the three defined here, or
> - (c) a fifth managed AWS resource (Lambdas + Step Functions + tables + buckets, excluding shared infra like CloudWatch log groups).

As of 2026-05-15, trigger (c) is **already crossed** before any #37 step-2 work lands. Current ReciterAI-managed resources:

| Resource | Class | Count |
|---|---|---|
| `reciterai` | DynamoDB table | 1 |
| `wcmc-reciterai-hierarchy` | S3 bucket | 1 |
| `wcmc-reciterai-artifacts` | S3 bucket | 1 |
| `reciterai-hot-path` | Step Functions | 1 |
| `reciterai-spotlight-orchestrator` | Lambda | 1 |
| `reciterai-drift-evaluator` | Lambda | 1 |

That's six countable resources; the README's own caveat ("The Lambda count alone will likely cross the threshold first") anticipated this. Adding #37 step-2 PR A's resources (ECR repo + ECS task def + IAM task role + security group + 2 secrets) pushes the count well past the threshold.

Trigger (b) also fires when #37 step-2 PR B lands (adds 2 cron rules — daily delta + Q6 annual rescore — for a total of 5 rules, beyond the 3 defined in `infra/eventbridge.json`).

**Conclusion:** the migration is not optional. The README's policy is now binding.

## What this Phase commits to

### End state

- `infra/eventbridge.json`, `infra/lambda_iam_policy.json`, and `scripts/deploy_cron.sh` are **removed from ReciterAI**.
- All ReciterAI-managed AWS resources (existing + new) are declared in `ReCiter-CDK` (Shape 1 — see §"Why Shape 1 not Shape 2" below).
- One `cdk deploy` produces the full ReciterAI deploy.
- The operator runbook (`README` + `docs/daily-enrichment.md` + `infra/README.md`) is updated to reflect CDK as the canonical substrate.

### Why Shape 1 not Shape 2

Endorsed by `infra/README.md`:
> Reciter-CDK (the existing IaC repo for the Java retrieval services) is the natural target if cross-repo coupling is acceptable; otherwise a new `ReciterAI-CDK` repo.

The user's call on 2026-05-15: cross-repo coupling is acceptable (Shape 1) because:
- One Java CDK toolchain the team already operates.
- One VPC, with ReciterAI's task reaching the existing ReCiter RDS by VPC reference (no peering).
- One `cdk deploy` for the WCM ReCiter platform.
- No external consumer is asking for ReciterAI standalone.

The "ReciterAI as separable product" argument is hypothetical; the migration favors operational ergonomics over speculative separation. If a future consumer ever needs standalone ReciterAI, extracting the relevant nested stack is bounded day-of-work.

## Sequencing

### Leading edge: #37 step 2 PR A (already drafted)

Ships the **new** daily-enrichment resources directly into `ReCiter-CDK`:

- ECR repo (`reciter/ai-daily-enrichment`)
- ECS Fargate task definition (0.5 vCPU / 2 GB, scoped IAM, log group)
- Security group with `allowFrom` on the ReCiter RDS
- Two plain-string secrets (OpenAI key, Teams webhook URL)
- CfnOutputs for task-def ARN + SG ID

**No legacy `infra/` files move in PR A.** The hybrid state starts here.

### Hybrid-state owner rules (binding while this Phase is incomplete)

Per the operator's 2026-05-15 framing:

> **Hybrid state ends when the migration Phase completes; until then:**
> - `infra/` owns the **existing** ML/ETL/spotlight/drift cron rules and their Lambda IAM.
> - `ReCiter-CDK` owns the **new** daily-enrichment task and any new resources from this point forward.
> - **No new work goes to `infra/`.** New cron rules, IAM, secrets, or compute resources land in CDK only.

This converts the hybrid from "transitional state nobody promised would end" into "transitional state with a named scope and a named end."

### Migration steps (post-PR-A, this Phase's actual work)

**M1. PR B's cron rules go directly into CDK, not `infra/eventbridge.json`.**

Per the hybrid-state owner rule. Two `Rule` constructs (or one `ScheduledFargateTask` covering both schedules) added to `ReCiterCDKECSStack.java`. Targets the daily-enrichment task def from PR A.

**M2. Migrate the three existing cron rules into CDK.**

`reciterai-hot-weekly` → Step Functions target; `reciterai-spotlight-monthly` and `reciterai-drift-daily` → Lambda targets. Requires CDK references to the existing Step Function ARN + Lambda ARNs (or migrating those resource definitions in too).

**M3. Migrate the existing Lambda functions into CDK.**

This is the bigger lift. Each Lambda becomes a `Function` construct with its existing code and config. Code may continue to live in `wcmc-its/ReciterAI` under a CDK assets reference, or be lifted into `ReCiter-CDK`'s `lambda/` directory (decision deferred to M3 execution).

**M4. Migrate the Step Functions state machine into CDK.**

`StateMachine` construct equivalent to the existing `reciterai-hot-path` definition.

**M5. Migrate IAM policies.**

`infra/lambda_iam_policy.json` content moves into per-resource scoped `PolicyStatement` blocks inside `ReCiter-CDK`.

**M6. Verification + rollback gate.**

The README's "preserve the JSON files until the CDK stack is verified in prod" rule applies. `infra/` files stay in the repo through M1–M5 as the rollback path. Verification:
- `cdk deploy` succeeds without surprises.
- Manual invocation of each migrated resource via AWS CLI proves IAM and connectivity work.
- One real run of each cron rule completes successfully against the new substrate.
- Cross-check: no orphan resources remain under the old names.

After verification, `infra/` files are removed in a follow-up PR (M7).

**M7. Remove `infra/` files + update READMEs.**

Final commit of the migration. `infra/README.md` becomes a "see ReCiter-CDK" pointer.

### What does NOT block PR A

- M2–M7 are scoped to this Phase, not PR A.
- PR A can merge with the hybrid state in place.
- PR B can merge after PR A and after M1 (the cron rules going into CDK), but **PR B does not have to wait for M2–M7**.
- The daily enrichment job becomes operational as soon as PR A + PR B (in CDK) ship and the operator-side post-merge work completes.

## Cost

- Engineering time: M1 is ~30 min (PR B already in scope). M2–M5 is the bulk; estimate 1–2 days for an experienced CDK author.
- Operator time: one prod `cdk deploy` per migration commit + verification.
- Blast radius: each migration step is reversible by `git revert` + `cdk deploy` to roll back the corresponding CloudFormation change. The `infra/` files as rollback path remain until M7.
- Coordination: minimal — ReciterAI continues operating throughout. The migration is purely substrate-level.

## Risks

- **Cross-repo coupling slows future ReciterAI feature work.** Mitigated by the existing pattern (other ReCiter services already in `ReCiter-CDK` move at their own pace; their dev cadence is fine).
- **CDK synth surprises.** Mitigated by `cdk diff` review on every M-step PR; surprises trigger a revert before deploy.
- **The 2-Lambda Step-Functions-state-machine handoff.** Mitigated by Phase 10's documentation of every input/output contract; CDK migration is mechanical against those contracts.
- **The hybrid state grows.** Mitigated by the binding owner rule above — no new work to `infra/` after 2026-05-15.

## Promotion to a ROADMAP Phase

When this is scheduled as actual work (vs. accepted as committed direction):

1. Add a row to `.planning/ROADMAP.md` under "Milestone M2 — SPS-feeding service" or a new milestone:
   `- [ ] **Phase D-10-migration: CDK substrate adoption** — Implements `infra/README.md`'s migration policy. Triggered by #37 step 2.`
2. Create `.planning/phases/{phase-number}-cdk-migration/` directory with the standard artifacts.
3. Link the M1–M7 sub-work above into the phase plan.

This doc is the seed; until promoted, it lives in `.planning/issues/` so the trigger event and committed direction are tracked even if the work isn't immediately scheduled.

## What I'm asking for

Confirm:
1. **Path 2 is the path** (matches your 2026-05-15 analysis).
2. **PR A ships as drafted** — Dockerfile in ReciterAI + CDK changes in ReCiter-CDK, both as the migration's leading edge.
3. **PR descriptions name the migration explicitly.** Both PRs reference this `0006-` doc and frame themselves as the leading edge, not as "smuggled" infra changes.
4. **The hybrid-state owner rule is binding** from 2026-05-15 onward. PR B's cron rules go into CDK, not `infra/eventbridge.json`.

Once confirmed, I'll update `0005-issue-37-step2-pr-a-plan.md` with the corrected framing, commit both branches, and push.
