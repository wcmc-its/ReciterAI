"""Cross-source GRANT# dedup: key normalization, source priority, blank-sponsor
grouping, the drop-one batch pass (dry-run default) and the ingest-time
CorpusKeyIndex guard. Faked DDB client throughout — no AWS."""
import pipeline_grants.dedupe as dd


def _rec(oid, source, title, sponsor="", ingested_at="2026-07-01T00:00:00Z"):
    return {"opportunity_id": oid, "source": source, "title": title,
            "sponsor": sponsor, "ingested_at": ingested_at}


# --- key normalization ---------------------------------------------------------
def test_norm_tokens_strips_case_punctuation_and_stopwords():
    # The runbook's measured near-dup: punctuation + articles + funding-generic words.
    a = dd.norm_tokens("Hartwell Foundation - Individual Biomedical Research Award")
    b = dd.norm_tokens("The Hartwell Foundation Individual Biomedical Research Award")
    assert a == b == frozenset({"hartwell", "individual", "biomedical"})
    assert dd.norm_tokens("") == frozenset()


def test_source_rank_order_and_unknown_lowest():
    ranks = [dd.source_rank(s) for s in ("grants_gov", "spin", "wcm_curated", "manual_url")]
    assert ranks == sorted(ranks, reverse=True)
    assert dd.source_rank("future_source") < dd.source_rank("manual_url")


# --- clustering + winner selection ----------------------------------------------
def test_find_duplicates_prefers_higher_priority_source():
    recs = [
        _rec("wcm_curated:hartwell-x", "wcm_curated",
             "Hartwell Foundation - Individual Biomedical Research Award", "The Hartwell Foundation"),
        _rec("grants_gov:123", "grants_gov",
             "Hartwell Foundation Individual Biomedical Research Award", "Hartwell Foundation"),
        _rec("spin:77", "spin",
             "Hartwell Foundation Individual Biomedical Research Award", "Hartwell Foundation"),
    ]
    groups = dd.find_duplicates(recs)
    assert len(groups) == 1
    assert groups[0]["winner"]["opportunity_id"] == "grants_gov:123"
    assert [l["opportunity_id"] for l in groups[0]["losers"]] == ["spin:77", "wcm_curated:hartwell-x"]


def test_manual_url_loses_to_curated():
    recs = [
        _rec("manual_url:keck-abc", "manual_url",
             "W. M. Keck Foundation Research Program", "W. M. Keck Foundation"),
        _rec("wcm_curated:keck-def", "wcm_curated",
             "W.M. Keck Foundation Research Program", "W.M. Keck Foundation"),
    ]
    groups = dd.find_duplicates(recs)
    assert len(groups) == 1
    assert groups[0]["winner"]["opportunity_id"] == "wcm_curated:keck-def"


def test_tie_keeps_most_recently_ingested():
    older = _rec("wcm_curated:a", "wcm_curated", "WorldQuant Initiative Prize",
                 "WorldQuant", "2026-06-01T00:00:00Z")
    newer = _rec("wcm_curated:b", "wcm_curated", "WorldQuant Initiative Prize",
                 "WorldQuant", "2026-06-28T00:00:00Z")
    groups = dd.find_duplicates([older, newer])
    assert groups[0]["winner"] is newer
    assert groups[0]["losers"] == [older]


def test_blank_sponsor_groups_by_title_alone():
    recs = [
        _rec("wcm_curated:wq", "wcm_curated", "WorldQuant Initiative Research Prize", ""),
        _rec("grants_gov:9", "grants_gov", "WorldQuant Initiative Research Prize",
             "WorldQuant Foundation"),
    ]
    groups = dd.find_duplicates(recs)
    assert len(groups) == 1
    assert groups[0]["winner"]["opportunity_id"] == "grants_gov:9"
    assert groups[0]["losers"][0]["opportunity_id"] == "wcm_curated:wq"


def test_distinct_sponsors_never_grouped():
    recs = [
        _rec("grants_gov:1", "grants_gov", "Young Investigator Award", "National Cancer Institute"),
        _rec("wcm_curated:2", "wcm_curated", "Young Investigator Award", "Cancer Society"),
    ]
    assert dd.find_duplicates(recs) == []


def test_blank_sponsor_ambiguous_across_two_sponsors_stays_ungrouped():
    # Two DIFFERENT sponsored programs share a title; a single blank-sponsor row
    # cannot be attributed to either -> conservative: nothing is dropped.
    recs = [
        _rec("grants_gov:1", "grants_gov", "Young Investigator Award", "National Cancer Institute"),
        _rec("wcm_curated:2", "wcm_curated", "Young Investigator Award", "Cancer Society"),
        _rec("wcm_curated:3", "wcm_curated", "Young Investigator Award", ""),
    ]
    assert dd.find_duplicates(recs) == []


def test_blank_sponsors_still_dedup_against_each_other_when_ambiguous():
    recs = [
        _rec("grants_gov:1", "grants_gov", "Innovator Prize Neuroscience", "NIH"),
        _rec("spin:2", "spin", "Innovator Prize Neuroscience", "NSF"),
        _rec("wcm_curated:a", "wcm_curated", "Innovator Prize Neuroscience", ""),
        _rec("manual_url:b", "manual_url", "Innovator Prize Neuroscience", ""),
    ]
    groups = dd.find_duplicates(recs)
    assert len(groups) == 1  # only the blank-vs-blank pair; NIH/NSF stay distinct
    assert groups[0]["winner"]["opportunity_id"] == "wcm_curated:a"
    assert [l["opportunity_id"] for l in groups[0]["losers"]] == ["manual_url:b"]


