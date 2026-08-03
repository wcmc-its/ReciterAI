"""Tests for ADR D5 layer 2 — taxonomy/data drift.

Prod data is currently clean (69 partitions == 69 taxonomy ids, 0 orphans, as
measured 2026-08-03), so the first deployed run correctly reports OK. That means
prod cannot validate this check — every failure path below is exercised against
a synthetic fixture, reproducing the 2026-07-10 incident shape rather than
trusting a green run.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pipeline_taxonomy_drift.checker import (
    DRIFT_PK,
    evaluate,
    run_check,
    scan_topic_partitions,
)
from utils.taxonomy import content_hash, topic_ids


def _table_returning(*pages):
    """A MagicMock table whose scan() yields the given pages then stops.

    tests/conftest.py guards the real paginating scans for a reason: a bare
    MagicMock returns a truthy LastEvaluatedKey forever. Pages here always end
    with one that omits the key.
    """
    table = MagicMock()
    table.scan.side_effect = list(pages)
    return table


class TestEvaluate:
    def test_clean_taxonomy_reports_ok(self):
        result = evaluate(["cardiology", "oncology"], ["cardiology", "oncology"])
        assert result["severity"] == "OK"
        assert result["orphan_topics"] == []
        assert result["unscored_topics"] == []

    def test_orphan_partition_is_an_error(self):
        # The 2026-07-10 incident: hematology_medical_oncology left the taxonomy
        # on 07-10 but the stale deployed taxonomy kept re-minting its rows.
        result = evaluate(
            ["cardiology", "hematology_medical_oncology"],
            ["cardiology"],
        )
        assert result["severity"] == "ERROR"
        assert result["orphan_topics"] == ["hematology_medical_oncology"]

    def test_topic_added_but_never_scored_is_a_warning(self):
        # The #339 half of the same incident: four new topics the deployed
        # taxonomy had never heard of, so nothing ever scored into them.
        result = evaluate(["cardiology"], ["cardiology", "basic_neuroscience"])
        assert result["severity"] == "WARN"
        assert result["unscored_topics"] == ["basic_neuroscience"]
        assert result["orphan_topics"] == []

    def test_orphan_outranks_unscored(self):
        result = evaluate(["retired_topic"], ["new_topic"])
        assert result["severity"] == "ERROR"
        assert result["orphan_topics"] == ["retired_topic"]
        assert result["unscored_topics"] == ["new_topic"]

    def test_counts_are_of_distinct_topics(self):
        result = evaluate(["a", "a", "b"], ["a", "b"])
        assert result["partition_count"] == 2
        assert result["taxonomy_topic_count"] == 2


class TestScan:
    def test_paginates_and_keeps_newest_created_at(self):
        table = _table_returning(
            {
                "Items": [
                    {"PK": "TOPIC#cardiology", "created_at": "2026-01-01T00:00:00Z"},
                    {"PK": "TOPIC#cardiology", "created_at": "2026-07-20T00:00:00Z"},
                ],
                "LastEvaluatedKey": {"PK": "x"},
            },
            {"Items": [{"PK": "TOPIC#oncology", "created_at": "2026-03-03T00:00:00Z"}]},
        )
        assert scan_topic_partitions(table) == {
            "cardiology": "2026-07-20T00:00:00Z",
            "oncology": "2026-03-03T00:00:00Z",
        }
        assert table.scan.call_count == 2

    def test_row_without_created_at_still_registers_its_partition(self):
        # created_at is absent on rows predating the field. Absence must not
        # hide the partition, and must never read as a violation.
        table = _table_returning({"Items": [{"PK": "TOPIC#legacy"}]})
        assert scan_topic_partitions(table) == {"legacy": ""}

    def test_topic_id_containing_a_hash_survives_the_split(self):
        table = _table_returning({"Items": [{"PK": "TOPIC#odd#name"}]})
        assert list(scan_topic_partitions(table)) == ["odd#name"]

    def test_bare_topic_prefix_is_skipped_not_keyed_as_empty_string(self):
        # A bare "TOPIC#" row yields "" as the topic id. It is never in the
        # taxonomy, so it would become an orphan, so it would become a key in
        # orphan_last_written — and DynamoDB rejects an empty map key with
        # ValidationException, aborting the whole check at put_item.
        table = _table_returning(
            {"Items": [{"PK": "TOPIC#", "SK": "x"}, {"PK": "TOPIC#cardiology"}]}
        )
        assert scan_topic_partitions(table) == {"cardiology": ""}

    def test_real_timestamp_is_not_clobbered_by_a_later_row_without_created_at(self):
        # The majority shape in prod: 10,361 rows carry created_at and 103,244
        # do not, interleaved within the same partitions. A row with no
        # created_at arriving after one that has it must not reset the value.
        table = _table_returning(
            {
                "Items": [
                    {"PK": "TOPIC#heme", "created_at": "2026-07-27T00:00:00Z"},
                    {"PK": "TOPIC#heme"},
                ]
            }
        )
        assert scan_topic_partitions(table) == {"heme": "2026-07-27T00:00:00Z"}


class TestRunCheck:
    def _table(self, items):
        table = MagicMock()
        table.scan.side_effect = [{"Items": items}]
        return table

    def test_writes_a_drift_row_on_its_own_partition(self):
        # DRIFT#evaluation is owned by pipeline_drift and consumed by
        # pipeline_feedback.sweep; this must not land there.
        table = self._table([{"PK": "TOPIC#cardiology", "created_at": "2026-01-01T00:00:00Z"}])
        run_check(table, ["cardiology"], taxonomy_hash="abc123", day="2026-08-03")

        item = table.put_item.call_args.kwargs["Item"]
        assert item["PK"] == DRIFT_PK == "DRIFT#taxonomy"
        assert item["SK"] == "DAY#2026-08-03"
        assert item["severity"] == "OK"
        assert item["taxonomy_hash"] == "abc123"

    def test_clean_run_does_not_alert(self, monkeypatch):
        # alerting.build_card raises ValueError on any severity outside
        # INFO|WARN|ERROR, and "OK" is a valid DRIFT# row severity. Passing it
        # straight through would raise — but only where the webhook is set,
        # i.e. in deployed Lambdas and never in CI.
        import pipeline_enrichment.alerting as alerting

        called = []
        monkeypatch.setattr(alerting, "alert", lambda *a, **k: called.append(a))

        table = self._table([{"PK": "TOPIC#cardiology"}])
        run_check(table, ["cardiology"], taxonomy_hash="abc", day="2026-08-03")
        assert called == []

    def test_ok_is_never_passed_to_build_card(self):
        # Guards the same defect from the other side: the real build_card must
        # reject "OK", proving the ALERTABLE filter is load-bearing.
        from pipeline_enrichment.alerting import build_card

        with pytest.raises(ValueError):
            build_card("OK", "t", "m")

    def test_orphan_alerts_with_error_and_mentions(self, monkeypatch):
        import pipeline_enrichment.alerting as alerting

        calls = []
        monkeypatch.setattr(
            alerting, "alert", lambda *a, **k: calls.append((a, k)) or True
        )

        table = self._table(
            [{"PK": "TOPIC#hematology_medical_oncology", "created_at": "2026-07-27T00:00:00Z"}]
        )
        result = run_check(table, ["cardiology"], taxonomy_hash="abc", day="2026-08-03")

        assert result["severity"] == "ERROR"
        (severity, _title, _msg, context), kwargs = calls[0]
        assert severity == "ERROR"
        assert kwargs["mention"] is True
        # The minting date is what made the 07-10 incident legible.
        assert context["orphan_last_written"] == {
            "hematology_medical_oncology": "2026-07-27T00:00:00Z"
        }

    def test_alert_still_fires_when_the_row_write_fails(self, monkeypatch):
        # Persistence is ordered before dispatch, so an unhandled put_item error
        # would silence the alert at exactly the moment drift was found.
        import pipeline_enrichment.alerting as alerting

        calls = []
        monkeypatch.setattr(alerting, "alert", lambda *a, **k: calls.append((a, k)) or True)

        table = self._table([{"PK": "TOPIC#retired_topic"}])
        table.put_item.side_effect = RuntimeError("ValidationException")

        result = run_check(table, ["cardiology"], taxonomy_hash="abc", day="2026-08-03")

        assert result["severity"] == "ERROR"
        assert result["row_persisted"] is False
        assert len(calls) == 1
        assert calls[0][0][3]["row_persisted"] is False

    def test_unscored_alerts_warn_without_mentioning(self, monkeypatch):
        import pipeline_enrichment.alerting as alerting

        calls = []
        monkeypatch.setattr(
            alerting, "alert", lambda *a, **k: calls.append((a, k)) or True
        )

        table = self._table([{"PK": "TOPIC#cardiology"}])
        run_check(table, ["cardiology", "basic_neuroscience"], taxonomy_hash="a", day="2026-08-03")

        (severity, *_), kwargs = calls[0]
        assert severity == "WARN"
        assert kwargs["mention"] is False


class TestContentHash:
    def test_hash_is_stable_under_reordering_and_reformatting(self):
        a = {"topics": [{"id": "b", "label": "B"}, {"id": "a", "label": "A"}]}
        b = {"topics": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
        assert content_hash(a) == content_hash(b)

    def test_hash_moves_when_a_non_id_field_changes(self):
        # ADR trap 6: two artifacts can agree on ids and still score
        # differently. Comparing id sets is too weak; the hash must catch this.
        base = {"topics": [{"id": "a", "label": "A", "display_threshold": 0.5}]}
        tweaked = {"topics": [{"id": "a", "label": "A", "display_threshold": 0.7}]}
        assert content_hash(base) != content_hash(tweaked)

    def test_hash_moves_when_a_topic_is_retired(self):
        before = {"topics": [{"id": "a"}, {"id": "b"}]}
        after = {"topics": [{"id": "a"}]}
        assert content_hash(before) != content_hash(after)

    def test_real_taxonomy_loads_and_hashes(self):
        ids = topic_ids()
        assert len(ids) > 50
        assert len(content_hash()) == 64


class TestBundledTaxonomyIsReadable:
    def test_taxonomy_ships_next_to_utils(self):
        from utils.taxonomy import TAXONOMY_PATH

        assert TAXONOMY_PATH.exists()
        assert json.loads(TAXONOMY_PATH.read_text())["taxonomy_version"] == "taxonomy_v2"
