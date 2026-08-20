"""Orchestrator smoke tests for pipeline_grants.ingest_spin (no network, no Bedrock)."""
from unittest.mock import MagicMock

import pipeline_grants.ingest_spin as ingest_spin


class _FakeClient:
    """Stands in for SpinClient: one canned row list per search() call."""

    def __init__(self, rows_by_query):
        self.rows_by_query = rows_by_query
        self.queries = []

    def search(self, keywords):
        self.queries.append(keywords)
        yield from self.rows_by_query.get(keywords, [])


def _row(pid, title, **overrides):
    row = {"id": pid, "prog_title": title, "spon_name": "Helmsley Charitable Trust",
           "synopsis": f"synopsis for {title}", "project_type": ["Research Grant"],
           "geographic": ["United States"], "deadline_date": ["2099-12-31"],
           "programurl": "https://example.org/prog"}
    row.update(overrides)
    return row


def _target(spin_name="Helmsley Charitable Trust"):
    return {"funder": "Helmsley", "spin_name": spin_name, "spon_code": "05382",
            "relevance": 10, "type": "Foundation"}


def _patch_pipeline(monkeypatch, *, client, judge=None):
    """Wire the standard mocks; returns (scored_ids, persisted, published)."""
    scored, persisted, published = [], [], []
    monkeypatch.setattr(ingest_spin.spin, "SpinClient", lambda *a, **k: client)
    monkeypatch.setattr(ingest_spin.scoring, "load_taxonomy",
                        lambda: {"taxonomy_version": "taxonomy_v2", "topics": []})
    monkeypatch.setattr(ingest_spin.scoring, "build_index", lambda tax: ({}, {}))

    def _score(**kw):
        scored.append(kw["opportunity_id"])
        return {"neurodegeneration": {"score": 0.9, "rationale": "r"}}
    monkeypatch.setattr(ingest_spin.scoring, "score_grant_text", _score)
    monkeypatch.setattr(
        ingest_spin, "judge_opportunity",
        judge or (lambda opp, bedrock: {"is_research": True, "is_biomedical_relevant": True,
                                        "reason": "", "appeal_by_stage": {}}))
    monkeypatch.setattr(ingest_spin, "BedrockClient", lambda *a, **k: object())
    monkeypatch.setattr(ingest_spin, "get_dynamo_client", lambda region=None: MagicMock())
    monkeypatch.setattr(ingest_spin, "load_corpus_key_index", lambda client: None)
    monkeypatch.setattr(ingest_spin, "put_grants",
                        lambda client, items, **kw: (persisted.extend(items), len(items))[1])
    monkeypatch.setattr(ingest_spin, "publish_opportunities_artifact",
                        lambda arts, **kw: (published.extend(arts), {"count": len(arts), "version": "vtest"})[1])
    return scored, persisted, published


def test_run_gates_before_scoring_and_returns_drop_counts(monkeypatch):
    # Four rows: one clean keep, one junk project_type, one exclusively-non-US,
    # one suspended. The gate-dropped rows must NEVER reach the scorer (no
    # Bedrock spend on junk) and every drop must be counted by reason.
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [
        _row("1", "T1D Research Program"),
        _row("2", "Best Lecture Prize", project_type=["Prize or Award"]),
        _row("3", "Outback Dementia Grant", geographic=["Australia"]),
        _row("4", "Frozen Fund (Temporarily Suspended)"),
    ]})
    scored, persisted, published = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    summary = ingest_spin.run()
    assert client.queries == [query]                      # exact quoted canonical name
    assert scored == ["spin:1"]                           # gates ran BEFORE scoring
    assert summary["fetched"] == 4
    assert summary["gate_drops"] == {"project_type": 1, "geo_non_us": 1, "suspended": 1}
    assert summary["built"] == 1 and summary["persisted"] == 1
    assert persisted[0]["PK"]["S"] == "GRANT#spin:1"
    assert persisted[0]["source"]["S"] == "spin"
    assert published[0]["opportunity_id"] == "spin:1"


def test_run_keeps_and_counts_unknown_project_type(monkeypatch):
    # Owner decision #1: an unseen project_type is kept (and judged), not dropped.
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [
        _row("1", "Novel Mechanism Program", project_type=["New or Existing Project"]),
    ]})
    scored, persisted, _ = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    summary = ingest_spin.run()
    assert summary["unknown_type"] == 1
    assert summary["gate_drops"] == {}
    assert scored == ["spin:1"] and len(persisted) == 1


