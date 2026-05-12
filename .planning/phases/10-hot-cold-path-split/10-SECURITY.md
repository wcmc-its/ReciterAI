---
phase: 10-hot-cold-path-split
audited: 2026-05-12
status: open_threats
asvs_level: 2
threats_total: 5
threats_closed: 4
threats_open: 1
block_on: open
---

# Phase 10: Hot/Cold Path Split — Security Audit

**Audit basis:** Phase 10 ships infrastructure (Step Functions, EventBridge, IAM, Slack/gh CLI alert dispatcher) and has no formal STRIDE register in PLAN.md. The five **Threat Flags** declared in `10-SUMMARY.md` are treated as the audit register; each is verified against the implementation, not against documentation.

**Method:** Every claim was re-grepped in source files. Test coverage was confirmed by reading the test bodies, not by counting test names.

---

## Threat Verification

| # | Threat | Disposition | Status | Evidence |
|---|--------|-------------|--------|----------|
| TF-01 | Slack webhook URL handling | mitigate | **CLOSED** | `pipeline_common/alert.py:46,89` |
| TF-02 | gh CLI shell-out (command injection) | mitigate | **CLOSED** | `pipeline_common/alert.py:108-115` |
| TF-03 | IAM least-privilege | mitigate | **CLOSED** (with note) | `infra/lambda_iam_policy.json:19-22,34-39`; `tests/test_infra_eventbridge_shape.py:147-173` |
| TF-04 | Lock-collision starvation (skip + WARN alert per D-11) | mitigate | **OPEN** | STAGE# row written; **WARN alert NOT emitted** (`pipeline_hot/orchestrator.py:260-271`, `state_machine.asl.json:32-34,201-203`) |
| TF-05 | Cost telemetry first-class zeros | mitigate | **CLOSED** | `utils/stage_records.py:44,215-246`; `tests/test_stage_records.py:200-218,301-304,338-348` |

---

## Detailed Findings

### TF-01 — Slack webhook URL handling — CLOSED

**Claim:** URL is env-sourced, never logged, never embedded in error messages, never written to STAGE# / DRIFT# rows.

**Verification:**
- Webhook URL is read from env at call time, never imported from disk or constants:
  `pipeline_common/alert.py:46` `SLACK_ENV = "RECITERAI_SLACK_WEBHOOK_URL"`
  `pipeline_common/alert.py:89` `webhook = os.environ.get(SLACK_ENV)`
- Absent webhook is logged by env-var **name only**, not value: `pipeline_common/alert.py:91-93`.
- Failure paths log the exception, not the URL: `pipeline_common/alert.py:80,83` (`logger.warning("Slack webhook returned HTTP %s", resp.status)`, `logger.warning("Slack webhook POST failed: %s", exc)`). The `urllib.error.URLError` repr does not include the full URL in the standard library implementation; this is best-practice but not bullet-proof — see Open Q below.
- `STAGE#` / `DRIFT#` builders do not accept a webhook param and never reference `SLACK_ENV`. Grep `grep -rn "RECITERAI_SLACK_WEBHOOK_URL" pipeline_*/ utils/` shows the env var name appears only in `pipeline_common/alert.py`.
- Timeout: 5 s (`pipeline_common/alert.py:48`).
- IAM grant for the webhook secret is scoped to `arn:aws:secretsmanager:*:*:secret:reciterai/slack-webhook-*` (`infra/lambda_iam_policy.json:84-91`) — Secrets Manager retrieval path is wired but not yet used by `alert.py` (the URL is fetched from env, not Secrets Manager). Acceptable for v1; flagged as a future migration in the **Accepted Risks** log below.

**Tests:**
- `tests/test_alert_dispatcher.py::test_slack_skipped_when_env_unset` (line 115) — env-absent → no POST, no leak.
- `tests/test_alert_dispatcher.py::test_slack_payload_contains_severity_and_context` (line 142) — verifies payload structure does not include the URL.

