"""Unit tests for pipeline_grants.spin — pure mapping + gates, no network.

Fixture: tests/fixtures/spin_program_search.json, a sanitized captured
ProgramSearch response (see its ``_note``). Gate tests inject ``today`` so they
stay deterministic as the captured deadlines age.
"""
import json
import urllib.error
from pathlib import Path

import pytest

from pipeline_grants import spin

FIXTURE = Path(__file__).parent / "fixtures" / "spin_program_search.json"
INGESTED_AT = "2026-08-20T12:00:00Z"
TODAY = "2026-08-20"


def _fixture() -> dict:
    with open(FIXTURE) as f:
        return json.load(f)


def _program(idx: int) -> dict:
    return _fixture()["Programs"][idx]


# --- normalize_spin ---------------------------------------------------------


def test_normalize_spin_maps_captured_row():
    opp = spin.normalize_spin(_program(0), ingested_at=INGESTED_AT)
    assert opp.opportunity_id == "spin:131037"
    assert opp.source == "spin"
    assert opp.source_id == "131037"
    assert opp.sponsor == "Alzheimer's Association"
    assert opp.title.startswith("Alzheimer's Disease Strategic Fund")
    assert opp.program_type == "Research Grant"          # list -> primary
    assert opp.due_date == "2026-09-14"                  # ['14-Sep-2026'] parsed
    assert opp.status == "open"                          # deadline after ingested_at's date
    assert opp.ingested_at == INGESTED_AT
    assert opp.source_url.startswith("https://www.alz.org/")
    # applicant_type + geographic joined, normalize's " | " style
    assert opp.eligibility_raw == "Faculty Member; Researcher or Investigator | No Restrictions"
    assert opp.cfda_list == []                           # cfda null on the wire


def test_normalize_spin_amounts_always_none():
    # SPIN exposes no amount field (spike finding 1) -> prestige size_bucket abstains.
    opp = spin.normalize_spin(_program(0), ingested_at=INGESTED_AT)
    assert opp.award_ceiling is None
    assert opp.award_floor is None
    assert opp.estimated_funding is None
    assert opp.number_of_awards is None


def test_normalize_spin_missing_synopsis_falls_back_to_objective():
    row = dict(_program(0), synopsis="", objective="<p>Objective &amp; aims.</p>")
    opp = spin.normalize_spin(row, ingested_at=INGESTED_AT)
    assert opp.synopsis == "Objective & aims."           # fallback + HTML stripped


def test_normalize_spin_scalar_typed_fields_and_derived_closed_status():
    # Fields arrive scalar on some rows; a wholly past deadline derives status=closed.
    row = {"id": 7, "prog_title": "Old R01 Companion Program", "spon_name": "X",
           "synopsis": "s", "project_type": "Research Grant",
           "deadline_date": "01/15/2020", "cfda": "93.242"}
    opp = spin.normalize_spin(row, ingested_at=INGESTED_AT)
    assert opp.opportunity_id == "spin:7"
    assert opp.program_type == "Research Grant"
    assert opp.due_date == "2020-01-15"
    assert opp.status == "closed"
    assert opp.cfda_list == ["93.242"]
    assert opp.mechanism == "R01"                        # activity code recovered from title


def test_normalize_spin_no_deadline_is_open_with_blank_due_date():
    row = {"id": "8", "prog_title": "Continuous Program", "spon_name": "X", "synopsis": "s"}
    opp = spin.normalize_spin(row, ingested_at=INGESTED_AT)
    assert opp.due_date == ""
    assert opp.status == "open"


def test_normalize_spin_multiple_deadlines_picks_next_upcoming():
    row = {"id": "9", "prog_title": "Two-Cycle Program", "spon_name": "X", "synopsis": "s",
           "deadline_date": ["2026-03-01", "2026-10-01"]}
    opp = spin.normalize_spin(row, ingested_at=INGESTED_AT)
    assert opp.due_date == "2026-10-01"                  # earliest deadline on/after today
    assert opp.status == "open"                          # latest deadline still future


