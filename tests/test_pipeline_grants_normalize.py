import json
from pathlib import Path
from pipeline_grants.normalize import normalize_grantsgov

FIXTURE = Path(__file__).parent / "fixtures" / "grantsgov_detail.json"


def _load():
    with open(FIXTURE) as f:
        return json.load(f)


def test_normalize_maps_core_fields():
    opp = normalize_grantsgov(_load())
    assert opp.opportunity_id == "grants_gov:359855"
    assert opp.source_id == "359855"
    assert opp.sponsor == "National Institutes of Health"
    assert opp.title.startswith("Advanced Development")
    assert opp.cfda_list == ["93.393"]


def test_normalize_strips_html_and_parses_amounts():
    opp = normalize_grantsgov(_load())
    assert "<p>" not in opp.synopsis and "NOFO" in opp.synopsis
    assert opp.award_ceiling == 600000
    assert opp.award_floor == 50000
    assert opp.number_of_awards == 3


def test_normalize_parses_detail_dates_to_iso():
    opp = normalize_grantsgov(_load())
    assert opp.due_date == "2026-10-19"
    assert opp.open_date == "2026-05-21"


def test_normalize_eligibility_and_status():
    opp = normalize_grantsgov(_load())
    assert "Higher education" in opp.eligibility_raw
    assert opp.status == "posted"  # forecast is null


def test_to_int_handles_currency_commas_and_decimals():
    from pipeline_grants.normalize import _to_int
    assert _to_int("600000") == 600000
    assert _to_int("$50,000.00") == 50000
    assert _to_int("$2,600,000") == 2600000
    assert _to_int("") is None
    assert _to_int(None) is None
    assert _to_int("TBD") is None


def test_normalize_recovers_activity_code_from_title():
    # FON has no embedded code; the title does → mechanism falls back to the title.
    resp = {"data": {"id": "999", "opportunityNumber": "RFA-CA-24-001",
                     "opportunityTitle": "Cancer Moonshot Research (R01 Clinical Trial Optional)",
                     "synopsis": {}}}
    assert normalize_grantsgov(resp).mechanism == "R01"
    # FON code wins when present (title not consulted).
    resp2 = {"data": {"id": "1", "opportunityNumber": "PAR DP2 program",
                      "opportunityTitle": "Something (R01)", "synopsis": {}}}
    assert normalize_grantsgov(resp2).mechanism == "DP2"