**Residual concern (informational, not blocking):** A `urllib.error.URLError` raised mid-handshake includes the full request URL in some Python versions when the underlying error message is `OSError` formatted. Current code logs `%s` of the exception — if the user later upgrades Python or swaps `urllib` for `requests`, this could leak. Mitigation: rotate webhook secrets regularly and confirm CloudWatch log filter masks `hooks.slack.com` URLs. Documented as accepted risk AR-1 below.

### TF-02 — gh CLI shell-out — CLOSED

**Claim:** No command injection via untrusted strings, no `shell=True`, timeouts present, output captured (not leaked to logs).

**Verification:**
- `_run_gh` uses argv list and never `shell=True`: `pipeline_common/alert.py:108-115`.
  ```
  subprocess.run(["gh", *args], capture_output=True, text=True,
                 timeout=GH_TIMEOUT_SECONDS, check=False)
  ```
- All callers pass list literals (no string interpolation into argv): `pipeline_common/alert.py:120-122,155,162`. The `--title` and `--body` values are passed as separate argv elements; `gh` does not re-evaluate them as shell.
- Timeout: 15 s (`pipeline_common/alert.py:49`).
- Output capture: `capture_output=True` — stdout/stderr are not auto-mirrored to the Lambda logger. Failure path logs only `result.stderr.strip()` (line 124, 157, 164), which contains gh CLI error text, not the title/body/context that might carry sensitive data.
- The label `"drift-alert"` is a hard-coded constant (line 47); not user-supplied.
- **Trust boundary check:** The `message` and `context` dict are constructed inside the drift evaluator from STAGE# / DRIFT# rows that this pipeline itself wrote. They are not user-supplied. However, PMIDs and topic IDs flow into `context` from DDB. These are bound to argv as separate `--body <json>` element so even an embedded `'; rm -rf /` payload cannot escape into a shell.
- `gh` auth context: relies on the deployment environment providing an authenticated `gh` CLI (Lambda layer + scoped token assumed). No token is read from / written to Python code. The CI / Lambda token scope is **out of scope of this audit** but flagged in the deploy notes (`infra/eventbridge.json:59`) and accepted risk AR-2.

**Tests:**
- `tests/test_alert_dispatcher.py::test_issue_body_includes_json_context` (line 218) — verifies `--body` is JSON-fenced (no shell evaluation).
- `tests/test_alert_dispatcher.py::test_issue_create_failure_returns_false` (line 203) — verifies non-zero exit is captured, not raised.
- `tests/test_alert_dispatcher.py::test_issue_skipped_when_gh_absent` (line 165) — gh missing → skip, not crash.

### TF-03 — IAM least-privilege — CLOSED (with note)

**Claim:** DynamoDB scoped to `reciterai-chatbot`, S3 scoped to `wcmc-reciterai-*`, enforced by tests.

**Verification:**
- DynamoDB Resource list: `arn:aws:dynamodb:*:*:table/reciterai-chatbot` + `.../index/*` (`infra/lambda_iam_policy.json:19-22`). Scoped to the single table + its GSIs.
- S3 Resource list: only `wcmc-reciterai-hierarchy` and `wcmc-reciterai-artifacts` plus their key wildcards (`infra/lambda_iam_policy.json:34-39`).
- Step Functions scoped to the single `reciterai-hot-path` state machine + its executions (`infra/lambda_iam_policy.json:61-64`).
- CloudWatch Logs scoped to `/aws/lambda/reciterai-*` log groups (`infra/lambda_iam_policy.json:82`).
- Secrets Manager scoped to `reciterai/slack-webhook-*` (`infra/lambda_iam_policy.json:90`).

**Tests:**
- `tests/test_infra_eventbridge_shape.py::test_iam_policy_scopes_dynamodb_to_reciterai_table` (line 147) — asserts every DDB statement's Resource list contains `reciterai-chatbot`. Enforced at parse time, not decorative.
- `tests/test_infra_eventbridge_shape.py::test_iam_policy_scopes_s3_to_wcmc_reciterai_buckets` (line 162) — asserts every S3 statement's Resource list contains `wcmc-reciterai-`.
- `tests/test_infra_eventbridge_shape.py::test_iam_policy_includes_plan_t12_actions` (line 134) — confirms required actions are present.

