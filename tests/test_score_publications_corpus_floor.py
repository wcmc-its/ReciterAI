"""#224: the full-corpus extracts abort on a degraded (under-floor) read; the
delta path is exempt."""
import pytest

import score_publications as sp
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


def _patch(monkeypatch, rows):
    monkeypatch.setattr(sp, "get_db_connection", lambda: _Conn(rows))
    monkeypatch.setattr(
        sp,
        "load_thresholds",
        lambda: {
            "corpus_read_floor_publications": 10,
            "corpus_read_floor_faculty": 10,
            "corpus_read_floor_author_links": 10,
        },
    )


def _faculty_row(i):
    return {
        "cwid": f"c{i}", "name": "N", "department": "D", "h_index": 1,
        "article_count": 1, "first_author_count": 0, "last_author_count": 0,
    }


def test_faculty_metadata_aborts_below_floor(monkeypatch):
    _patch(monkeypatch, rows=[_faculty_row(0)])
    with pytest.raises(DegradedReadError) as ei:
        sp.extract_faculty_metadata()
    assert ei.value.source == "extract_faculty_metadata"


def test_faculty_metadata_passes_above_floor(monkeypatch):
    _patch(monkeypatch, rows=[_faculty_row(i) for i in range(12)])
    assert len(sp.extract_faculty_metadata()) == 12


def test_author_mapping_aborts_below_floor(monkeypatch):
    _patch(monkeypatch, rows=[{"pmid": 1, "cwid": "c1", "authorPosition": "first"}])
    with pytest.raises(DegradedReadError):
        sp.extract_author_mapping()


def test_author_mapping_passes_above_floor(monkeypatch):
    rows = [{"pmid": i, "cwid": "c1", "authorPosition": "first"} for i in range(12)]
    _patch(monkeypatch, rows=rows)
    assert len(sp.extract_author_mapping()) == 12


def test_publications_full_corpus_aborts_below_floor(monkeypatch):
    _patch(monkeypatch, rows=[{"pmid": 1, "title": "t", "abstract": "a"}])
    monkeypatch.setattr(sp, "_attach_synopses_from_ddb", lambda c: c)
    with pytest.raises(DegradedReadError) as ei:
        sp.extract_publications()  # delta_since=None
    assert ei.value.source == "extract_publications"


def test_publications_delta_path_is_exempt(monkeypatch):
    _patch(monkeypatch, rows=[])  # empty delta is normal
    monkeypatch.setattr(sp, "_attach_synopses_from_ddb", lambda c: c)
    assert sp.extract_publications(delta_since="2026-01-01T00:00:00") == []


def test_publications_passes_above_floor(monkeypatch):
    rows = [{"pmid": i, "title": "t", "abstract": "a"} for i in range(12)]
    _patch(monkeypatch, rows=rows)
    monkeypatch.setattr(sp, "_attach_synopses_from_ddb", lambda c: c)
    assert len(sp.extract_publications()) == 12
