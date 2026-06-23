"""Opportunity exclusion list — ids held out of GrantRecs reverse-matching.

Both ingest paths skip-persist these (never written to the GRANT# table), so SPS
never indexes or matches them. Reversible: remove an id from
``config/excluded_opportunities.json`` and re-ingest. Source-agnostic by
``opportunity_id`` (works for curated and grants_gov alike)."""
import json
import os

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "excluded_opportunities.json")


def load_excluded_ids(path: str = _DEFAULT_PATH) -> set:
    """Return the set of excluded opportunity_ids (empty set if the file is absent)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return set()
    return {e["opportunity_id"] for e in data.get("excluded_opportunities", []) if e.get("opportunity_id")}