def test_parse_spin_date_formats():
    assert spin.parse_spin_date("14-Sep-2026") == "2026-09-14"
    assert spin.parse_spin_date("2026-09-14") == "2026-09-14"
    assert spin.parse_spin_date("9/2/2026") == "2026-09-02"
    assert spin.parse_spin_date("September 14, 2026") == "2026-09-14"
    assert spin.parse_spin_date("Rolling") == ""
    assert spin.parse_spin_date(None) == ""


# --- keep_opportunity: project_type gate [decision #1] ----------------------


def test_keep_allows_listed_project_type():
    kept, reason = spin.keep_opportunity(_program(0), today=TODAY)
    assert (kept, reason) == (True, "")


def test_keep_allows_mixed_list_with_one_allowed_type():
    # Captured AARFA row: ['Training and Professional Development', 'Fellowship'].
    row = dict(_program(2), geographic=["No Restrictions"], project_location=["No Restrictions"])
    kept, reason = spin.keep_opportunity(row, today=TODAY)
    assert (kept, reason) == (True, "")


def test_drops_junk_project_type():
    row = {"prog_title": "Best Lecture Medal", "project_type": ["Prize or Award"],
           "geographic": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "project_type")


def test_junk_type_not_rescued_by_unknown_companion():
    row = {"prog_title": "T", "project_type": ["Prize or Award", "Some New Label"],
           "geographic": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "project_type")


def test_unknown_project_type_kept_and_flagged():
    # Owner decision #1: never silently drop a category we haven't seen.
    row = {"prog_title": "T", "project_type": ["New or Existing Project"],
           "geographic": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "unknown_type")


def test_missing_project_type_kept_and_flagged():
    row = {"prog_title": "T", "geographic": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "unknown_type")


# --- keep_opportunity: geographic gate [decision #6] ------------------------


def test_drops_exclusively_non_us_geographic():
    # Captured CBIDR row: non-US country lists in both geographic and project_location.
    assert spin.keep_opportunity(_program(1), today=TODAY) == (False, "geo_non_us")


def test_no_restrictions_with_non_us_project_location_drops():
    # The spike's Australian-award leak: geographic says "No Restrictions" but the
    # project must be located in Australia -> NOT US-eligible.
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["No Restrictions"], "project_location": ["Australia"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "geo_non_us")


def test_no_restrictions_with_open_project_location_keeps():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["No Restrictions"], "project_location": ["No Restrictions"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "")


def test_empty_geographic_and_location_keeps():
    row = {"prog_title": "T", "project_type": ["Research Grant"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "")


def test_explicit_united_states_keeps_despite_foreign_location_entry():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["United States", "Canada"], "project_location": ["Canada"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "")


def test_non_us_country_list_drops_even_with_us_project_location():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["Germany", "France"], "project_location": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "geo_non_us")


# --- keep_opportunity: status gate ------------------------------------------


def test_drops_temporarily_suspended_title():
    row = {"prog_title": "Ramsey Research Fund (Temporarily Suspended)",
           "project_type": ["Research Grant"], "geographic": ["United States"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "suspended")


def test_drops_wholly_past_deadline():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["United States"], "deadline_date": ["01-Jan-2026"]}
    assert spin.keep_opportunity(row, today=TODAY) == (False, "past_deadline")


def test_keeps_when_any_deadline_still_upcoming():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["United States"],
           "deadline_date": ["2026-01-01", "2026-12-01"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "")


def test_keeps_missing_or_unparseable_deadline():
    row = {"prog_title": "T", "project_type": ["Research Grant"],
           "geographic": ["United States"], "deadline_date": ["Rolling"]}
    assert spin.keep_opportunity(row, today=TODAY) == (True, "")


