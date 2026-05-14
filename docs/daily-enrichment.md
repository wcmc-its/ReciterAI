# Daily enrichment job — operator guide

The daily enrichment job adds synopsis + impact rows to MariaDB for new
WCM-faculty publications. It is the in-repo replacement for the laptop
POC's manual workflow (see #37).

**Operating mode (as of 2026-05-14):** operator-run-by-hand from the
operator's laptop, once per day. Not scheduled.

## Why no cron / Lambda yet

The OpenAI API key currently used is a **personal, transitional credential**
on the operator's local machine. Propagating it to shared infrastructure
(ReciterDB host, Lambda env, Secrets Manager) would harden a deployment
pattern the operator explicitly wants to replace. Automation hardens
the auth in place; we are deliberately not hardening this pattern.

Tracked as a follow-up: see [the linked issue](#follow-up-trigger) — the
automation revisit is triggered when an org-managed OpenAI key (or Azure
OpenAI tenant access) is available, not on a calendar.

## Prerequisites

The following must be set in the operator's environment (`~/.zshrc`):

| Env var | Purpose | Notes |
|---|---|---|
| `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME` | MariaDB connection for synopsis + impact writes | |
| `OPENAI_API_KEY` | GPT-5.1 calls (synopsis + impact prompts) | Personal key. Do not copy to shared hosts. |
| `AWS_DEFAULT_REGION` | DynamoDB watermark | Defaults to `us-east-1` if unset |
| AWS credentials | DynamoDB watermark writes | Standard credential chain (env vars / `~/.aws/credentials` / SSO) |
| `RECITERAI_TEAMS_WEBHOOK_URL` | Teams alerts on failure / cost-guard trip | Workflows webhook URL. Treat as a credential — do not commit. |
| `RECITERAI_ALERT_MENTION_UPN` | UPN to @-mention on actionable alerts | e.g. `paa2013@med.cornell.edu` |
| `RECITERAI_ALERT_MENTION_NAME` | Display name for the @-mention | First token becomes the at-tag |

## Bootstrap (one-time)

The first production-style run faces a backlog: the gap between the
laptop POC's last run and today. As of 2026-05-14 that was **~1,862
papers**, accruing daily until bootstrap completes.

```bash
# From the repo root:
source ~/.zshrc
python -m scripts.run_daily_enrichment --full --verbose
```

`--full` bypasses the cost guard (which would otherwise refuse a delta
this large). At the rate observed in the PR 1 smoke test (~7.4 s/paper
sync, ~$0.067/paper), bootstrap is:

- Wall time: **~4 hours** for ~1,862 papers (longer by the time you
  actually run it — the backlog accrues).
- Cost: **~$125** at observed rates. Note this is materially higher
  than #37's modeling estimate of ~$0.018/paper; the gap is most likely
  GPT-5.1 reasoning-token usage on the impact prompt
  (`reasoning_effort=medium`). The number will be re-baselined after
  a few cycles of measured data.

**Operational constraints during bootstrap:**

- Laptop must be awake, lid open, on network for the duration. Mac
  power-management settings worth checking: caffeinate / Energy Saver
  → "Prevent automatic sleeping when display is off" while AC-powered.
- OpenAI rate limits could bite. Tier 5 limits handle the throughput
  comfortably, but if you hit a 429 the script's retry logic backs off
  and continues — net effect is wall time inflation, not failure.
- Don't close the terminal. Use `tmux` or `screen` if the run might
  outlast your session.
- Treat this as an overnight or weekend task, not a Tuesday morning
  task.

Output is JSON-serialized RunResult on stdout. On completion you'll
see `"status": "complete"` and a `cost_observed_usd` field. Save the
output for the post-bootstrap cost audit (below).

## Daily operation (steady state)

Once bootstrap is done, the watermark is current. Daily deltas are
expected to be 5–15 papers (~30–60 s wall time, well under $1/run).

```bash
# Once per day, from the repo root:
source ~/.zshrc
python -m scripts.run_daily_enrichment
```

Exit code 0 = clean (status `complete` or `no_op`). Exit code 1 =
something needs attention. Teams will already have alerted you on
real failures or cost-guard trips; the exit code matters mostly for
shell scripting if you ever do wrap this in a launcher.

### What success looks like

```json
{
  "status": "complete",
  "delta_size": 7,
  "successes": 7,
  "new_watermark_pmid": 42199999,
  "cost_observed_usd": "0.47",
  "cost_summary": { "call_count": 14, "total_input_tokens": 9800, ... },
  ...
}
```

### What failure looks like

```json
{
  "status": "failed",
  "delta_size": 7,
  "failure_reason": "1/7 pmids failed",
  ...
}
```

The watermark does NOT advance. The next run retries the same delta;
idempotent writes in `pipeline_enrichment/mariadb_writer.py` make
that safe. If a particular pmid persistently fails, investigate
manually before the daily delta grows unmanageably.

### Operator-availability constraint

Manual operation couples freshness to the operator's calendar. Two
weeks of PTO = two weeks of accruing delta = a longer catch-up run on
return. The cost guard will refuse a delta that's grown past ~500
papers (default threshold $30 / per-paper $0.06); `--full` is the
escape hatch but is itself a multi-hour run.

If sustained absence is expected, options in declining order of
sensibility:

1. Run `--full` on the last day before leaving (clears the backlog up
   to that point).
2. Hand the daily run off to another operator with appropriate creds
   (treat this as a real handoff, not informal coverage).
3. Accept the catch-up cost on return.

This constraint is part of the case for automation. It is written
down here so future-you knows why the org-key blocker matters
operationally, not just technically.

## Watermark + ops state

The watermark lives in DDB at `PK = WATERMARK#daily_enrichment` /
`SK = STATE`. Useful reads from `aws` CLI when debugging:

```bash
aws dynamodb get-item \
  --table-name reciterai \
  --key '{"PK":{"S":"WATERMARK#daily_enrichment"},"SK":{"S":"STATE"}}'
```

Fields of interest:
- `last_successful_max_pmid` — only advances on a clean run
- `last_run_status` — `complete | failed | in_progress`
- `last_run_started_at` — set every `mark_run_started` call
- `last_run_id` — UUID4, useful for cross-referencing in Teams alerts

## Cost reconciliation (deferred)

The `cost_guard.py` default of $0.060/paper and the projected ~$430/yr
were sized against #37's original modeling estimate. The 3-paper smoke
test came in at ~$0.067/paper (~12% higher), suggesting the model
under-counted output tokens (likely cause: reasoning tokens from
`reasoning_effort=medium` on the impact prompt).

A proper audit should happen once we have ≥2 weeks of measured
`cost_observed_usd` data from real daily runs. Until then, the docs
above use the smoke-test rate; the cost_guard default stays at $0.060
since it remains conservative against the observed rate; the $430/yr
projection is not quoted in this doc because it's built on an estimate
that didn't survive contact with measured data.

## Follow-up trigger

The automation revisit is captured as [issue #46](https://github.com/wcmc-its/ReciterAI/issues/46).
The trigger is:

> When an org-managed OpenAI API key (or Azure OpenAI tenant access)
> becomes available, revisit the deploy story: B1 (cron on ReciterDB
> host) or A (CDK migration → Lambda + VPC). The choice depends on
> whether other MariaDB-reaching workloads are coming alongside.

Until that trigger fires, run the CLI by hand. The job already does
the load-bearing work — watermark, cost guard, alerts, idempotent
writes; the cron layer is gravy and gravy can wait.
