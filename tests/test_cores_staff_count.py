"""Unit tests for the per-core staff counts published to SPS (PK=CORE#{id}, SK=STAFF_DICT).

The item carries TWO numbers — `staff_count`, the people the dictionary lists, and
`staff_tracked_count`, the subset `signals.coauthorship_index` can actually match — and
the second one is the load-bearing one. So the properties under test are the ones that
make the pair safe rather than the ones that make either number pretty: both are written
together, under SK="STAFF_DICT", into the named table, and the write can never fail the
scoring run. No AWS.
"""
import logging

import pytest
from botocore.exceptions import ClientError

from pipeline_cores import persist
from pipeline_cores.dictionary import load_core, load_cores


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
        raise AssertionError("put_core_staff_dict_counts must not write a whole item")

    batch_write_item = put_item


def _only_call(db, *, table_name=None):
    """The single recorded UpdateItem — and every one of them names a table.

    TableName is checked HERE, not only in the test that is about it, because the two
    ways to lose it are both silent at runtime: dropping the kwarg raises
    ParamValidationError and naming a table that does not exist raises
    ResourceNotFoundException, and put_core_staff_dict_counts' never-fatal wrapper
    turns either into one warning line and a False nobody reads. A table-name
    regression publishes nothing, forever, with nothing red.
    """
    assert len(db.calls) == 1
    call = db.calls[0]
    assert call.get("TableName") == (table_name or persist.TABLE_NAME)
    return call


def _write_dictionary(tmp_path, staff_yaml: str):
    """A one-core dictionary file with the given `staff:` block (may be empty)."""
    path = tmp_path / "core_dictionary.yaml"
    path.write_text(
        "cores:\n"
        '  - core_id: "99"\n'
        "    name: Test Core\n"
        "    aliases: [Test Core]\n" + staff_yaml,
        encoding="utf-8",
    )
    (core,) = load_cores(path)
    return core


# --- the happy path: BOTH counts, one write -------------------------------
def test_publishes_both_counts_in_one_write(caplog):
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.INFO):
        assert persist.put_core_staff_dict_counts("14", 4, 1, client=db) is True

    call = _only_call(db)
    assert call["Key"] == {"PK": {"S": "CORE#14"}, "SK": {"S": "STAFF_DICT"}}
    assert call["ExpressionAttributeValues"] == {":n": {"N": "4"}, ":t": {"N": "1"}}
    # ONE UpdateExpression, not two round trips: a listed count from tonight beside a
    # tracked count from a previous night is a pair no reader could interpret.
    assert call["UpdateExpression"] == "SET staff_count = :n, staff_tracked_count = :t"
    # One line per core published, carrying BOTH numbers — the tracked one is what an
    # operator reading the log needs to reconcile against a chip that looks wrong.
    assert "core 14 -> staff_count=4, staff_tracked_count=1" in caplog.text


def test_the_write_names_the_shared_reciterai_table():
    """The regression this exists for: two mutations — dropping the `TableName=` kwarg,
    and pointing it at some other table — left the previous suite fully green, and both
    are swallowed at runtime by the never-fatal wrapper into a warning nobody reads."""
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_dict_counts("14", 4, 1, client=db)
    assert db.calls[0]["TableName"] == persist.TABLE_NAME == "reciterai"


def test_an_explicit_table_name_is_honoured():
    """...and the default is a default, not a hardcode: the table stays injectable for
    the live-AWS suite, which writes to a scratch table rather than to production."""
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_dict_counts(
        "14", 4, 1, client=db, table_name="reciterai-test")
    assert _only_call(db, table_name="reciterai-test")["TableName"] == "reciterai-test"


def test_writes_only_the_two_counts_under_the_staff_dict_sort_key():
    """The CORE#{id} partition already holds the CLIENTS item SPS writes and this repo
    READS. A whole-item Put, a stray third attribute, or the wrong SK would each break
    the other direction of that contract."""
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_dict_counts("14", 4, 1, client=db)
    call = _only_call(db)

    assert call["Key"]["SK"] == {"S": "STAFF_DICT"}
    assert "REMOVE" not in call["UpdateExpression"]   # owns nothing else, clears nothing
    assert call.get("ConditionExpression") is None
    # Exactly two attributes are assigned: two `=` in the expression, two bound values.
    assert call["UpdateExpression"].count("=") == 2
    assert sorted(call["ExpressionAttributeValues"]) == [":n", ":t"]