**Notes (informational):**
- **Bedrock is `Resource: "*"`** (`infra/lambda_iam_policy.json:51`). This is the AWS-documented pattern for `bedrock:InvokeModel` (model ARNs are too many to enumerate; foundation-model invocation cannot be wildcarded to a single model ID without ongoing maintenance). SUMMARY flagged this; auditor concurs — accepted as AR-3.
- **`events:PutEvents` is `Resource: "*"`** (`infra/lambda_iam_policy.json:72`). This permits posting custom events to any EventBridge bus the role can reach. Default bus access is the AWS norm and is below the audit-blocking threshold; flagged as AR-4 for a future tightening.
- **DynamoDB allows `Scan`** (`infra/lambda_iam_policy.json:14`). Scan is enabled because the drift evaluator's 14-day window query is currently expressed as `Scan` rather than `Query` on a GSI. This is a cost/performance concern more than a security one, but a future GSI-based read path should remove `Scan` from the role. Flagged as AR-5.

### TF-04 — Lock-collision starvation — **OPEN (BLOCKER)**

**Claim:** Orchestrator skip path writes a STAGE# audit row AND emits a WARN alert per D-11 (severity table row: "Hot-path lock collision → WARN").

**Verification:**

**Half of the claim is implemented:**
- `pipeline_hot/orchestrator.py:166-187` `write_skipped_hot_run_locked` writes a `STAGE#hot_run#GLOBAL` skipped row with `skip_reason="prior_run_in_progress"`. Unit-tested at `tests/test_pipeline_hot_orchestrator.py:148-162`. ✓

**The WARN alert half is NOT implemented:**
- `pipeline_hot/orchestrator.py:260-271` — when the lock fires, the orchestrator (a) writes the skipped row, (b) calls `logger.warning(...)`, and (c) returns `{"status": "skipped", ...}`. **No call to `pipeline_common.alert.dispatch(severity="WARN", ...)` exists.** Grep `grep -rn "alert.dispatch\|from pipeline_common import alert" pipeline_hot/` returns zero matches.
- `pipeline_hot/state_machine.asl.json:27-36` — `CheckLockOrProceed` is a Choice state that routes the skipped status straight to `"End"` (a `Succeed` terminal at line 201-203). There is no Lambda Task between `Orchestrate` and `End` that would invoke the `${AlertDispatcherLambdaArn}` for the skip path. The only `NotifyError` invocation is reachable from `WriteHotRunFailed` (line 169-199), i.e., only on Task failures — not on the orderly lock-skip.
- `pipeline_drift/severity.py` does map `Condition.HOT_PATH_LOCK_COLLISION → WARN` (confirmed by `tests/test_alert_dispatcher.py::test_severity_for_table_d11_values` line 258), so the severity mapping exists. What is missing is the **producer**: nothing emits the `HOT_PATH_LOCK_COLLISION` condition into the dispatcher.

**Impact:**
- A persistently hung prior execution silently produces a string of `skipped` STAGE# rows. The only signal an operator gets is by querying DynamoDB or reading the Lambda CloudWatch logs for the WARN `logger.warning` line. The Slack channel will be quiet — the user-visible alerting plane that D-11 promises does not fire.
- Starvation is mitigated to "no work happens until you notice", but the threat flag's *escalation* mitigation (WARN alert + manual escalation when repeated) is not in code.

**Required to close:**
1. After `write_skipped_hot_run_locked` returns in `pipeline_hot/orchestrator.py:260`, call `pipeline_common.alert.dispatch(severity="WARN", message="Hot path skipped — prior execution still RUNNING", context={"state_machine_arn": state_machine_arn, "started_at": started_at})`.
2. Add a unit test that mocks `alert.dispatch` and asserts it is called exactly once with severity `"WARN"` when `is_state_machine_running` returns True.
3. (Optional, recommended) Add a Choice-state pre-empt in `state_machine.asl.json` so the lock-skip routes through a `NotifyWarn` Lambda task instead of relying on the Python orchestrator to dispatch — this gives the state-machine-level audit trail consistency the rest of the ASL has.

