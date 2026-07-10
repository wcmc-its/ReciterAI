"""Prestige producer: tiering, size scaling, renormalization, honorific flag."""
from pipeline_grants.models import Opportunity
from pipeline_grants.normalize import _activity_code
from pipeline_grants import prestige


def _opp(title="X", mechanism="", award_ceiling=None, estimated_funding=None,
         sponsor="NIH", program_type=""):
    return Opportunity(
        opportunity_id="t:1", source="grants_gov", source_id="1", source_url="",
        sponsor=sponsor, title=title, synopsis="s", program_type=program_type,
        mechanism=mechanism, award_ceiling=award_ceiling, estimated_funding=estimated_funding)


def test_mechanism_tier():
    t = prestige.mechanism_tier
    assert t("R01") == 0.85 and t("R35") == 0.85
    assert t("K23") == 0.7
    assert t("DP1") == 1.0 and t("DP2") == 1.0   # 2-letter flagship prefix
    assert t("U01") == 1.0 and t("P30") == 1.0
    assert t("R21") == 0.4 and t("R03") == 0.4   # pilot/exploratory
    assert t("F31") == 0.5 and t("T32") == 0.5
    assert t("") is None                         # no code -> abstain (non-NIH funders)
    assert t("Z99") == 0.3                        # present-but-untiered NIH code -> neutral-low


def test_size_bucket_fixed_anchor_and_no_signal():
    assert prestige.size_bucket(_opp(award_ceiling=10_000)) == 0.0   # LO anchor
    assert prestige.size_bucket(_opp(award_ceiling=10_000_000)) == 1.0  # HI anchor
    mid = prestige.size_bucket(_opp(award_ceiling=500_000))
    assert 0.0 < mid < 1.0
    assert prestige.size_bucket(_opp()) is None                      # unknown -> no-signal, NOT 0
    # curated rows carry estimated_funding, not ceiling
    assert prestige.size_bucket(_opp(estimated_funding=100_000)) is not None


def test_compute_prestige_renormalizes_and_orders():
    big = prestige.compute_prestige(_opp(mechanism="R01", award_ceiling=500_000))
    pilot = prestige.compute_prestige(_opp(mechanism="R03", award_ceiling=50_000))
    assert big["score"] > pilot["score"]                # R01 outranks pilot R03 (§3.6)
    # a mid-ceiling ($500k -> size 0.57) blends R01's 0.85 down to ~0.76 (Major); pilot -> Standard
    assert big["label"] == "Major" and pilot["label"] == "Standard"
    # Flagship needs BOTH a top mechanism AND a large ceiling
    flagship = prestige.compute_prestige(_opp(mechanism="P30", award_ceiling=5_000_000))
    assert flagship["label"] == "Flagship"
    # missing size -> score is exactly mechanism_tier (renormalized over the one present signal)
    no_size = prestige.compute_prestige(_opp(mechanism="K23"))
    assert no_size["score"] == 0.7 and no_size["label"] == "Major"
    # deferred inputs are honest-null, not fabricated
    assert no_size["sponsor_tier"] is None and no_size["selectivity"] is None
    assert no_size["size_bucket"] is None


def test_is_honorific():
    h = prestige.is_honorific
    assert h(_opp(title="The Wolf Prize"))                      # prize
    assert h(_opp(title="NAS Public Welfare Medal"))           # medal
    assert h(_opp(title="AACR-Women in Cancer Research Lectureship"))
    assert h(_opp(title="Vannevar Bush Award"))                # recognition award, no mechanism
    # applyable NIH awards carry an activity code -> NOT honorific
    assert not h(_opp(title="Outstanding Investigator Award", mechanism="R35"))
    assert not h(_opp(title="NIH Director New Innovator Award", mechanism="DP2"))
    assert not h(_opp(title="Cancer Research Project Grant", mechanism="R01"))
    # applyable funding types spared even with no mechanism
    assert not h(_opp(title="ASCI PSSF Fellowship"))
    assert not h(_opp(title="JEM Early Career Travel Award"))


