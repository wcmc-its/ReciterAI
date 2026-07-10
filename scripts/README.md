# scripts/

One-shot operational tools — migrations, deploys, smoke tests.

## Lifecycle

Scripts here are **one-shot by default**: they exist to be run a handful of times against a specific environment, then deleted. They are not part of the runtime pipeline.

- A migration script that has been applied to every environment it targets should be removed in the same merge cycle as whatever marks the migration as complete (issue close, runbook update, etc.).
- A deploy script that's been superseded by infra-as-code should be removed when the IaC lands.
- Smoke scripts (`smoke_*.sh`) are exempt — they're long-lived debugging aids.

## What goes elsewhere

- Long-lived utilities → `utils/`
- Repeated operational entry points → top-level Python modules or `pipeline_*/`
- Tests → `tests/`
- One-shot scripts that need permanent test coverage are an antipattern; if you find yourself writing them, the script probably wasn't actually one-shot.

## Current contents

| File | Purpose |
|---|---|
| `build_lambda_zips.sh` | Build the 10 Lambda zips — 6 hot-path + 4 onboarding |
| `deploy_cron.sh` | EventBridge cron deploy (hot / spotlight / drift / onboarding-detector) |
| `deploy_state_machine.sh` | Step Functions deploy — hot path (`reciterai-hot-path`) |
| `deploy_onboarding_state_machine.sh` | Step Functions deploy — onboarding (`reciterai-onboarding`) |
| `smoke_hot_path.sh` | Manual hot-path end-to-end smoke against live infra |
| `smoke_onboarding.sh` | Manual onboarding end-to-end smoke against live infra |
| `verify_98_topic_rows.sh` | One-shot — verify #98's hot-path `TOPIC#` materialization in prod; delete when #98 closes |
| `eligibility_audit/` | Reproducible eligibility-capture audit (regex flags vs Sonnet reference, fixed seed) — see its README + `docs/grant-matching-measurements-runbook.md`. Long-lived measurement aid, exempt like `smoke_*.sh` |

Anything else added here should justify itself against the lifecycle rule above.

## Notes (#80 Phase 2 / PR 6)

- **`deploy_state_machine.sh` and `deploy_onboarding_state_machine.sh` are intentional near-clones.** The onboarding ASL has its own 8-placeholder set, and generalizing the hot script would edit the tool that deploys the *live* hot path. Both retire together at the CDK migration. **Fix bugs in both.**
- **The `finalize` zip is deployed as two functions.** `build_lambda_zips.sh` produces `build/reciterai-onboarding-finalize.zip`; it is deployed as `reciterai-onboarding-finalize` (`--handler pipeline_onboarding.finalize.handler`) *and* `reciterai-onboarding-notify` (`--handler pipeline_onboarding.finalize.notify_handler`). A future `update-function-code` against that zip must update **both** functions.
