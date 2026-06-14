"""#224: get_engine() must build the engine with pymysql connect/read/write
timeouts, defaulted and env-overridable."""
import utils.db as db


def _set_db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "h")
    monkeypatch.setenv("DB_USERNAME", "u")
    monkeypatch.setenv("DB_PASSWORD", "p")
    monkeypatch.setenv("DB_NAME", "n")


def test_connect_args_defaults(monkeypatch):
    db._engine_singleton = None
    captured = {}
    monkeypatch.setattr(
        db, "create_engine", lambda dsn, **kw: captured.update(kw) or object()
    )
    _set_db_env(monkeypatch)
    for k in ("DB_CONNECT_TIMEOUT", "DB_READ_TIMEOUT", "DB_WRITE_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)

    db.get_engine()
    assert captured["pool_pre_ping"] is True
    assert captured["pool_recycle"] == 3600
    assert captured["connect_args"] == {
        "connect_timeout": 10,
        "read_timeout": 120,
        "write_timeout": 60,
    }
    db._engine_singleton = None


def test_read_timeout_env_override(monkeypatch):
    db._engine_singleton = None
    captured = {}
    monkeypatch.setattr(
        db, "create_engine", lambda dsn, **kw: captured.update(kw) or object()
    )
    _set_db_env(monkeypatch)
    monkeypatch.setenv("DB_READ_TIMEOUT", "300")

    db.get_engine()
    assert captured["connect_args"]["read_timeout"] == 300
    db._engine_singleton = None


def test_int_env_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("DB_READ_TIMEOUT", "not-an-int")
    assert db._int_env("DB_READ_TIMEOUT", 120) == 120
    monkeypatch.delenv("DB_READ_TIMEOUT", raising=False)
    assert db._int_env("DB_READ_TIMEOUT", 120) == 120
