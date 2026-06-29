"""backfill_match: compile-only scan that adds match_dsl/match_query to existing GRANT# items.
Faked DDB client + monkeypatched compile, so the scan/skip/write logic is verifiable offline."""
import pipeline_grants.backfill_match as bf


class _FakeClient:
    def __init__(self, items):
        self._items = items
        self.updated = []

    def get_paginator(self, _op):
        items = self._items

        class _P:
            def paginate(self, **_kw):
                return [{"Items": items}]

        return _P()

    def update_item(self, **kw):
        self.updated.append(kw)


def _item(pk, **attrs):
    it = {"PK": {"S": pk}, "SK": {"S": "META"}}
    for k, v in attrs.items():
        it[k] = {"S": v}
    return it


def test_backfill_skips_done_and_incomplete_then_writes(monkeypatch):
    items = [
        _item("GRANT#a", title="T", synopsis="S"),                                   # compile
        _item("GRANT#b", title="T", synopsis="S", match_dsl='{"require":["x"]}'),     # already done -> skip
        _item("GRANT#c", title="", synopsis="S"),                                     # missing title -> skip
    ]
    fake = _FakeClient(items)
    monkeypatch.setattr(bf, "get_dynamo_client", lambda: fake)
    monkeypatch.setattr(bf, "load_vocab_or_disable", lambda _log: ["sub_a", "sub_b"])
    monkeypatch.setattr(bf, "BedrockClient", lambda **_kw: object())
    monkeypatch.setattr(
        bf, "compile_match",
        lambda title, synopsis, vocab, *, bedrock: ({"require": ["sub_a"], "penalize": []}, [{"q": "t", "w": 1.0}]),
    )

    summary = bf.run()
    assert summary["compiled"] == 1
    assert summary["skipped"] == 2
    assert len(fake.updated) == 1
    upd = fake.updated[0]
    assert upd["Key"]["PK"] == {"S": "GRANT#a"}
    assert "match_dsl" in upd["UpdateExpression"] and "match_query" in upd["UpdateExpression"]
    # written as compact-JSON `S` blobs (exactly what the SPS mapper JSON.parses)
    assert upd["ExpressionAttributeValues"][":d"]["S"].startswith("{")
    assert upd["ExpressionAttributeValues"][":q"]["S"].startswith("[")


def test_backfill_dry_run_counts_without_spending(monkeypatch):
    items = [_item("GRANT#a", title="T", synopsis="S"), _item("GRANT#b", title="T", synopsis="S")]
    fake = _FakeClient(items)
    monkeypatch.setattr(bf, "get_dynamo_client", lambda: fake)
    monkeypatch.setattr(bf, "load_vocab_or_disable", lambda _log: ["sub_a"])
    # compile_match must NOT be called in dry-run; make it explode if it is.
    def _boom(*_a, **_k):
        raise AssertionError("compile_match called during dry-run")
    monkeypatch.setattr(bf, "compile_match", _boom)

    summary = bf.run(dry_run=True)
    assert summary["candidates"] == 2
    assert summary["compiled"] == 0
    assert fake.updated == []
