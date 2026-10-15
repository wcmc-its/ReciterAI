"""pipeline_area_signals.subareas: subarea assignment for grant and trial texts.

Covers the reusable classifier (the publication request stays byte-identical),
floor and tie-break parity with assign_subtopics, which areas get a call
(assign floor, draft-less topics, the grant-excluded areas), and the drain:
subareas on a fresh score, the assign-only path (backfill, stale draft hash,
subarea prompt bump), its failure accounting, metering and the dry-run
estimate. The Bedrock fake sits at the boto3 `converse` seam under the real
MeteredBedrockClient; drafts are synthetic files under tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import assign_subtopics as ast
import score_publications as sp
from pipeline_area_signals import drain, prompts, run as run_mod, signals, subareas
from prompts.subtopic_assignment import ASSIGNMENT_SYSTEM_PROMPT, BUILD_ASSIGNMENT_USER_MESSAGE
from test_pipeline_area_signals_run import inp, make_table
from utils.bedrock_client import BedrockClient, HAIKU_MODEL
from utils.llm_cost import CostAccumulator
from utils.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
TV = signals.current_taxonomy_version(TAXONOMY)
INT_TO_ID, ID_TO_INT = sp.build_topic_index(TAXONOMY)
# Two areas grants are not excluded from, and their scorer indexes.
T0, T1 = [INT_TO_ID[str(i)] for i in range(len(INT_TO_ID))
          if INT_TO_ID[str(i)] not in subareas.GRANT_EXCLUDED_TOPICS][:2]
I0, I1 = str(ID_TO_INT[T0]), str(ID_TO_INT[T1])
EXCLUDED = "biomedical_informatics"   # one of the six grant-excluded areas
assert EXCLUDED in subareas.GRANT_EXCLUDED_TOPICS
assert {T0, T1}.isdisjoint(subareas.GRANT_EXCLUDED_TOPICS)

# sha256 of ASSIGNMENT_SYSTEM_PROMPT on origin/main before this change.
PUBLICATION_PROMPT_SHA256 = "00551a95e5b289aba2ebeb871725a9d4b4451d2132449b3501192bd54581ab22"


# --- fakes and fixtures --------------------------------------------------------------

def _resp(body, usage=(3000, 80)):
    return {"output": {"message": {"content": [{"text": json.dumps(body)}]}},
            "usage": {"inputTokens": usage[0], "outputTokens": usage[1]},
            "stopReason": "end_turn"}


class FakeConverse:
    """Answers synopsis, screening, dense and subarea calls by prompt shape.

    ``assignments`` maps topic_id -> the classifier's raw assignments list;
    ``fail_topics`` raises a (non-systemic) ValidationException for those topics.
    """

    def __init__(self, assignments=None, fail_topics=(), dense=None):
        self.assignments = assignments or {}
        self.fail_topics = set(fail_topics)
        self.dense = dense or {I0: {"score": 0.82, "rationale": "r"},
                               I1: {"score": 0.55, "rationale": "r"}}
        self.calls = []

    def converse(self, **kw):
        system = " ".join(s.get("text", "") for s in kw.get("system", []))
        user = kw["messages"][0]["content"][0]["text"]
        if "subtopics it belongs to" in system:
            topic = re.search(r"\(id: ([a-z0-9_]+)\)", user)[1]
            self.calls.append(("subarea", topic, system, user, kw))
            if topic in self.fail_topics:
                raise ClientError({"Error": {"Code": "ValidationException"}}, "Converse")
            return _resp({"pmid": "x", "topic_id": topic,
                          "assignments": self.assignments.get(topic, [])})
        if "synopses" in system:
            self.calls.append(("synopsis",))
            return _resp({"synopsis": "Synthetic synopsis"}, (500, 30))
        if system.startswith("Score this publication"):
            self.calls.append(("screening",))
            return _resp({I0: 0.9, I1: 0.8}, (3000, 60))
        self.calls.append(("dense",))
        return _resp(self.dense, (2000, 200))

    def kinds(self):
        return [c[0] for c in self.calls]

    def subarea_topics(self):
        return sorted(c[1] for c in self.calls if c[0] == "subarea")


def metered(fake):
    c = signals.MeteredBedrockClient()
    c._client = fake
    return c


def _draft(topic, subs, status="approved"):
    return {"topic_id": topic, "review_status": status, "generated_at": "2026-01-01",
            "subtopics": [{"id": sid, "label": sid.replace("_", " "), "description": f"About {sid}",
                           "seed_pmids": list(range(seeds))} for sid, seeds in subs]}


DRAFTS = {
    T0: _draft(T0, [("t0_alpha", 3), ("t0_beta", 9), ("t0_gamma", 1)]),
    T1: _draft(T1, [("t1_one", 2), ("t1_two", 2)]),
    EXCLUDED: _draft(EXCLUDED, [("bi_x", 1), ("bi_y", 1)]),
}


def write_drafts(tmp_path, drafts=None, covered=None):
    drafts = DRAFTS if drafts is None else drafts
    d = tmp_path / "drafts"
    d.mkdir(exist_ok=True)
    for topic, body in drafts.items():
        (d / f"hierarchy_draft_{topic}.json").write_text(json.dumps(body))
    cov = tmp_path / "coverage.json"
    cov.write_text(json.dumps({"draft_covered_topics": sorted(covered or drafts)}))
    return d, cov


@pytest.fixture
def ctx(tmp_path):
    d, cov = write_drafts(tmp_path)
    return subareas.load_context(TAXONOMY, draft_dir=d, coverage_path=cov)


def signal_row(kind="grant", key="k1", scores=None, **over):
    row = {"PK": signals.signal_pk(kind, key), "SK": signals.SIGNAL_SK, "kind": kind,
           "text_key": key, "mode": "full", "synopsis": "Stored synopsis",
           "topic_scores": [{"topic_id": t, "score": Decimal(str(s))}
                            for t, s in (scores or [(T0, 0.8), (T1, 0.5)])]}
    row.update(over)
    return row


def scored_input(key="k1", kind="grant", **over):
    """An AREAINPUT item scored under the current taxonomy and prompt, plus its
    current AREASIGNAL row's input hash."""
    item = inp(kind=kind, key=key, status="scored", taxonomy_version=TV,
               prompt_version=prompts.PROMPT_VERSION, **over)
    return item, signals.AreaInput.from_item(item).input_hash()


