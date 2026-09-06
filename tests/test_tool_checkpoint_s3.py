"""Unit tests for the externalised (S3) A2 extraction checkpoint.

A scheduled Fargate run gets a fresh container every tick, so a disk-backed
checkpoint resumes from nothing and the sweep re-extracts all ≈8,146 papers.
These tests pin the S3 store that keeps the log across runs: the round trip, the
fresh-container resume, the STRICT read (a failed or truncated log raises rather
than silently buying a full re-sweep), the absent-log first run, and the
buffer/flush window a hard kill can cost. The local store's behaviour is
unchanged and is covered by tests/test_tool_checkpoint.py; the last test here
re-asserts the two properties this branch could plausibly have broken.
"""
from __future__ import annotations

import json

import pytest

from pipeline_tools import checkpoint as checkpoint_mod
from pipeline_tools.checkpoint import (
    DEFAULT_S3_KEY,
    ExtractionCheckpoint,
    S3JsonlStore,
    make_s3_store,
)
from utils.bedrock_client import HAIKU_MODEL

KEY = "tools/_checkpoint/test.jsonl"


class _FakeS3:
    """Duck-typed S3HierarchyClient: in-memory store + read/write counters.

    Same three methods the store actually uses (key_exists / get_object_bytes /
    put_object), mirroring tests/test_pipeline_cores.py's fake.
    """

    def __init__(self, store=None, *, fail_head=False, fail_get=False, bucket="fake-artifacts"):
        self.bucket = bucket
        self.store = dict(store or {})     # key -> bytes
        self.fail_head = fail_head
        self.fail_get = fail_get
        self.reads = self.writes = 0

    def key_exists(self, key):
        if self.fail_head:
            raise RuntimeError("AccessDenied")
        return key in self.store

    def get_object_bytes(self, key):
        if self.fail_get:
            raise RuntimeError("connection reset by peer")
        self.reads += 1
        return self.store[key]

    def put_object(self, key, body, content_type=None):
        self.writes += 1
        self.store[key] = body


def _mentions(pmid: str, *names: str) -> list[dict]:
    return [{"raw_name": n, "pmid": pmid, "context": None} for n in names]


def _open(s3, **kw) -> ExtractionCheckpoint:
    """Open a checkpoint over the fake bucket — a 'container' in these tests."""
    return ExtractionCheckpoint.load(store=S3JsonlStore(KEY, s3=s3, **kw))


def _record(cp: ExtractionCheckpoint, pmid: str, *names: str) -> None:
    cp.record(pmid, _mentions(pmid, *names), model=HAIKU_MODEL, input_tokens=1200, output_tokens=300)


def _lines(s3) -> list[str]:
    return s3.store[KEY].decode("utf-8").splitlines()


# --- round trip -------------------------------------------------------------


def test_round_trip_through_a_duck_typed_stand_in():
    s3 = _FakeS3()
    cp = _open(s3)
    assert len(cp) == 0                          # absent object -> empty log
    _record(cp, "100", "scRNA-seq", "10x Chromium")
    _record(cp, "200", "patch-clamp")
    cp.flush()

    resumed = _open(s3)
    assert len(resumed) == 2
    assert resumed.done_pmids() == {"100", "200"}
    assert [m["raw_name"] for m in resumed.all_mentions()] == ["scRNA-seq", "10x Chromium", "patch-clamp"]
    usd, calls = resumed.prior_cost()
    assert calls == 2 and usd > 0                # the ceiling seed survives the move


def test_is_done_skips_a_recorded_pmid_in_a_fresh_container():
    # Run 1: one "container" extracts two PMIDs and exits.
    s3 = _FakeS3()
    with _open(s3) as run1:
        _record(run1, "100", "A")
        _record(run1, "200", "B")

    # Run 2: a brand-new container (new store, new checkpoint, empty disk) —
    # this is the property the whole ticket exists for.
    run2 = _open(s3)
    assert run2.is_done("100") and run2.is_done("200")
    assert not run2.is_done("300")
    _record(run2, "300", "C")
    run2.flush()

    assert _open(s3).done_pmids() == {"100", "200", "300"}


def test_flush_writes_a_superset_and_never_truncates_a_prior_run():
    s3 = _FakeS3()
    with _open(s3) as run1:
        _record(run1, "100", "A")
    first = s3.store[KEY]

    with _open(s3) as run2:
        _record(run2, "200", "B")
    # Every flush re-PUTs the whole log; run 1's line is still the first line.
    assert s3.store[KEY].startswith(first.rstrip(b"\n"))
    assert len(_lines(s3)) == 2


def test_append_before_an_explicit_load_does_not_clobber_the_prior_log():
    # A caller that constructs the store and records WITHOUT calling load() must
    # not have its first flush replace run N's log with run N+1's tail.
    s3 = _FakeS3()
    with _open(s3) as run1:
        _record(run1, "100", "A")

    unloaded = ExtractionCheckpoint(store=S3JsonlStore(KEY, s3=s3))
    _record(unloaded, "200", "B")
    unloaded.flush()
    assert _open(s3).done_pmids() == {"100", "200"}


# --- strict reads -----------------------------------------------------------


