"""#224: scan_faculty_publication_gaps enforces its 'empty is never normal'
docstring invariant — it aborts on an empty SQL result and never reaches DDB."""
import pytest

import utils.sql_queries as sq
import utils.dynamodb_helpers as ddb
from utils.read_guards import DegradedReadError


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return _Result(self._rows)

    def close(self):
        pass


def test_scan_gaps_aborts_on_empty_and_skips_ddb(monkeypatch):
    monkeypatch.setattr(sq, "get_db_connection", lambda: _Conn([]))

    def _boom(*a, **k):
        raise AssertionError("DDB must not be reached on an empty SQL result")

    monkeypatch.setattr(ddb, "get_dynamo_client", _boom)
    monkeypatch.setattr(ddb, "fetch_synopses_for_pmids", _boom)

    with pytest.raises(DegradedReadError) as ei:
        sq.scan_faculty_publication_gaps()
    assert ei.value.source == "scan_faculty_publication_gaps"


def test_scan_gaps_passes_above_floor(monkeypatch):
    monkeypatch.setattr(sq, "get_db_connection", lambda: _Conn([{"cwid": "c1", "pmid": 1}]))
    monkeypatch.setattr(ddb, "fetch_synopses_for_pmids", lambda client, pmids: {})
    out = sq.scan_faculty_publication_gaps(client=object())
    assert len(out) == 1
    assert out[0]["cwid"] == "c1"