def do_run(table, fake, ctx, **kw):
    alerts = []
    code, summary = run_mod.run(
        table=table, client_factory=lambda: metered(fake),
        alert=lambda *a, **k: alerts.append(a), taxonomy=TAXONOMY,
        preflight=lambda: (True, "preflight OK"), subarea_ctx=ctx, **kw)
    return code, summary, alerts


# --- the reusable classifier ---------------------------------------------------------

def test_publication_prompt_is_unchanged():
    assert hashlib.sha256(ASSIGNMENT_SYSTEM_PROMPT.encode()).hexdigest() == PUBLICATION_PROMPT_SHA256


def test_publication_classifier_request_is_byte_identical():
    """_classify_activity sends exactly the pre-refactor Converse request."""
    fake = FakeConverse(assignments={T0: [{"subtopic_id": "t0_alpha", "confidence": 0.9}]})
    client = BedrockClient()
    client._client = fake
    activity = {"pmid": 12345, "title": "A title", "synopsis": "A synopsis"}
    topic_meta = {"id": T0, "label": "Label", "description": "Desc"}
    defs = [{"id": "t0_alpha", "label": "Alpha", "description": "About alpha"}]

    raw, usage = ast._classify_activity(client=client, activity=activity,
                                        topic_meta=topic_meta, subtopic_defs=defs)

    assert raw == [{"subtopic_id": "t0_alpha", "confidence": 0.9}]
    assert usage == {"inputTokens": 3000, "outputTokens": 80}
    sent = fake.calls[0][4]
    assert sent == {
        "modelId": HAIKU_MODEL,
        "messages": [{"role": "user", "content": [{"text": BUILD_ASSIGNMENT_USER_MESSAGE(
            activity=activity, topic_meta=topic_meta, subtopic_defs=defs)}]}],
        "inferenceConfig": {"maxTokens": 400, "temperature": 0.0},
        "system": [{"text": ASSIGNMENT_SYSTEM_PROMPT}],
    }