def test_default_today_uses_current_date():
    base = {"prog_title": "T", "project_type": ["Research Grant"], "geographic": ["United States"]}
    assert spin.keep_opportunity(dict(base, deadline_date=["2099-01-01"])) == (True, "")
    assert spin.keep_opportunity(dict(base, deadline_date=["1999-01-01"])) == (False, "past_deadline")


# --- SpinClient -------------------------------------------------------------


def test_client_requires_all_three_env_vars(monkeypatch):
    monkeypatch.setenv("SPIN_PUBLIC_KEY", "k")
    monkeypatch.delenv("SPIN_SIGNATURE", raising=False)
    monkeypatch.setenv("SPIN_INSTITUTION_CODE", "c")
    with pytest.raises(RuntimeError) as exc:
        spin.SpinClient()
    assert "SPIN_SIGNATURE" in str(exc.value)
    assert "k" not in str(exc.value).replace("SPIN_", "")  # never echoes a value


def test_client_search_paginates_to_number_of_pages(monkeypatch):
    for name in ("SPIN_PUBLIC_KEY", "SPIN_SIGNATURE", "SPIN_INSTITUTION_CODE"):
        monkeypatch.setenv(name, "x")
    client = spin.SpinClient(page_size=2)
    pages = {
        1: {"Programs": [{"id": "1"}, {"id": "2"}], "PageNumber": 1, "NumberOfPages": 2},
        2: {"Programs": [{"id": "3"}], "PageNumber": 2, "NumberOfPages": 2},
    }
    calls = []
    def _get_page(keywords, page_number):
        calls.append((keywords, page_number))
        return pages[page_number]
    monkeypatch.setattr(client, "_get_page", _get_page)
    rows = list(client.search('[SOLR]spon_name:"X"'))
    assert [r["id"] for r in rows] == ["1", "2", "3"]
    assert calls == [('[SOLR]spon_name:"X"', 1), ('[SOLR]spon_name:"X"', 2)]


def test_client_http_error_message_carries_no_credentials(monkeypatch):
    # The request URL embeds the auth params; a failure must re-raise with only
    # the status + page number — never the URL (HTTPError.url would leak it).
    monkeypatch.setenv("SPIN_PUBLIC_KEY", "PUBLIC-KEY-SECRET")
    monkeypatch.setenv("SPIN_SIGNATURE", "SIGNATURE-SECRET")
    monkeypatch.setenv("SPIN_INSTITUTION_CODE", "INSTCODE-SECRET")
    client = spin.SpinClient()

    def _boom(url, timeout=None):
        raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
    monkeypatch.setattr(spin.urllib.request, "urlopen", _boom)
    with pytest.raises(RuntimeError) as exc:
        client._get_page('[SOLR]spon_name:"X"', 1)
    message = str(exc.value)
    assert "403" in message
    for secret in ("PUBLIC-KEY-SECRET", "SIGNATURE-SECRET", "INSTCODE-SECRET", "http"):
        assert secret not in message
    assert exc.value.__cause__ is None and exc.value.__suppress_context__  # chain severed


def test_client_non_json_body_raises_without_url(monkeypatch):
    import io
    monkeypatch.setenv("SPIN_PUBLIC_KEY", "PUBLIC-KEY-SECRET")
    monkeypatch.setenv("SPIN_SIGNATURE", "SIGNATURE-SECRET")
    monkeypatch.setenv("SPIN_INSTITUTION_CODE", "INSTCODE-SECRET")
    client = spin.SpinClient()

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(spin.urllib.request, "urlopen",
                        lambda url, timeout=None: _Resp(b"<html>maintenance</html>"))
    with pytest.raises(RuntimeError) as exc:
        client._get_page("q", 1)
    message = str(exc.value)
    assert "non-JSON" in message
    for secret in ("PUBLIC-KEY-SECRET", "SIGNATURE-SECRET", "INSTCODE-SECRET"):
        assert secret not in message
