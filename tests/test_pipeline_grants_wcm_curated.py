from unittest.mock import MagicMock

import pipeline_grants.ingest_curated as ic
import pipeline_grants.wcm_curated as wc
from pipeline_grants import enrich_wcm_curated as enrich

# --- a tiny curated CSV: banner row, real header, two awards ------------------
_CSV = (
    "Document Last Updated on 04-22-2026,,,,,,,,\n"
    "Award number,Award Name,Sponsoring Organization,Award Field,Career Stage,"
    "Award Amount,awards with nomination deadlines during,Nomination Deadline,Website\n"
    '1,The Wolf Prize,The Wolf Foundation,Multiple disciplines,All stages,"100,000",'
    "September-December awards,\"February 16, 2026\",https://example.org/wolf\n"
    "2,Young Investigator Award,Cancer Society,Cancer Research,Early-career,50000,"
    "September-December awards,Rolling,https://example.org/yia\n"
)


def _write(tmp_path, text=_CSV):
    p = tmp_path / "curated.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- parsing ------------------------------------------------------------------
def test_read_curated_csv_skips_banner_and_finds_header(tmp_path):
    rows = wc.read_curated_csv(_write(tmp_path))
    assert len(rows) == 2
    assert rows[0][wc.H_NAME] == "The Wolf Prize"
    assert rows[1][wc.H_FIELD] == "Cancer Research"


def test_read_curated_csv_handles_clean_header(tmp_path):
    # The enriched file we write has no banner row.
    clean = (
        "Award Name,Sponsoring Organization,Award Field,Career Stage,Website,synopsis\n"
        "X Prize,Org,Neuroscience,Senior-level,https://e.org,A synopsis.\n"
    )
    rows = wc.read_curated_csv(_write(tmp_path, clean))
    assert len(rows) == 1 and rows[0]["synopsis"] == "A synopsis."


def test_parse_amount():
    assert wc.parse_amount("100,000") == 100000
    assert wc.parse_amount("$50000") == 50000
    assert wc.parse_amount("up to $1,000,000 over 3 years") == 1000000
    assert wc.parse_amount("Varies") is None
    assert wc.parse_amount("") is None


def test_parse_deadline():
    assert wc.parse_deadline("February 16, 2026") == "2026-02-16"
    assert wc.parse_deadline("September 15, 2024 (submission deadline)") == "2024-09-15"
    assert wc.parse_deadline("Rolling") == ""
    assert wc.parse_deadline("") == ""


def test_career_stage_to_appeal_buckets():
    allstg = wc.career_stage_to_appeal("All stages")
    assert set(allstg) == {"grad", "postdoc", "early", "mid", "senior"}
    assert all(v >= 0.6 for v in allstg.values())

    senior = wc.career_stage_to_appeal("Senior-level (established investigators)")
    assert senior["senior"] == 0.9 and senior["grad"] == 0.1

    early = wc.career_stage_to_appeal("Early-career (within 10 years of terminal degree)")
    assert early["early"] == 0.9 and early["senior"] == 0.1

    midsen = wc.career_stage_to_appeal("Middle to Senior-level")
    assert midsen["mid"] == 0.9 and midsen["senior"] == 0.9


def test_make_source_id_is_stable_slugged_and_collision_proof():
    a = wc.make_source_id("The Wolf Prize", "The Wolf Foundation")
    b = wc.make_source_id("The Wolf Prize", "The Wolf Foundation")
    assert a == b                                    # stable for identical input
    assert a.startswith("the-wolf-prize-the-wolf-foundation-")
    assert " " not in a and a == a.lower()
    # Distinct awards sharing an 80-char prefix must NOT collide (hash suffix).
    long1 = "A" * 100 + " Distinct One"
    long2 = "A" * 100 + " Distinct Two"
    assert wc.make_source_id(long1, "Org") != wc.make_source_id(long2, "Org")


def test_make_curated_opportunity_maps_fields(tmp_path):
    row = wc.read_curated_csv(_write(tmp_path))[0]
    opp = wc.make_curated_opportunity(row, synopsis="Funds basic science.", ingested_at="2026-06-21T00:00:00Z")
    assert opp.opportunity_id.startswith("wcm_curated:the-wolf-prize-the-wolf-foundation-")
    assert opp.source == "wcm_curated"
    assert opp.title == "The Wolf Prize"
    assert opp.synopsis == "Funds basic science."
    assert opp.source_url == "https://example.org/wolf"
    assert opp.estimated_funding == 100000
    assert opp.due_date == "2026-02-16"
    assert opp.program_type == "award"
    assert opp.eligibility_raw == "All stages"   # curated career stage retained


def test_fallback_synopsis_uses_field_and_sponsor(tmp_path):
    row = wc.read_curated_csv(_write(tmp_path))[1]
    fb = wc.fallback_synopsis(row)
    assert "Young Investigator Award" in fb and "Cancer Research" in fb and "Cancer Society" in fb


# --- enrichment ---------------------------------------------------------------
def test_enrich_rows_page_then_metadata_summary(tmp_path):
    rows = wc.read_curated_csv(_write(tmp_path))
    bedrock = MagicMock()
    bedrock.call_json.return_value = {"synopsis": "Targets oncology research."}

    # Row 0 has a fetchable page; row 1 has no page -> metadata-only summary.
    def fetcher(url):
        return "Lots of page text about cancer." if url.endswith("/wolf") else ""

    out = enrich.enrich_rows(rows, bedrock, fetcher=fetcher, sleep_s=0)
    assert out[0]["enrich_status"] == "summarized"            # from the page
    assert out[1]["enrich_status"] == "summarized_from_name"  # no page -> from name
    assert out[0]["synopsis"] == out[1]["synopsis"] == "Targets oncology research."