def test_classify_activity_sends_the_given_system_prompt():
    fake = FakeConverse()
    client = BedrockClient()
    client._client = fake
    ast.classify_activity(client=client, activity={"pmid": "grant:k", "title": "t", "synopsis": "s"},
                          topic_meta={"id": T0, "label": "L", "description": "D"},
                          subtopic_defs=[{"id": "a", "label": "A", "description": "x"}],
                          system_prompt=prompts.GRANT_SUBAREA_SYSTEM)
    assert fake.calls[0][4]["system"] == [{"text": prompts.GRANT_SUBAREA_SYSTEM}]
    assert "- pmid: grant:k" in fake.calls[0][3]


def test_subarea_prompts_are_kind_aware():
    grant, trial = prompts.subarea_system_prompt("grant"), prompts.subarea_system_prompt("trial")
    assert "(a funded research grant)" in grant and '"grant:<key>"' in grant
    assert "(a registered clinical trial)" in trial and '"trial:<key>"' in trial
    for p in (grant, trial):
        assert p != ASSIGNMENT_SYSTEM_PROMPT
        assert "integer" not in p and "scientific publication" not in p
        assert "subtopics it belongs to" in p   # same task framing as publications
    assert prompts.SUBAREA_PROMPT_VERSION


# --- policy parity with assign_subtopics ---------------------------------------------

@pytest.mark.parametrize("raw", [
    # clear winner; a below-floor, an unknown id and a duplicate are dropped
    [{"subtopic_id": "t0_alpha", "confidence": 0.9}, {"subtopic_id": "t0_beta", "confidence": 0.5},
     {"subtopic_id": "t0_gamma", "confidence": 0.29}, {"subtopic_id": "nope", "confidence": 0.99},
     {"subtopic_id": "t0_beta", "confidence": 0.6}],
    # a tie within tie_epsilon: seed density (t0_beta has 9) beats alphabetical
    [{"subtopic_id": "t0_alpha", "confidence": 0.8}, {"subtopic_id": "t0_beta", "confidence": 0.8}],
    # everything below the floor: unassigned
    [{"subtopic_id": "t0_alpha", "confidence": 0.2}],
    [],
])
def test_floor_and_tie_break_match_publications(ctx, monkeypatch, raw):
    writes = []
    monkeypatch.setattr(ast, "_classify_activity", lambda **kw: (raw, {}))
    monkeypatch.setattr(ast, "update_activity_subtopics", lambda **kw: writes.append(kw))
    h = ctx.hierarchies[T0]
    ast._process_pmid(
        "1", {"activity": {"pmid": 1, "title": "t", "synopsis": "s"},
              "rows": [{"PK": f"TOPIC#{T0}", "SK": "SCORE#0800#ACTIVITY#pmid_1#cwid_x"}]},
        client=None, topic_meta=h.topic_meta, subtopic_defs=h.subtopic_defs,
        valid_subtopic_ids=set(h.valid_ids), hierarchy_draft=h.draft,
        confidence_floor=ctx.confidence_floor, dry_run=False)

    fake = FakeConverse(assignments={T0: raw})
    entry = subareas.assign(signal_row(scores=[(T0, 0.8)]), title="t", client=metered(fake),
                            ctx=ctx).item["topic_scores"][0]

    if not writes:
        assert not any(f in entry for f in subareas.SUBTOPIC_FIELDS)
        return
    pub = writes[0]
    assert entry["primary_subtopic_id"] == pub["primary_subtopic_id"]
    assert entry["subtopic_ids"] == pub["subtopic_ids"]
    assert {k: float(v) for k, v in entry["subtopic_confidences"].items()} == pub["confidences"]