def test_all_stopword_titles_are_unkeyable_and_never_grouped():
    recs = [_rec("grants_gov:1", "grants_gov", "Research Grant Program", "NIH"),
            _rec("spin:2", "spin", "Research Grant Program", "NIH")]
    assert dd.find_duplicates(recs) == []


# --- batch run (dry-run default; --apply deletes) --------------------------------
class _FakeClient:
    def __init__(self, items):
        self._items = items
        self.deleted = []

    def get_paginator(self, _op):
        items = self._items

        class _P:
            def paginate(self, **_kw):
                return [{"Items": items}]

        return _P()

    def delete_item(self, **kw):
        self.deleted.append(kw)


def _ddb_grant(oid, source, title, sponsor="", ingested_at="2026-07-01T00:00:00Z"):
    return {"PK": {"S": f"GRANT#{oid}"}, "SK": {"S": "META"},
            "opportunity_id": {"S": oid}, "source": {"S": source}, "title": {"S": title},
            "sponsor": {"S": sponsor}, "ingested_at": {"S": ingested_at}}


def _fake_corpus():
    return _FakeClient([
        _ddb_grant("grants_gov:1", "grants_gov",
                   "Hartwell Individual Biomedical Research Award", "Hartwell Foundation"),
        _ddb_grant("wcm_curated:h", "wcm_curated",
                   "Hartwell - Individual Biomedical Research Award", "The Hartwell Foundation"),
        _ddb_grant("grants_gov:2", "grants_gov", "Distinct Ocean Science Initiative", "NSF"),
    ])


def test_run_dry_run_is_default_and_deletes_nothing(monkeypatch):
    fake = _fake_corpus()
    monkeypatch.setattr(dd, "get_dynamo_client", lambda: fake)
    summary = dd.run()
    assert summary == {"grants": 3, "duplicate_groups": 1, "losers": 1,
                       "deleted": 0, "dry_run": True}
    assert fake.deleted == []


def test_run_apply_deletes_losers_only(monkeypatch):
    fake = _fake_corpus()
    monkeypatch.setattr(dd, "get_dynamo_client", lambda: fake)
    summary = dd.run(apply=True)
    assert summary["deleted"] == 1 and summary["dry_run"] is False
    assert fake.deleted == [{
        "TableName": dd.TABLE_NAME,
        "Key": {"PK": {"S": "GRANT#wcm_curated:h"}, "SK": {"S": "META"}},
    }]


# --- ingest-time guard ------------------------------------------------------------
def test_corpus_key_index_blocks_equal_or_higher_priority_only():
    idx = dd.CorpusKeyIndex()
    idx.add(opportunity_id="spin:1", source="spin",
            title="Hartwell Individual Biomedical Research Award", sponsor="Hartwell Foundation")
    # lower-priority incoming (wcm_curated) is blocked by spin
    assert idx.blocking_id(
        opportunity_id="wcm_curated:x", source="wcm_curated",
        title="Hartwell - Individual Biomedical Research Award",
        sponsor="The Hartwell Foundation") == "spin:1"
    # equal priority blocks too (a second spin listing under a different id)
    assert idx.blocking_id(
        opportunity_id="spin:2", source="spin",
        title="Hartwell Individual Biomedical Research Award",
        sponsor="Hartwell Foundation") == "spin:1"
    # higher-priority incoming is NOT blocked — the batch pass drops the spin row later
    assert idx.blocking_id(
        opportunity_id="grants_gov:9", source="grants_gov",
        title="Hartwell Individual Biomedical Research Award",
        sponsor="Hartwell Foundation") is None


def test_corpus_key_index_self_refresh_never_blocks():
    idx = dd.CorpusKeyIndex()
    idx.add(opportunity_id="grants_gov:1", source="grants_gov",
            title="Cancer Moonshot Initiative", sponsor="NIH")
    assert idx.blocking_id(opportunity_id="grants_gov:1", source="grants_gov",
                           title="Cancer Moonshot Initiative", sponsor="NIH") is None


def test_corpus_key_index_blank_sponsor_matches_any_sponsor():
    idx = dd.CorpusKeyIndex()
    idx.add(opportunity_id="grants_gov:1", source="grants_gov",
            title="Translational Prize Neuroscience", sponsor="NIH")
    # blank-sponsor incoming matches the sponsored corpus row
    assert idx.blocking_id(opportunity_id="wcm_curated:x", source="wcm_curated",
                           title="Translational Prize Neuroscience", sponsor="") == "grants_gov:1"
    # a DIFFERENT sponsor does not
    assert idx.blocking_id(opportunity_id="wcm_curated:y", source="wcm_curated",
                           title="Translational Prize Neuroscience", sponsor="NSF") is None


def test_load_corpus_key_index_scans_grant_meta():
    idx = dd.load_corpus_key_index(_fake_corpus())
    assert idx.blocking_id(opportunity_id="manual_url:new", source="manual_url",
                           title="The Hartwell Individual Biomedical Research Award",
                           sponsor="") == "grants_gov:1"
