import json
from pathlib import Path
from pipeline_grants.normalize import canonical_sponsor, normalize_grantsgov

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


def test_mechanism_falls_back_to_title_activity_code():
    # opportunityNumber carries no code (typical NIH FOA) -> recover R01 from the title
    opp = normalize_grantsgov({"data": {
        "opportunityNumber": "PAR-23-065", "opportunityTitle": "Cancer Research (R01 Clinical Trial Required)"}})
    assert opp.mechanism == "R01"
    # opportunityNumber wins when it has one
    opp2 = normalize_grantsgov({"data": {
        "opportunityNumber": "DP2-OD-99", "opportunityTitle": "Some R03 thing"}})
    assert opp2.mechanism == "DP2"


def test_to_int_handles_currency_commas_and_decimals():
    from pipeline_grants.normalize import _to_int
    assert _to_int("600000") == 600000
    assert _to_int("$50,000.00") == 50000
    assert _to_int("$2,600,000") == 2600000
    assert _to_int("") is None
    assert _to_int(None) is None
    assert _to_int("TBD") is None


def _detail(*, agency=None, sub=None, top=None):
    """Minimal grants.gov detail_resp for sponsor-selection tests (#294)."""
    d = {"data": {"id": "1", "opportunityTitle": "X",
                  "synopsis": {"agencyName": agency}}}
    if sub is not None:
        d["data"]["agencyDetails"] = {"agencyName": sub}
    if top is not None:
        d["data"]["topAgencyDetails"] = {"agencyName": top}
    return d


def test_sponsor_rejects_contact_blob_and_falls_through():
    # synopsis.agencyName is a "<Person>\n<Role>" contact block -> use agencyDetails.
    opp = normalize_grantsgov(_detail(
        agency="Linton C Browning\nGrants Management Specialist",
        sub="National Institute of Allergy and Infectious Diseases",
        top="Department of Health and Human Services"))
    assert opp.sponsor == "National Institute of Allergy and Infectious Diseases"


def test_sponsor_falls_to_top_agency_when_sub_also_dirty():
    opp = normalize_grantsgov(_detail(
        agency="Jane Doe\nGrantor", sub="Adam Hoque\nGrantor",
        top="Department of Defense"))
    assert opp.sponsor == "Department of Defense"


def test_sponsor_clean_synopsis_name_is_kept():
    opp = normalize_grantsgov(_detail(
        agency="National Science Foundation", sub="Directorate for Biological Sciences"))
    assert opp.sponsor == "National Science Foundation"


def test_sponsor_single_line_role_title_rejected():
    opp = normalize_grantsgov(_detail(
        agency="Sarah McGarvey, Grants Manager", sub="Health Resources and Services Administration"))
    assert opp.sponsor == "Health Resources and Services Administration"


def test_sponsor_blank_when_nothing_clean():
    opp = normalize_grantsgov(_detail(agency="John Smith\nGrantor"))
    assert opp.sponsor == ""


def test_canonical_sponsor_collapses_known_variants():
    # &/and, acronym + Inc. suffix, and outright typos all fold to one facet label.
    assert canonical_sponsor("Leukemia & Lymphoma Society") == "Leukemia and Lymphoma Society"
    assert canonical_sponsor("American Cancer Society (ACS)") == "American Cancer Society"
    assert canonical_sponsor("American Cancer Society, Inc.") == "American Cancer Society"
    assert canonical_sponsor("AmerisourceBergen Foundtion") == "AmerisourceBergen Foundation"
    assert (canonical_sponsor("NewYork Presbyterian Hospital William Rodes Center for Glioblastoma")
            == "The NewYork-Presbyterian Hospital William Rhodes Center for Glioblastoma")


def test_canonical_sponsor_passes_through_unknown_and_blank():
    assert canonical_sponsor("National Institutes of Health") == "National Institutes of Health"
    assert canonical_sponsor("  American Cancer Society, Inc.  ") == "American Cancer Society"  # trims
    assert canonical_sponsor("") == ""
    assert canonical_sponsor(None) == ""


# A forecasted NOFO carries no `synopsis` block at all — its body lives under `forecast`,
# with `forecastDesc` / `estApplicationResponseDate` in place of `synopsisDesc` /
# `responseDate`. Reading only `synopsis` produced an empty body, which failed screening
# 100% of the time, so no forecast ever entered the corpus (#269). These assert the fallback.
FORECAST_DETAIL = {
    "data": {
        "id": "999001",
        "opportunityTitle": "Autism Centers of Excellence (ACE) (R01 Clinical Trial Optional)",
        "opportunityNumber": "RFA-HD-27-001",
        "docType": "forecast",
        "synopsis": None,
        "forecast": {
            "forecastDesc": "<p>This NOFO invites applications for Autism Centers of Excellence.</p>",
            "postingDate": "May 21, 2026 12:00:00 AM EDT",
            "estApplicationResponseDate": "Oct 19, 2026 12:00:00 AM EDT",
            "awardCeiling": "600000",
            "awardFloor": "50000",
            "estimatedFunding": "3000000",
            "numberOfAwards": "3",
            "applicantEligibilityDesc": "Higher education institutions may apply.",
            "applicantTypes": [{"description": "Public and State controlled institutions of higher education"}],
            "agencyName": "National Institutes of Health",
        },
        "cfdas": [{"cfdaNumber": "93.865"}],
    }
}


def test_normalize_reads_forecast_block_when_synopsis_absent():
    opp = normalize_grantsgov(FORECAST_DETAIL)
    # The regression: without the `data.forecast` fallback this is "" and the item is unscoreable.
    assert "Autism Centers of Excellence" in opp.synopsis
    assert "<p>" not in opp.synopsis
    assert opp.status == "forecasted"


def test_normalize_maps_forecast_renamed_date_key():
    # `estApplicationResponseDate`, not `responseDate`. An empty due_date also left
    # denoise's expired-deadline gate dead for every forecast.
    opp = normalize_grantsgov(FORECAST_DETAIL)
    assert opp.due_date == "2026-10-19"
    assert opp.open_date == "2026-05-21"


def test_normalize_forecast_shares_key_names_for_the_rest():
    # Everything except the two renamed keys is identical, so the rest of the normalizer
    # starts working unchanged the moment `syn` points at the forecast block.
    opp = normalize_grantsgov(FORECAST_DETAIL)
    assert opp.award_ceiling == 600000
    assert opp.number_of_awards == 3
    assert opp.sponsor == "National Institutes of Health"
    assert "Higher education" in opp.eligibility_raw


def test_normalize_still_prefers_synopsis_when_both_present():
    # `synopsis` wins; the fallback must not hijack a posted NOFO that also carries a
    # forecast block (status is derived from `forecast` being present, so both coexist).
    detail = _load()
    detail["data"]["forecast"] = {"forecastDesc": "SHOULD NOT BE USED"}
    opp = normalize_grantsgov(detail)
    assert "SHOULD NOT BE USED" not in opp.synopsis
    assert "NOFO" in opp.synopsis
