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
laptop POC's last run and today. As of 2026-05-14 (after a 50-paper
test run) that was **~1,815 papers**, accruing daily until bootstrap
completes.

```bash
# From the repo root:
source ~/.zshrc
python3 -m scripts.run_daily_enrichment --full --verbose
```

`--full` bypasses the cost guard. At the rate measured in a 50-paper
run on 2026-05-14 (`$0.471495 / 50 papers = $0.00943/paper`, n=100 API
calls), bootstrap is:

- Cost: **~$18** for ~1,815 papers. Materially lower than #37's
  modeling estimate of ~$0.035/paper Batch / ~$0.060/paper sync —
  the model under-counted output tokens (likely because it didn't
  account for the level of reasoning the impact prompt actually
  produces, or it priced against an older tier).
- Wall time: TBD. The 50-paper run took several minutes; bootstrap is
  proportional. Plan for hours, not days; use `tmux` / `screen` so the
  run survives a terminal close.

**Operational constraints during bootstrap:**

- Laptop must be awake, lid open, on network for the duration. Mac
  power-management settings worth checking: caffeinate / Energy Saver
  → "Prevent automatic sleeping when display is off" while AC-powered.
- OpenAI rate limits could bite. Tier 5 limits handle the throughput
  comfortably, but if you hit a 429 the script's retry logic backs off
  and continues — net effect is wall time inflation, not failure.
- Don't close the terminal. Use `tmux` or `screen` if the run might
  outlast your session.

Output is JSON-serialized RunResult on stdout. On completion you'll
see `"status": "complete"` and a `cost_observed_usd` field. Save the
output as another data point for the cost numbers in this doc.

## Daily operation (steady state)

Once bootstrap is done, the watermark is current. Daily deltas are
expected to be 5–15 papers. At measured rate (~$0.0094/paper sync),
that's **~$0.05–0.15 per run** — trivially small.

```bash
# Once per day, from the repo root:
source ~/.zshrc
python3 -m scripts.run_daily_enrichment
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
return. The cost guard will refuse a delta that's grown past ~3,000
papers (default threshold $30 / per-paper $0.010); `--full` is the
escape hatch.

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

## Cost reconciliation

Done as of 2026-05-14, based on a 50-paper test run (n=100 API calls):

| Metric | Measured | Earlier guess |
|---|---|---|
| Per-paper sync | **$0.00943** | ~$0.067 (hand-wave) / ~$0.060 (cost_guard default) |
| Bootstrap (~1,815 papers) | **~$17** | ~$125 |
| Annual at 5 papers/day × 250 days | **~$12/yr** | ~$430/yr |
| Annual rescore (~6,200 papers) | **~$58** | ~$110 (Batch) / ~$415 (sync) |

The earlier numbers were predicated on $0.067/paper, which was an
unverified eyeball from the PR 1 smoke test rather than a measurement.
The 50-paper run captured token counts directly: 1,649 input + 265
output tokens per call × 2 calls/paper × ($1.25/$10 per Mtok input/output)
= $0.00943/paper.

The `cost_guard.py` default per-paper has been bumped to **$0.010**
(slight conservative overestimate of measured). The $30 threshold
hasn't moved; at the new rate it trips at ~3,000 papers, well above
plausible non-anomalous workloads. The threshold is still useful as
an anomaly detector for "watermark hasn't advanced in months" or
"corpus filter regression" scenarios.

**What this changes architecturally:** the Batch-mode + threshold-
selection conversation that was happening before this audit is largely
moot at these prices. Batch's ~50% savings is ~$9 on the bootstrap and
~$6/yr on the annual rescore. Not worth the architectural overhead
(two-process coordination, async result-collection, in-flight state in
DDB). If costs ever drift materially upward, revisit; for now sync
everywhere is the right answer.

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
