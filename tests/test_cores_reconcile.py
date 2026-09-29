"""`run.py --reconcile`: demote the surfaced rows a full-corpus run did not re-surface.

run.py persists only confirmed/candidate records, so without this sweep a pair that
falls below threshold keeps its old status forever (pipeline_cores/README.md, "What a
run WRITES"). Pinned here:

- stale candidate/confirmed rows of a scored core ARE demoted;
- human claimed/rejected rows are NEVER touched — including one claimed between the
  Scan and the write, which only the ConditionExpression can catch;
- rows this run wrote (scored_at == the run's stamp) and rows of other cores are left alone;
- a concurrent re-score (scored_at moved) is skipped and counted;
- the sweep is refused before any work on --test / --pmids-file;
- --dry-run reports a count and writes nothing;
- the zero-surfaced and demote-fraction safety guards.

The double below EVALUATES the Scan FilterExpression and the UpdateItem
ConditionExpression rather than accepting them, for the same reason
test_pipeline_cores._FakeUpdateDynamo models put_core_usage's guard: a double that
ignores a condition passes every guard assertion against a write that guards nothing.
It raises on any clause shape it does not understand, so a rewritten expression fails
loudly here instead of silently evaluating to "match".
"""
from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

import pytest

import utils.db as db_mod
import utils.dynamodb_helpers as ddb
from pipeline_cores import ingest, persist, run
from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    STATUS_CLAIMED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    CoreUsageRecord,
    SignalResult,
)

RUN_TS = "2026-09-30T05:00:10Z"
OLD_TS = "2026-09-29T05:00:07Z"
VINTAGE_TS = "2026-06-22T00:57:00Z"


class _FakeDynamo:
    """In-memory `reciterai` table: paginated Scan + conditional UpdateItem."""

    class exceptions:
        class ConditionalCheckFailedException(Exception):
            pass

    def __init__(self, rows, *, page_size=2, before_update=None):
        # rows: list of plain dicts {"PK", "SK", "status", "scored_at"?, ...} (str values)
        self.store = {(r["PK"], r["SK"]): {k: {"S": v} for k, v in r.items()} for r in rows}
        self.page_size = page_size
        self.before_update = before_update
        self.scans = []
        self.updates = []

    # -- expression evaluation ------------------------------------------------
    @staticmethod
    def _check_placeholders(exprs, names, values):
        text = " ".join(e for e in exprs if e)
        unused = (sorted(set(names) - set(re.findall(r"#\w+", text)))
                  + sorted(set(values) - set(re.findall(r":\w+", text))))
        if unused:
            raise AssertionError(f"unused expression placeholders {unused} — DynamoDB "
                                 f"rejects the whole request for these")

    @staticmethod
    def _eval(expr, item, names, values) -> bool:
        def attr(tok):
            tok = tok.strip()
            key = names[tok] if tok.startswith("#") else tok
            return item.get(key, {}).get("S")

        for clause in expr.split(" AND "):
            clause = clause.strip()
            if m := re.fullmatch(r"begins_with\((\S+), (:\w+)\)", clause):
                v = attr(m[1])
                ok = v is not None and v.startswith(values[m[2]]["S"])
            elif m := re.fullmatch(r"(\S+) IN \(([^)]*)\)", clause):
                v = attr(m[1])
                ok = v is not None and v in {values[x.strip()]["S"] for x in m[2].split(",")}
            elif m := re.fullmatch(r"(\S+) (=|<) (:\w+)", clause):
                v, rhs = attr(m[1]), values[m[3]]["S"]
                ok = v is not None and (v == rhs if m[2] == "=" else v < rhs)
            else:
                raise AssertionError(f"fake cannot evaluate clause {clause!r}")
            if not ok:
                return False
        return True

    # -- API --------------------------------------------------------------------
    def scan(self, TableName, FilterExpression, ExpressionAttributeValues,
             ProjectionExpression, ExpressionAttributeNames=None, ExclusiveStartKey=None):
        names = ExpressionAttributeNames or {}
        self._check_placeholders([FilterExpression, ProjectionExpression], names,
                                 ExpressionAttributeValues)
        self.scans.append(FilterExpression)
        keys = sorted(self.store)
        start = int(ExclusiveStartKey["i"]["N"]) if ExclusiveStartKey else 0
        page = keys[start:start + self.page_size]     # DynamoDB filters AFTER paging
        proj = [names.get(p.strip(), p.strip()) for p in ProjectionExpression.split(",")]
        items = [{a: self.store[k][a] for a in proj if a in self.store[k]}
                 for k in page
                 if self._eval(FilterExpression, self.store[k], names, ExpressionAttributeValues)]
        resp = {"Items": items}
        if start + self.page_size < len(keys):
            resp["LastEvaluatedKey"] = {"i": {"N": str(start + self.page_size)}}
        return resp

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression,
                    ExpressionAttributeNames, ExpressionAttributeValues):
        self._check_placeholders([UpdateExpression, ConditionExpression],
                                 ExpressionAttributeNames, ExpressionAttributeValues)
        k = (Key["PK"]["S"], Key["SK"]["S"])
        if self.before_update:
            self.before_update(self, k)
        self.updates.append(k)
        item = self.store.get(k, {})
        if not self._eval(ConditionExpression, item, ExpressionAttributeNames,
                          ExpressionAttributeValues):
            raise self.exceptions.ConditionalCheckFailedException(str(k))
        m = re.fullmatch(r"SET (#\w+) = (:\w+)", UpdateExpression)
        assert m, f"reconcile must SET status and nothing else, got {UpdateExpression!r}"
        item[ExpressionAttributeNames[m[1]]] = ExpressionAttributeValues[m[2]]
        return {}

    def status(self, pmid, core="14"):
        return self.store[(f"PUB#{pmid}", f"CORE#{core}")]["status"]["S"]


