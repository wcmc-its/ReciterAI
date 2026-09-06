"""Unit tests for the per-core staff COUNT published to SPS (PK=CORE#{id}, SK=STAFF).

The count drives one sentence in SPS's core review queue ("Co-author signal draws on N
core staff from the facility dictionary"), so the properties under test are the ones
that make it safe rather than the ones that make it accurate: it publishes only the
count, only under SK="STAFF", and it can never fail the scoring run. No AWS.
"""
import logging

import pytest
from botocore.exceptions import ClientError

from pipeline_cores import persist
from pipeline_cores.dictionary import load_core


class _FakeUpdateItemDynamo:
    """Records UpdateItem calls; optionally raises instead, like a throttled client."""

    def __init__(self, raise_exc=None):
        self._raise_exc = raise_exc
        self.calls = []

    def update_item(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append(kwargs)
        return {}

    def put_item(self, **kwargs):
        raise AssertionError("put_core_staff_count must not write a whole item")

    batch_write_item = put_item


def _only_call(db):
    assert len(db.calls) == 1
    return db.calls[0]


# --- the happy path --------------------------------------------------------
def test_publishes_the_count_for_a_core_with_staff(caplog):
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.INFO):
        assert persist.put_core_staff_count("14", 4, client=db) is True

    call = _only_call(db)
    assert call["Key"] == {"PK": {"S": "CORE#14"}, "SK": {"S": "STAFF"}}
    assert call["ExpressionAttributeValues"] == {":n": {"N": "4"}}
    # One line per core published, carrying the count (requirement 4).
    assert "core 14 -> staff_count=4" in caplog.text


def test_writes_only_staff_count_under_the_staff_sort_key():
    """The CORE#{id} partition already holds the CLIENTS item SPS writes and this repo
    READS. A whole-item Put, a stray second attribute, or the wrong SK would each break
    the other direction of that contract."""
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_count("14", 4, client=db)
    call = _only_call(db)

    assert call["Key"]["SK"] == {"S": "STAFF"}              # never "CLIENTS", never CORE#
    assert call["UpdateExpression"] == "SET staff_count = :n"
    assert "REMOVE" not in call["UpdateExpression"]         # owns nothing else, clears nothing
    assert call.get("ConditionExpression") is None
    # Exactly one attribute is assigned: one `=` in the expression, one bound value.
    assert call["UpdateExpression"].count("=") == 1
    assert list(call["ExpressionAttributeValues"]) == [":n"]


def test_core_ids_are_scoped_to_their_own_partition():
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_count("1", 4, client=db)
    persist.put_core_staff_count("2", 7, client=db)
    assert [c["Key"]["PK"]["S"] for c in db.calls] == ["CORE#1", "CORE#2"]
    assert [c["ExpressionAttributeValues"][":n"]["N"] for c in db.calls] == ["4", "7"]


# --- zero is a real answer, not a missing one ------------------------------
@pytest.mark.parametrize("core_id", ["4", "6", "7"])
def test_a_core_with_an_empty_staff_list_publishes_zero(core_id):
    """`staff: []` in the dictionary (cores 4, 6 and 7 today) is a derived 0 and must
    be published as one — SPS renders "0 core staff", which is true, rather than
    falling back to a stale count from a previous run."""
    core = load_core(core_id)
    assert core.staff == []
    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_count(core.core_id, len(core.staff), client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {":n": {"N": "0"}}


def test_an_absent_staff_key_publishes_zero(tmp_path):
    """The other shape of the same 0: a dictionary entry with no `staff:` key at all.
    load_cores defaults it to [], so it is just as positively derived as `staff: []`."""
    yaml_path = tmp_path / "core_dictionary.yaml"
    yaml_path.write_text(
        "cores:\n"
        '  - core_id: "99"\n'
        "    name: Staffless Core\n"
        "    aliases: [Staffless Core]\n",
        encoding="utf-8",
    )
    from pipeline_cores.dictionary import load_cores

    (core,) = load_cores(yaml_path)
    assert core.staff == []
    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_count(core.core_id, len(core.staff), client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {":n": {"N": "0"}}


# --- never fatal to the scoring run ----------------------------------------
@pytest.mark.parametrize("exc", [
    ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                           "Message": "x"}}, "UpdateItem"),
    ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "UpdateItem"),
    RuntimeError("connection reset"),
])
def test_a_failed_write_warns_and_does_not_raise(caplog, exc):
    """Publishing a display count must never be able to fail the nightly. Same posture
    as get_curated_clients, and deliberately the opposite of scan_core_llm_scores."""
    db = _FakeUpdateItemDynamo(raise_exc=exc)
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_count("14", 4, client=db) is False
    assert "put_core_staff_count" in caplog.text
    assert type(exc).__name__ in caplog.text