def test_confidence_floor_is_the_publication_floor(ctx):
    assert ctx.confidence_floor == ast._get_threshold("confidence_floor")


# --- which areas get a call ----------------------------------------------------------

def test_grant_skips_excluded_areas_trial_does_not(ctx):
    scores = [(T0, 0.8), (EXCLUDED, 0.9)]
    grant = FakeConverse()
    subareas.assign(signal_row("grant", scores=scores), title="t", client=metered(grant), ctx=ctx)
    assert grant.subarea_topics() == [T0]
    trial = FakeConverse()
    subareas.assign(signal_row("trial", scores=scores), title="t", client=metered(trial), ctx=ctx)
    assert trial.subarea_topics() == sorted([T0, EXCLUDED])


def test_below_assign_floor_and_draftless_topics_are_skipped(tmp_path):
    d, cov = write_drafts(tmp_path, {T0: DRAFTS[T0]})
    ctx = subareas.load_context(TAXONOMY, draft_dir=d, coverage_path=cov)
    fake = FakeConverse(assignments={T0: [{"subtopic_id": "t0_alpha", "confidence": 0.9}]})
    row = signal_row(scores=[(T0, 0.3), (T1, 0.9)])
    row["topic_scores"][1]["primary_subtopic_id"] = "stale_slug"   # left from an old draft
    out = subareas.assign(row, title="t", client=metered(fake), ctx=ctx).item
    assert fake.subarea_topics() == [T0]                       # 0.3 is at the floor
    assert "primary_subtopic_id" not in out["topic_scores"][1]  # draft-less: stripped
    assert out["subarea_versions"] == {T0: ctx.hierarchies[T0].sha256}

    below = FakeConverse()
    subareas.assign(signal_row(scores=[(T0, 0.29)]), title="t", client=metered(below), ctx=ctx)
    assert below.calls == []


def test_load_context_skips_missing_and_unapproved_drafts(tmp_path):
    d, cov = write_drafts(tmp_path, {T0: DRAFTS[T0], T1: _draft(T1, [("x", 1)], status="draft")},
                          covered=[T0, T1, EXCLUDED])
    ctx = subareas.load_context(TAXONOMY, draft_dir=d, coverage_path=cov)
    assert set(ctx.hierarchies) == {T0}
    assert sorted(ctx.missing) == sorted([T1, EXCLUDED])
    assert ctx.version and ctx.version.startswith(prompts.SUBAREA_PROMPT_VERSION + "@")

    empty = subareas.load_context(TAXONOMY, draft_dir=tmp_path / "none", coverage_path=cov)
    assert empty.hierarchies == {} and empty.version is None


def test_load_context_ignores_a_covered_topic_the_taxonomy_retired(tmp_path):
    d, cov = write_drafts(tmp_path, {T0: DRAFTS[T0], "retired_topic": _draft("retired_topic", [("r", 1)])})
    ctx = subareas.load_context(TAXONOMY, draft_dir=d, coverage_path=cov)
    assert set(ctx.hierarchies) == {T0} and ctx.missing == []


def test_assign_writes_the_contract_fields(ctx):
    fake = FakeConverse(assignments={
        T0: [{"subtopic_id": "t0_beta", "confidence": 0.7}, {"subtopic_id": "t0_alpha", "confidence": 0.4}],
        T1: []})
    out = subareas.assign(signal_row(), title="Grant title", client=metered(fake), ctx=ctx).item
    e0, e1 = out["topic_scores"]
    assert e0["primary_subtopic_id"] == "t0_beta"
    assert e0["subtopic_ids"] == ["t0_beta", "t0_alpha"]
    assert e0["subtopic_confidences"] == {"t0_beta": Decimal("0.7"), "t0_alpha": Decimal("0.4")}
    assert not any(f in e1 for f in subareas.SUBTOPIC_FIELDS)    # validly unassigned
    assert out["subarea_versions"] == {T0: ctx.hierarchies[T0].sha256, T1: ctx.hierarchies[T1].sha256}
    assert out["subarea_prompt_version"] == prompts.SUBAREA_PROMPT_VERSION
    user = fake.calls[0][3]
    assert "- pmid: grant:k1" in user and "- title: Grant title" in user
    assert "- synopsis: Stored synopsis" in user


