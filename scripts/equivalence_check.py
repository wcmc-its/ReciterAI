"""Step-1 gate for #37: equivalence sanity check.

Picks N recent PMIDs that already have synopsis + impact rows in MariaDB
(populated by the POC laptop runs), runs the ported ReciterAI code path
against the same inputs, and emits a diff report.

Acceptance criteria (per #37 step 1 gate):
- Synopsis: semantically equivalent by eyeball. Output is a side-by-side
  diff that the operator reads.
- Impact: numeric. Acceptance is within ±5 points on ≥18/20 PMIDs;
  anything wider on >2/20 is a red flag worth investigating before
  step 2 (the scheduled job).

Usage:
    python -m scripts.equivalence_check --n 20
    python -m scripts.equivalence_check --n 20 --output /tmp/eq.json

Requires:
- DB_HOST / DB_USERNAME / DB_PASSWORD / DB_NAME env vars (MariaDB read)
- OPENAI_API_KEY env var

This is a one-shot script. Delete once #37 step 1 ships and the
equivalence evidence is captured in the step-1 PR description.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Ensure repo root is on path when invoked as `python -m scripts.equivalence_check`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from pipeline_enrichment.impact import score_impact
from pipeline_enrichment.synopsis import generate_synopsis
from utils.db import get_engine

logger = logging.getLogger(__name__)


# Pull the N most-recently-enriched PMIDs that have BOTH synopsis + impact.
# Filtering on `entity_type='publication'` matches the daily-job scope;
# ordering by `external_id DESC` is a reasonable proxy for recency given
# the upstream synopsis pipeline assigns IDs sequentially.
_SAMPLE_SQL = """
SELECT
    a.pmid,
    a.articleTitle,
    a.journalTitleVerbose,
    a.articleYear,
    a.datePublicationAddedToEntrez,
    a.citationCountNIH,
    a.percentileNIH,
    a.relativeCitationRatioNIH,
    r.abstractVarchar,
    s.synopsis AS old_synopsis,
    i.impactScore AS old_impact_score,
    i.justification AS old_impact_justification
FROM reciterai_synopsis s
JOIN reciterai_impact i
    ON i.external_id = s.external_id
    AND i.entity_type = s.entity_type
JOIN analysis_summary_article a ON a.pmid = s.external_id
LEFT JOIN reporting_abstracts r ON r.pmid = a.pmid
WHERE s.entity_type = 'publication'
  AND s.synopsis IS NOT NULL
  AND s.synopsis != ''
  AND a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
ORDER BY s.external_id DESC
LIMIT :n
"""


@dataclass
class EquivalenceRow:
    """One PMID's old vs. new comparison, suitable for JSON dump."""

    pmid: str
    title: str
    old_synopsis: str
    new_synopsis: str | None
    new_synopsis_error: str | None
    old_impact_score: int
    new_impact_score: int | None
    impact_delta: int | None
    old_impact_justification: str
    new_impact_justification: str | None
    new_impact_error: str | None
    new_synopsis_input_tokens: int
    new_synopsis_output_tokens: int
    new_impact_input_tokens: int
    new_impact_output_tokens: int


def fetch_sample(engine, n: int) -> list[dict]:
    """Read N PMIDs + old synopsis/impact from MariaDB."""
    with engine.connect() as conn:
        rows = conn.execute(text(_SAMPLE_SQL), {"n": n}).mappings().all()
    return [dict(r) for r in rows]