This is a **shipping issue**: the EventBridge cron will run weekly, a Bedrock Batch wait-loop collision is plausible per PLAN §Risks, and the hot-path lock collision is the very threat the spec promised to surface to operators.

### TF-05 — Cost telemetry first-class zeros — CLOSED

**Claim:** Skipped rows populate `cost_observed_usd: Decimal('0')`, never omitted. Builder contract enforces. Tests cover.

**Verification:**
- `utils/stage_records.py:44` `SKIP_COST_OBSERVED_USD = Decimal("0")` — module-level constant.
- `utils/stage_records.py:215-246` `build_skipped_record` unconditionally calls `_base_item` with `cost_observed_usd=SKIP_COST_OBSERVED_USD`. There is no code path that omits the field — `_base_item` (lines 144-166) always sets `cost_observed_usd` in the dict it returns.
- `utils/stage_records.py:302-306` `write_skipped` delegates to `build_skipped_record` then `put_item` — same guarantee on persistence.

**Tests:**
- `tests/test_stage_records.py:200-218` `test_write_skipped_pins_cost_to_skip_constant` — asserts `item["cost_observed_usd"] == SKIP_COST_OBSERVED_USD == Decimal("0")`.
- `tests/test_stage_records.py:301-304` `test_skip_cost_is_pinned_to_zero` — invariant on the module-level constant.
- `tests/test_stage_records.py:338-348` `test_build_skipped_record_pins_cost_and_carries_skip_reason` — same assertion on the pure builder.
- `tests/test_stage_records.py:221-233` `test_write_skipped_emits_a_row_per_skip` — skips are not silent (one row per skip).

The non-omission contract is enforced by the builder's call signature: `cost_observed_usd` is a required keyword in `_base_item`, and `build_skipped_record` passes the constant — no caller can omit it without changing the substrate itself.

---

## Unregistered Flags

None. The five threat flags in `10-SUMMARY.md ## Threat Flags` are an exhaustive list of new attack surface introduced by Phase 10; no additional surface area was discovered during the audit that lacks a mapping.

---

## Incidental Findings (auditor-discovered, advisory)

| Severity | Finding | Location | Recommendation |
|----------|---------|----------|----------------|
| INFO | Hot-path Lambda handlers shell out via argv (`subprocess.run([sys.executable, "-m", ...])`) to `score_publications`, `assign_subtopics`, `rollup_by_cwid`, embedding `topic_id`, `delta_pmids`, `dirty_cwids` from the Step Functions event input. No `shell=True` and argv is a list, so command injection is not possible. However, there is **no allowlist validation** of `topic_id` against a known taxonomy id pattern before it reaches argv. | `pipeline_hot/handlers/assign.py:38-51`, `score.py:46-53`, `rollup.py:35-44` | Add a regex check `^[a-z0-9_-]+$` on `topic_id` and `cwid` items at handler entry. The trust boundary is internal (EventBridge input is fixed; state-machine inputs are produced by our own orchestrator), so the residual risk is low. Defense-in-depth. |
| INFO | `pipeline_hot/orchestrator.py:285-292` builds a SQL query via `sqlalchemy.text(...)` with `:since` bound — correctly parameterized, no injection. Confirmed safe. | n/a | none |
| INFO | `pipeline_common/alert.py:161` `title = f"[drift-alert] {message[:80]}"` — `message` is producer-controlled (we author it); not a risk today. If a future caller passes externally-sourced text, the 80-char truncation does not strip newlines or markdown that could affect issue rendering. | `pipeline_common/alert.py:161` | Strip control chars + `\n` from `message` before formatting the title. Cosmetic. |
| INFO | `pipeline_hot/orchestrator.py:121-124` bootstrap-lookback uses `lookback.replace(day=max(1, lookback.day - 14))`. This computes wrong dates in the first ~14 days of a month (May 3 → May 1, not Apr 19). Not a security issue — but worth flagging since the bootstrap path is the one a fresh deploy will take. | `pipeline_hot/orchestrator.py:121-124` | Use `timedelta(days=14)` instead of `.replace(day=...)`. Already known correctness issue, not security. |
| INFO | No findings of hardcoded credentials, AWS keys, or passwords in the Phase 10 added files (`pipeline_*/`, `infra/`, `scripts/`). Verified with `grep -rn "AKIA\|aws_access_key\|password.*=.*['\"]"`. | n/a | none |