def test_a_failed_call_strips_the_area_and_leaves_it_stale(ctx):
    fake = FakeConverse(assignments={T1: [{"subtopic_id": "t1_one", "confidence": 0.9}]},
                        fail_topics={T0})
    row = signal_row()
    row["topic_scores"][0]["primary_subtopic_id"] = "t0_alpha"
    row["subarea_versions"] = {T0: "old"}
    res = subareas.assign(row, title="t", client=metered(fake), ctx=ctx)
    assert [t for t, _ in res.errors] == [T0]
    assert "primary_subtopic_id" not in res.item["topic_scores"][0]
    assert T0 not in res.item["subarea_versions"]
    assert subareas.stale_topics(res.item, ctx) == [T0]


def test_a_failed_call_keeps_fields_assigned_under_the_current_draft(ctx):
    """A subarea prompt bump hit by an outage must not wipe still-valid slugs."""
    fake = FakeConverse(fail_topics={T0, T1})
    row = signal_row(subarea_prompt_version="2000-01-01",
                     subarea_versions={T0: ctx.hierarchies[T0].sha256, T1: "old-draft"})
    row["topic_scores"][0].update(primary_subtopic_id="t0_alpha", subtopic_ids=["t0_alpha"])
    row["topic_scores"][1].update(primary_subtopic_id="dead_slug", subtopic_ids=["dead_slug"])
    res = subareas.assign(row, title="t", client=metered(fake), ctx=ctx,
                          topics=subareas.stale_topics(row, ctx))
    e0, e1 = res.item["topic_scores"]
    assert e0["primary_subtopic_id"] == "t0_alpha"       # current draft: kept
    assert "primary_subtopic_id" not in e1               # old draft: stripped
    assert res.item["subarea_versions"] == {}            # both retried


# --- the drain -----------------------------------------------------------------------

def test_fresh_score_assigns_subareas_and_meters_them(ctx):
    table = make_table([inp("grant", "g1")])
    fake = FakeConverse(assignments={T0: [{"subtopic_id": "t0_alpha", "confidence": 0.9}],
                                     T1: [{"subtopic_id": "t1_two", "confidence": 0.6}]})
    code, summary, _ = do_run(table, fake, ctx)
    assert code == 0
    assert fake.subarea_topics() == sorted([T0, T1])
    row = table.store[(signals.signal_pk("grant", "g1"), "META")]
    assert {e["topic_id"]: e.get("primary_subtopic_id") for e in row["topic_scores"]} == {
        T0: "t0_alpha", T1: "t1_two"}
    item = table.store[("AREAINPUT", "grant#g1")]
    assert item["status"] == "scored" and item["subarea_version"] == ctx.version
    assert summary["assign_calls"] == 2
    # Haiku spend = the screening call + both subarea calls, from their own usage
    acc = CostAccumulator()
    acc.record(model=HAIKU_MODEL, input_tokens=3000, output_tokens=60)
    for _ in range(2):
        acc.record(model=HAIKU_MODEL, input_tokens=3000, output_tokens=80)
    assert summary["model_breakdown"][HAIKU_MODEL] == str(acc.by_model[HAIKU_MODEL])
    # not due again
    assert drain.select_due([item], TV, subarea_version=ctx.version) == []


def test_assign_only_backfills_a_row_scored_before_subareas(ctx):
    item, ih = scored_input("g1")
    row = signal_row(key="g1", input_hash=ih, taxonomy_version=TV)
    table = make_table([item, row])
    fake = FakeConverse(assignments={T0: [{"subtopic_id": "t0_gamma", "confidence": 0.8}]})
    code, summary, _ = do_run(table, fake, ctx)
    assert code == 0
    assert summary["by_reason"] == {drain.REASON_SUBAREA: 1}
    assert fake.kinds() == ["subarea", "subarea"]          # no synopsis, no re-score
    stored = table.store[(row["PK"], "META")]
    assert stored["synopsis"] == "Stored synopsis"
    assert stored["topic_scores"][0]["primary_subtopic_id"] == "t0_gamma"
    after = table.store[("AREAINPUT", "grant#g1")]
    assert after["subarea_version"] == ctx.version
    assert after.get("scored_at") == item.get("scored_at")  # not re-marked scored
    assert summary["outcomes"] == {run_mod.OUTCOME_SUBAREAS: 1}