def run_one(row: dict) -> EquivalenceRow:
    """Run the ported code path against one MariaDB row."""
    pmid = str(row["pmid"])
    title = row["articleTitle"] or ""

    syn = generate_synopsis(
        pmid=pmid,
        title=title,
        journal=row.get("journalTitleVerbose"),
        year=row.get("articleYear"),
        abstract=row.get("abstractVarchar"),
    )

    # Impact prompt builder takes the same dict shape MariaDB returns.
    imp = score_impact(pub_data=row)

    old_score = int(row.get("old_impact_score") or 0)
    new_score = imp.impact_score
    delta = abs(new_score - old_score) if new_score is not None else None

    return EquivalenceRow(
        pmid=pmid,
        title=title[:120],
        old_synopsis=row["old_synopsis"] or "",
        new_synopsis=syn.synopsis,
        new_synopsis_error=syn.error,
        old_impact_score=old_score,
        new_impact_score=new_score,
        impact_delta=delta,
        old_impact_justification=row.get("old_impact_justification") or "",
        new_impact_justification=imp.justification,
        new_impact_error=imp.error,
        new_synopsis_input_tokens=syn.input_tokens,
        new_synopsis_output_tokens=syn.output_tokens,
        new_impact_input_tokens=imp.input_tokens,
        new_impact_output_tokens=imp.output_tokens,
    )


def render_report(results: list[EquivalenceRow]) -> str:
    """Human-readable report — side-by-side synopses + impact deltas."""
    n = len(results)
    n_impact_pass = sum(
        1 for r in results
        if r.impact_delta is not None and r.impact_delta <= 5
    )
    n_synopsis_ok = sum(1 for r in results if r.new_synopsis and not r.new_synopsis_error)
    n_impact_err = sum(1 for r in results if r.new_impact_error)

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("Equivalence check — #37 step 1 gate")
    lines.append("=" * 80)
    lines.append(f"PMIDs sampled:                {n}")
    lines.append(f"Synopsis produced cleanly:    {n_synopsis_ok}/{n}")
    lines.append(f"Impact within +/- 5 points:   {n_impact_pass}/{n}  "
                 f"(acceptance gate: >= {max(0, n - 2)}/{n})")
    lines.append(f"Impact errors:                {n_impact_err}/{n}")
    lines.append("")

    for r in results:
        lines.append("-" * 80)
        lines.append(f"PMID {r.pmid}: {r.title}")
        lines.append("")
        lines.append("  OLD synopsis: " + (r.old_synopsis or "<empty>"))
        lines.append("  NEW synopsis: " + (r.new_synopsis or f"<error: {r.new_synopsis_error}>"))
        lines.append("")
        delta_str = (
            f"Δ={r.impact_delta}" if r.impact_delta is not None
            else f"Δ=NA error={r.new_impact_error}"
        )
        lines.append(
            f"  Impact: old={r.old_impact_score}  new={r.new_impact_score}  {delta_str}"
        )
        lines.append("  OLD justification: " + (r.old_impact_justification or "<empty>"))
        lines.append("  NEW justification: " + (r.new_impact_justification or "<empty>"))
    lines.append("=" * 80)

    if n_impact_pass < n - 2:
        lines.append(
            "GATE FAILED: too many impact deltas > 5 points. "
            "Investigate before step 2."
        )
    elif n_synopsis_ok < n:
        lines.append(
            "GATE WARN: some synopses failed to generate. "
            "Inspect synopsis errors before step 2."
        )
    else:
        lines.append("GATE: numeric impact gate passed; eyeball synopsis equivalence above.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.equivalence_check",
        description=__doc__,
    )
    parser.add_argument(
        "--n", type=int, default=20,
        help="number of PMIDs to sample (default: 20).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="optional path to write the JSON results (default: skip JSON).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = get_engine()
    sample = fetch_sample(engine, args.n)
    if not sample:
        print("No PMIDs returned from MariaDB; check DB credentials and the sample SQL.")
        return 1

    print(f"Running equivalence check on {len(sample)} PMIDs...", flush=True)
    results: list[EquivalenceRow] = []
    for i, row in enumerate(sample, 1):
        print(f"  [{i}/{len(sample)}] pmid={row['pmid']}", flush=True)
        results.append(run_one(row))

    report = render_report(results)
    print()
    print(report)

    if args.output:
        args.output.write_text(
            json.dumps([asdict(r) for r in results], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nJSON written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
