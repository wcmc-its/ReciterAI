"""#233: the pre-flight that fails fast when the B1 OR rule is enabled but the
schema still rejects an empty Author.personIdentifier (vs the late publish.py
gate), plus a lock that the shipped schema + flag are coherent and that an
empty-pid author validates against the relaxed live schema.
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

import cli.backfill_spotlight as bf
import spotlight.author_resolver as ar

LIVE_SCHEMA = Path("./docs/spotlight.schema.json")


def _schema(min_len):
    author = {"type": "object", "properties": {"personIdentifier": {"type": "string"}}}
    if min_len is not None:
        author["properties"]["personIdentifier"]["minLength"] = min_len
    return {"$defs": {"Author": author}}


def _write(tmp_path, schema):
    p = tmp_path / "spotlight.schema.json"
    p.write_text(json.dumps(schema), encoding="utf-8")
    return p


def test_coherent_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: False)
    # Flag off -> coherent regardless of the schema constraint.
    assert bf._check_or_flag_schema_coherent(_write(tmp_path, _schema(1))) is True


def test_incoherent_when_flag_on_and_schema_requires_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: True)
    assert bf._check_or_flag_schema_coherent(_write(tmp_path, _schema(1))) is False
    assert "pre-flight" in capsys.readouterr().out


def test_coherent_when_flag_on_and_schema_relaxed(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: True)
    # No minLength -> relaxed -> coherent.
    assert bf._check_or_flag_schema_coherent(_write(tmp_path, _schema(None))) is True


def test_unreadable_schema_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: True)
    assert bf._check_or_flag_schema_coherent(tmp_path / "does_not_exist.json") is False
    assert "pre-flight" in capsys.readouterr().out


def test_live_schema_and_flag_are_coherent():
    """Shipped config (flag on) + shipped schema (relaxed) must be coherent — the
    #233 enabled state; catches an accidental revert of either side."""
    assert bf._check_or_flag_schema_coherent() is True


def test_empty_personidentifier_validates_against_live_schema():
    """The relaxed live schema accepts an author with an empty personIdentifier."""
    schema = json.loads(LIVE_SCHEMA.read_text(encoding="utf-8"))
    author = {"personIdentifier": "", "displayName": "Jane Doe", "position": "first"}
    errors = list(Draft202012Validator(schema["$defs"]["Author"]).iter_errors(author))
    assert errors == []