def test_stale_draft_hash_reassigns_only_that_area(ctx):
    item, ih = scored_input("g1", subarea_version="older")
    row = signal_row(key="g1", input_hash=ih, taxonomy_version=TV,
                     subarea_prompt_version=prompts.SUBAREA_PROMPT_VERSION,
                     subarea_versions={T0: "recomputed-draft", T1: ctx.hierarchies[T1].sha256})
    row["topic_scores"][0].update(primary_subtopic_id="old_slug", subtopic_ids=["old_slug"],
                                  subtopic_confidences={"old_slug": Decimal("0.9")})
    row["topic_scores"][1].update(primary_subtopic_id="t1_one", subtopic_ids=["t1_one"],
                                  subtopic_confidences={"t1_one": Decimal("0.5")})
    table = make_table([item, row])
    fake = FakeConverse(assignments={T0: [{"subtopic_id": "t0_beta", "confidence": 0.7}]})
    do_run(table, fake, ctx)
    assert fake.subarea_topics() == [T0]
    e0, e1 = table.store[(row["PK"], "META")]["topic_scores"]
    assert e0["primary_subtopic_id"] == "t0_beta"
    assert e1["primary_subtopic_id"] == "t1_one"            # current area kept
    assert table.store[("AREAINPUT", "grant#g1")]["subarea_version"] == ctx.version


def test_subarea_prompt_bump_reassigns_every_area(ctx):
    item, ih = scored_input("g1", subarea_version="older")
    row = signal_row(key="g1", input_hash=ih, taxonomy_version=TV, subarea_prompt_version="2000-01-01",
                     subarea_versions={t: h.sha256 for t, h in ctx.hierarchies.items()})
    table = make_table([item, row])
    fake = FakeConverse()
    do_run(table, fake, ctx)
    assert fake.subarea_topics() == sorted([T0, T1])


def test_current_version_with_nothing_stale_stamps_without_a_call(ctx):
    item, ih = scored_input("g1", subarea_version="older")
    row = signal_row(key="g1", input_hash=ih, taxonomy_version=TV,
                     subarea_prompt_version=prompts.SUBAREA_PROMPT_VERSION,
                     subarea_versions={T0: ctx.hierarchies[T0].sha256, T1: ctx.hierarchies[T1].sha256})
    table = make_table([item, row])
    fake = FakeConverse()
    do_run(table, fake, ctx)
    assert fake.calls == []
    assert table.store[("AREAINPUT", "grant#g1")]["subarea_version"] == ctx.version


def test_assign_only_failures_count_attempts_then_give_up(ctx):
    item, ih = scored_input("g1")
    row = signal_row(key="g1", input_hash=ih, taxonomy_version=TV)
    table = make_table([item, row])
    fake = FakeConverse(fail_topics={T0})
    for attempt in (1, 2):
        code, summary, alerts = do_run(table, fake, ctx)
        after = table.store[("AREAINPUT", "grant#g1")]
        assert code == 0 and after["subarea_attempts"] == attempt
        assert "subarea_version" not in after and after["status"] == "scored"
        assert summary["outcomes"] == {run_mod.OUTCOME_RETRY: 1}
    # T1 succeeded and is kept while T0 is retried
    assert fake.subarea_topics().count(T1) == 1
    code, summary, alerts = do_run(table, fake, ctx)
    after = table.store[("AREAINPUT", "grant#g1")]
    assert after["subarea_version"] == ctx.version and "ValidationException" in after["subarea_error"]
    assert "subarea_attempts" not in after and after["status"] == "scored"
    assert any(a[0] == "WARN" and "failed" in a[1] for a in alerts)
    fake.calls.clear()
    do_run(table, fake, ctx)
    assert fake.calls == []                                 # not retried until the version moves


