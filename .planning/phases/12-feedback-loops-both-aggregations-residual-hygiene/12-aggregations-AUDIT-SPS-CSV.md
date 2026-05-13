# SPS Audit — `cwid_subtopic_counts.csv` Downstream Reader Check

**Phase:** 12 (feedback-loops-both-aggregations-residual-hygiene)
**Plan:** `12-aggregations`
**Checkpoint:** Task 2.5 (W-3 SPS-audit) — required by CONTEXT D-13 before Task 3 (CSV rename + dual-write deprecation).
**Audit date:** 2026-05-12
**Auditor:** automated grep against pinned SPS commit.

## SPS repo pin

| Field | Value |
|---|---|
| Repo path | `~/Dropbox/GitHub/Scholars-Profile-System/` |
| Remote | `wcmc-its/Scholars-Profile-System` |
| Branch | `master` |
| Commit SHA | `4e0e6b8d4c35899159d7d29687f85bacad10a7d6` (short `4e0e6b8`) |
| Commit date | 2026-05-12 20:32:51 -0400 |
| Commit message | `fix(ui): hide header search until hero scrolls past (closes #215) (#222)` |
| Tracked files | 327 |

## Search patterns

Three patterns, separated for evidence quality:

1. Exact filename: `cwid_subtopic_counts.csv`
2. Bare basename: `cwid_subtopic_counts` (catches variable names, partial path references)
3. Broader substring: `subtopic_counts` (catches abbreviations / renames that still point at this file)

## File-category split

| Category | Extensions | File count | Hits |
|---|---|---|---|
| Code | `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.sql` | 301 | **0** |
| Config | `.yml`, `.yaml`, `.json`, `.env`, `.env.*` | 9 | **0** |
| Docs | `.md`, `.rst`, `.txt` | 11 | **0** |
| Other tracked (sh, conf, Dockerfile, Makefile, etc.) | — | 6 | **0** |

## Commands executed (reproducible)

```bash
SPS=~/Dropbox/GitHub/Scholars-Profile-System

# Code references
git -C "$SPS" grep -nE "cwid_subtopic_counts|subtopic_counts" -- \
  '*.py' '*.js' '*.ts' '*.tsx' '*.jsx' '*.sql'
# (no output)

# Config references
git -C "$SPS" grep -nE "cwid_subtopic_counts|subtopic_counts" -- \
  '*.yml' '*.yaml' '*.json' '*.env' '*.env.*'
# (no output)

# Doc references
git -C "$SPS" grep -nE "cwid_subtopic_counts|subtopic_counts" -- \
  '*.md' '*.rst' '*.txt'
# (no output)

# Everything else tracked
git -C "$SPS" grep -nE "cwid_subtopic_counts|subtopic_counts" -- \
  ':!*.py' ':!*.js' ':!*.ts' ':!*.tsx' ':!*.jsx' ':!*.sql' \
  ':!*.yml' ':!*.yaml' ':!*.json' ':!*.env' ':!*.env.*' \
  ':!*.md' ':!*.rst' ':!*.txt'
# (no output)
```

All four commands returned zero matches.

## Conclusion

**Zero references** to `cwid_subtopic_counts.csv` (or any of the broader patterns) anywhere in SPS at pinned commit `4e0e6b8`. The CSV is a ReciterAI-internal artifact that SPS does not consume — it never made it into the SPS contract.

## Decision

Per the CONTEXT D-13 decision tree:

> Zero hits in current code + zero hits in active docs → option A. The filename was a pre-Phase-12 ReciterAI artifact that SPS never consumed. Drop the legacy name in Phase 13; no migration needed.

**Answer recorded for the `12-aggregations` continuation agent:**

> audit performed — SPS confirms **no** downstream readers of `cwid_subtopic_counts.csv` (pinned commit `4e0e6b8`, 327 tracked files, zero hits across code / config / docs / other). Phase 12 ships the rename + dual-write deprecation window per plan Task 3. Legacy `cwid_subtopic_counts.csv` can be dropped in Phase 13; no SPS-side coordination required.

## Carry-forward note

Although there are zero hits today, the rename should still ship with the dual-write deprecation window as planned. The rename's safe-path default protects against:

- Out-of-tree consumers (operator scripts, ad-hoc notebooks, archived prototypes) that are not in SPS but might exist elsewhere on this machine or in `wcmc-its` repos this audit did not cover.
- A future commit on SPS that adds a reference between now and Phase 13 — the dual-write gives one cycle of grace.

Phase 13 (or whichever phase drops the legacy CSV) should re-run this audit at that point's pinned SPS commit before removing the dual-write. The grep is cheap; redoing it then is cheaper than discovering a new reader after the legacy emit is gone.
