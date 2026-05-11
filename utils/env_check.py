"""
Environment pre-check script for ReCiter AI Chatbot integration pipeline.

Runs DESCRIBE queries against ReciterDB to resolve RESEARCH.md Open Questions 2-4
BEFORE any pipeline SQL executes.

Usage:
    python3 utils/env_check.py
    # or from within a script:
    from utils.env_check import run_env_checks
    run_env_checks()
"""

import os
import sys
import logging

# Import POC's core/db.py for ReciterDB SQLAlchemy connection management.
# The POC repo (formerly /Users/paulalbert/Dropbox/GitHub/ReciterAI) was renamed
# to ReciterAI-POC during the restructure; core/db.py + dependencies (config,
# logging_setup, models) were not lifted into this repo to avoid pulling in the
# full POC package. Re-home into a local lib/ module when this dependency gets
# revisited.
sys.path.insert(0, '/Users/paulalbert/Dropbox/GitHub/ReciterAI-POC')

logger = logging.getLogger(__name__)


def run_env_checks():
    """
    Run all environment pre-checks for the ReCiter AI pipeline.

    Checks:
    1. DB_USERNAME environment variable is set (Open Question 1)
    2. reciterai_synopsis.external_id column exists (Open Question 3 / A6 correction)
    3. analysis_summary_person column names (Open Question 2)
    4. reciterai_keyword_relevance schema and data presence (Open Question 4)

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

    # Import get_engine after validating DB_USER
    try:
        from core.db import get_engine
    except ImportError as e:
        errors.append(f"Cannot import ReciterAI core.db: {e}. Check sys.path points to ReciterAI repo.")
        _print_results(results, errors)
        sys.exit(1)

    # Get database connection
    try:
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

        if 'external_id' not in synopsis_col_names:
            errors.append(
                "CRITICAL: reciterai_synopsis.external_id NOT FOUND. "
                "The pipeline uses external_id as the PMID-equivalent join column (per A6 correction). "
                f"Actual columns: {synopsis_col_names}"
            )
        else:
            results['reciterai_synopsis.external_id'] = 'EXISTS (join column confirmed)'
            print("  [OK] external_id column confirmed (PMID join column)")

        if 'synopsis' not in synopsis_col_names:
            errors.append(
                f"CRITICAL: reciterai_synopsis.synopsis NOT FOUND. "
                f"Actual columns: {synopsis_col_names}"
            )
        else:
            print("  [OK] synopsis column confirmed")

        # --- Check 3: analysis_summary_person columns (Open Question 2) ---
        print("\n[CHECK] analysis_summary_person schema:")
        asp_cols = _describe_table(conn, 'analysis_summary_person')
        asp_col_names = [c['Field'] for c in asp_cols]
        print(f"  Columns: {asp_col_names}")

        # Check for the specific columns the SQL queries use
        expected_asp_cols = ['nameFirst', 'nameLast', 'department', 'hindexNIH', 'personIdentifier']
        found_asp_cols = []
        missing_asp_cols = []
        for col in expected_asp_cols:
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
            print("  [OK] All expected columns present: nameFirst, nameLast, department, hindexNIH, personIdentifier")

        # --- Check 4: reciterai_keyword_relevance schema (Open Question 4) ---
        print("\n[CHECK] reciterai_keyword_relevance schema:")
        kw_cols = _describe_table(conn, 'reciterai_keyword_relevance')
        kw_col_names = [c['Field'] for c in kw_cols]
        print(f"  Columns: {kw_col_names}")

        expected_kw_cols = ['keyword', 'relevanceScore', 'external_id', 'entity_type']
        for col in expected_kw_cols:
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
