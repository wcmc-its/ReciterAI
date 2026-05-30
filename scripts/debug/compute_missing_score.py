"""Recompute the detector's missing_score PMID set, ad hoc.

Mirrors pipeline_onboarding/detector.py's `_evaluate` filter:
  missing_score = PMIDs with synopsis AND PROCESSING# status not in
                  {complete, quarantined}

The detector's STAGE# row publishes the *count* (today: 1163) but not the
individual PMIDs, and the per-CWID GitHub issues that would carry the
detail are currently suppressed (issue #106 — cold-start flood guard).

Writes the missing_score PMID list to stdout (newline-separated) for
piping into `score_publications.py --pmids`. Also prints summary to stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable when this script runs from scripts/debug/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Module-level imports after the path hack.
import utils.secrets_loader  # noqa: F401,E402 — populates DB_* env vars

from utils.dynamodb_helpers import (  # noqa: E402
    get_dynamo_client,
    get_processing_rows,
)
from utils.sql_queries import scan_faculty_publication_gaps  # noqa: E402
from utils.stage_records import STATUS_COMPLETE  # noqa: E402

_STATUS_QUARANTINED = "quarantined"
_SCORED_OR_TERMINAL = frozenset({STATUS_COMPLETE, _STATUS_QUARANTINED})

TABLE_NAME = "reciterai"


def main() -> int:
    print("Running faculty publication gap scan…", file=sys.stderr)
    gap_rows = scan_faculty_publication_gaps()
    print(f"  gap_rows: {len(gap_rows)}", file=sys.stderr)

    pmids_with_synopsis = sorted({
        str(r["pmid"]) for r in gap_rows if r["has_synopsis"]
    })
    print(
        f"  unique PMIDs with synopsis: {len(pmids_with_synopsis)}",
        file=sys.stderr,
    )

    print("Fetching PROCESSING# rows…", file=sys.stderr)
    client = get_dynamo_client()
    processing_rows = get_processing_rows(client, TABLE_NAME, pmids_with_synopsis)
    print(f"  PROCESSING# rows returned: {len(processing_rows)}", file=sys.stderr)

    missing_score = sorted(
        p for p in pmids_with_synopsis
        if processing_rows.get(p, {}).get("status") not in _SCORED_OR_TERMINAL
    )
    print(f"\nmissing_score PMIDs: {len(missing_score)}", file=sys.stderr)

    for pmid in missing_score:
        print(pmid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
