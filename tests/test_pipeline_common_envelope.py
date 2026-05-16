"""pipeline_common.envelope — STAGE# envelope parse + DDB-typing helpers.

Covers:
- parse_envelope_from_stdout picks the last JSON object off stdout and
  raises when none is present. (The parser tests moved here from
  test_pipeline_hot_orchestrator.py when the helpers were promoted out of
  pipeline_hot/handlers/score.py — #80 Phase 2 PR 1.)
- to_ddb_typed_envelope wraps values in DynamoDB attribute types and
  re-casts cost_observed_usd from a JSON string to a numeric (N) attr.
"""

from __future__ import annotations

import pytest

from pipeline_common.envelope import (
    parse_envelope_from_stdout,
    to_ddb_typed_envelope,
)


# ---------- parse_envelope_from_stdout ----------


def test_parse_envelope_picks_last_json_object_on_stdout():
    stdout = (
        "Loaded taxonomy: taxonomy_v2 (50 topics)\n"
        "Some log line\n"
        '{"PK": "STAGE#score_publications#GLOBAL", "status": "complete"}\n'
    )
    env = parse_envelope_from_stdout(stdout)
    assert env["PK"] == "STAGE#score_publications#GLOBAL"
    assert env["status"] == "complete"


def test_parse_envelope_skips_trailing_log_noise():
    # The envelope is not necessarily the literal last line — plain-text
    # log noise can follow it. The bottom-up scan still finds it.
    stdout = (
        '{"PK": "STAGE#score_publications#GLOBAL", "status": "complete"}\n'
        "wrote 5 rows\n"
        "done\n"
    )
    assert parse_envelope_from_stdout(stdout)["status"] == "complete"


def test_parse_envelope_raises_when_no_json_found():
    with pytest.raises(RuntimeError, match="no JSON envelope"):
        parse_envelope_from_stdout("just some text\nmore text\n")


# ---------- to_ddb_typed_envelope ----------


def test_to_ddb_typed_envelope_wraps_strings_and_ints():
    typed = to_ddb_typed_envelope({
        "PK": "STAGE#score_publications#GLOBAL",
        "status": "complete",
        "duration_ms": 42,
    })
    assert typed["PK"] == {"S": "STAGE#score_publications#GLOBAL"}
    assert typed["status"] == {"S": "complete"}
    assert typed["duration_ms"] == {"N": "42"}


def test_to_ddb_typed_envelope_recasts_cost_string_to_numeric():
    # cost_observed_usd arrives as a JSON string (a Decimal serialized via
    # default=str upstream); the typed envelope must carry N, not S.
    typed = to_ddb_typed_envelope({"cost_observed_usd": "0.1234"})
    assert typed["cost_observed_usd"] == {"N": "0.1234"}


def test_to_ddb_typed_envelope_cost_zero_string_is_numeric():
    typed = to_ddb_typed_envelope({"cost_observed_usd": "0"})
    assert typed["cost_observed_usd"] == {"N": "0"}
