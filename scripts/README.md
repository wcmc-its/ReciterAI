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
| `deploy_cron.sh` | EventBridge cron deploy for hot-path scheduler |
| `deploy_state_machine.sh` | Step Functions state-machine deploy for hot path |
| `smoke_hot_path.sh` | Manual hot-path end-to-end smoke against live infra |

Anything else added here should justify itself against the lifecycle rule above.
