"""pipeline_area_signals.drain + run: queue selection, status marking, cost
guard, taxonomy re-score, preflight, dry run, STAGE# row and alerts.

The table is a MagicMock whose query/get_item/put_item/update_item side effects
read and write a dict, so status transitions are observable; the Bedrock fake
sits at the boto3 `converse` seam under the real MeteredBedrockClient.
Fixtures are synthetic.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from pipeline_area_signals import drain, run as run_mod, signals
from utils.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
TV = signals.current_taxonomy_version(TAXONOMY)


# --- fakes -----------------------------------------------------------------------

class FakeConverse:
    def __init__(self, *, fail_dense=False):
        self.fail_dense = fail_dense
        self.calls = 0

    def converse(self, **kw):
        self.calls += 1
        system = " ".join(s.get("text", "") for s in kw.get("system", []))
        if "synopses" in system:
            body, usage = {"synopsis": "Synthetic synopsis"}, (500, 30)
        elif system.startswith("Score this publication"):
            body, usage = {"0": 0.9}, (3000, 60)
        elif self.fail_dense:
            body, usage = {"999": {"score": 0.9}}, (2000, 200)  # unrecognized ids
        else:
            body, usage = {"0": {"score": 0.8, "rationale": "r"}}, (2000, 200)
        return {"output": {"message": {"content": [{"text": json.dumps(body)}]}},
                "usage": {"inputTokens": usage[0], "outputTokens": usage[1]},
                "stopReason": "end_turn"}


def client_factory(fake):
    def make():
        c = signals.MeteredBedrockClient()
        c._client = fake
        return c
    return make


def _conditional_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


def make_table(items):
    """MagicMock resource Table over {(PK, SK): item}."""
    store = {(i["PK"], i["SK"]): dict(i) for i in items}
    table = MagicMock(name="reciterai")
    table.store = store

    def query(**kw):
        pk = kw["KeyConditionExpression"].get_expression()["values"][1]
        rows = sorted((v for (p, _), v in store.items() if p == pk), key=lambda v: v["SK"])
        start = 0
        if "ExclusiveStartKey" in kw:
            start = next(n for n, r in enumerate(rows) if r["SK"] == kw["ExclusiveStartKey"]["SK"]) + 1
        page = rows[start:start + 2]  # tiny pages exercise pagination
        out = {"Items": [dict(r) for r in page]}
        if start + 2 < len(rows):
            out["LastEvaluatedKey"] = {"PK": pk, "SK": page[-1]["SK"]}
        return out

    def get_item(Key):
        row = store.get((Key["PK"], Key["SK"]))
        return {"Item": dict(row)} if row else {}

    def put_item(Item):
        store[(Item["PK"], Item["SK"])] = dict(Item)

    def update_item(Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression,
                    ExpressionAttributeNames=None):
        assert ConditionExpression == "attribute_exists(PK)"
        row = store.get((Key["PK"], Key["SK"]))
        if row is None:
            raise _conditional_failed()
        names = ExpressionAttributeNames or {}
        set_part, _, remove_part = UpdateExpression.partition(" REMOVE ")
        for assign in set_part.removeprefix("SET ").split(", "):
            attr, value = (x.strip() for x in assign.split(" = "))
            row[names.get(attr, attr)] = ExpressionAttributeValues[value]
        for attr in filter(None, (a.strip() for a in remove_part.split(","))):
            row.pop(attr, None)

    table.query.side_effect = query
    table.get_item.side_effect = get_item
    table.put_item.side_effect = put_item
    table.update_item.side_effect = update_item
    return table


def inp(kind="grant", key="k1", **over):
    item = {"PK": "AREAINPUT", "SK": f"{kind}#{key}", "kind": kind, "text_key": key,
            "mode": "full", "title": f"Synthetic {kind} {key}", "text": "Synthetic abstract text.",
            "start_year": Decimal("2023"), "status": "pending",
            "created_at": "2026-10-14T07:00:00Z"}
    item.update(over)
    return item


def do_run(table, fake=None, **kw):
    alerts = []
    kw.setdefault("preflight", lambda: (True, "preflight OK"))
    code, summary = run_mod.run(
        table=table, client_factory=client_factory(fake or FakeConverse()),
        alert=lambda *a, **k: alerts.append(a), taxonomy=TAXONOMY, **kw)
    return code, summary, alerts


def stage_rows(table):
    return [v for (pk, _), v in table.store.items() if pk.startswith("STAGE#")]


# --- selection ---------------------------------------------------------------------------

def test_select_due_takes_pending_and_stale_taxonomy_only():
    items = [
        inp(key="a", created_at="2026-10-02T00:00:00Z"),
        inp(key="b", status="scored", taxonomy_version="taxonomy_v2@old",
            created_at="2026-10-01T00:00:00Z"),
        inp(key="c", status="scored", taxonomy_version=TV),
        inp(key="d", status="failed"),
        inp(key="e", created_at="2026-10-01T00:00:00Z"),
    ]
    due = drain.select_due(items, TV)
    assert [(i["text_key"], r) for i, r in due] == [
        ("e", drain.REASON_PENDING), ("a", drain.REASON_PENDING), ("b", drain.REASON_TAXONOMY)]


def test_list_inputs_follows_pagination():
    table = make_table([inp(key=str(n)) for n in range(5)])
    assert len(drain.list_inputs(table)) == 5
    assert table.query.call_count == 3


# --- the happy path ---------------------------------------------------------------------------

def test_run_scores_persists_and_marks_inputs():
    table = make_table([inp("grant", "g1"), inp("trial", "t1", mode="title", text="")])
    code, summary, alerts = do_run(table)

    assert code == 0 and alerts == []
    assert summary["outcomes"] == {"scored": 2}
    for kind, key in (("grant", "g1"), ("trial", "t1")):
        row = table.store[(f"AREASIGNAL#{kind}:{key}", "META")]
        assert row["taxonomy_version"] == TV
        assert row["topic_scores"][0]["score"] == Decimal("0.8")
        src = table.store[("AREAINPUT", f"{kind}#{key}")]
        assert src["status"] == "scored" and src["taxonomy_version"] == TV
        assert src["attempts"] == 0
    (stage,) = stage_rows(table)
    assert stage["PK"] == "STAGE#area_signals#GLOBAL" and stage["status"] == "complete"
    assert stage["records_written"] == 2
    # Measured from the responses: grant = synopsis + screen + dense, trial = screen + dense.
    assert stage["cost_observed_usd"] > 0
    assert stage["tunable_inputs"]["model_breakdown"]


def test_status_flip_after_listing_is_honored():
    table = make_table([inp(key="a")])
    original = table.get_item.side_effect

    def flip_then_get(Key):
        if Key["PK"] == "AREAINPUT":
            table.store[(Key["PK"], Key["SK"])]["status"] = "failed"
        return original(Key=Key)

    table.get_item.side_effect = flip_then_get
    fake = FakeConverse()
    code, summary, _ = do_run(table, fake)
    assert summary["outcomes"] == {"skipped": 1}
    assert fake.calls == 0
    assert ("AREASIGNAL#grant:a", "META") not in table.store


def test_input_deleted_after_listing_is_not_recreated():
    table = make_table([inp(key="a")])
    original = table.get_item.side_effect

    def delete_then_get(Key):
        table.store.pop(("AREAINPUT", "grant#a"), None)
        return original(Key=Key)

    table.get_item.side_effect = delete_then_get
    _, summary, _ = do_run(table)
    assert summary["outcomes"] == {"skipped": 1}
    assert ("AREAINPUT", "grant#a") not in table.store


def test_current_stored_signal_is_reused_without_model_calls():
    item = inp(key="a")
    stored = {"PK": "AREASIGNAL#grant:a", "SK": "META", "taxonomy_version": TV,
              "input_hash": signals.AreaInput.from_item(item).input_hash()}
    table = make_table([item, stored])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)
    assert summary["outcomes"] == {"reused": 1}
    assert fake.calls == 0
    assert table.store[("AREAINPUT", "grant#a")]["status"] == "scored"


# --- taxonomy change ------------------------------------------------------------------------

def test_taxonomy_change_rescores_scored_items():
    item = inp(key="a", status="scored", taxonomy_version="taxonomy_v2@0123456789ab")
    stored = {"PK": "AREASIGNAL#grant:a", "SK": "META", "taxonomy_version": "taxonomy_v2@0123456789ab",
              "input_hash": signals.AreaInput.from_item(item).input_hash()}
    table = make_table([item, stored])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)

    assert summary["by_reason"] == {drain.REASON_TAXONOMY: 1}
    assert summary["outcomes"] == {"scored": 1}
    assert fake.calls == 3
    assert table.store[("AREASIGNAL#grant:a", "META")]["taxonomy_version"] == TV
    assert table.store[("AREAINPUT", "grant#a")]["taxonomy_version"] == TV


def test_current_taxonomy_items_are_left_alone():
    table = make_table([inp(key="a", status="scored", taxonomy_version=TV)])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)
    assert summary["due"] == 0 and fake.calls == 0


# --- failures ------------------------------------------------------------------------------

def test_transient_failure_retries_then_marks_failed_with_reason():
    table = make_table([inp(key="a")])
    fake = FakeConverse(fail_dense=True)

    for attempt in (1, 2):
        _, summary, alerts = do_run(table, fake)
        src = table.store[("AREAINPUT", "grant#a")]
        assert summary["outcomes"] == {"retry": 1}
        assert src["status"] == "pending" and src["attempts"] == attempt
        assert "area scores" in src["last_error"]
        assert alerts == []

    _, summary, alerts = do_run(table, fake)
    src = table.store[("AREAINPUT", "grant#a")]
    assert summary["outcomes"] == {"failed": 1}
    assert src["status"] == "failed" and src["attempts"] == drain.MAX_ATTEMPTS
    assert "area scores" in src["fail_reason"]
    assert ("AREASIGNAL#grant:a", "META") not in table.store
    assert [a[0] for a in alerts] == ["WARN"]
    # A failed attempt's spend is still recorded.
    assert stage_rows(table)[-1]["cost_observed_usd"] > 0


def test_invalid_input_fails_at_once_without_spend():
    table = make_table([inp(key="a", mode="abstract")])
    fake = FakeConverse()
    _, summary, alerts = do_run(table, fake)
    src = table.store[("AREAINPUT", "grant#a")]
    assert src["status"] == "failed" and "unknown mode" in src["fail_reason"]
    assert fake.calls == 0
    assert alerts and alerts[0][0] == "WARN"


def test_unknown_kind_is_marked_failed_not_left_pending():
    table = make_table([inp(key="a", kind="patent")])
    _, summary, _ = do_run(table, kinds=("grant",))
    src = table.store[("AREAINPUT", "patent#a")]
    assert src["status"] == "failed" and "unknown kind" in src["fail_reason"]


def test_listing_failure_fails_the_run_before_any_spend():
    table = make_table([inp(key="a")])
    table.query.side_effect = RuntimeError("AccessDenied")
    fake = FakeConverse()
    code, summary, alerts = do_run(table, fake)
    assert code == 1 and "AccessDenied" in summary["message"]
    assert fake.calls == 0 and alerts[0][0] == "ERROR"
    (stage,) = stage_rows(table)
    assert stage["status"] == "failed"


def test_table_failure_fails_the_run_and_alerts():
    table = make_table([inp(key="a")])
    table.put_item.side_effect = RuntimeError("throttled")
    code, summary, alerts = do_run(table)
    assert code == 1 and summary["run_status"] == "failed"
    assert alerts[0][0] == "ERROR"


# --- cost guard ------------------------------------------------------------------------------

def test_cost_ceiling_stops_new_batches_and_leaves_the_rest_due():
    table = make_table([inp(key=str(n)) for n in range(6)])
    # One full item is estimated at $0.02; two items fit under $0.05, a third would not.
    _, summary, alerts = do_run(table, max_cost_usd=Decimal("0.05"), workers=2)

    assert summary["run_status"] == "budget_stopped"
    assert summary["outcomes"] == {"scored": 2}
    statuses = sorted(table.store[("AREAINPUT", f"grant#{n}")]["status"] for n in range(6))
    assert statuses == ["pending"] * 4 + ["scored"] * 2
    assert [a[0] for a in alerts] == ["WARN"]
    (stage,) = stage_rows(table)
    assert stage["status"] == "complete"
    assert stage["tunable_inputs"]["run_status"] == "budget_stopped"


def test_measured_cost_counts_toward_the_ceiling():
    table = make_table([inp(key=str(n)) for n in range(4)])
    # Each fake item measures $0.01425 (Sonnet synopsis + Haiku screen + Sonnet
    # dense at config/llm_prices.yaml rates). With one worker: $0 + $0.02 est,
    # then $0.01425 + $0.02 <= $0.035, then $0.0285 + $0.02 > $0.035 -> stop.
    # A guard that ignored measured spend would run all four.
    _, summary, _ = do_run(table, max_cost_usd=Decimal("0.035"), workers=1)
    assert summary["outcomes"] == {"scored": 2}
    assert Decimal(summary["measured_usd"]) == Decimal("0.0285")


def test_max_cost_env_default(monkeypatch):
    monkeypatch.setenv(run_mod.MAX_COST_ENV, "7.5")
    assert run_mod._max_cost_default() == Decimal("7.5")
    monkeypatch.setenv(run_mod.MAX_COST_ENV, "lots")
    assert run_mod._max_cost_default() == run_mod.DEFAULT_MAX_COST_USD


# --- preflight and dry run -------------------------------------------------------------

def test_blocked_preflight_spends_nothing_and_alerts():
    table = make_table([inp(key="a")])
    fake = FakeConverse()
    code, summary, alerts = do_run(table, fake, preflight=lambda: (False, "BLOCKED: stale"))

    assert code == 1 and summary["run_status"] == "preflight_blocked"
    assert fake.calls == 0
    table.query.assert_not_called()
    assert alerts[0][0] == "ERROR"
    (stage,) = stage_rows(table)
    assert stage["status"] == "failed" and stage["error_code"] == "preflight_blocked"


def test_dry_run_reports_and_writes_nothing():
    table = make_table([inp(key="a"), inp(key="b", mode="title", text=""),
                        inp("trial", "c", status="scored", taxonomy_version="x@y")])
    fake = FakeConverse()
    code, summary, alerts = do_run(table, fake, dry_run=True,
                                   preflight=lambda: (False, "BLOCKED"))
    assert code == 0 and alerts == []
    assert summary["due"] == 3
    assert summary["by_reason"] == {"pending": 2, "taxonomy_changed": 1}
    assert summary["by_kind_mode"] == {"grant/full": 1, "grant/title": 1, "trial/full": 1}
    assert Decimal(summary["estimated_usd"]) == Decimal("0.0525")
    assert summary["preflight_ok"] is False
    assert fake.calls == 0
    table.put_item.assert_not_called()
    table.update_item.assert_not_called()


def test_kind_filter_and_limit():
    table = make_table([inp("grant", "a"), inp("trial", "b"), inp("trial", "c")])
    _, summary, _ = do_run(table, kinds=("trial",), limit=1)
    assert summary["due"] == 1 and summary["outcomes"] == {"scored": 1}
    assert table.store[("AREAINPUT", "grant#a")]["status"] == "pending"


def test_main_parses_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(run_mod, "run", lambda **kw: (seen.update(kw) or (0, {})))
    monkeypatch.setattr("utils.dynamodb_helpers.get_table", lambda: "TABLE")
    assert run_mod.main(["--dry-run", "--max-cost-usd", "3", "--kind", "grant", "--limit", "2"]) == 0
    assert seen["dry_run"] is True and seen["max_cost_usd"] == Decimal("3")
    assert seen["kinds"] == ("grant",) and seen["limit"] == 2 and seen["table"] == "TABLE"


def test_status_expressions_name_only_used_placeholders():
    """DynamoDB rejects unused ExpressionAttributeNames/Values."""
    table = make_table([inp(key="a")])
    drain.mark_scored(table, "grant#a", taxonomy_version=TV)
    drain.mark_failed(table, "grant#a", reason="r", attempts=3)
    drain.record_attempt(table, "grant#a", error="e", attempts=1)
    for call in table.update_item.call_args_list:
        kw = call.kwargs
        expr = kw["UpdateExpression"]
        for name in kw.get("ExpressionAttributeNames", {}):
            assert name in expr
        for value in kw["ExpressionAttributeValues"]:
            assert re.search(re.escape(value) + r"\b", expr), value
