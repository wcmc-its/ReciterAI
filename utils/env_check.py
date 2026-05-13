"""
Environment pre-check script for ReCiter AI pipeline.

Runs DESCRIBE queries against ReciterDB to resolve RESEARCH.md Open Questions 2-4
BEFORE any pipeline SQL executes. Also validates config/thresholds.json against its
schema at startup (Phase 12 D-27).

Usage:
    python3 utils/env_check.py
    # or from within a script:
    from utils.env_check import run_env_checks
    run_env_checks()
"""

import json
import os
import sys
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Repo root: two levels up from this file (utils/env_check.py -> utils/ -> root).
REPO_ROOT = Path(__file__).resolve().parents[1]

# --- G-1: Data-driven column expectations ---
# Replace inline string literals with a single data structure so adding or
# renaming a column is a one-line change here, not a grep-and-replace across
# the whole module. Each Check-N block iterates over its table's list.
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "reciterai_synopsis": [
        "external_id",
        "synopsis",
    ],
    "analysis_summary_person": [
        "nameFirst",
        "nameLast",
        "department",
        "hindexNIH",
        "personIdentifier",
    ],
    "reciterai_keyword_relevance": [
        "keyword",
        "relevanceScore",
        "external_id",
        "entity_type",
    ],
}

# --- D-27: thresholds.json schema validation paths ---
THRESHOLDS_FILE = REPO_ROOT / "config/thresholds.json"
THRESHOLDS_SCHEMA = REPO_ROOT / "config/thresholds.schema.json"


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    """Load config/thresholds.json and return as a dict.

    Phase 12 D-27: exported helper so stages can read tunables without
    importing the full env-check module (which has DB dependencies).

    Args:
        path: Override path for testing. Defaults to THRESHOLDS_FILE.

    Returns:
        Parsed dict from the JSON file.

    Raises:
        FileNotFoundError: If the file does not exist. Message includes
            "thresholds.json" and a hint to run from the repo root.
    """
    target = path if path is not None else THRESHOLDS_FILE
    if not target.exists():
        raise FileNotFoundError(
            f"config/thresholds.json not found at {target}. "
            "Run from repo root or check config/thresholds.json exists."
        )
    return json.loads(target.read_text(encoding="utf-8"))


