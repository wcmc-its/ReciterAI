"""TOPIC#-only loader for a partial scoring_results.json.

Use case: I ran `score_publications.py --pmids …` (cold-path mode without
`--emit-envelope`) and got `scoring_results.json` on disk, but NOT TOPIC#
rows in DDB. The full `load_dynamodb.py` would also rebuild FACULTY#
records' `top_topics` vectors — and since scoring_results.json contains
only the catchup batch (1,060 PMIDs, not the full ~8K corpus), the
FACULTY# rebuild would CORRUPT affected CWIDs' top_topics by recomputing
them from the partial input.

This wrapper imports `build_topic_records` and `load_records` from
`load_dynamodb` so the TOPIC# build/write logic stays single-sourced,
and skips everything else (FACULTY#, TOOL#, DEEPDIVE#, TOOL_INDEX#,
TAXONOMY# — those are either dangerous on a partial input, or already
stable).

TOPIC# rows are batch-written via boto3 `batch_writer()` — pure upsert on
(PK, SK). For PMIDs with no prior TOPIC# rows (the catchup case), this is
equivalent to insert. For PMIDs that already had TOPIC# rows, the upsert
overwrites — which is what we want when re-scoring.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Repo root on sys.path so we can import the top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import utils.secrets_loader  # noqa: F401,E402

from load_dynamodb import build_topic_records, load_records, DEFAULT_MIN_SCORE  # noqa: E402
from utils.dynamodb_helpers import get_dynamo_client, TABLE_NAME  # noqa: E402


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    scoring_path = repo_root / "scoring_results.json"
    author_path = repo_root / "author_mapping.json"

    assert scoring_path.exists(), f"missing {scoring_path}"
    assert author_path.exists(), f"missing {author_path}"

    scoring_data = json.load(open(scoring_path))
    scoring_results = scoring_data["scored_publications"]
    taxonomy_version = scoring_data["taxonomy_version"]
    author_mapping = json.load(open(author_path))

    print(f"Loaded {len(scoring_results)} scored publications "
          f"(taxonomy: {taxonomy_version})")
    print(f"Loaded {len(author_mapping)} author mappings")
    print(f"min_score: {DEFAULT_MIN_SCORE}")
    print()

    print("--- Building TOPIC# records ---")
    topic_records = build_topic_records(
        scoring_results, author_mapping, taxonomy_version, DEFAULT_MIN_SCORE
    )

    if not topic_records:
        print("No TOPIC# records to write — nothing to do.")
        return 0

    print(f"\n--- Writing {len(topic_records)} TOPIC# records to DDB ---")
    dynamo_client = get_dynamo_client()
    load_records(dynamo_client, TABLE_NAME, topic_records, "TOPIC#")
    print("\nDone. Skipped: FACULTY# (would corrupt on partial input), "
          "TOOL#, DEEPDIVE#, TOOL_INDEX#, TAXONOMY# (stable / not needed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