def _row(pmid, status, scored_at=OLD_TS, core="14"):
    r = {"PK": f"PUB#{pmid}", "SK": f"CORE#{core}", "pmid": str(pmid),
         "core_id": core, "status": status}
    if scored_at is not None:
        r["scored_at"] = scored_at
    return r


def _core(cid="14"):
    c = MagicMock(name=f"core{cid}")
    c.core_id = cid
    return c


def _rec(pmid, core="14", status=STATUS_CANDIDATE):
    return CoreUsageRecord(pmid=str(pmid), core_id=core, likelihood=0.5, status=status,
                           signals=SignalResult(), scored_at=RUN_TS)


# Lots of re-surfaced rows so the fraction guard stays out of the way unless a test
# is about it.
def _fresh(n=20, start=1000, core="14"):
    return ([_row(p, STATUS_CANDIDATE, RUN_TS, core) for p in range(start, start + n)],
            [_rec(p, core) for p in range(start, start + n)])


def _sweep(db, surfaced, cores=("14",), **kw):
    kw.setdefault("dry_run", False)
    return run.reconcile_stale_rows([_core(c) for c in cores], surfaced, RUN_TS,
                                    client=db, **kw)


# --- the demotion ---------------------------------------------------------------

def test_stale_candidate_and_confirmed_rows_are_demoted(caplog):
    fresh_rows, surfaced = _fresh()
    db = _FakeDynamo(fresh_rows + [
        _row(1, STATUS_CANDIDATE, OLD_TS),
        _row(2, STATUS_CONFIRMED, VINTAGE_TS),       # the stale engine-confirmed case
        _row(3, STATUS_BELOW, OLD_TS),                # already demoted: not re-touched
    ])
    with caplog.at_level(logging.INFO):
        out = _sweep(db, surfaced)
    assert db.status(1) == STATUS_BELOW
    assert db.status(2) == STATUS_BELOW
    assert out["14"] == {"stale": 2, "surfaced": 20, "demoted": 2, "skipped": 0,
                         "refused": None}
    assert ("PUB#3", "CORE#14") not in db.updates
    assert "reconcile: core 14 demoted 2, skipped 0 (condition failed)" in caplog.text


def test_rows_this_run_wrote_are_untouched():
    fresh_rows, surfaced = _fresh()
    db = _FakeDynamo(fresh_rows + [_row(1, STATUS_CANDIDATE, OLD_TS)])
    _sweep(db, surfaced)
    assert all(db.status(p) == STATUS_CANDIDATE for p in range(1000, 1020))
    assert db.updates == [("PUB#1", "CORE#14")]


def test_human_statuses_are_never_touched():
    """claimed/rejected rows with an OLD scored_at — the exact shape of a pair a
    reviewer decided and the engine has since stopped surfacing."""
    fresh_rows, surfaced = _fresh()
    db = _FakeDynamo(fresh_rows + [
        _row(1, STATUS_CLAIMED, VINTAGE_TS),
        _row(2, STATUS_REJECTED, OLD_TS),
        _row(3, STATUS_CLAIMED, None),               # SPS writeback rows can lack scored_at
    ])
    _sweep(db, surfaced)
    assert (db.status(1), db.status(2), db.status(3)) == (
        STATUS_CLAIMED, STATUS_REJECTED, STATUS_CLAIMED)
    assert db.updates == []