def run_env_checks():
    """
    Run all environment pre-checks for the ReCiter AI pipeline.

    Checks:
    1. DB_USERNAME environment variable is set (Open Question 1)
    2. reciterai_synopsis columns — external_id, synopsis (Open Question 3 / A6 correction)
    3. analysis_summary_person column names (Open Question 2)
    4. reciterai_keyword_relevance schema and data presence (Open Question 4)
    5. config/thresholds.json schema validation (Phase 12 D-27)

    Exits with code 1 if any critical check fails.
    """
    results = {}
    errors = []

    print("=== Running ReCiter AI Pipeline Environment Pre-Checks ===\n")

    # --- Check 1: DB_USERNAME env var (Open Question 1) ---
    db_user = os.environ.get('DB_USERNAME')
    assert db_user, "DB_USERNAME not set -- set it in ~/.zshrc and source it"
    results['DB_USERNAME'] = 'OK'
    print(f"[OK] DB_USERNAME is set: {db_user}")

    # Get database connection
    try:
        from utils.db import get_engine
        engine = get_engine()
        conn = engine.connect()
    except Exception as e:
        errors.append(f"Cannot connect to ReciterDB: {e}. Check DB_HOST, DB_USERNAME, DB_PASSWORD, DB_NAME env vars.")
        _print_results(results, errors)
        sys.exit(1)

    try:
        # Use sqlalchemy text() for raw SQL
        from sqlalchemy import text

        # --- Check 2: reciterai_synopsis columns (Open Question 3 / A6 correction) ---
        print("\n[CHECK] reciterai_synopsis schema:")
        synopsis_cols = _describe_table(conn, 'reciterai_synopsis')
        synopsis_col_names = [c['Field'] for c in synopsis_cols]
        print(f"  Columns: {synopsis_col_names}")

        for col in EXPECTED_COLUMNS['reciterai_synopsis']:
            if col not in synopsis_col_names:
                errors.append(
                    f"CRITICAL: reciterai_synopsis.{col} NOT FOUND. "
                    f"Actual columns: {synopsis_col_names}"
                )
            else:
                results[f'reciterai_synopsis.{col}'] = 'EXISTS'
                print(f"  [OK] {col} column confirmed")

        # --- Check 3: analysis_summary_person columns (Open Question 2) ---
        print("\n[CHECK] analysis_summary_person schema:")
        asp_cols = _describe_table(conn, 'analysis_summary_person')
        asp_col_names = [c['Field'] for c in asp_cols]
        print(f"  Columns: {asp_col_names}")

        found_asp_cols = []
        missing_asp_cols = []
        for col in EXPECTED_COLUMNS['analysis_summary_person']:
            if col in asp_col_names:
                found_asp_cols.append(col)
            else:
                missing_asp_cols.append(col)

        results['analysis_summary_person columns'] = asp_col_names
        print(f"  [OK] Found expected columns: {found_asp_cols}")

        if missing_asp_cols:
            errors.append(
                f"WARNING: analysis_summary_person is missing expected columns: {missing_asp_cols}. "
                f"Update SQL queries in utils/sql_queries.py to match actual schema. "
                f"Actual columns: {asp_col_names}"
            )
            print(f"  [WARNING] Missing expected columns: {missing_asp_cols}")
        else:
            print("  [OK] All expected columns present")

        # --- Check 4: reciterai_keyword_relevance schema (Open Question 4) ---
        print("\n[CHECK] reciterai_keyword_relevance schema:")
        kw_cols = _describe_table(conn, 'reciterai_keyword_relevance')
        kw_col_names = [c['Field'] for c in kw_cols]
        print(f"  Columns: {kw_col_names}")

        for col in EXPECTED_COLUMNS['reciterai_keyword_relevance']:
            if col not in kw_col_names:
                errors.append(
                    f"CRITICAL: reciterai_keyword_relevance.{col} NOT FOUND. "
                    f"Actual columns: {kw_col_names}"
                )
            else:
                print(f"  [OK] {col} column confirmed")

        # Sample TOOL# records
        print("\n  [SAMPLE] reciterai_keyword_relevance (entity_type='publication', LIMIT 5):")
        try:
            sample_rows = list(conn.execute(text(
                "SELECT keyword, relevanceScore, external_id "
                "FROM reciterai_keyword_relevance "
                "WHERE entity_type = 'publication' "
                "LIMIT 5"
            )))
            if sample_rows:
                for row in sample_rows:
                    print(f"    keyword={row[0]!r}, relevanceScore={row[1]}, external_id={row[2]!r}")
            else:
                print("    (no rows returned)")
        except Exception as e:
            print(f"    [WARNING] Could not run sample query: {e}")

        # Count check for publication keyword_relevance records
        print("\n  [COUNT] reciterai_keyword_relevance WHERE entity_type='publication':")
        try:
            count_result = conn.execute(text(
                "SELECT COUNT(*) FROM reciterai_keyword_relevance WHERE entity_type = 'publication'"
            ))
            pub_count = count_result.scalar()
            results['reciterai_keyword_relevance publication records'] = pub_count
            print(f"  Count: {pub_count}")
            if pub_count == 0:
                print(
                    "\n  *** HARD WARNING: No publication keyword_relevance records found. "
                    "TOOL# records will be empty. Resolve before running load_dynamodb.py. ***\n"
                )
            else:
                print(f"  [OK] {pub_count} publication keyword_relevance records found")
        except Exception as e:
            print(f"  [WARNING] Could not count keyword_relevance records: {e}")

    finally:
        conn.close()

    # --- Check 5: thresholds.json schema validation (Phase 12 D-27) ---
    print("\n[CHECK] config/thresholds.json schema validation:")
    try:
        import jsonschema
        cfg = json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))
        schema = json.loads(THRESHOLDS_SCHEMA.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(instance=cfg, schema=schema)
            results['thresholds.json'] = 'schema-valid'
            print("  [OK] config/thresholds.json is schema-valid")
        except jsonschema.ValidationError as err:
            errors.append(
                f"CRITICAL: thresholds.json invalid at {list(err.absolute_path)}: {err.message}"
            )
            print(f"  [ERROR] thresholds.json invalid: {err.message}")
    except FileNotFoundError as e:
        errors.append(f"CRITICAL: thresholds.json or thresholds.schema.json not found: {e}")
        print(f"  [ERROR] {e}")

    # --- Print summary ---
    _print_results(results, errors)

    if errors:
        sys.exit(1)


def _describe_table(conn, table_name: str) -> list:
    """Run DESCRIBE on a table and return rows as list of dicts."""
    from sqlalchemy import text
    rows = conn.execute(text(f"DESCRIBE {table_name}"))
    return [{'Field': row[0], 'Type': row[1], 'Null': row[2], 'Key': row[3],
             'Default': row[4], 'Extra': row[5]} for row in rows]


def _print_results(results: dict, errors: list):
    """Print the final summary."""
    print("\n=== Environment Pre-Check Results ===")
    for key, value in results.items():
        if isinstance(value, list):
            print(f"{key}: {value}")
        else:
            print(f"{key}: {value}")

    if errors:
        print("\n=== ERRORS ===")
        for error in errors:
            print(f"  ERROR: {error}")
        print("\nPre-check FAILED. Resolve errors before running pipeline scripts.")
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    run_env_checks()
