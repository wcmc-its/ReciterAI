"""#224: spotlight publish aborts (zero PutObject) on a catastrophic card shrink,
fail-open on a missing/unreadable prior, overridable with force=True."""
import json

from spotlight import publish as sp


class _FakeS3:
    def __init__(self, prior_cards=None):
        self.prior_cards = prior_cards
        self.puts = []

    def key_exists(self, key):
        return key.endswith("latest/spotlight.json") and self.prior_cards is not None

    def get_object_bytes(self, key):
        if key.endswith("latest/spotlight.json"):
            return json.dumps(
                {"spotlights": [{} for _ in range(self.prior_cards)]}
            ).encode("utf-8")
        raise KeyError(key)

    def put_object(self, key, body, **kw):
        self.puts.append(key)


def _artifact(n_cards):
    return {"taxonomy_version": "t", "spotlights": [{} for _ in range(n_cards)]}


def _publish(monkeypatch, s3, artifact, **kw):
    # history writeback is out of scope for the shrink guard
    monkeypatch.setattr(sp, "update_history", lambda *a, **k: None)
    return sp.publish_artifact(artifact, {}, [], s3_client=s3, **kw)


def test_shrink_aborts_with_no_puts(monkeypatch):
    s3 = _FakeS3(prior_cards=9)
    rc = _publish(monkeypatch, s3, _artifact(5))  # 5 < 9*(1-0.34)=5.94
    assert rc == 1
    assert s3.puts == []


def test_within_tolerance_publishes(monkeypatch):
    s3 = _FakeS3(prior_cards=9)
    rc = _publish(monkeypatch, s3, _artifact(8))
    assert rc == 0
    assert len(s3.puts) >= 6


def test_no_prior_publishes(monkeypatch):
    s3 = _FakeS3(prior_cards=None)
    rc = _publish(monkeypatch, s3, _artifact(3))
    assert rc == 0
    assert len(s3.puts) >= 6


def test_force_overrides_shrink(monkeypatch):
    s3 = _FakeS3(prior_cards=9)
    rc = _publish(monkeypatch, s3, _artifact(1), force=True)
    assert rc == 0
    assert len(s3.puts) >= 6


def test_prior_fetch_error_fails_open(monkeypatch):
    class _BadS3(_FakeS3):
        def get_object_bytes(self, key):
            raise RuntimeError("s3 down")

    s3 = _BadS3(prior_cards=9)
    rc = _publish(monkeypatch, s3, _artifact(2))
    assert rc == 0  # unreadable prior must not block a publish
    assert len(s3.puts) >= 6