def test_is_honorific_sponsor_veto():
    """#314 — a bare '…Award' from an open-competition grantmaker is applyable, not an
    honor. These trip tier-2 (no prize wording, no mechanism, no applyable *type* keyword)
    and are rescued only by the sponsor. Regression anchors: a change that re-flags any of
    these as honorific must fail CI."""
    h = prestige.is_honorific
    for title, sponsor in [
        ("Department of Defense - Investigator-Initiated Research Award", "Department of Defense"),
        ("Department of Defense - Clinical Trial Award", "Department of Defense"),
        ("Department of Defense - Technology/Therapeutic Development Award", "Department of Defense"),
        ("Hartwell Foundation Individual Biomedical Research Award", "The Hartwell Foundation"),
        ("Damon Runyon Clinical Investigator Award", "Damon Runyon Cancer Research Foundation"),
        ("Physician-Scientist Award", "Research to Prevent Blindness"),
        ("Investigator Award", "Rheumatology Research Foundation"),
        ("Clinical Research Award", "Cystic Fibrosis Foundation"),
    ]:
        assert not h(_opp(title=title, sponsor=sponsor)), f"should be applyable: {title}"

    # The veto is narrow: professional societies / academies confer HONORS titled 'Award'
    # and must stay flagged. Same bare-'Award' titles, honor-conferring sponsor -> honorific.
    for title, sponsor in [
        ("AACR Team Science Award", "American Association for Cancer Research"),
        ("NAS Award in Molecular Biology", "National Academy of Sciences"),
        ("AAI Meritorious Career Award", "The American Association of Immunologists"),
        ("Avanti Award in Lipids", "American Society for Biochemistry and Molecular Biology"),
    ]:
        assert h(_opp(title=title, sponsor=sponsor)), f"should stay honorific: {title}"

    # Explicit prize wording is honorific regardless of sponsor (tier-1, sponsor never consulted).
    assert h(_opp(title="Some Prize", sponsor="Department of Defense"))


def test_sponsor_tier_matches_curated_funders():
    s = prestige.sponsor_tier
    assert s(_opp(sponsor="U.S. National Science Foundation")) == 0.7
    assert s(_opp(sponsor="American Association for Cancer Research")) == 0.8
    assert s(_opp(sponsor="National Academies of Sciences, Engineering, and Medicine")) == 0.8
    assert s(_opp(sponsor="National Institutes of Health")) is None   # NIH abstains; mechanism covers it
    assert s(_opp(sponsor="Jane Q Grantor")) is None                  # person-name feed noise -> abstain


def test_non_nih_scores_on_sponsor_and_size_not_the_old_floor():
    # NSF $500k: pre-fix the 0.3 mechanism constant compressed this to ~0.5; now sponsor+size drive it
    nsf = prestige.compute_prestige(_opp(sponsor="National Science Foundation", award_ceiling=500_000))
    assert nsf["mechanism_tier"] is None and nsf["sponsor_tier"] == 0.7
    assert nsf["score"] > 0.6 and nsf["label"] in ("Major", "Flagship")
    # untiered funder rests on size alone -> real spread, ranked below the NSF grant
    small = prestige.compute_prestige(_opp(sponsor="Bureau of Land Management", award_ceiling=30_000))
    assert small["sponsor_tier"] is None and small["score"] < nsf["score"]


def test_no_signal_falls_to_floor_not_crash():
    # untiered funder, no code, no amount -> floor, and no ZeroDivision on empty blend
    bare = prestige.compute_prestige(_opp(title="Some Prize", sponsor="Tiny Local Foundation"))
    assert bare["score"] == 0.3 and bare["label"] == "Standard"


def test_prestige_item_attrs_ddb_shape():
    attrs = prestige.prestige_item_attrs(_opp(title="The Wolf Prize", estimated_funding=100_000))
    assert attrs["is_honorific"] == {"BOOL": True}
    m = attrs["prestige"]["M"]
    assert "N" in m["score"]
    assert m["mechanism_tier"] == {"NULL": True}        # no activity code -> abstains
    assert m["sponsor_tier"] == {"NULL": True} and m["selectivity"] == {"NULL": True}
    assert "N" in m["size_bucket"]                       # present here (estimated_funding given)
    # an NIH code present -> mechanism_tier is a number; unknown size -> NULL, not fabricated
    nih = prestige.prestige_item_attrs(_opp(title="X", mechanism="R01"))["prestige"]["M"]
    assert "N" in nih["mechanism_tier"] and nih["size_bucket"] == {"NULL": True}


def test_activity_code_parses_two_letter_prefix():
    assert _activity_code("RFA-CA-24-001") == ""        # no embedded activity code
    assert _activity_code("PAR DP2 program") == "DP2"   # 2-letter prefix now captured
    assert _activity_code("see R01 here") == "R01"
