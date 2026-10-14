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

from pipeline_area_signals import drain, prompts, run as run_mod, signals
from pipeline_enrichment.synopsis import SynopsisResult
from utils.openai_client import GPT5_MODEL
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


def _condition_holds(row, expression, names, values):
    """Evaluate the condition forms drain.py uses (AND of clauses; one OR group)."""
    def clause(c):
        c = c.strip()
        if c.startswith("(") and c.endswith(")"):
            return any(clause(part) for part in c[1:-1].split(" OR "))
        if m := re.fullmatch(r"attribute_exists\((\S+)\)", c):
            return row is not None and names.get(m[1], m[1]) in row
        if m := re.fullmatch(r"attribute_not_exists\((\S+)\)", c):
            return row is None or names.get(m[1], m[1]) not in row
        attr, op, value = c.split(" ")
        current = (row or {}).get(names.get(attr, attr))
        if op == "=":
            return current == values[value]
        if op == "<":
            return current is not None and current < values[value]
        raise AssertionError(f"unsupported condition {c!r}")
    return all(clause(c) for c in expression.split(" AND "))


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

    def update_item(Key, UpdateExpression, ConditionExpression,
                    ExpressionAttributeValues=None, ExpressionAttributeNames=None):
        values = ExpressionAttributeValues or {}
        names = ExpressionAttributeNames or {}
        row = store.get((Key["PK"], Key["SK"]))
        if not _condition_holds(row, ConditionExpression, names, values):
            raise _conditional_failed()
        expr = " " + UpdateExpression
        set_part, _, remove_part = expr.partition(" REMOVE ")
        set_part = set_part.strip().removeprefix("SET ")
        for assign in filter(None, set_part.split(", ")):
            attr, value = (x.strip() for x in assign.split(" = "))
            row[names.get(attr, attr)] = values[value]
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
        inp(key="c", status="scored", taxonomy_version=TV,
            prompt_version=prompts.PROMPT_VERSION),
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
    table = make_table([inp(key="a", status="scored", taxonomy_version=TV,
                            prompt_version=prompts.PROMPT_VERSION)])
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


def _fixed_cost_result(inp_, *, estimated_usd=Decimal("0"), usage=()):
    return signals.SignalResult(
        item={"PK": signals.signal_pk(inp_.kind, inp_.text_key), "SK": "META",
              "input_hash": inp_.input_hash(), "taxonomy_version": TV,
              "scorer_fallback_model": "gpt-5.1" if estimated_usd else None},
        usage=list(usage), estimated_usd=estimated_usd)


def test_estimated_fallback_charges_count_toward_the_ceiling(monkeypatch):
    # Each item reports no measured usage but a $1 estimated dense-fallback
    # charge. $0 + $0.02 est, then $1 + $0.02 <= $1.5, then $2 + $0.02 > $1.5.
    # A guard that ignored estimated charges would run all three.
    monkeypatch.setattr(signals, "score_input",
                        lambda inp_, **kw: _fixed_cost_result(inp_, estimated_usd=Decimal("1")))
    table = make_table([inp(key=str(n)) for n in range(3)])
    _, summary, _ = do_run(table, max_cost_usd=Decimal("1.5"), workers=1)
    assert summary["outcomes"] == {"scored": 2}
    assert summary["run_status"] == "budget_stopped"
    assert stage_rows(table)[-1]["cost_observed_usd"] == Decimal("2")


def test_openai_synopsis_tokens_count_toward_the_ceiling(monkeypatch):
    def gpt_synopsis(**kw):
        return SynopsisResult(pmid=kw["pmid"], synopsis="Synthetic synopsis", model=GPT5_MODEL,
                              input_tokens=2_000_000, output_tokens=0, error=None)
    monkeypatch.setattr(signals, "generate_synopsis", gpt_synopsis)
    table = make_table([inp(key=str(n)) for n in range(3)])
    _, summary, _ = do_run(table, max_cost_usd=Decimal("2"), workers=1)
    assert GPT5_MODEL in summary["model_breakdown"]
    # 2M gpt-5.1 input tokens is $2.50, so after one item nothing fits; a guard
    # that missed the OpenAI tokens (~$0.01 of Bedrock per item) would run all three.
    assert summary["outcomes"] == {"scored": 1}
    assert summary["run_status"] == "budget_stopped"


def test_failed_attempt_openai_spend_is_recorded(monkeypatch):
    def gpt_empty(**kw):
        return SynopsisResult(pmid=kw["pmid"], synopsis=None, model=GPT5_MODEL,
                              input_tokens=1_000_000, output_tokens=0, error="filtered")
    monkeypatch.setattr(signals, "generate_synopsis", gpt_empty)
    table = make_table([inp(key="a")])
    _, summary, _ = do_run(table)
    assert summary["outcomes"] == {"retry": 1}
    assert GPT5_MODEL in summary["model_breakdown"]
    assert Decimal(summary["measured_usd"]) > 0


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
    assert seen["requeue_failed"] is False
    assert run_mod.main(["--requeue-failed"]) == 0 and seen["requeue_failed"] is True