def test_failed_read_raises_rather_than_returning_an_empty_checkpoint():
    # A degraded read here is not a degradation: an empty done-set re-extracts
    # the whole corpus and spends real money on a green run.
    with pytest.raises(RuntimeError, match="AccessDenied"):
        _open(_FakeS3({KEY: b'{"pmid": "1", "mentions": [], "usage": {}}\n'}, fail_head=True))
    with pytest.raises(RuntimeError, match="connection reset"):
        _open(_FakeS3({KEY: b'{"pmid": "1", "mentions": [], "usage": {}}\n'}, fail_get=True))


def test_truncated_log_raises():
    good = json.dumps({"pmid": "100", "mentions": [], "usage": {"model": HAIKU_MODEL}})
    s3 = _FakeS3({KEY: (good + '\n{"pmid": "200", "mentions": [  ').encode("utf-8")})
    with pytest.raises(ValueError, match="unparseable line"):
        _open(s3)


def test_existing_but_empty_log_raises():
    with pytest.raises(ValueError, match="yields 0 done PMID"):
        _open(_FakeS3({KEY: b"\n\n"}))


def test_absent_log_is_not_an_error():
    # The first ever run: no object at the key. Distinct from a failed read.
    s3 = _FakeS3()
    cp = _open(s3)
    assert len(cp) == 0
    assert cp.all_mentions() == []
    assert s3.writes == 0                        # nothing recorded -> nothing PUT


# --- flush window -----------------------------------------------------------


def test_buffer_flushes_every_n_records_and_a_crash_loses_at_most_that_window():
    s3 = _FakeS3()
    cp = _open(s3, flush_every=3, flush_seconds=10_000)   # interval disabled
    for i in range(7):
        _record(cp, str(100 + i), f"tool-{i}")
    # 7 records at flush_every=3 -> two PUTs (after 3 and 6); 1 still buffered.
    assert s3.writes == 2
    assert len(_lines(s3)) == 6

    # Simulate a hard kill: the process dies, cp is never flushed. A fresh
    # container resumes the 6 durable PMIDs and re-extracts only the 7th.
    resumed = _open(s3)
    assert len(resumed) == 6
    assert not resumed.is_done("106")


def test_flush_seconds_bounds_the_window_when_records_are_slow(monkeypatch):
    clock = {"t": 1_000.0}
    monkeypatch.setattr(checkpoint_mod.time, "monotonic", lambda: clock["t"])
    s3 = _FakeS3()
    cp = _open(s3, flush_every=1_000, flush_seconds=60)   # record count disabled
    _record(cp, "100", "A")
    assert s3.writes == 0                                 # under both bounds
    clock["t"] += 61
    _record(cp, "200", "B")
    assert s3.writes == 1                                 # interval fired
    assert _open(s3).done_pmids() == {"100", "200"}


def test_context_manager_flushes_a_partial_buffer_on_the_way_out():
    s3 = _FakeS3()
    with _open(s3, flush_every=1_000) as cp:
        _record(cp, "100", "A")
        assert s3.writes == 0
    assert s3.writes == 1
    assert _open(s3).done_pmids() == {"100"}


def test_flush_is_a_noop_when_nothing_was_recorded():
    s3 = _FakeS3()
    with _open(s3) as cp:
        cp.flush()
    assert s3.writes == 0                        # never PUT an empty log


# --- wiring -----------------------------------------------------------------


def test_make_s3_store_defaults_to_the_artifacts_key_and_stays_boto3_free():
    store = make_s3_store(s3=_FakeS3())          # injected client -> no lazy import
    assert store.key == DEFAULT_S3_KEY
    assert store.flush_every == checkpoint_mod.DEFAULT_FLUSH_EVERY
    assert store.flush_seconds == checkpoint_mod.DEFAULT_FLUSH_SECONDS
    # boto3 / S3HierarchyClient are imported inside make_s3_store, never at module
    # scope: importing pipeline_tools.checkpoint must carry no AWS dependency.
    assert not hasattr(checkpoint_mod, "boto3")
    assert not hasattr(checkpoint_mod, "S3HierarchyClient")


def test_checkpoint_needs_exactly_one_of_path_or_store(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        ExtractionCheckpoint()
    with pytest.raises(ValueError, match="exactly one"):
        ExtractionCheckpoint(tmp_path / "ckpt.jsonl", store=S3JsonlStore(KEY, s3=_FakeS3()))


# --- the local path is unchanged --------------------------------------------


def test_local_store_still_appends_per_record_and_tolerates_a_torn_line(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    cp = ExtractionCheckpoint.load(path)         # positional path, as callers use it
    assert cp.path == path
    _record(cp, "100", "A")
    # Durable IMMEDIATELY — no flush() call, unlike the buffered S3 store.
    assert path.read_text(encoding="utf-8").count("\n") == 1
    _record(cp, "200", "B")
    assert path.read_text(encoding="utf-8").count("\n") == 2

    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"pmid": "300", "mentions": [  ')   # hard kill mid-append
    resumed = ExtractionCheckpoint.load(path)
    assert resumed.done_pmids() == {"100", "200"}      # tolerated, NOT raised