def test_a_claim_landing_between_scan_and_write_is_not_overwritten():
    """The Scan is a snapshot; SPS claim-writeback can flip a row to `claimed` after it.
    Only the write-time ConditionExpression can protect that decision."""
    fresh_rows, surfaced = _fresh()

    def reviewer_claims(db, key):
        if key == ("PUB#1", "CORE#14"):
            db.store[key]["status"] = {"S": STATUS_CLAIMED}

    db = _FakeDynamo(fresh_rows + [_row(1, STATUS_CANDIDATE), _row(2, STATUS_CANDIDATE)],
                     before_update=reviewer_claims)
    out = _sweep(db, surfaced)
    assert db.status(1) == STATUS_CLAIMED
    assert db.status(2) == STATUS_BELOW
    assert (out["14"]["demoted"], out["14"]["skipped"]) == (1, 1)


def test_a_concurrent_rescore_is_skipped_and_counted(caplog):
    fresh_rows, surfaced = _fresh()

    def rescored(db, key):
        if key == ("PUB#1", "CORE#14"):
            db.store[key]["scored_at"] = {"S": "2026-09-30T05:00:11Z"}

    db = _FakeDynamo(fresh_rows + [_row(1, STATUS_CANDIDATE)], before_update=rescored)
    with caplog.at_level(logging.INFO):
        out = _sweep(db, surfaced)
    assert db.status(1) == STATUS_CANDIDATE
    assert (out["14"]["demoted"], out["14"]["skipped"]) == (0, 1)
    assert "reconcile: core 14 demoted 0, skipped 1 (condition failed)" in caplog.text


def test_other_cores_rows_are_never_touched():
    """A --core 14 run cannot see core 5's queue, so it must not sweep it."""
    fresh_rows, surfaced = _fresh()
    db = _FakeDynamo(fresh_rows + [_row(1, STATUS_CANDIDATE, OLD_TS, core="5"),
                                   _row(2, STATUS_CANDIDATE, OLD_TS)])
    _sweep(db, surfaced)
    assert db.status(1, core="5") == STATUS_CANDIDATE
    assert db.status(2) == STATUS_BELOW


def test_multi_core_run_sweeps_each_scored_core():
    rows14, rec14 = _fresh(core="14")
    rows5, rec5 = _fresh(core="5")
    db = _FakeDynamo(rows14 + rows5 + [_row(1, STATUS_CANDIDATE, core="5"),
                                       _row(1, STATUS_CONFIRMED, core="14"),
                                       _row(1, STATUS_CANDIDATE, core="7")])  # not scored
    out = _sweep(db, rec14 + rec5, cores=("14", "5"))
    assert db.status(1, "5") == db.status(1, "14") == STATUS_BELOW
    assert db.status(1, "7") == STATUS_CANDIDATE
    assert set(out) == {"14", "5"}


def test_scan_paginates():
    fresh_rows, surfaced = _fresh()
    stale = [_row(p, STATUS_CANDIDATE) for p in range(1, 8)]
    db = _FakeDynamo(fresh_rows + stale, page_size=3)
    out = _sweep(db, surfaced)
    assert out["14"]["demoted"] == 7
    assert len(db.scans) > 1


# --- safety guards --------------------------------------------------------------

def test_zero_surfaced_core_is_refused(caplog):
    """A core this run surfaced nothing for looks exactly like a run that silently
    failed. Reconciling it would demote the whole core — so it is refused, loudly,
    even with the fraction guard fully opened."""
    db = _FakeDynamo([_row(p, STATUS_CANDIDATE) for p in range(1, 6)])
    with caplog.at_level(logging.INFO):
        out = _sweep(db, [], max_demote_fraction=1.0)
    assert out["14"]["refused"] == "zero_surfaced"
    assert db.updates == []
    assert all(db.status(p) == STATUS_CANDIDATE for p in range(1, 6))
    assert any(r.levelno == logging.ERROR and "REFUSED" in r.message for r in caplog.records)


def test_empty_core_with_nothing_stale_is_not_an_error(caplog):
    """Cores 6/7/8/10 have no rows at all; an all-cores run must not ERROR on them."""
    db = _FakeDynamo([])
    with caplog.at_level(logging.INFO):
        out = _sweep(db, [])
    assert out["14"]["refused"] is None
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_demote_fraction_guard_refuses_then_yields_to_an_explicit_override(caplog):
    fresh_rows, surfaced = _fresh(n=2)
    stale = [_row(p, STATUS_CANDIDATE) for p in range(1, 4)]      # 3 stale vs 2 fresh
    db = _FakeDynamo(fresh_rows + stale)
    with caplog.at_level(logging.INFO):
        out = _sweep(db, surfaced)                                 # default 0.5
    assert out["14"]["refused"] == "fraction"
    assert db.updates == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)

    out = _sweep(db, surfaced, max_demote_fraction=1.0)
    assert out["14"]["demoted"] == 3


# --- dry run ----------------------------------------------------------------------