def test_enrich_rows_bare_fallback_when_model_fails(tmp_path):
    rows = wc.read_curated_csv(_write(tmp_path))
    bedrock = MagicMock()
    bedrock.call_json.side_effect = RuntimeError("bedrock down")
    out = enrich.enrich_rows(rows, bedrock, fetcher=lambda u: "", sleep_s=0)
    assert out[1]["enrich_status"] == "fallback_no_page"
    assert "Young Investigator Award" in out[1]["synopsis"]   # field-based fallback


def test_enrich_rows_resumes_good_but_reattempts_fallback(tmp_path):
    rows = wc.read_curated_csv(_write(tmp_path))
    sid0 = wc.make_source_id(rows[0][wc.H_NAME], rows[0][wc.H_SPONSOR])
    existing = {sid0: {"synopsis": "cached text", "enrich_status": "summarized"}}
    bedrock = MagicMock()
    bedrock.call_json.return_value = {"synopsis": "fresh from name"}
    out = enrich.enrich_rows(rows, bedrock, fetcher=lambda u: "", existing=existing, sleep_s=0)
    assert out[0]["synopsis"] == "cached text"               # row 0 cached (good), skipped
    assert out[1]["enrich_status"] == "summarized_from_name"  # row 1 re-processed


def test_is_enriched_predicate():
    assert enrich._is_enriched({"enrich_status": "summarized", "synopsis": "x"})
    assert enrich._is_enriched({"enrich_status": "summarized_from_name", "synopsis": "x"})
    assert not enrich._is_enriched({"enrich_status": "fallback_no_page", "synopsis": "x"})
    assert not enrich._is_enriched({"enrich_status": "summarized", "synopsis": ""})


# --- curated ingest (bypasses denoise; synthesizes verdict) -------------------
def _patch_ingest(monkeypatch, captured):
    monkeypatch.setattr(ic.scoring, "load_taxonomy", lambda: {"taxonomy_version": "taxonomy_v2"})
    monkeypatch.setattr(ic.scoring, "build_index", lambda tax: ({}, {}))
    monkeypatch.setattr(ic.scoring, "score_grant_text",
                        lambda **kw: {"cancer_research": {"score": 0.9, "rationale": "r"}})
    monkeypatch.setattr(ic, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(ic, "get_dynamo_client", lambda region=None: MagicMock())

    def _fake_put(client, items, **kw):
        captured["items"] = items
        return len(items)

    monkeypatch.setattr(ic, "put_grants", _fake_put)
    monkeypatch.setattr(ic, "publish_opportunities_artifact", lambda arts, **kw: {"version": "vtest", "count": len(arts)})


def _enriched_csv(tmp_path):
    text = (
        "Award Name,Sponsoring Organization,Award Field,Career Stage,Award Amount,"
        "Nomination Deadline,Website,synopsis\n"
        '"The Wolf Prize",Wolf Foundation,Multiple disciplines,All stages,"100,000",'
        '"February 16, 2026",https://e.org/w,Funds outstanding biomedical research.\n'
        "Young Investigator Award,Cancer Society,Cancer Research,Early-career,50000,"
        "Rolling,https://e.org/y,Supports early-career cancer researchers.\n"
    )
    return _write(tmp_path, text)


def test_curated_ingest_synthesizes_verdict_and_persists(tmp_path, monkeypatch):
    captured = {}
    _patch_ingest(monkeypatch, captured)
    summary = ic.run(_enriched_csv(tmp_path), dry_run=False)

    assert summary["built"] == 2 and summary["persisted"] == 2
    items = captured["items"]
    first = items[0]
    assert first["PK"]["S"].startswith("GRANT#wcm_curated:the-wolf-prize-wolf-foundation-")
    assert first["is_research"]["BOOL"] is True            # curated => always research
    assert first["primary_topic_id"]["S"] == "cancer_research"
    # appeal_by_stage derived from the curated "All stages" column.
    appeal = first["appeal_by_stage"]["M"]
    assert set(appeal) == {"grad", "postdoc", "early", "mid", "senior"}


def test_curated_ingest_dedups_repeat_rows(tmp_path, monkeypatch):
    captured = {}
    _patch_ingest(monkeypatch, captured)
    # Same award (name+sponsor) listed twice -> one item, scored once.
    text = (
        "Award Name,Sponsoring Organization,Award Field,Career Stage,Award Amount,"
        "Nomination Deadline,Website,synopsis\n"
        "The Harvey Prize,Technion,Peace,All stages,100000,Rolling,https://e.org/h,Big science.\n"
        "The Harvey Prize,Technion,Peace and health,All stages (seniors),100000,TBD,https://e.org/h,Big science.\n"
    )
    summary = ic.run(_write(tmp_path, text), dry_run=False)
    assert summary["built"] == 1 and summary["persisted"] == 1
    assert len(captured["items"]) == 1


def test_curated_ingest_dry_run_writes_nothing(tmp_path, monkeypatch):
    captured = {}
    _patch_ingest(monkeypatch, captured)
    summary = ic.run(_enriched_csv(tmp_path), dry_run=True)
    assert summary["dry_run"] is True and summary["persisted"] == 0
    assert "items" not in captured                          # put_grants never called