def test_the_sort_key_is_staff_dict_and_leaves_staff_free():
    """SK="STAFF" is reserved for a future SPS-CURATED staff list, which by the CLIENTS
    precedent (SPS writes, this repo reads) would want exactly that key. This item is
    dictionary-sourced and runs the other way, so it must not squat on it — and, like
    CLIENTS, it must never begin with "CORE#", which scan_prior_core_usage and the SPS
    ETL both read as a (pub, core) usage row."""
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_dict_counts("14", 4, 1, client=db)
    sk = _only_call(db)["Key"]["SK"]["S"]
    assert sk == "STAFF_DICT"
    assert sk != "STAFF"
    assert not sk.startswith("CORE#")


def test_core_ids_are_scoped_to_their_own_partition():
    db = _FakeUpdateItemDynamo()
    persist.put_core_staff_dict_counts("1", 4, 4, client=db)
    persist.put_core_staff_dict_counts("2", 7, 4, client=db)
    assert [c["Key"]["PK"]["S"] for c in db.calls] == ["CORE#1", "CORE#2"]
    assert [c["TableName"] for c in db.calls] == [persist.TABLE_NAME] * 2
    assert [(c["ExpressionAttributeValues"][":n"]["N"],
             c["ExpressionAttributeValues"][":t"]["N"]) for c in db.calls] == [
        ("4", "4"), ("7", "4")]


# --- tracked is a SUBSET, and the two numbers are not interchangeable ------
class _ResolvedEngine:
    """An engine whose analysis_summary_author has resolved exactly `resolved`."""
    def __init__(self, resolved):
        self.resolved, self.asked = set(resolved), None

    def connect(self):
        eng = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, stmt, params):
                eng.asked = list(params["cwids"])
                return [type("R", (), {"personIdentifier": c, "pmid": 1})() for c in params["cwids"]
                        if c in eng.resolved]
        return _Conn()