def test_status_expressions_name_only_used_placeholders():
    """DynamoDB rejects unused ExpressionAttributeNames/Values (and empty Values)."""
    table = make_table([inp(key="a"), inp(key="b", status="failed")])
    assert drain.claim(table, table.store[("AREAINPUT", "grant#a")], run_id="r")
    assert drain.claim(table, dict(table.store[("AREAINPUT", "grant#b")]), run_id="r",
                       requeue=True)
    drain.release(table, "grant#a")
    drain.mark_scored(table, "grant#a", taxonomy_version=TV)
    drain.mark_failed(table, "grant#a", reason="r", attempts=3)
    drain.record_attempt(table, "grant#a", error="e", attempts=1)
    assert table.update_item.call_count == 6
    for call in table.update_item.call_args_list:
        kw = call.kwargs
        expr = kw["UpdateExpression"] + " " + kw["ConditionExpression"]
        for name in kw.get("ExpressionAttributeNames", {}):
            assert name in expr
        assert kw.get("ExpressionAttributeValues", None) != {}
        for value in kw.get("ExpressionAttributeValues", {}):
            assert re.search(re.escape(value) + r"\b", expr), value


# --- prompt change and the reuse check ------------------------------------------------

def test_prompt_version_bump_rescores_scored_items(monkeypatch):
    table = make_table([inp(key="a")])
    do_run(table)
    first_hash = table.store[("AREASIGNAL#grant:a", "META")]["input_hash"]
    assert table.store[("AREAINPUT", "grant#a")]["prompt_version"] == prompts.PROMPT_VERSION

    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)
    assert summary["due"] == 0 and fake.calls == 0  # nothing changed, nothing due

    monkeypatch.setattr(prompts, "PROMPT_VERSION", "2099-01-01")
    _, summary, _ = do_run(table, fake)
    assert summary["by_reason"] == {drain.REASON_PROMPT: 1}
    assert summary["outcomes"] == {"scored": 1} and fake.calls == 3
    assert table.store[("AREASIGNAL#grant:a", "META")]["input_hash"] != first_hash
    assert table.store[("AREAINPUT", "grant#a")]["prompt_version"] == "2099-01-01"


def test_stored_signal_with_a_different_input_hash_is_rescored_not_reused():
    item = inp(key="a")
    stored = {"PK": "AREASIGNAL#grant:a", "SK": "META", "taxonomy_version": TV,
              "input_hash": "0" * 64}
    table = make_table([item, stored])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)
    assert summary["outcomes"] == {"scored": 1} and fake.calls == 3
    assert (table.store[("AREASIGNAL#grant:a", "META")]["input_hash"]
            == signals.AreaInput.from_item(item).input_hash())


# --- circuit breaker -----------------------------------------------------------------

class DeniedConverse:
    """Every call fails the way an invalid or expired bearer token does."""

    def __init__(self, code="UnrecognizedClientException"):
        self.code = code
        self.calls = 0

    def converse(self, **kw):
        self.calls += 1
        raise ClientError({"Error": {"Code": self.code, "Message": "denied"}}, "Converse")


@pytest.mark.parametrize("code", ["UnrecognizedClientException", "AccessDeniedException",
                                  "ServiceUnavailableException"])
