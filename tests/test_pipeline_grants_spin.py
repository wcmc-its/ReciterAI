"""SPIN source: field mapping, precision gates, and the ingest precision flow."""
import json
from pathlib import Path
from unittest.mock import MagicMock

from pipeline_grants import spin
from pipeline_grants import ingest_spin as isp

FIXTURE = Path(__file__).parent / "fixtures" / "spin_programs.json"


def _rows():
    return json.loads(FIXTURE.read_text())


# --- normalize ---------------------------------------------------------------
def test_normalize_spin_maps_fields():
    row = {"id": "9981", "prog_title": "  Discovery Grant  ", "spon_name": "Acme Foundation",
           "synopsis": "Funds discovery.", "project_type": ["Research Grant"],
           "geographic": ["United States"], "applicant_type": ["Nonprofit"],
           "programurl": "https://acme.org/g", "deadline_date": "12/31/2026", "cfda": None}
    opp = spin.normalize_spin(row, ingested_at="2026-06-26T00:00:00Z")
    assert opp.opportunity_id == "spin:9981" and opp.source == "spin" and opp.source_id == "9981"
    assert opp.sponsor == "Acme Foundation" and opp.title == "Discovery Grant"
    assert opp.program_type == "Research Grant"
    assert opp.award_ceiling is None and opp.estimated_funding is None   # SPIN has no amount
    assert opp.due_date == "2026-12-31" and opp.status == "posted"
    assert "United States" in opp.eligibility_raw


def test_normalize_no_deadline_is_rolling():
    opp = spin.normalize_spin({"id": "1", "prog_title": "X", "project_type": ["Research Grant"]})
    assert opp.due_date == "" and opp.status == "continuous"   # rolling, not "expired"


# --- precision gate ----------------------------------------------------------
def test_keep_opportunity_precision():
    base = {"id": "1", "prog_title": "Grant", "project_type": ["Research Grant"],
            "geographic": ["United States"]}
    assert spin.keep_opportunity(base)[0] is True
    # any non-research type in a multi-typed row -> drop (precision over recall)
    assert spin.keep_opportunity({**base, "project_type": ["Research Grant", "Prize or Award"]})[0] is False
    # ambiguous type that isn't on the allow-list -> drop
    assert spin.keep_opportunity({**base, "project_type": ["Training and Professional Development"]})[0] is False
    # non-US geography -> drop; "No Restrictions" -> keep
    assert spin.keep_opportunity({**base, "geographic": ["Australia"]})[0] is False
    assert spin.keep_opportunity({**base, "geographic": ["No Restrictions"]})[0] is True
    # suspended in title -> drop
    assert spin.keep_opportunity({**base, "prog_title": "Grant (Temporarily Suspended)"})[0] is False


def test_keep_opportunity_drops_majority_of_raw_feed():
    rows = _rows()
    kept = [r for r in rows if spin.keep_opportunity(r)[0]]
    # the fixture is deliberately noisy (prizes/conferences/training); gate keeps a minority
    assert 0 < len(kept) < len(rows)
    for r in kept:
        assert not (set(r.get("project_type") or []) & spin._HARD_DROP_TYPES)


# --- ingest precision flow ---------------------------------------------------
def _patch(monkeypatch, *, is_research=True):
    monkeypatch.setattr(isp.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2"})
    monkeypatch.setattr(isp.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(isp.scoring, "score_grant_text",
                        lambda **kw: {"cancer_research": {"score": 0.9, "rationale": "r"}})
    monkeypatch.setattr(isp, "judge_opportunity",
                        lambda opp, bedrock: {"is_research": is_research, "reason": "",
                                              "appeal_by_stage": {s: 0.5 for s in
                                                                  ("grad", "postdoc", "early", "mid", "senior")}})
    monkeypatch.setattr(isp.spin, "search_sponsor", lambda name, **kw: _rows())


def test_build_items_precision_flow(monkeypatch):
    _patch(monkeypatch)
    items, artifact, drops = isp.build_items([{"spin_name": "Acme"}], bedrock=object())
    assert items, "expected some items to survive the gates"
    for it in items:
        assert it["PK"]["S"].startswith("GRANT#spin:")   # provenance prefix
    assert sum(drops.values()) > 0                       # noisy rows were dropped + counted
    # every built item came from an allowed project_type
    assert len(items) == len(artifact)


def test_build_items_judge_drops_non_research(monkeypatch):
    _patch(monkeypatch, is_research=False)
    items, _artifact, drops = isp.build_items([{"spin_name": "Acme"}], bedrock=object())
    assert items == []
    assert drops["llm:not-research"] > 0


def test_load_target_funders_config_present():
    funders = isp.load_target_funders()
    assert len(funders) >= 50
    assert all(f.get("spin_name") for f in funders)