def test_systemic_error_in_assign_only_opens_the_breaker(ctx):
    items = []
    for n in range(2):
        item, ih = scored_input(f"g{n}")
        items += [item, signal_row(key=f"g{n}", input_hash=ih, taxonomy_version=TV)]
    table = make_table(items)

    class Denied(FakeConverse):
        def converse(self, **kw):
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "Converse")

    code, summary, _ = do_run(table, Denied(), ctx, workers=1)
    assert code == 1 and summary["run_status"] == "aborted"
    for n in range(2):
        assert "subarea_attempts" not in table.store[("AREAINPUT", f"grant#g{n}")]


def test_partial_failure_on_a_fresh_score_comes_back_assign_only(ctx):
    table = make_table([inp("grant", "g1")])
    fake = FakeConverse(fail_topics={T1})
    code, summary, _ = do_run(table, fake, ctx)
    after = table.store[("AREAINPUT", "grant#g1")]
    assert code == 0 and after["status"] == "scored" and "subarea_version" not in after
    assert summary["subareas_incomplete"] == 1
    fake.fail_topics.clear()
    fake.calls.clear()
    _, summary, _ = do_run(table, fake, ctx)
    assert summary["by_reason"] == {drain.REASON_SUBAREA: 1}
    assert fake.kinds() == ["subarea"] and fake.subarea_topics() == [T1]
    assert table.store[("AREAINPUT", "grant#g1")]["subarea_version"] == ctx.version


def test_no_drafts_means_no_subarea_work(tmp_path):
    item, ih = scored_input("g1")
    table = make_table([item, signal_row(key="g1", input_hash=ih, taxonomy_version=TV)])
    fake = FakeConverse()
    _, summary, _ = do_run(table, fake, subareas.SubareaContext())
    assert summary["due"] == 0 and fake.calls == []


def test_missing_drafts_alert(tmp_path):
    d, cov = write_drafts(tmp_path, {T0: DRAFTS[T0]}, covered=[T0, T1])
    ctx = subareas.load_context(TAXONOMY, draft_dir=d, coverage_path=cov)
    _, summary, alerts = do_run(make_table([]), FakeConverse(), ctx)
    assert summary["subarea_drafts_missing"] == [T1]
    assert any(a[0] == "WARN" and "draft" in a[1] for a in alerts)


def test_dry_run_estimates_assign_calls_without_calls_or_writes(ctx):
    stale, ih = scored_input("g1")
    trial, tih = scored_input("t1", kind="trial")
    rows = [signal_row(key="g1", input_hash=ih, taxonomy_version=TV,
                       scores=[(T0, 0.8), (T1, 0.5), (EXCLUDED, 0.9)]),
            signal_row("trial", "t1", input_hash=tih, taxonomy_version=TV,
                       scores=[(T0, 0.8), (EXCLUDED, 0.9)])]
    table = make_table([stale, trial, inp("grant", "new1"), *rows])
    before = {k: dict(v) for k, v in table.store.items()}
    fake = FakeConverse()
    code, summary, _ = do_run(table, fake, ctx, dry_run=True)
    assert code == 0 and fake.calls == []
    assert table.store == before
    # g1: T0 + T1 (EXCLUDED skipped for a grant) = 2; t1: T0 + EXCLUDED = 2;
    # new1 is scored first: the expected grant count
    assert summary["estimated_assign_calls"] == 2 + 2 + run_mod.EST_ASSIGN_CALLS["grant"]
    expected = (run_mod.EST_SCORE_USD + run_mod.EST_SYNOPSIS_USD
                + run_mod.EST_ASSIGN_USD * summary["estimated_assign_calls"])
    assert Decimal(summary["estimated_usd"]) == expected
    assert summary["subarea_version"] == ctx.version