---

## Accepted Risks Log

| ID | Risk | Justification | Revisit |
|----|------|---------------|---------|
| AR-1 | Slack webhook URL could appear in `urllib.error.URLError` repr under some failure modes | Best-effort transport; URL rotation + log filtering is the compensating control. Slack incoming webhooks have no admin scope — a leaked URL allows posting to one channel only. | When `requests` replaces `urllib` or on Python upgrade |
| AR-2 | `gh` CLI auth context lives in the Lambda layer / env, not in audited Phase 10 code | Token issuance is a deploy-environment concern (Lambda execution-role permissions to read the token from Secrets Manager are scoped at AR-2 boundary, not in this phase) | When the Lambda layer is built / before first prod deploy |
| AR-3 | `bedrock:InvokeModel` Resource: "*" | AWS-documented pattern; the foundation-model ARN list is large and unstable. Scoping would require ongoing maintenance with low risk reduction. | If Bedrock adds resource-level IAM, revisit |
| AR-4 | `events:PutEvents` Resource: "*" | Default bus access; ReciterAI does not publish to custom buses today. | When custom buses are introduced |
| AR-5 | `dynamodb:Scan` permitted on the role | Required by drift evaluator's 14-day window; cost / performance issue more than security | When a GSI on `created_at` lands (Phase 12 candidate) |

---

## Pre-Deploy Blockers

Before the EventBridge cron rules are enabled in production:

1. **TF-04 (this audit):** Wire `pipeline_common.alert.dispatch(severity="WARN", ...)` into the lock-collision branch of `pipeline_hot/orchestrator.py:260-271`. Add a unit test asserting the dispatch call.
2. **Spotlight handler NotImplementedError** (from `10-VERIFICATION.md` PARTIAL 1): `pipeline_spotlight/orchestrator.py:244-253` must implement the production STAGE# query path or the monthly cron target will fail. Not a security blocker per se, but a reliability blocker that lives in the same Lambda surface as the alert flow.
3. **Drift handler missing** (from `10-VERIFICATION.md` PARTIAL 2): `pipeline_drift/evaluator.py` has no `handler(event, context)` to be the EventBridge Lambda entry point, and no production call to `alert.dispatch`. The daily cron will publish a non-invokable Lambda. Not a Phase 10 security threat flag per se, but it is the same severity-dispatch wiring gap that TF-04 surfaces — both share the symptom *"the dispatcher exists, the producer doesn't"*.

Items 2 and 3 are flagged in the Verification report and are outside the strict scope of the five Phase 10 threat flags. They are noted here because they share the same root cause as TF-04: end-of-wire integration between mature components was deferred.

---

## Verdict

4 of 5 declared threat flags are CLOSED with file:line evidence and passing tests. **TF-04 is OPEN**: the lock-collision skip writes the STAGE# audit row but does not emit the WARN Slack alert that D-11 promises operators — the orchestrator only emits `logger.warning` and the state machine routes the skip directly to `Succeed`. This is a one-call-site fix (`alert.dispatch("WARN", …)` in `pipeline_hot/orchestrator.py:260-271`) plus a unit test. Per `block_on: open`, the audit blocks production cron enablement until TF-04 is closed.