def test_the_tracked_count_is_a_live_lookup_of_resolved_staff(tmp_path):
    """Listed 3, resolved by ReCiter today 2 — and the item must say both. No stored
    flag: the second number comes from analysis_summary_author on this run, so it
    moves as ReCiter starts (or stops) resolving people."""
    from pipeline_cores import signals
    core = _write_dictionary(
        tmp_path,
        "    staff:\n"
        "      - {cwid: aaa0001}\n"
        "      - {cwid: aaa0002}\n"
        "      - {cwid: aaa0003}\n",
    )
    eng = _ResolvedEngine({"aaa0001", "aaa0002"})
    tracked = signals.resolved_staff(eng, core.staff_cwids)
    assert (len(core.staff), len(tracked)) == (3, 2)

    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_dict_counts(
        core.core_id, len(core.staff), len(tracked), client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {
        ":n": {"N": "3"}, ":t": {"N": "2"}}


def test_a_core_whose_staff_are_all_unresolved_publishes_tracked_zero(tmp_path):
    """Listed staff ReCiter has resolved none of: the co-author signal cannot fire, and
    0 is published, not skipped and not silently equalised to the listed count."""
    from pipeline_cores import signals
    core = _write_dictionary(
        tmp_path, "    staff:\n      - {cwid: bbb0001}\n      - {cwid: bbb0002}\n")
    tracked = signals.resolved_staff(_ResolvedEngine(set()), core.staff_cwids)
    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_dict_counts(
        core.core_id, len(core.staff), len(tracked), client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {":n": {"N": "2"}, ":t": {"N": "0"}}


def test_the_coauthor_signal_asks_for_every_listed_and_curated_staff_member(tmp_path):
    """Binds the signal to the live lookup: coauthorship_index asks the DB about every
    listed CWID plus SPS's curated ones (unresolved ones just match nothing), and
    short-circuits to {} without touching the DB only when there is nobody at all."""
    from pipeline_cores import signals
    core = _write_dictionary(
        tmp_path, "    staff:\n      - {cwid: bbb0001}\n      - {cwid: bbb0002}\n")
    eng = _ResolvedEngine({"bbb0002", "ccc0009"})
    assert signals.coauthorship_index(eng, core, extra_cwids={"ccc0009"}) == {"1": ["bbb0002", "ccc0009"]}
    assert sorted(eng.asked) == ["bbb0001", "bbb0002", "ccc0009"]

    class _EngineThatMustNotBeUsed:
        def connect(self):
            raise AssertionError("coauthorship_index queried the DB for a core with no staff")
    empty = _write_dictionary(tmp_path, "")
    assert signals.coauthorship_index(_EngineThatMustNotBeUsed(), empty) == {}
    assert signals.resolved_staff(_EngineThatMustNotBeUsed(), []) == set()


# --- zero is a real answer, not a missing one ------------------------------
@pytest.mark.parametrize("core_id", ["4", "6", "7"])
def test_a_core_with_an_empty_staff_list_publishes_zero_and_zero(core_id):
    """`staff: []` in the dictionary (cores 4, 6 and 7 today) is a derived 0 on both
    counts and must be published as one — SPS renders "0 core staff", which is true,
    rather than falling back to a stale count from a previous run."""
    core = load_core(core_id)
    assert core.staff == []
    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_dict_counts(
        core.core_id, len(core.staff), 0, client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {
        ":n": {"N": "0"}, ":t": {"N": "0"}}


def test_an_absent_staff_key_publishes_zero_and_zero(tmp_path):
    """The other shape of the same 0: a dictionary entry with no `staff:` key at all.
    load_cores defaults it to [], so it is just as positively derived as `staff: []`."""
    core = _write_dictionary(tmp_path, "")
    assert core.staff == []
    db = _FakeUpdateItemDynamo()
    assert persist.put_core_staff_dict_counts(
        core.core_id, len(core.staff), 0, client=db) is True
    assert _only_call(db)["ExpressionAttributeValues"] == {
        ":n": {"N": "0"}, ":t": {"N": "0"}}


# --- never fatal to the scoring run ----------------------------------------
@pytest.mark.parametrize("exc", [
    ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                           "Message": "x"}}, "UpdateItem"),
    ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "UpdateItem"),
    ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "UpdateItem"),
    RuntimeError("connection reset"),
])
def test_a_failed_write_warns_and_does_not_raise(caplog, exc):
    """Publishing a display count must never be able to fail the nightly. Same posture
    as get_curated_clients, and deliberately the opposite of scan_core_llm_scores."""
    db = _FakeUpdateItemDynamo(raise_exc=exc)
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_dict_counts("14", 4, 1, client=db) is False
    assert "put_core_staff_dict_counts" in caplog.text
    assert type(exc).__name__ in caplog.text


def test_an_unusable_dynamo_client_does_not_raise_either(caplog, monkeypatch):
    """get_dynamo_client itself can blow up (bad region, no credentials) — that is
    inside the guard too, not just the write."""
    def _boom(*a, **k):
        raise RuntimeError("no region configured")
    monkeypatch.setattr(persist, "get_dynamo_client", _boom)

    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_dict_counts("14", 4, 1) is False
    assert "put_core_staff_dict_counts" in caplog.text


# --- a number that was not counted is never published ----------------------
@pytest.mark.parametrize("bad", [None, -1, "4", 4.0, True])
def test_a_listed_count_that_was_not_derived_is_never_written(caplog, bad):
    """0 is a derived answer; None, a float, a string or a negative is not one. This
    write path exists to publish something COUNTED — writing a guess would be worse
    than publishing nothing, since SPS renders whatever it finds."""
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_dict_counts("14", bad, 1, client=db) is False
    assert db.calls == []
    assert "staff_count" in caplog.text


@pytest.mark.parametrize("bad", [None, -1, "1", 1.0, True])
def test_a_tracked_count_that_was_not_derived_is_never_written(caplog, bad):
    """The second number gets the same guard as the first, and refusing it refuses the
    WHOLE write: a listed count published beside a missing tracked count is exactly the
    over-claiming chip this item exists to prevent."""
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_dict_counts("14", 4, bad, client=db) is False
    assert db.calls == []
    assert "staff_tracked_count" in caplog.text


