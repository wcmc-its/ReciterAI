"""#224: spotlight author_resolver uses the shared hardened engine factory
(utils.db.get_engine) instead of building its own bare engine."""
import spotlight.author_resolver as ar
import utils.db as db


def test_get_default_engine_delegates_to_shared_factory(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(db, "get_engine", lambda: sentinel)
    assert ar._get_default_engine() is sentinel


def test_resolve_authors_routes_through_default_engine(monkeypatch):
    called = {}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _R:
                def fetchall(self):
                    return []

            return _R()

    class _Engine:
        def connect(self):
            called["connect"] = True
            return _Conn()

    monkeypatch.setattr(ar, "_get_default_engine", lambda: _Engine())
    out = ar.resolve_authors(["123"])  # engine=None -> default
    assert called.get("connect") is True
    assert out == {}
