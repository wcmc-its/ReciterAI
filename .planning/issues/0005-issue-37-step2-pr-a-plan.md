---
issue: 0005
github_issue: 37
title: Issue #37 step 2 PR A — plan (Dockerfile + ECR + ECS task def + IAM, manually runnable)
status: drafted 2026-05-15; reframed 2026-05-15 as the D-10 CDK migration's leading edge (see 0006). Code complete in both feature branches; max-effort audit pass corrected three IAM/secret bugs. Awaiting operator confirmation to push.
filed: 2026-05-15
filed_by: scoping pass before code changes
related_issues: [37]
related_artifacts:
  - .planning/issues/0006-d10-cdk-migration-phase.md  (the migration Phase this PR seeds)
  - Dockerfile (new, ReciterAI)
  - requirements.txt (ReciterAI)
  - .dockerignore (new, ReciterAI)
  - ReCiter-CDK/src/main/java/edu/wcm/reciter/ReCiterCDKECRStack.java
  - ReCiter-CDK/src/main/java/edu/wcm/reciter/ReCiterCDKECSStack.java
  - ReCiter-CDK/src/main/java/edu/wcm/reciter/ReCiterCdkSecretsManagerStack.java
  - ReCiter-CDK/src/main/java/edu/wcm/reciter/ReCiterCdkApp.java
---

# #37 Step 2 PR A — concrete file-by-file scope

## Decisions this plan inherits

- **Shape 1 confirmed.** ReciterAI extends `ReCiter-CDK` alongside `reCiterMachineLearningFargateTask`. Not a new CDK.
- **Q2 (PR shape) confirmed.** PR A includes Dockerfile + ECR + ECS task def + IAM. **No EventBridge rule.** PR B (separate) adds the schedule.
- **Q6 (annual rescore) confirmed.** Same task def serves both daily delta and annual rescore; PR B adds two EventBridge rules pointing at the same task.
- **OpenAI key propagation acknowledged.** Personal key → Secrets Manager. Rotation = one-PR change when org key lands. See `feedback_transitional_auth_no_propagation.md` 2026-05-15 override.
- **D-10 CDK migration is the framing.** Per `0006-d10-cdk-migration-phase.md`: trigger (c) — fifth managed AWS resource — was already crossed before today (six countable ReciterAI resources today). Trigger (b) — second cron rule beyond the three defined — fires in PR B. PR A is the migration's **leading edge**, not a per-PR detour around the migration policy. Hybrid-state owner rule applies from 2026-05-15: `infra/` owns existing rules; CDK owns all new work.

## What this PR is in the larger story

Per `0006-d10-cdk-migration-phase.md` §"Leading edge: #37 step 2 PR A":

- This PR ships the **new** daily-enrichment resources directly into CDK because the migration trigger has fired.
- This PR does **not** migrate the existing `infra/` JSON files — those stay as the rollback path until later migration sub-steps (M2–M7) complete.
- This PR does **not** wait for the migration Phase to complete — the leading edge is allowed to ship under the binding hybrid-state owner rule.
- This PR's framing in its description should reference the migration explicitly so future readers don't see it as smuggled infra.

## Exit criterion (what "manually runnable" means)

After PR A merges + image builds + push, operator can run:

```bash
aws ecs run-task \
  --cluster <reciter-cluster-name> \
  --task-definition reciterai-daily-enrichment \
  --launch-type FARGATE \
  --network-configuration "..." \
  --overrides '{"containerOverrides":[{"name":"reciterai-daily-enrichment","command":["python","-m","scripts.run_daily_enrichment","--verbose"]}]}'
```

…and the task pulls the image, reads secrets, queries MariaDB for the delta, calls OpenAI + Bedrock, writes synopsis + impact rows, advances the watermark, emits a STAGE# row, exits with status 0. **No cron yet.** Operator-triggered only.

## Repo 1 — ReciterAI changes

### `Dockerfile` (new)