def test_dry_run_counts_and_writes_nothing(caplog):
    """Under --dry-run nothing carries this run's stamp, so EVERY live row is older —
    the would-demote count must exclude the pairs this run would re-surface."""
    old_rows = [_row(p, STATUS_CANDIDATE, OLD_TS) for p in range(1000, 1020)]
    surfaced = [_rec(p) for p in range(1000, 1020)]
    db = _FakeDynamo(old_rows + [_row(1, STATUS_CANDIDATE), _row(2, STATUS_CONFIRMED)])
    with caplog.at_level(logging.INFO):
        out = _sweep(db, surfaced, dry_run=True)
    assert db.updates == []
    assert out["14"]["stale"] == 2 and out["14"]["demoted"] == 0
    assert "reconcile: core 14 would demote 2" in caplog.text


# --- main(): refusal, wiring --------------------------------------------------------

@pytest.mark.parametrize("scope", [["--test", "500"], ["--test", "0"],
                                   ["--pmids-file", "pool.txt"]])
def test_reconcile_is_refused_on_a_partial_corpus_run(monkeypatch, scope):
    """Refused before ANY work — no DB engine, no scoring, no write."""
    def _boom(*a, **k):
        raise AssertionError("a refused --reconcile run must do no work")
    monkeypatch.setattr(db_mod, "get_engine", _boom)
    monkeypatch.setattr(persist, "put_core_staff_dict_counts", _boom)
    with pytest.raises(SystemExit) as exc:
        run.main(["--core", "14", "--reconcile", *scope])
    assert exc.value.code == 2


def test_reconcile_is_refused_on_a_partial_corpus_dry_run_too(monkeypatch):
    monkeypatch.setattr(db_mod, "get_engine",
                        lambda: (_ for _ in ()).throw(AssertionError("no work")))
    with pytest.raises(SystemExit):
        run.main(["--core", "14", "--reconcile", "--test", "5", "--dry-run"])


def _stub_main(monkeypatch, db, surfaced_pmids):
    monkeypatch.setattr(db_mod, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications", lambda e, pmids=None, limit=None: [])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(persist, "put_core_staff_dict_counts", lambda *a, **k: True)
    monkeypatch.setattr(ddb, "get_table", lambda *a, **k: MagicMock(name="reciterai"))
    monkeypatch.setattr(persist, "get_dynamo_client", lambda *a, **k: db)
    monkeypatch.setattr(run, "run_core", lambda core, pubs, *, scored_at, **k: [
        CoreUsageRecord(pmid=str(p), core_id=core.core_id, likelihood=0.5,
                        status=STATUS_CANDIDATE, signals=SignalResult(), scored_at=scored_at)
        for p in surfaced_pmids])

    def _put(recs):     # stand-in for put_core_usage: stamps each row like the real one
        for r in recs:
            db.store[(f"PUB#{r.pmid}", f"CORE#{r.core_id}")] = {
                "PK": {"S": f"PUB#{r.pmid}"}, "SK": {"S": f"CORE#{r.core_id}"},
                "pmid": {"S": r.pmid}, "status": {"S": r.status},
                "scored_at": {"S": r.scored_at}}
        return len(recs)
    monkeypatch.setattr(persist, "put_core_usage", _put)


def test_main_with_reconcile_demotes_what_the_run_did_not_resurface(monkeypatch):
    db = _FakeDynamo([_row(p, STATUS_CANDIDATE, VINTAGE_TS) for p in range(1, 4)]
                     + [_row(9, STATUS_CLAIMED, VINTAGE_TS)])
    _stub_main(monkeypatch, db, surfaced_pmids=[1, 2, 10, 11])
    run.main(["--core", "14", "--reconcile"])
    assert db.status(1) == db.status(2) == STATUS_CANDIDATE     # re-surfaced
    assert db.status(3) == STATUS_BELOW                          # stale
    assert db.status(9) == STATUS_CLAIMED                        # human


def test_main_without_reconcile_never_sweeps(monkeypatch):
    db = _FakeDynamo([_row(3, STATUS_CANDIDATE, VINTAGE_TS)])
    _stub_main(monkeypatch, db, surfaced_pmids=[1, 2])
    run.main(["--core", "14"])
    assert db.status(3) == STATUS_CANDIDATE
    assert db.scans == [] and db.updates == []


def test_main_dry_run_with_reconcile_writes_nothing(monkeypatch, caplog):
    db = _FakeDynamo([_row(p, STATUS_CANDIDATE, VINTAGE_TS) for p in range(1, 4)])
    _stub_main(monkeypatch, db, surfaced_pmids=[1, 2, 10, 11])

    def _boom(*a, **k):
        raise AssertionError("put_core_usage must not be called under --dry-run")
    monkeypatch.setattr(persist, "put_core_usage", _boom)
    with caplog.at_level(logging.INFO):
        run.main(["--core", "14", "--reconcile", "--dry-run"])
    assert db.updates == []
    assert db.status(3) == STATUS_CANDIDATE
    assert "reconcile: core 14 would demote 1" in caplog.text
