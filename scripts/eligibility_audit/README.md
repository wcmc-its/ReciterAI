# scripts/eligibility_audit/

Reproducible eligibility-capture audit: how much of the eligibility prose on `GRANT#`
items does the SPS regex flag derivation (`deriveEligibilityFlags`) actually capture,
scored against a Sonnet reference extraction. Results, proposed schema, and the draft
replacement judge prompt live in `docs/grant-matching-measurements-runbook.md` (§2).
Tracking issue: #290.

## Regeneration order

```
python scripts/eligibility_audit/scan_grants.py   # 1. corpus scan + canary + 100-item sample
python scripts/eligibility_audit/extract.py       # 2. Sonnet reference extraction (Bedrock)
python scripts/eligibility_audit/score.py         # 3. per-facet agreement + uncaptured prevalence
```

Each step reads the previous step's output from `out/` in this directory. The sample
seed is fixed (`20260709`), so against the same corpus snapshot the sample — and
therefore the whole audit — is reproducible. Against a *later* corpus (new ingests),
the sample differs by construction; treat each run as a fresh point-in-time snapshot
and record the date.

## Requirements

- AWS credentials with **read-only** DynamoDB access to the `reciterai` table
  (us-east-1) and Bedrock Runtime `Converse` access to `us.anthropic.claude-sonnet-4-6`.
- `boto3` (already in `requirements.txt`). No other dependencies.

## Runtime / cost

| Step | Calls | Wall time |
|---|---|---|
| `scan_grants.py` | DynamoDB scan only (table is multi-tenant; ~180k items scanned) | ~1–2 min |
| `extract.py` | **~100 Bedrock Sonnet Converse calls** (one per sampled item, 4 workers) | ~5–10 min |
| `score.py` | none (local) | seconds |

## Outputs

Everything lands in `scripts/eligibility_audit/out/` (created on first run, covered by
the repo `.gitignore` `out/` pattern):

- `corpus_stats.json` — corpus counts, per-source breakdown, canaries, regex fire counts
- `sample.json` — the 100 stratified items **including full eligibility/synopsis text**
- `extractions.json` / `extraction_errors.json` — reference labels per item pk
- `scores.json` — agreement, miss rows (with prose snippets), uncaptured-facet prevalence

**Never commit the outputs.** They are point-in-time snapshots of live grant text;
only the aggregate numbers belong in docs.