def test_more_tracked_than_listed_is_refused_as_swapped_arguments(caplog):
    """Tracked staff are a SUBSET of listed staff, so this pair cannot have been
    counted — it is (listed, tracked) passed the other way round, which would publish
    core 14's "1 of 4 matchable" as "4 of 1"."""
    db = _FakeUpdateItemDynamo()
    with caplog.at_level(logging.WARNING):
        assert persist.put_core_staff_dict_counts("14", 1, 4, client=db) is False
    assert db.calls == []
    assert "swapped" in caplog.text
    # ...but equal counts are the normal state of a fully-tracked core (1, 12 today).
    assert persist.put_core_staff_dict_counts("14", 4, 4, client=db) is True


# --- run.py wiring ---------------------------------------------------------
def _stub_a_scoring_run(monkeypatch):
    from unittest.mock import MagicMock

    from pipeline_cores import ingest, run
    import utils.db as db_mod
    import utils.dynamodb_helpers as ddb

    monkeypatch.setattr(db_mod, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications",
                        lambda e, pmids=None, limit=None: [])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(run, "run_core", lambda *a, **k: [])
    monkeypatch.setattr(persist, "put_core_usage", lambda recs: len(recs))
    # A real run also writes the STAGE#cores_run#GLOBAL liveness row, and it reaches
    # DynamoDB through a resource Table rather than the client every persist seam
    # above uses. The tests below drive main() with no --dry-run, so an unstubbed
    # get_table is a real PutItem against the shared `reciterai` table.
    monkeypatch.setattr(ddb, "get_table", lambda *a, **k: MagicMock(name="reciterai"))
    # Live lookup stub: "ReCiter has resolved the first listed staff member of each core".
    from pipeline_cores import signals
    monkeypatch.setattr(signals, "resolved_staff", lambda engine, cwids: set(list(cwids)[:1]))
    return run


def test_main_publishes_every_core_in_the_dictionary(monkeypatch):
    """DECLARED-BUT-NEVER-CONNECTED guard, and the `--core 14` regression: the deployed
    nightly scores ONE core, so publishing only the scored subset would leave thirteen
    of the fourteen items unwritten — not stale, ABSENT — and SPS would render no chip
    for them indefinitely. The counts are a property of the dictionary, not of a
    scoring pass, so every core is published on every run."""
    run = _stub_a_scoring_run(monkeypatch)
    published = []
    monkeypatch.setattr(persist, "put_core_staff_dict_counts",
                        lambda core_id, count, tracked: published.append(
                            (core_id, count, tracked)))

    expected = [(c.core_id, len(c.staff), min(len(c.staff), 1)) for c in load_cores()]

    run.main(["--core", "14"])
    assert published == expected
    assert len(published) > 1               # the whole point of the fix
    assert any(c == "14" for c, _, _ in published)

    # ...and a run with no --core publishes exactly the same fourteen, zeros included.
    # The call site must not "helpfully" skip a core with no staff: 0 is the derived
    # answer for cores 4, 6 and 7, and skipping them would leave SPS rendering whatever
    # a previous run happened to publish.
    published.clear()
    run.main([])
    assert published == expected
    assert ("4", 0, 0) in published


def test_main_publishes_the_tracked_count_not_the_listed_one_twice(monkeypatch):
    """The pair per core is (listed, resolved-today) — len(core.staff) and the live
    signals.resolved_staff lookup. Passing the listed count for both would recreate the
    over-claim in the call site after the persist layer refused it."""
    run = _stub_a_scoring_run(monkeypatch)
    published = {}
    monkeypatch.setattr(persist, "put_core_staff_dict_counts",
                        lambda core_id, count, tracked: published.__setitem__(
                            core_id, (count, tracked)))

    run.main(["--core", "14"])
    for core in load_cores():
        assert published[core.core_id] == (len(core.staff), min(len(core.staff), 1))
    assert any(t < n for n, t in published.values())


def test_main_dry_run_publishes_nothing(monkeypatch):
    """--dry-run is documented as writing nothing to DynamoDB (and needing no AWS)."""
    run = _stub_a_scoring_run(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError(
            "put_core_staff_dict_counts must not be called under --dry-run")
    monkeypatch.setattr(persist, "put_core_staff_dict_counts", _boom)

    run.main(["--core", "14", "--dry-run"])
