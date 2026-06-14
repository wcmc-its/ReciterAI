"""#224: resolve_authors emits a fast WARN when the resolved/requested ratio
falls below the floor (symptom of a degraded analysis_summary_author read)."""
import logging

import spotlight.author_resolver as ar


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        class _R:
            def fetchall(self):
                return []  # nothing resolves -> ratio 0

        return _R()


class _Engine:
    def connect(self):
        return _Conn()


def test_warns_on_low_resolve_ratio(monkeypatch, caplog):
    monkeypatch.setattr(ar, "_get_default_engine", lambda: _Engine())
    with caplog.at_level(logging.WARNING):
        out = ar.resolve_authors(["1", "2", "3"])
    assert out == {}
    assert any(
        "degraded analysis_summary_author" in r.getMessage() for r in caplog.records
    )