def test_an_unusable_dynamo_client_does_not_raise_either(caplog, monkeypatch):
    """get_dynamo_client itself can blow up (bad region, no credentials) — that is
    inside the guard too, not just the write."""
    def _boom(*a, **k):
        raise RuntimeError("no region configured")
    monkeypatch.setattr(persist, "get_dynamo_client", _boom)

    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_count("14", 4) is False
    assert "put_core_staff_count" in caplog.text


@pytest.mark.parametrize("bad", [None, -1, "4", 4.0, True])
def test_a_count_that_was_not_derived_is_never_written(caplog, bad):
    """0 is a derived answer; None, a float, a string or a negative is not one. This
    write path exists to publish something COUNTED — writing a guess would be worse
    than publishing nothing, since SPS renders whatever it finds."""
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_count("14", bad, client=db) is False
    assert db.calls == []
    assert "put_core_staff_count" in caplog.text


# --- run.py wiring ---------------------------------------------------------
def test_main_publishes_one_staff_count_per_loaded_core(monkeypatch):
    """DECLARED-BUT-NEVER-CONNECTED guard: nothing about a score or a status changes
    when this publish silently stops happening, so only a wiring test can see it."""
    from unittest.mock import MagicMock

    from pipeline_cores import ingest, run
    import utils.db as db_mod

    monkeypatch.setattr(db_mod, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications",
                        lambda e, pmids=None, limit=None: [])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(run, "run_core", lambda *a, **k: [])
    monkeypatch.setattr(persist, "put_core_usage", lambda recs: len(recs))
    published = []
    monkeypatch.setattr(persist, "put_core_staff_count",
                        lambda core_id, count: published.append((core_id, count)))

    run.main(["--core", "14"])
    # `--core 14` is what the deployed nightly runs: exactly one item is refreshed.
    assert published == [("14", len(load_core("14").staff))]

    # ...and a run without --core publishes EVERY core in the dictionary, zeros
    # included. The call site must not "helpfully" skip a core with no staff: 0 is the
    # derived answer for cores 4, 6 and 7, and skipping them would leave SPS rendering
    # whatever a previous run happened to publish.
    from pipeline_cores.dictionary import load_cores

    published.clear()
    run.main([])
    assert published == [(c.core_id, len(c.staff)) for c in load_cores()]
    assert ("4", 0) in published


def test_main_dry_run_publishes_nothing(monkeypatch):
    """--dry-run is documented as writing nothing to DynamoDB (and needing no AWS)."""
    from unittest.mock import MagicMock

    from pipeline_cores import ingest, run
    import utils.db as db_mod

    monkeypatch.setattr(db_mod, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications",
                        lambda e, pmids=None, limit=None: [])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(run, "run_core", lambda *a, **k: [])

    def _boom(*a, **k):
        raise AssertionError("put_core_staff_count must not be called under --dry-run")
    monkeypatch.setattr(persist, "put_core_staff_count", _boom)

    run.main(["--core", "14", "--dry-run"])
