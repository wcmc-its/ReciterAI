"""scripts/backfill_synopsis_stage_wide.py — the pure pieces, the spend cap and the dry run."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from pipeline_enrichment.synopsis import SynopsisResult
from scripts import backfill_synopsis_stage_wide as mod
from utils.bedrock_client import SONNET_MODEL


def _put(**over):
    kw = dict(pmid="42", synopsis="A short synopsis.", model=SONNET_MODEL, stage="T2",
              confidence="high", enriched_at="2026-11-18T00:00:00Z")
    kw.update(over)
    return mod.build_put(**kw)


def test_build_put_never_emits_synopsis_or_impact_score():
    # Readers treat an IMPACT# row with a 'synopsis' as an enriched paper; this
    # row must not look like one.
    item = _put()["Item"]
    assert "synopsis" not in item and "impact_score" not in item
    assert item == {
        "PK": {"S": "IMPACT#pmid_42"}, "SK": {"S": "SCORE"}, "pmid": {"S": "42"},
        "stage_synopsis": {"S": "A short synopsis."}, "stage_synopsis_model": {"S": SONNET_MODEL},
        "translational_stage": {"S": "T2"}, "translational_stage_confidence": {"S": "high"},
        "translational_stage_source": {"S": "abstract"}, "enrichment_scope": {"S": "stage_only"},
        "enriched_at": {"S": "2026-11-18T00:00:00Z"},
    }


def test_build_put_is_conditional_on_the_row_not_existing():
    assert _put()["ConditionExpression"] == "attribute_not_exists(PK)"
    assert _put()["TableName"] == "reciterai"


def test_build_put_omits_invalid_stage_and_missing_confidence():
    item = _put(stage=None, confidence="high")["Item"]
    assert "translational_stage" not in item and "translational_stage_confidence" not in item
    item = _put(confidence=None)["Item"]
    assert item["translational_stage"] == {"S": "T2"} and "translational_stage_confidence" not in item


def test_select_work_set_excludes_existing_and_invalid_and_is_newest_first():
    in_scope = ["5", "100", "20", "7", "9"]
    assert mod.select_work_set(in_scope, {"20"}, {"7"}) == ["100", "9", "5"]
    assert mod.select_work_set(in_scope, {"20"}, {"7"}, limit=2) == ["100", "9"]
    assert mod.select_work_set(in_scope, set(in_scope), set()) == []


def test_scan_impact_pmids_takes_any_impact_row_and_paginates():
    client = MagicMock()
    client.scan.side_effect = [
        {"Items": [{"PK": {"S": "IMPACT#pmid_1"}}], "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"PK": {"S": "IMPACT#pmid_2"}}]},
    ]
    assert mod.scan_impact_pmids(client) == {"1", "2"}
    # No attribute filter: a stage-only row and a daily-job row both count.
    assert client.scan.call_args_list[0].kwargs["FilterExpression"] == "begins_with(PK, :p)"


ABSTRACT = "Background: an abstract long enough to be a real one. Methods: some. Results: some."


def _row(pmid):
    return {"pmid": int(pmid), "articleTitle": f"T{pmid}", "journalTitleVerbose": "J",
            "articleYear": 2024, "abstractVarchar": ABSTRACT}


def _fetch(chunk):
    return [_row(p) for p in chunk]


def _ok(row, usd=1.0, stage="T1"):
    return {"pmid": str(row["pmid"]), "title": row["articleTitle"], "synopsis": "S", "stage": stage,
            "confidence": "high", "model": SONNET_MODEL, "usd": usd, "error": ""}


def test_summarize_calls_the_production_synopsis_with_the_daily_job_arguments():
    syn = SynopsisResult(pmid="7", synopsis="S", model=SONNET_MODEL, input_tokens=1000,
                         output_tokens=60, stage="T0", stage_confidence="medium")
    with patch.object(mod, "generate_synopsis", return_value=syn) as gen:
        out = mod.summarize(_row("7"))
    assert gen.call_args.kwargs == {"pmid": "7", "title": "T7", "journal": "J", "year": 2024,
                                    "abstract": ABSTRACT}
    assert (out["synopsis"], out["stage"], out["confidence"], out["error"]) == ("S", "T0", "medium", "")
    assert out["usd"] > 0


def test_summarize_never_raises_and_flags_a_missing_stage():
    with patch.object(mod, "generate_synopsis", side_effect=RuntimeError("boom")):
        out = mod.summarize(_row("7"))
    assert out["synopsis"] is None and "boom" in out["error"] and out["usd"] == 0.0

    syn = SynopsisResult(pmid="7", synopsis="S", model=SONNET_MODEL, input_tokens=10, output_tokens=5)
    with patch.object(mod, "generate_synopsis", return_value=syn):
        out = mod.summarize(_row("7"))
    assert out["stage"] is None and out["error"] == "no valid stage in response"


def test_max_usd_stops_submission():
    calls = []

    def fake(row):
        calls.append(row["pmid"])
        return _ok(row, usd=1.0)

    s = mod.run([str(p) for p in range(10, 0, -1)], _fetch, max_usd=3.0, workers=1, summarize_fn=fake)
    assert calls == [10, 9, 8]  # newest first, and nothing after the cap is reached
    assert s["budget_hit"] and s["not_submitted"] == 7 and s["usd"] == 3.0


def test_max_usd_checked_before_fetching_the_next_chunk():
    fetch = MagicMock(side_effect=_fetch)
    with patch.object(mod, "FETCH_CHUNK", 2):
        s = mod.run(["4", "3", "2", "1"], fetch, max_usd=2.0, workers=1, summarize_fn=_ok)
    assert fetch.call_count == 1 and s["not_submitted"] == 2 and len(s["results"]) == 2


def test_dry_run_writes_nothing():
    client = MagicMock()
    s = mod.run(["2", "1"], _fetch, max_usd=100, client=client, apply=False, summarize_fn=_ok)
    assert len(s["results"]) == 2 and s["written"] == 0
    assert client.method_calls == []


def test_apply_writes_only_papers_with_synopsis_and_stage_and_counts_outcomes():
    def fake(row):
        r = _ok(row)
        if row["pmid"] == 3:
            r["stage"] = None          # no valid stage: no row
        if row["pmid"] == 2:
            r["synopsis"] = None       # call failed: no row
        return r

    client = MagicMock()
    cond = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
    boom = ClientError({"Error": {"Code": "ThrottlingException"}}, "PutItem")
    client.put_item.side_effect = [None, cond, boom]
    s = mod.run(["6", "5", "4", "3", "2"], _fetch, max_usd=100, client=client, apply=True,
                workers=1, summarize_fn=fake)
    assert sorted(c.kwargs["Item"]["PK"]["S"] for c in client.put_item.call_args_list) == [
        "IMPACT#pmid_4", "IMPACT#pmid_5", "IMPACT#pmid_6"]
    assert all("synopsis" not in c.kwargs["Item"] for c in client.put_item.call_args_list)
    assert (s["written"], s["already"], s["write_errors"]) == (1, 1, 1)


def test_papers_missing_from_the_reciterdb_fetch_are_counted_not_processed():
    s = mod.run(["2", "1"], lambda chunk: [_row("2")], max_usd=100, summarize_fn=_ok)
    assert s["not_found"] == 1 and [r["pmid"] for r in s["results"]] == ["2"]


@pytest.mark.parametrize("abstract", [None, "", "\r", " \r\n\t ", "Not available.", "Not available.\r",
                                      "[Figure: see text].", "N",
                                      "PURPOSE: T METHODS: T RESULTS: T CONCLUSION: T",
                                      "x" + " " * 80 + "y"])
def test_usable_abstract_rejects_blank_and_placeholder_text(abstract):
    assert not mod.usable_abstract(abstract)


def test_usable_abstract_accepts_a_real_abstract():
    assert mod.usable_abstract(ABSTRACT)
    assert mod.usable_abstract("  " + "word " * 12 + "\r")


def test_work_set_sql_does_not_rely_on_trim_for_blank_abstracts():
    # MySQL TRIM strips spaces only, so TRIM('\r') <> '' is true.
    assert "TRIM(" not in mod.WORK_SET_SQL
    assert f"CHAR_LENGTH(COALESCE(r.abstractVarchar, '')) >= {mod.MIN_ABSTRACT_CHARS}" in mod.WORK_SET_SQL


def test_papers_without_a_usable_abstract_get_no_model_call_and_no_row():
    def fetch(chunk):
        rows = [_row(p) for p in chunk]
        rows[0]["abstractVarchar"] = "\r"
        rows[1]["abstractVarchar"] = "Not available."
        return rows

    called = []
    client = MagicMock()
    s = mod.run(["4", "3", "2", "1"], fetch, max_usd=100, client=client, apply=True, workers=1,
                summarize_fn=lambda row: called.append(row["pmid"]) or _ok(row))
    assert sorted(called) == [1, 2]
    assert s["no_abstract"] == 2 and s["written"] == 2 and s["not_submitted"] == 0
    assert sorted(c.kwargs["Item"]["pmid"]["S"] for c in client.put_item.call_args_list) == ["1", "2"]


def test_a_slow_call_does_not_hold_back_finished_results():
    # The newest paper is submitted first and is the slow one. Its result must
    # not gate the write or the CSV row of the papers that finished meanwhile.
    release = threading.Event()
    seen = []

    def fake(row):
        if row["pmid"] == 9:
            release.wait(timeout=10)
        return _ok(row)

    def on_result(r):
        seen.append(r["pmid"])
        if len(seen) == 3:
            release.set()

    client = MagicMock()
    s = mod.run(["9", "3", "2", "1"], _fetch, max_usd=100, client=client, apply=True, workers=4,
                summarize_fn=fake, on_result=on_result)
    assert seen[-1] == "9" and sorted(seen[:3]) == ["1", "2", "3"]
    assert client.put_item.call_args_list[-1].kwargs["Item"]["pmid"] == {"S": "9"}
    assert s["written"] == 4


def test_a_non_client_error_on_put_is_counted_and_the_run_continues():
    client = MagicMock()
    client.put_item.side_effect = [None, EndpointConnectionError(endpoint_url="https://example.invalid"), None]
    s = mod.run(["3", "2", "1"], _fetch, max_usd=100, client=client, apply=True, workers=1, summarize_fn=_ok)
    assert (s["written"], s["write_errors"], s["aborted"]) == (2, 1, "")
    assert len(s["results"]) == 3


def test_consecutive_write_errors_stop_the_run_and_still_return_a_summary():
    client = MagicMock()
    client.put_item.side_effect = EndpointConnectionError(endpoint_url="https://example.invalid")
    calls = []
    with patch.object(mod, "FETCH_CHUNK", 5):
        s = mod.run([str(p) for p in range(40, 0, -1)], _fetch, max_usd=100, client=client, apply=True,
                    workers=1, summarize_fn=lambda row: calls.append(1) or _ok(row, usd=0.5))
    assert "consecutive write errors" in s["aborted"]
    assert s["written"] == 0 and s["write_errors"] >= mod.MAX_CONSECUTIVE_WRITE_ERRORS
    assert len(calls) <= 5                      # only the chunk in hand; later chunks never start
    assert s["usd"] == 0.5 * len(calls)
    assert s["not_submitted"] == 40 - len(s["results"])


def test_a_failed_chunk_fetch_stops_the_run_and_keeps_what_was_done():
    def fetch(chunk):
        if "2" in chunk:
            raise RuntimeError("db gone")
        return _fetch(chunk)

    client = MagicMock()
    with patch.object(mod, "FETCH_CHUNK", 2):
        s = mod.run(["4", "3", "2", "1"], fetch, max_usd=100, client=client, apply=True, workers=1,
                    summarize_fn=_ok)
    assert s["aborted"] == "RuntimeError: db gone"
    assert s["written"] == 2 and s["not_submitted"] == 2 and s["usd"] == 2.0


def test_an_interrupt_keeps_finished_results_and_returns_a_summary():
    def on_result(r):
        if r["pmid"] == "3":
            raise KeyboardInterrupt

    client = MagicMock()
    with patch.object(mod, "FETCH_CHUNK", 1):
        s = mod.run(["3", "2", "1"], _fetch, max_usd=100, client=client, apply=True, workers=1,
                    summarize_fn=_ok, on_result=on_result)
    assert s["aborted"] == "interrupted"
    assert [r["pmid"] for r in s["results"]] == ["3"] and s["not_submitted"] == 2


def test_main_prints_the_summary_and_exits_nonzero_when_the_run_aborts(capsys):
    syn = SynopsisResult(pmid="1", synopsis="S", model=SONNET_MODEL, input_tokens=1000,
                         output_tokens=60, stage="T1", stage_confidence="high")
    client = MagicMock()
    client.put_item.side_effect = EndpointConnectionError(endpoint_url="https://example.invalid")
    with patch.object(mod, "get_engine"), patch.object(mod, "get_dynamo_client", return_value=client), \
         patch.object(mod, "fetch_in_scope_pmids", return_value=[str(p) for p in range(1, 9)]), \
         patch.object(mod, "scan_impact_pmids", return_value=set()), \
         patch.object(mod, "scan_invalid_pmids", return_value=[]), \
         patch.object(mod, "fetch_publications_for_enrichment", side_effect=lambda e, c: _fetch(c)), \
         patch.object(mod, "generate_synopsis", return_value=syn):
        rc = mod.main(["--apply"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ABORTED (" in out and "cost: $" in out and "written: 0" in out