def test_system_wide_bedrock_error_opens_the_breaker_and_burns_no_attempts(code, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # the client's own retry backoff
    table = make_table([inp(key=str(n)) for n in range(5)])
    fake = DeniedConverse(code)
    for _day in range(drain.MAX_ATTEMPTS + 1):
        code_, summary, alerts = do_run(table, fake, workers=4)
        assert code_ == 1 and summary["run_status"] == "aborted"
        assert summary["outcomes"] == {"released": 4}  # the first batch; the 5th never ran
        assert [a[0] for a in alerts] == ["ERROR"] and "circuit breaker" in alerts[0][1]
        assert stage_rows(table)[-1]["status"] == "failed"
        assert stage_rows(table)[-1]["error_code"] == "aborted"
    for n in range(5):
        src = table.store[("AREAINPUT", f"grant#{n}")]
        assert src["status"] == "pending" and not src.get("attempts")
        assert "lease_until" not in src


def test_a_long_streak_of_unrecognized_failures_opens_the_breaker():
    n_items = run_mod.BREAKER_STREAK + 3
    table = make_table([inp(key=f"{n:02d}") for n in range(n_items)])
    code, summary, alerts = do_run(table, FakeConverse(fail_dense=True), workers=1)
    assert code == 1 and summary["run_status"] == "aborted"
    assert summary["outcomes"] == {"released": run_mod.BREAKER_STREAK}
    assert [a[0] for a in alerts] == ["ERROR"]
    assert all(not table.store[("AREAINPUT", f"grant#{n:02d}")].get("attempts")
               for n in range(n_items))


class SomeItemsFail(FakeConverse):
    """Dense scoring fails only for texts carrying a marker (item-specific failures)."""

    def converse(self, **kw):
        user = kw["messages"][0]["content"][0]["text"]
        self.fail_dense = "POISON" in user
        return super().converse(**kw)


def test_item_failures_before_a_success_are_counted_against_the_items():
    table = make_table([inp(key="a", created_at="2026-10-01T00:00:00Z", text="POISON a"),
                        inp(key="b", created_at="2026-10-02T00:00:00Z", text="POISON b"),
                        inp(key="c", created_at="2026-10-03T00:00:00Z")])
    fake = SomeItemsFail()
    code, summary, alerts = do_run(table, fake, workers=1)
    assert code == 0 and summary["outcomes"] == {"retry": 2, "scored": 1}
    assert alerts == []
    for key in ("a", "b"):
        src = table.store[("AREAINPUT", f"grant#{key}")]
        assert src["attempts"] == 1 and "lease_until" not in src


def test_failures_before_a_success_stay_counted_when_the_breaker_opens_later(monkeypatch):
    monkeypatch.setattr(run_mod, "BREAKER_STREAK", 2)
    table = make_table([inp(key="a", created_at="2026-10-01T00:00:00Z", text="POISON a"),
                        inp(key="b", created_at="2026-10-02T00:00:00Z"),
                        inp(key="c", created_at="2026-10-03T00:00:00Z", text="POISON c"),
                        inp(key="d", created_at="2026-10-04T00:00:00Z", text="POISON d")])
    code, summary, _ = do_run(table, SomeItemsFail(), workers=1)
    assert code == 1 and summary["run_status"] == "aborted"
    assert summary["outcomes"] == {"retry": 1, "scored": 1, "released": 2}
    assert table.store[("AREAINPUT", "grant#a")]["attempts"] == 1
    assert not table.store[("AREAINPUT", "grant#c")].get("attempts")
    assert not table.store[("AREAINPUT", "grant#d")].get("attempts")


def test_requeue_failed_brings_failed_items_back():
    table = make_table([inp(key="a", status="failed", attempts=3, fail_reason="outage")])
    _, summary, _ = do_run(table)
    assert summary["due"] == 0

    _, summary, _ = do_run(table, requeue_failed=True)
    assert summary["by_reason"] == {drain.REASON_REQUEUED: 1}
    assert summary["outcomes"] == {"scored": 1}
    src = table.store[("AREAINPUT", "grant#a")]
    assert src["status"] == "scored" and src["attempts"] == 0 and "fail_reason" not in src


def test_requeued_item_that_fails_again_is_pending_with_one_attempt():
    table = make_table([inp(key="a", status="failed", attempts=3)])
    _, summary, _ = do_run(table, FakeConverse(fail_dense=True), requeue_failed=True)
    assert summary["outcomes"] == {"retry": 1}
    src = table.store[("AREAINPUT", "grant#a")]
    assert src["status"] == "pending" and src["attempts"] == 1


# --- overlapping runs ------------------------------------------------------------------

def test_item_leased_by_another_run_is_skipped_without_spend():
    table = make_table([inp(key="a", lease_until="2999-01-01T00:00:00Z", lease_run="other")])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake)
    assert summary["outcomes"] == {"skipped": 1} and fake.calls == 0
    assert table.store[("AREAINPUT", "grant#a")]["lease_run"] == "other"


def test_expired_lease_from_a_crashed_run_is_taken_over():
    table = make_table([inp(key="a", lease_until="2000-01-01T00:00:00Z", lease_run="dead")])
    _, summary, _ = do_run(table)
    assert summary["outcomes"] == {"scored": 1}
    assert "lease_until" not in table.store[("AREAINPUT", "grant#a")]


def test_claim_fails_when_another_run_finished_the_item_after_our_read():
    table = make_table([inp(key="a")])
    seen = dict(table.store[("AREAINPUT", "grant#a")])
    drain.mark_scored(table, "grant#a", taxonomy_version=TV)  # the other run
    assert drain.claim(table, seen, run_id="late") is False


def test_overlapping_runs_score_each_item_once():
    table = make_table([inp(key=str(n)) for n in range(3)])
    original = table.get_item.side_effect
    fake_a, fake_b = FakeConverse(), FakeConverse()
    started = []

    def get_then_overlap(Key):
        row = original(Key=Key)
        if Key["PK"] == "AREAINPUT" and not started:
            started.append(1)
            # A second run lists and drains while the first holds its first item.
            do_run(table, fake_b, workers=1)
        return row

    table.get_item.side_effect = get_then_overlap
    do_run(table, fake_a, workers=1)
    assert fake_a.calls + fake_b.calls == 3 * 3  # each item scored once, by one run
    assert all(table.store[("AREAINPUT", f"grant#{n}")]["status"] == "scored" for n in range(3))
