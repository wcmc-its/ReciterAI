#!/usr/bin/env python3
"""
Wave 0b -- validate the onboarding cost-guard threshold.

#80 Phase 2, PLAN section 5 Wave 0b / spec OQ#1.

Computes the distribution of per-CWID accepted-publication PMID counts for
WCM full-time faculty, to check whether `onboarding_cost_guard_max_pmids`
(config/thresholds.json) is set sensibly: routine onboarding of one faculty
member should not trip the guard, while genuine anomalies should.

Population : WCM full-time faculty (identity.fullTimeFaculty = 'yes') with
             at least one qualifying publication.
Per-CWID   : exactly what get_pmids_for_cwid() returns -- Academic Articles,
             articleYear >= 2020 (the D4 cutoff). This is the worst-case net
             work set when onboarding a never-scored researcher (every PMID
             is unscored, so net work == the full set).

Read-only. No writes. DB credentials come from DB_* env vars via utils.db.
"""

from __future__ import annotations

import json
import os
import sys

# Make `utils` importable when run as scripts/debug/<this>.py
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import text  # noqa: E402

from utils.db import get_engine  # noqa: E402

# Per-CWID PMID counts for full-time faculty. Mirrors PMIDS_BY_CWID_SQL's join
# + filters (the query behind get_pmids_for_cwid in utils/sql_queries.py), adds
# the faculty scope used by AUTHOR_MAPPING_SQL, and aggregates per CWID.
PER_CWID_COUNT_SQL = """
SELECT
    au.personIdentifier      AS cwid,
    COUNT(DISTINCT a.pmid)   AS pmid_count
FROM analysis_summary_author au
JOIN analysis_summary_article a ON a.pmid = au.pmid
JOIN identity id ON id.cwid = au.personIdentifier
WHERE a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
  AND id.fullTimeFaculty = 'yes'
GROUP BY au.personIdentifier
"""

_THRESHOLDS_PATH = os.path.join(_REPO_ROOT, "config", "thresholds.json")
with open(_THRESHOLDS_PATH) as _f:
    THRESHOLD = int(json.load(_f)["onboarding_cost_guard_max_pmids"])


def percentile(sorted_vals: list[int], p: float) -> float:
    """Linear-interpolated percentile (p in 0..100)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return float(sorted_vals[lo])
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(PER_CWID_COUNT_SQL)).all()

    counts = sorted(int(r[1]) for r in rows)
    n = len(counts)
    if n == 0:
        print("No faculty rows returned -- check DB connection / data.")
        return

    mean = sum(counts) / n
    pctls = {p: percentile(counts, p) for p in (50, 75, 90, 95, 99)}

    print("=" * 66)
    print("Wave 0b -- onboarding cost-guard threshold validation")
    print("=" * 66)
    print("Population : WCM full-time faculty with >=1 qualifying pub")
    print("Per-CWID   : Academic Article, articleYear >= 2020 (get_pmids_for_cwid)")
    print(f"Faculty N  : {n:,}")
    print(f"Min        : {counts[0]:,}")
    print(f"Mean       : {mean:,.1f}")
    print(f"Median p50 : {pctls[50]:,.1f}")
    print(f"p75        : {pctls[75]:,.1f}")
    print(f"p90        : {pctls[90]:,.1f}")
    print(f"p95        : {pctls[95]:,.1f}")
    print(f"p99        : {pctls[99]:,.1f}")
    print(f"Max        : {counts[-1]:,}")
    print("-" * 66)
    for t in (150, 200, 250, 300, 400, 500):
        over = sum(1 for c in counts if c > t)
        print(f"  CWIDs with > {t:<4} PMIDs : {over:>5,}  ({over / n * 100:6.2f}%)")
    print("-" * 66)
    print(f"Top 10 counts : {counts[-10:][::-1]}")
    print("=" * 66)

    p95, p99 = pctls[95], pctls[99]
    over_threshold = sum(1 for c in counts if c > THRESHOLD)
    pct_over = over_threshold / n * 100
    print(f"\nCurrent onboarding_cost_guard_max_pmids = {THRESHOLD}")
    if THRESHOLD >= p99:
        print(
            f"VERDICT: {THRESHOLD} sits at/above p99 ({p99:,.0f}). Routine "
            f"onboarding does not trip the guard; {over_threshold} faculty "
            f"({pct_over:.2f}%) exceed it and would need --allow-cost-override "
            f"-- intended behavior for high-volume outliers. CONFIRM 300."
        )
    elif THRESHOLD >= p95:
        print(
            f"VERDICT: {THRESHOLD} is between p95 ({p95:,.0f}) and p99 "
            f"({p99:,.0f}); {pct_over:.2f}% of faculty exceed it. Consider "
            f"raising toward p99 so the top decile do not routinely need an "
            f"override."
        )
    else:
        print(
            f"VERDICT: {THRESHOLD} is below p95 ({p95:,.0f}); {pct_over:.2f}% "
            f"of faculty exceed it. Too low -- routine onboarding would "
            f"frequently trip the guard. Raise it."
        )


if __name__ == "__main__":
    main()