def test_run_llm_judge_verdict_is_not_synthesized(monkeypatch):
    # Owner decision #2: the grants_gov judge decides is_research; an explicit
    # False drops the item after scoring, counted under llm_drops.
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [_row("1", "Career Prize Disguised As Grant")]})
    judge = lambda opp, bedrock: {"is_research": False, "is_biomedical_relevant": True,
                                  "reason": "honorific", "appeal_by_stage": {}}
    scored, persisted, _ = _patch_pipeline(monkeypatch, client=client, judge=judge)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    summary = ingest_spin.run()
    assert scored == ["spin:1"]
    assert summary["llm_drops"] == {"not_research": 1}
    assert summary["built"] == 0 and persisted == []


def test_run_dedups_and_skips_excluded_before_scoring(monkeypatch):
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [
        _row("1", "T1D Research Program"),
        _row("1", "T1D Research Program"),          # duplicate id
        _row("2", "Held-Out Program"),              # on the exclusion list
    ]})
    scored, _, _ = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])
    monkeypatch.setattr(ingest_spin, "load_excluded_ids", lambda: {"spin:2"})

    summary = ingest_spin.run(dry_run=True)
    assert scored == ["spin:1"]
    assert summary["dup"] == 1 and summary["excluded"] == 1
    assert summary["dry_run"] is True and summary["persisted"] == 0


def test_run_dry_run_writes_nothing(monkeypatch):
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [_row("1", "T1D Research Program")]})
    scored, persisted, published = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    summary = ingest_spin.run(dry_run=True)
    assert summary["built"] == 1 and summary["persisted"] == 0
    assert summary["sample_ids"] == ["spin:1"]
    assert persisted == [] and published == []


def test_run_limit_caps_total_programs(monkeypatch):
    q1 = '[SOLR]spon_name:"Funder One"'
    q2 = '[SOLR]spon_name:"Funder Two"'
    client = _FakeClient({
        q1: [_row("1", "Program A"), _row("2", "Program B")],
        q2: [_row("3", "Program C")],
    })
    scored, _, _ = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [
        _target("Funder One"), _target("Funder Two")])

    summary = ingest_spin.run(dry_run=True, limit=1)
    assert summary["fetched"] == 1
    assert scored == ["spin:1"]
    assert client.queries == [q1]                        # second funder never pulled


def test_run_skips_key_already_held_by_corpus(monkeypatch):
    # The persisted corpus already holds this normalized key under a
    # higher-priority source -> skipped before any Bedrock spend.
    from pipeline_grants.dedupe import CorpusKeyIndex

    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [_row("1", "T1D Research Program")]})
    scored, persisted, _ = _patch_pipeline(monkeypatch, client=client)
    index = CorpusKeyIndex()
    index.add(opportunity_id="grants_gov:99", source="grants_gov",
              title="The T1D Research Program", sponsor="Helmsley Charitable Trust")
    monkeypatch.setattr(ingest_spin, "load_corpus_key_index", lambda client: index)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    summary = ingest_spin.run(dry_run=True)
    assert summary["crossdup"] == 1
    assert scored == [] and persisted == []


def test_run_isolates_a_failed_item(monkeypatch):
    query = '[SOLR]spon_name:"Helmsley Charitable Trust"'
    client = _FakeClient({query: [_row("1", "Program A"), _row("2", "Program B")]})
    scored, persisted, _ = _patch_pipeline(monkeypatch, client=client)
    monkeypatch.setattr(ingest_spin, "load_targets", lambda *a, **k: [_target()])

    def _score(**kw):
        if kw["opportunity_id"] == "spin:1":
            raise RuntimeError("grant scoring failed for spin:1: Expecting value")
        scored.append(kw["opportunity_id"])
        return {"neurodegeneration": {"score": 0.9, "rationale": "r"}}
    monkeypatch.setattr(ingest_spin.scoring, "score_grant_text", _score)

    summary = ingest_spin.run()
    assert summary["failed"] == 1
    assert summary["built"] == 1 and summary["persisted"] == 1
    assert persisted[0]["PK"]["S"] == "GRANT#spin:2"


def test_load_targets_reads_resolved_funders_only():
    # The real committed config: exactly the 113 resolved funders; the
    # _residual_for_human_pass block is deliberately not pulled [decision #4].
    targets = ingest_spin.load_targets()
    assert len(targets) == 113
    assert all(t.get("spin_name") for t in targets)


def test_load_targets_funder_filter_matches_either_name():
    assert all("helmsley" in (t["funder"] + t["spin_name"]).lower()
               for t in ingest_spin.load_targets(funder="helmsley"))
    assert len(ingest_spin.load_targets(funder="helmsley")) == 1
    assert ingest_spin.load_targets(funder="no-such-funder-xyz") == []


def test_run_with_no_matching_targets_is_a_noop(monkeypatch):
    called = []
    monkeypatch.setattr(ingest_spin.spin, "SpinClient",
                        lambda *a, **k: called.append("client"))
    summary = ingest_spin.run(funder="no-such-funder-xyz")
    assert summary == {"targets": 0, "built": 0, "persisted": 0}
    assert called == []                                   # no client, no AWS touched