- Base: `python:3.12-slim` (3.14 is the operator's local but slim images lag; 3.12-slim is the latest with broad library compat).
- Install system deps for `pymysql` + `cryptography`: minimal — `gcc` build-time only, removed after install.
- `COPY requirements.txt . && pip install --no-cache-dir -r requirements.txt`
- `COPY . .` — relies on `.dockerignore` to keep image lean.
- No `ENTRYPOINT` / `CMD` baked in. The ECS task definition supplies the command. This matches the existing `reCiterMachineLearningFargateTask` pattern where `containerOverrides` drives invocation, and keeps the same image usable for `--full` annual rescore (PR B sets a different command on the schedule).
- Non-root user inside container.

### `requirements.txt` (update)

Append:
```
openai>=2.0.0
```

(Currently missing — works on operator's mac via system install. Will fail in container without this.)

### `.dockerignore` (new)

Exclude: `.git/`, `.planning/`, `tests/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.env*`, `.venv/`, `node_modules/`, `docs/` (except specifically what the runtime needs — `config/llm_prices.yaml` is in `config/`, not `docs/`, so docs is fully excludable).

### What this PR does NOT touch in ReciterAI

- No code changes in `pipeline_enrichment/`, `scripts/`, `utils/`. The CLI already exists and is the canonical entry point.
- No changes to `docs/daily-enrichment.md` yet (that doc is the operator-run guide; it stays accurate until PR B adds the cron and the operating mode flips).

## Repo 2 — ReCiter-CDK changes

### `ReCiterCDKECRStack.java` — add ReciterAI ECR repo

Mirror the existing pattern (each ReCiter service has its own ECR repo). One new `Repository` instance, exposed via a getter the ECS stack consumes.

Repo name: `reciter-ai-daily-enrichment` (kebab-case, matching the existing `reciter-machine-learning-analysis` convention).

### `ReCiterCDKECSStack.java` — add daily enrichment task definition

Mirror lines 788–916 (`reCiterMachineLearningFargateTask`) but **without the `ScheduledFargateTask` wrapper** — just the `FargateTaskDefinition` + container + IAM, exposed via a getter so PR B can wrap it in EventBridge.

Concretely:
- New field: `private FargateTaskDefinition reciterAIDailyEnrichmentTaskDef;`
- New method block (mirrors lines 788–916 minus the `ScheduledFargateTask` + EventBridge wiring):
  - `FargateTaskDefinition` with cpu(512) memoryLimitMiB(2048). Daily enrichment is light (text-only LLM calls, small MariaDB delta). 1 vCPU / 8 GB used by the ML cron is overprovisioned for this workload. Re-tune after first manual run.
  - Container: `reciterai-daily-enrichment`, image from the new ECR repo, log group `/ecs/reciterai/daily-enrichment`, env vars + Secrets Manager refs (see below).
  - Task role: scoped IAM permissions (see below).
  - Execution role: ECR pull + CloudWatch logs (CDK default).
- New getter: `public FargateTaskDefinition getReciterAIDailyEnrichmentTaskDef()` — for PR B's EventBridge rule.

**Environment variables (plain text, non-secret):**

| Var | Value | Source |
|---|---|---|
| `DB_HOST` | `reciterDb.getDbInstanceEndpointAddress()` | existing RDS reference |
| `DB_USERNAME` | `"admin"` | matches existing ML task |
| `DB_NAME` | `"reciter"` | matches existing ML task |
| `AWS_DEFAULT_REGION` | from `reCiterSecret` | matches existing ML task |
| `RECITERAI_ALERT_MENTION_UPN` | `paa2013@med.cornell.edu` | operator default |
| `RECITERAI_ALERT_MENTION_NAME` | `"Paul Albert"` | operator default |

**Secrets (Secrets Manager refs):**

| Container env var | Secret source |
|---|---|
| `DB_PASSWORD` | `Secret.fromSecretsManager(reciterDb.getSecret(), "password")` — existing RDS-managed secret |
| `OPENAI_API_KEY` | New secret entry (see `ReCiterCdkSecretsManagerStack.java` changes below) |
| `RECITERAI_TEAMS_WEBHOOK_URL` | New secret entry (see below) |

**Task role IAM (scoped — not wildcard):**

- `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` on `reciter-analysis-*` table ARNs (the WATERMARK# + IMPACT# + STAGE# items live here)
- `bedrock:InvokeModel` on `arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*` and `…/anthropic.claude-sonnet-4-6` only — not all of Bedrock
- `secretsmanager:GetSecretValue` on the two new secrets (RDS secret access already granted by the construct)
- `logs:CreateLogStream`, `logs:PutLogEvents` on its own log group

### `ReCiterCdkSecretsManagerStack.java` — two new secret entries

Append:
- `reciterai/openai-api-key` (initial value: empty string placeholder; operator populates manually via console or `aws secretsmanager update-secret` after stack deploys — this avoids the value ever being in CDK source or CloudFormation events)
- `reciterai/teams-webhook-url` (same pattern)

Both as `Secret` constructs, exported for the ECS stack to reference.

### `ReCiterCdkApp.java` / `ReCiterCdkStack.java` — wire references through

Whatever pattern the existing nested stacks use to receive `vpc`, `reciterDb`, `reCiterSecret`, etc., the new ReciterAI block in `ReCiterCDKECSStack.java` consumes the same references. Likely no changes to the app entry point — the additions land inside the existing ECS stack constructor, which already receives all the references it needs.

## Out of scope for PR A (tracked for PR B / later)

- EventBridge rules (PR B — Q3 = `cron(0 4 * * ? *)` daily + Q6 annual `cron(0 6 1 1 ? *)`)
- Cost-guard threshold validation against the new env (Q5 = $3, baked into code, not infra)
- Operator-guide doc update (defer until PR B, when the operating mode actually flips)
- Auto-build pipeline for the ReciterAI image — first build is operator-manual (`docker build && docker push`); pipeline integration is a follow-up

## Failure-mode review (what could go wrong)

- **Image pulls fail.** Operator hasn't pushed an initial image yet. Mitigation: PR A description includes the `docker build / docker push` recipe; first `aws ecs run-task` only works after first push.
- **OpenAI key secret empty.** Task starts, openai client constructs, first API call returns 401. Mitigation: operator populates secret value before first run-task; CDK creates the secret entry empty so the deploy succeeds.
- **MariaDB unreachable from new task.** Task lands in a security group that doesn't have outbound to the RDS SG. Mitigation: copy the SG pattern from `reCiterMachineLearningFargateTask` which is already proven to reach the RDS.
- **Bedrock not reachable from the VPC.** Bedrock calls go via VPC endpoint or public NAT. Mitigation: `reCiterMachineLearningFargateTask` already runs in `PRIVATE_WITH_NAT` and presumably reaches Bedrock fine; use the same subnet selection.
- **DynamoDB scoping wrong.** Task role can't write the watermark. Mitigation: scope to specific table ARNs (avoid wildcard); test by manually invoking and checking CloudWatch logs.

## Verification plan (post-merge)

1. Operator: `cdk deploy ReCiterCdkStack` (or whatever the top-level deploy alias is in ReCiter-CDK's README).
2. Operator: populate the two new secrets via console / CLI.
3. Operator: `docker build` ReciterAI image locally; `aws ecr get-login-password | docker login`; `docker push` to the new ECR repo.
4. Operator: `aws ecs run-task` with a small synthetic command (`["python","--version"]`) to validate image pull + container start + log streaming.
5. Operator: real `aws ecs run-task` with `python -m scripts.run_daily_enrichment --verbose --dry-run` (or equivalent — if `--dry-run` doesn't exist, an empty-delta day works).
6. Operator: real `aws ecs run-task` with the actual command on a non-empty delta. Verify: watermark advances, MariaDB rows written, STAGE# row emitted, no Teams alert (or expected Teams alert on synthetic failure).
7. Sign off; open PR B for the EventBridge cron.

## Estimated PR size

- ReciterAI: ~50 lines new (Dockerfile + .dockerignore + requirements.txt diff).
- ReCiter-CDK: ~150 lines new (task def block mirroring lines 788–916 minus the schedule wrapper, plus 2 secret entries, plus 1 ECR repo).

## Why this is the right unit of work to ship next

- Validates the riskiest pieces (image, IAM, network, secrets) before any cron fires (Q2 reasoning).
- Self-contained: doesn't change any application code, just adds deploy primitives.
- Reversible: stack delete cleanly tears it down; no schema/data migration.
- Unblocks the rest of step 2 (PR B is ~30 min once PR A validates).
