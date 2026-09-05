"""Unit tests for the deterministic, dependency-light parts of pipeline_cores:
dictionary load, the acknowledgement matcher, and the combiner. No DB/Bedrock/AWS.
"""
import math
import statistics

from pipeline_cores.combine import (
    DEFAULT_CONFIRM_THRESHOLD,
    DEFAULT_TRIAGE_THRESHOLD,
    PRIOR_LOGIT,
    combine,
    evidence_features,
    explain,
    score,
)
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    CoreDefinition,
    SignalResult,
)
from pipeline_cores.signals import (
    SCREEN_CUTOFF,
    acknowledgement_signal,
    author_affinity,
    build_affinity_index,
    llm_triage,
    match_section,
)


# --- dictionary ------------------------------------------------------------
def test_dictionary_loads_imaging_core():
    cores = load_cores()
    assert any(c.core_id == "2" and c.name == "Biomedical Imaging" for c in cores)


def test_imaging_staff_resolved_and_tracking_split():
    c = load_core("2")
    assert len(c.staff) == 7
    tracked = set(c.tracked_staff_cwids)
    assert tracked == {"djb2001", "jpd2001", "dcs7001", "bih2006"}      # active in person tables
    untracked = {s.cwid for s in c.staff if not s.tracked}
    assert untracked == {"hev2006", "job2060", "cof2003"}              # need upstream ReCiter fix


# --- acknowledgement matcher ----------------------------------------------
_CORE = CoreDefinition(
    core_id="2", name="Biomedical Imaging",
    aliases=["Citigroup Biomedical Imaging Center", "CBIC"],
)


def test_ack_matches_full_name_and_snippet():
    text = "...staff at the Citigroup Biomedical Imaging Center of Weill Cornell..."
    r = acknowledgement_signal(text, _CORE)
    assert r.ack_matched and r.ack_alias == "Citigroup Biomedical Imaging Center"
    assert "Citigroup Biomedical Imaging Center" in r.ack_snippet


def test_ack_acronym_requires_word_boundary():
    assert acknowledgement_signal("scanned at the CBIC core", _CORE).ack_matched is True
    # substring inside another token must NOT match the acronym
    assert acknowledgement_signal("the CBICX consortium", _CORE).ack_matched is False


def test_ack_empty_text_is_no_match():
    assert acknowledgement_signal("", _CORE).ack_matched is False


# --- combiner --------------------------------------------------------------
# Status is a THRESHOLD on the score now, not a deterministic flag (SPEC decision 1),
# so these assert bands rather than the old 0.98/0.95 constants.
def test_acknowledgement_at_home_still_confirms():
    """A distinctive alias next to a home institution is the strongest evidence
    there is — it clears CONFIRM by a mile, but because it EARNED it."""
    sig = SignalResult(ack_matched=True, ack_alias="Citigroup Biomedical Imaging Center",
                       ack_alias_hits=197, ack_institution="home", llm_score=1)
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_CONFIRMED and rec.likelihood > 0.99


def test_staff_coauthor_still_confirms_on_its_own():
    """The other historically 100%-precision confirmer. The default confirm
    threshold is set BELOW a lone staff co-author on purpose, so signal 2 keeps
    confirming by itself."""
    rec = combine("1", "2", SignalResult(coauthor_cwids=["djb2001"]))
    assert rec.status == STATUS_CONFIRMED
    assert rec.likelihood > DEFAULT_CONFIRM_THRESHOLD


def test_a_dismissive_llm_score_can_now_hold_a_staff_paper_back():
    """The other half of decision 1, worth pinning because it IS a behaviour change:
    nothing has precedence any more, so an LLM 1 ("clearly unrelated") subtracts from
    a staff co-authorship instead of being overridden by it. The pair still ranks
    high in the claim queue — it just stops short of auto-confirm."""
    rec = combine("1", "2", SignalResult(coauthor_cwids=["djb2001"], llm_score=1))
    assert rec.status == STATUS_CANDIDATE
    assert rec.likelihood > DEFAULT_TRIAGE_THRESHOLD


def test_the_llm_alone_never_confirms():
    """The one piece of the old hard-coded precedence worth keeping: the LLM ranks
    the queue, it does not label. Even a 10 tops out below the confirm bar, because
    its weight is clamped to the top score the fitting panel actually contained."""
    for score in (7, 8, 9, 10):
        rec = combine("1", "2", SignalResult(llm_score=score))
        assert rec.status != STATUS_CONFIRMED, score
    assert combine("1", "2", SignalResult(llm_score=9)).status == STATUS_CANDIDATE


def test_low_signal_falls_below_threshold():
    rec = combine("1", "2", SignalResult(llm_score=2))
    assert rec.status == STATUS_BELOW


def test_a_trace_user_no_longer_carries_a_weak_paper_on_its_own():
    """A behaviour change worth pinning: the old noisy-OR handed a lone repeat-user
    prior straight past the queue bar. An author who has given 2% of their output to
    this core lifts a weak paper above the base rate and no further — which is what
    stops one confirmation flooding the queue with everything else that author wrote."""
    weak = combine("1", "2", SignalResult(llm_score=2, author_affinity=0.02))
    assert weak.status == STATUS_BELOW
    assert weak.likelihood > 1 / (1 + math.exp(-PRIOR_LOGIT))     # still above the prior
    # ...and it compounds: a working relationship plus a real LLM score reaches the queue.
    assert combine("1", "2", SignalResult(llm_score=4, author_affinity=0.6)).status == STATUS_CANDIDATE
    # A rate that says this core IS most of what the author does confirms on its own —
    # fitted at 4.93, just above a staff co-author's 4.89. That is new (the old constant
    # topped out at 4.60 * 0.85 = 3.91, a candidate), measured, and deliberate.
    assert combine("1", "2", SignalResult(author_affinity=0.85)).status == STATUS_CONFIRMED


# --- full-text loader (pure + cache, no network) ---------------------------
def test_to_plain_text_strips_xml():
    from pipeline_cores.fulltext import to_plain_text
    assert to_plain_text("<sec><p>Citigroup  Biomedical\nImaging</p></sec>") == "Citigroup Biomedical Imaging"
    assert to_plain_text("") == ""


def test_to_plain_text_drops_ref_list():
    from pipeline_cores.fulltext import to_plain_text
    xml = (
        '<body><p>Analysis used the WCM Research Informatics core.</p></body>'
        '<back><ref-list id="ref1"><ref><article-title>WCM Research Informatics: a cited paper'
        '</article-title></ref></ref-list></back>'
    )
    text = to_plain_text(xml)
    assert text.count("WCM Research Informatics") == 1   # the body mention, not the citation
    assert "a cited paper" not in text


def test_fulltext_cache_hit_skips_network(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    client = PmcFullTextClient(cache_dir=tmp_path)
    (tmp_path / "999.xml").write_text("<body>scanned at the CBIC core</body>", encoding="utf-8")
    assert "CBIC core" in client.get("999")           # served from cache, no HTTP


def test_fulltext_negative_cache(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient, _NO_PMC
    client = PmcFullTextClient(cache_dir=tmp_path)
    (tmp_path / "888.xml").write_text(_NO_PMC, encoding="utf-8")
    assert client.get("888") == ""                    # cached "no PMC" -> empty, no HTTP


# --- S3-backed full-text cache (tier 2; in-memory fake, no AWS/network) -----
class _FakeS3:
    """Duck-typed S3HierarchyClient: in-memory store + read/write counters."""
    def __init__(self, store=None, fail=False):
        self.store = dict(store or {})        # key -> bytes
        self.fail = fail                      # simulate AccessDenied / network
        self.reads = self.writes = 0

    def key_exists(self, key):
        if self.fail:
            raise RuntimeError("AccessDenied")
        return key in self.store

    def get_object_bytes(self, key):
        self.reads += 1
        return self.store[key]

    def put_object(self, key, body, content_type=None):
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.writes += 1
        self.store[key] = body


def _no_network(client):
    """Make any NCBI call explode so a test proves it never hit the origin."""
    def _boom(*a, **k):
        raise AssertionError("unexpected NCBI fetch — should have served from cache")
    client._pmid_to_pmcid = _boom
    client._get = _boom


def test_s3_hit_serves_and_populates_disk_without_network(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    s3 = _FakeS3({"cores/fulltext/999.xml": b"<body>scanned at the CBIC core</body>"})
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    _no_network(client)
    assert "CBIC core" in client.get("999")        # served from S3, no NCBI
    assert s3.reads == 1
    assert (tmp_path / "999.xml").exists()          # read-through populated disk
    # second read is a disk hit — S3 not touched again
    assert "CBIC core" in client.get("999")
    assert s3.reads == 1


def test_s3_negative_sentinel_serves_empty_without_network(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient, _NO_PMC
    s3 = _FakeS3({"cores/fulltext/888.xml": _NO_PMC.encode("utf-8")})
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    _no_network(client)
    assert client.get("888") == ""                  # cached "no PMC" -> empty
    assert (tmp_path / "888.xml").read_text(encoding="utf-8") == _NO_PMC


def test_origin_fetch_writes_through_to_disk_and_s3(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    s3 = _FakeS3()
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    client._pmid_to_pmcid = lambda pmid: "PMC321"
    client._get = lambda path, params: "<body>imaged at the CBIC core facility</body>"
    assert "CBIC core" in client.get("321")
    assert (tmp_path / "321.xml").exists()           # disk written
    assert s3.writes == 1                            # write-through to S3
    assert s3.store["cores/fulltext/321.xml"] == b"<body>imaged at the CBIC core facility</body>"


def test_origin_no_pmc_writes_sentinel_through_to_both(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient, _NO_PMC
    s3 = _FakeS3()
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    client._pmid_to_pmcid = lambda pmid: ""          # elink found no PMC link
    assert client.get("777") == ""
    assert (tmp_path / "777.xml").read_text(encoding="utf-8") == _NO_PMC
    assert s3.store["cores/fulltext/777.xml"] == _NO_PMC.encode("utf-8")


def test_disk_hit_takes_priority_over_s3(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    s3 = _FakeS3({"cores/fulltext/555.xml": b"<body>stale S3 copy</body>"})
    (tmp_path / "555.xml").write_text("<body>fresh disk copy</body>", encoding="utf-8")
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    _no_network(client)
    assert "fresh disk copy" in client.get("555")
    assert s3.reads == 0                             # disk short-circuits S3


def test_s3_read_failure_degrades_to_origin_without_raising(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    s3 = _FakeS3(fail=True)                          # key_exists raises (AccessDenied)
    client = PmcFullTextClient(cache_dir=tmp_path, s3=s3)
    client._pmid_to_pmcid = lambda pmid: ""          # origin says no PMC
    assert client.get("404") == ""                  # degraded gracefully, no crash
    assert client.s3_read_failures == 1


def test_no_s3_backend_behaves_as_disk_only(tmp_path):
    from pipeline_cores.fulltext import PmcFullTextClient
    client = PmcFullTextClient(cache_dir=tmp_path)   # s3 defaults to None
    assert client.s3 is None
    client._pmid_to_pmcid = lambda pmid: "PMC1"
    client._get = lambda path, params: "<body>at the CBIC core</body>"
    assert "CBIC core" in client.get("111")          # origin path, no S3 involvement


def test_with_s3_targets_artifacts_bucket():
    from pipeline_cores.fulltext import PmcFullTextClient
    client = PmcFullTextClient.with_s3()             # lazy boto3, no AWS call at init
    assert client.s3.bucket == "wcmc-reciterai-artifacts"
    assert client._s3_key("123") == "cores/fulltext/123.xml"


# --- author affinity (signal 1, repeat-user RATE) --------------------------
def test_build_affinity_index_is_a_rate_not_a_count():
    """The share of an author's OWN corpus output that already belongs to the core."""
    idx = build_affinity_index({"djb2001": {"2": 3}, "abc1001": {"5": 1}},
                               {"djb2001": 4, "abc1001": 50})
    assert idx["djb2001"]["2"] == 0.75                        # 3 of their 4 papers
    assert idx["abc1001"]["5"] == 0.02                        # 1 of their 50
    assert author_affinity(idx, ["djb2001"], "2") == 0.75
    assert author_affinity(idx, ["abc1001"], "2") == 0.0      # different core
    assert author_affinity(idx, ["nobody"], "2") == 0.0       # unknown author
    # one contributing author across the byline (the rest are zero) == that rate
    assert author_affinity(idx, ["nobody", "djb2001"], "2") == 0.75


def test_the_corpus_denominator_separates_a_regular_from_a_passer_by():
    """FAILS on the old curve, which read both of these as "has prior confirms" and
    gave the passer-by 0.45 — 2.07 nats — for the one paper. The denominator is what
    makes a single confirm mean different things for different authors."""
    idx = build_affinity_index({"regular": {"2": 3}, "passer": {"2": 1}},
                               {"regular": 4, "passer": 60})
    assert evidence_features(SignalResult(author_affinity=idx["passer"]["2"])) == ["aff:trace"]
    assert evidence_features(SignalResult(author_affinity=idx["regular"]["2"])) == ["aff:core"]
    assert (score(SignalResult(author_affinity=idx["passer"]["2"]))
            < score(SignalResult(author_affinity=idx["regular"]["2"])))


def test_many_confirms_no_longer_score_the_same_as_four_single_ones():
    """The collapse this replaced: with the cap at 4 confirmed papers, one author with
    24 core papers and four co-authors who each used the core once both landed on 0.85
    -> 3.91 nats, exactly. 83.9% of core 14's live queue sat on that one value."""
    idx = build_affinity_index(
        {"heavy": {"2": 24}, "a": {"2": 1}, "b": {"2": 1}, "c": {"2": 1}, "d": {"2": 1}},
        {"heavy": 30, "a": 40, "b": 40, "c": 40, "d": 40})
    heavy = author_affinity(idx, ["heavy"], "2")                  # 24/30 = 0.80
    four_light = author_affinity(idx, ["a", "b", "c", "d"], "2")  # 1/40  = 0.025
    assert heavy == 0.8 and four_light == 0.025
    assert score(SignalResult(author_affinity=heavy)) > score(SignalResult(author_affinity=four_light))


def test_author_affinity_is_the_max_not_a_noisy_or():
    """Noisy-OR across the byline is monotone in HOW MANY authors fire, and that count
    is the feature measured NOT to separate (AUC 0.6505). Under it, four people who
    each used the core once out-scored one author with a real working relationship."""
    idx = build_affinity_index(
        {"a": {"2": 1}, "b": {"2": 1}, "c": {"2": 1}, "d": {"2": 1}, "solo": {"2": 2}},
        {"a": 40, "b": 40, "c": 40, "d": 40, "solo": 10})
    four = author_affinity(idx, ["a", "b", "c", "d"], "2")
    assert four == author_affinity(idx, ["a"], "2") == 0.025      # adding bodies adds nothing
    assert four < 1 - 0.975 ** 4                                  # what noisy-OR would have given
    assert author_affinity(idx, ["a", "b", "c", "d", "solo"], "2") == 0.2   # the max, not the pile
    assert author_affinity(idx, [], "2") == 0.0


def test_affinity_bucket_edges_sit_where_the_fit_drew_them():
    """0.05 and 0.70 are fitted cell boundaries, closed on the upper bucket."""
    from pipeline_cores.combine import _AFF_CORE_MIN, _AFF_REGULAR_MIN
    assert (_AFF_REGULAR_MIN, _AFF_CORE_MIN) == (0.05, 0.70)

    def feat(rate):
        return evidence_features(SignalResult(author_affinity=rate))

    assert feat(0.0) == []                        # absent evidence contributes no key
    assert feat(0.049) == ["aff:trace"]
    assert feat(0.05) == ["aff:regular"]
    assert feat(0.699) == ["aff:regular"]
    assert feat(0.70) == ["aff:core"]
    assert feat(1.0) == ["aff:core"]


def test_an_unmeasurable_author_is_dropped_not_read_as_maximal(caplog):
    """"We could not measure this author" and "this author is entirely a core user" are
    OPPOSITE claims. Flooring a missing total to rate 1.0 conflated them into `aff:core`
    (+4.93), which alone clears the confirm bar — so an unmeasurable author auto-confirmed
    every paper they touched with no ack, no staff co-author and no LLM score. Absent
    evidence contributes nothing, as everywhere else in the model.

    A confirm count ABOVE the corpus total means the caller skipped
    ingest.filter_corpus_pmids; it is clamped AND said out loud, never silent."""
    import logging
    with caplog.at_level(logging.WARNING):
        idx = build_affinity_index({"unknown": {"2": 2}, "stale": {"2": 5}}, {"stale": 3})
    assert "unknown" not in idx                       # dropped, NOT floored to 1.0
    assert idx["stale"]["2"] == 1.0                   # clamped...
    assert "no corpus paper total" in caplog.text
    assert "were DROPPED" in caplog.text
    assert "not gated through" in caplog.text         # ...and the skipped gate named


# --- LLM triage two-pass logic (fake Bedrock, no network) ------------------
class _FakeBedrock:
    """Records calls; returns a screen int from .call and a dense dict from .call_json."""
    def __init__(self, screen, dense_score=None):
        self.screen, self.dense_score = screen, dense_score
        self.call_count = self.call_json_count = 0

    def call(self, model, messages, **kw):
        self.call_count += 1
        return str(self.screen)

    def call_json(self, model, messages, **kw):
        self.call_json_count += 1
        return {"score": self.dense_score, "rationale": "used 3T MRI at the core"}


_PUB = [{"pmid": "5", "title": "Quantitative MRI of the brain", "abstract": "3T MRI study."}]


def test_triage_screened_out_skips_dense_pass():
    fb = _FakeBedrock(screen=1)                       # below SCREEN_CUTOFF
    out = llm_triage(fb, _CORE, _PUB)
    assert out["5"]["score"] == 1
    assert fb.call_json_count == 0                    # dense pass skipped


def test_triage_screened_in_runs_dense_and_keeps_rationale():
    fb = _FakeBedrock(screen=SCREEN_CUTOFF, dense_score=8)
    out = llm_triage(fb, _CORE, _PUB)
    assert fb.call_json_count == 1
    assert out["5"]["score"] == 8 and out["5"]["screen"] == SCREEN_CUTOFF
    assert out["5"]["rationale"] == "used 3T MRI at the core"


def test_triage_rationale_is_trimmed_on_a_word_boundary():
    """The dense prompt asks for "<=80 chars" and the model overruns anyway; the bare
    [:80] slice that used to enforce it put rationales ending "retrospective coho" in
    the live claim queue. _fit cuts on a word boundary instead, but never at the cost
    of the rationale itself: an oversized unbreakable token (a URL, a DOI, an
    accession) keeps the hard slice, because a 3-character evidence chip is worse in
    a reviewer's queue than one cut word. That last case is why this is not
    textwrap.shorten, which drops every word after such a token."""
    class _Rationale(_FakeBedrock):
        def __init__(self, text):
            super().__init__(screen=SCREEN_CUTOFF, dense_score=8)
            self.text = text

        def call_json(self, model, messages, **kw):
            self.call_json_count += 1
            return {"score": self.dense_score, "rationale": self.text}

    def triaged(text):
        return llm_triage(_Rationale(text), _CORE, _PUB)["5"]["rationale"]

    long = ("core-run 3T MRI acquisition and analysis for a multi-site "
            "retrospective cohort of patients")
    out = triaged(long)
    assert len(out) <= 80 and out.endswith("…")
    assert long.startswith(out[:-1].rstrip())        # a whole word, not "...coho"

    assert triaged("used 3T MRI at the core") == "used 3T MRI at the core"   # fits

    # One oversized token must not swallow the rationale. shorten() returns "…"
    # here, and backing up to the last space returns "Uses core:…" — both blank
    # the chip, so both are regressions on the [:80] slice this replaced.
    url = "Uses core: https://example.org/" + "a" * 80
    assert len(triaged(url)) == 80 and triaged(url).startswith("Uses core: https://")
    assert triaged("x" * 100) == "x" * 79 + "…"      # no space at all: sliced, not blanked


# --- one-Haiku-screens-all-cores (the 13x cost lever) ----------------------
class _FakeAllCoresBedrock:
    """call_json returns a core-keyed screen for Haiku, a dense dict for Sonnet."""
    def __init__(self, screen_scores, dense_score=8):
        self.screen_scores = screen_scores
        self.dense_score = dense_score
        self.haiku_calls = self.sonnet_calls = 0

    def call(self, model, messages, **kw):
        raise AssertionError("all-cores path must not use the per-core .call screen")

    def call_json(self, model, messages, **kw):
        if "haiku" in model.lower():
            self.haiku_calls += 1
            return dict(self.screen_scores)
        self.sonnet_calls += 1
        return {"score": self.dense_score, "rationale": "dense via shared screen"}


_CORES_2 = [_CORE, CoreDefinition(core_id="5", name="Genomics Resources", aliases=["GRCF"])]
_PUB2 = [{"pmid": "42", "title": "MRI + RNA-seq study", "abstract": "3T MRI and sequencing."}]


def test_screen_all_cores_one_haiku_call_covers_every_core():
    from pipeline_cores.signals import screen_all_cores
    fb = _FakeAllCoresBedrock({"2": 7, "5": 1})
    out = screen_all_cores(fb, _CORES_2, _PUB2)
    assert fb.haiku_calls == 1                       # ONE call for the pub, not one per core
    assert out["42"] == {"2": 7, "5": 1}


def test_screen_all_cores_missing_core_defaults_to_screened_out():
    from pipeline_cores.signals import screen_all_cores
    fb = _FakeAllCoresBedrock({"2": 9})              # reply omits core 5
    out = screen_all_cores(fb, _CORES_2, _PUB2)
    assert out["42"] == {"2": 9, "5": 1}


def test_screen_all_cores_safety_filter_defaults_all_to_1():
    from pipeline_cores.signals import screen_all_cores
    from utils.bedrock_client import BedrockEmptyContentError

    class _Empty:
        def call_json(self, model, messages, **kw):
            raise BedrockEmptyContentError(stop_reason="content_filtered", model=model)

    out = screen_all_cores(_Empty(), _CORES_2, _PUB2)
    assert out["42"] == {"2": 1, "5": 1}


def test_llm_triage_with_screen_map_skips_haiku_and_below_cutoff_skips_dense():
    fb = _FakeAllCoresBedrock({}, dense_score=8)
    out = llm_triage(fb, _CORE, _PUB2, screen_map={"42": {"2": 1}})
    assert out["42"]["screen"] == 1 and out["42"]["score"] == 1
    assert fb.haiku_calls == 0 and fb.sonnet_calls == 0   # no screen call; below cutoff -> no dense


def test_llm_triage_with_screen_map_runs_dense_at_cutoff():
    fb = _FakeAllCoresBedrock({}, dense_score=8)
    out = llm_triage(fb, _CORE, _PUB2, screen_map={"42": {"2": SCREEN_CUTOFF}})
    assert fb.haiku_calls == 0 and fb.sonnet_calls == 1
    assert out["42"]["screen"] == SCREEN_CUTOFF and out["42"]["score"] == 8
    assert out["42"]["rationale"] == "dense via shared screen"


def test_llm_triage_screen_map_missing_pmid_defaults_to_screened_out():
    fb = _FakeAllCoresBedrock({}, dense_score=8)
    out = llm_triage(fb, _CORE, _PUB2, screen_map={})    # no entry for pmid 42
    assert out["42"]["score"] == 1 and fb.sonnet_calls == 0


# --- threaded triage (scale fix: serial loop hung the full run) -------------
def test_llm_triage_threads_all_pubs_one_screen_each():
    import threading

    class _Counting:
        def __init__(self):
            self.calls = 0
            self._lock = threading.Lock()

        def call(self, model, messages, **kw):
            with self._lock:
                self.calls += 1
            return "1"                                   # below cutoff -> no dense pass

        def call_json(self, model, messages, **kw):
            return {"score": 5, "rationale": ""}

    pubs = [{"pmid": str(i), "title": "t", "abstract": "a"} for i in range(10)]
    fb = _Counting()
    out = llm_triage(fb, _CORE, pubs, max_workers=4)
    assert set(out) == {str(i) for i in range(10)}       # every pub scored concurrently
    assert fb.calls == 10                                # exactly one screen per pub


def test_llm_triage_resilient_to_call_errors_screens_out_and_keeps_going():
    class _Boom:
        def call(self, model, messages, **kw):
            raise RuntimeError("simulated read timeout")

        def call_json(self, model, messages, **kw):
            return {"score": 9, "rationale": "x"}

    pubs = [{"pmid": str(i), "title": "t", "abstract": "a"} for i in range(5)]
    out = llm_triage(_Boom(), _CORE, pubs, max_workers=4)
    assert set(out) == {str(i) for i in range(5)}        # one bad call must not kill the batch
    assert all(out[p]["score"] == 1 for p in out)        # errored screen -> screened out


def test_llm_triage_serial_when_max_workers_1():
    class _Counting:
        def __init__(self):
            self.calls = 0

        def call(self, model, messages, **kw):
            self.calls += 1
            return "1"

        def call_json(self, model, messages, **kw):
            return {"score": 5, "rationale": ""}

    pubs = [{"pmid": "a", "title": "t", "abstract": ""}, {"pmid": "b", "title": "t", "abstract": ""}]
    fb = _Counting()
    out = llm_triage(fb, _CORE, pubs, max_workers=1)
    assert set(out) == {"a", "b"} and fb.calls == 2


# --- two-phase affinity recompute in run_core (monkeypatched DB reads) ------
def test_run_core_affinity_lifts_sibling_paper(monkeypatch):
    """A co-author-confirmed paper makes the same author's other (weak) paper a
    candidate via the repeat-user prior — the compounding behavior."""
    from pipeline_cores import ingest, run, signals

    core = load_core("2")
    pubs = [
        {"pmid": "100", "title": "MRI paper by core staff", "abstract": ""},
        {"pmid": "200", "title": "Weak paper, same author", "abstract": ""},
    ]
    # 100 is co-authored by core staff djb2001 -> auto-confirmed.
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p: {"100": ["djb2001"]})
    # Both papers share author djb2001 on the byline.
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"100": ["djb2001"], "200": ["djb2001"]})
    # ...and djb2001 has 20 corpus papers, so one confirm is a 5% rate: aff:regular.
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"djb2001": 20})

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True)}
    assert recs["100"].status == STATUS_CONFIRMED
    # 200 has no direct signal; it inherits djb2001's affinity. One confirm out of 20
    # corpus papers is a 5% rate (aff:regular), so the sibling reaches the queue and
    # still sits well below the paper that was actually confirmed.
    assert recs["200"].status == STATUS_CANDIDATE
    assert 1 / (1 + math.exp(-PRIOR_LOGIT)) < recs["200"].likelihood < recs["100"].likelihood
    # ...and it is the DENOMINATOR doing that, not the count: the same single confirm by
    # an author with 100 corpus papers is a trace and stays out of the queue. On the old
    # curve both were 0.45 — the count knew nothing about who the author was.
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"djb2001": 100})
    prolific = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                                scored_at="t", engine=None, dry_run=True)}
    assert prolific["200"].status == STATUS_BELOW
    assert prolific["200"].likelihood < recs["200"].likelihood


def test_run_core_marks_a_curated_client_on_the_byline(monkeypatch):
    """Wiring, not scoring. The weight is 0.00, so no status or likelihood assertion
    anywhere can notice this signal never being populated — this is the only thing that
    would catch the key being declared and never connected."""
    from pipeline_cores import ingest, run, signals

    core = load_core("2")
    core.clients = ["cwid1"]
    pubs = [{"pmid": "100", "title": "client paper", "abstract": ""},
            {"pmid": "200", "title": "someone else's paper", "abstract": ""}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines",
                        lambda e, p: {"100": ["cwid1", "cwid9"], "200": ["cwid9"]})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True)}
    assert recs["100"].signals.client_cwids == ["cwid1"]     # only the curated one
    assert recs["200"].signals.client_cwids == []

    # CWID casing is not stable across sources, and the curated list is hand-typed,
    # so the match is case-insensitive on both sides (load_cores lowercases the list;
    # run_core lowercases the byline).
    core.clients = ["cwid1"]
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"100": ["CWID1"], "200": []})
    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True)}
    assert recs["100"].signals.client_cwids == ["CWID1"]     # byline casing preserved


def test_run_core_dry_run_skips_the_dynamodb_curated_client_read(monkeypatch):
    """--dry-run needs no AWS (README): the curated-client GetItem must not fire,
    let alone be allowed to fail the run, when dry_run=True."""
    from pipeline_cores import ingest, run, signals
    import pipeline_cores.persist as persist

    core = load_core("2")
    core.clients = ["cwid1"]
    pubs = [{"pmid": "100", "title": "client paper", "abstract": ""}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"100": ["cwid1"]})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})

    def _boom(*a, **k):
        raise AssertionError("get_curated_clients must not be called under dry_run")
    monkeypatch.setattr(persist, "get_curated_clients", _boom)

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True)}
    # The YAML-only client still marks — dry_run only removes the DynamoDB half.
    assert recs["100"].signals.client_cwids == ["cwid1"]


def test_run_core_unions_yaml_and_dynamodb_curated_clients(monkeypatch):
    """The engine half of #383: a CWID SPS curated into DynamoDB (not YAML) still
    marks the byline, alongside a YAML-curated one."""
    from pipeline_cores import ingest, run, signals
    import pipeline_cores.persist as persist

    core = load_core("2")
    core.clients = ["cwid1"]
    pubs = [{"pmid": "100", "title": "yaml client paper", "abstract": ""},
            {"pmid": "200", "title": "dynamo client paper", "abstract": ""},
            {"pmid": "300", "title": "nobody's paper", "abstract": ""}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {
        "100": ["cwid1"], "200": ["cwid2"], "300": ["cwid9"],
    })
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})
    monkeypatch.setattr(persist, "get_curated_clients", lambda core_id: {"cwid2"})

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=False)}
    assert recs["100"].signals.client_cwids == ["cwid1"]     # YAML-curated
    assert recs["200"].signals.client_cwids == ["cwid2"]     # DynamoDB-curated
    assert recs["300"].signals.client_cwids == []            # in neither list


def test_ack_signal_end_to_end_from_cached_fulltext(tmp_path):
    """fulltext cache -> acknowledgement_signal -> combine == confirmed."""
    from pipeline_cores.fulltext import PmcFullTextClient
    client = PmcFullTextClient(cache_dir=tmp_path)
    (tmp_path / "777.xml").write_text(
        "<body>data acquired at the Citigroup Biomedical Imaging Center of Weill Cornell</body>",
        encoding="utf-8")
    sig = acknowledgement_signal(client.get("777"), load_core("2"))
    rec = combine("777", "2", sig)
    assert rec.status == STATUS_CONFIRMED and sig.ack_alias == "Citigroup Biomedical Imaging Center"


# --- #312 affinity prior: one scan for all cores, loud on failure -----------
def test_main_scans_prior_usage_once_for_all_cores(monkeypatch):
    """The cross-run affinity prior is scanned ONCE (grouped by core_id in
    memory), not once per core — one full-table Scan instead of len(cores)."""
    from unittest.mock import MagicMock
    from pipeline_cores import ingest, run
    import pipeline_cores.persist as persist
    import utils.db as db

    monkeypatch.setattr(db, "get_engine", lambda: MagicMock(name="engine"))
    monkeypatch.setattr(ingest, "fetch_publications", lambda e, pmids=None, limit=None: [{"pmid": "1"}])
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(run, "run_core", lambda *a, **k: [])
    scan = MagicMock(return_value=[])
    monkeypatch.setattr(persist, "scan_prior_core_usage", scan)

    assert len(load_cores()) > 1  # otherwise the per-core/once distinction is moot
    run.main(["--with-affinity", "--dry-run"])
    assert scan.call_count == 1


def test_scan_prior_core_usage_logs_loudly_on_error(caplog):
    """A scan failure must not silently zero the affinity prior: it logs loudly
    and still degrades to [] so the run continues on its own confirmations."""
    import logging
    from pipeline_cores import persist

    class _BoomClient:
        def scan(self, **kwargs):
            raise RuntimeError("throttled")

    with caplog.at_level(logging.ERROR):
        out = persist.scan_prior_core_usage("2", client=_BoomClient())
    assert out == []
    assert "scan_prior_core_usage failed" in caplog.text


# --- get_curated_clients: SPS's "Known clients" panel, read from DynamoDB --
class _FakeGetItemDynamo:
    def __init__(self, item=None, raise_exc=None):
        self._item = item
        self._raise_exc = raise_exc

    def get_item(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return {"Item": self._item} if self._item is not None else {}


def test_get_curated_clients_returns_the_lowercased_dynamo_set():
    from pipeline_cores import persist

    item = {"client_cwids": {"L": [{"S": "CWID1"}, {"S": "cwid2"}]}}
    out = persist.get_curated_clients("2", client=_FakeGetItemDynamo(item=item))
    assert out == {"cwid1", "cwid2"}


def test_get_curated_clients_returns_empty_set_with_no_item(caplog):
    """No curated-clients item is the NORMAL state for 9 of 10 cores — it must
    not warn."""
    import logging
    from pipeline_cores import persist

    with caplog.at_level(logging.WARNING):
        out = persist.get_curated_clients("2", client=_FakeGetItemDynamo(item=None))
    assert out == set()
    assert not [r for r in caplog.records if "get_curated_clients" in r.message]


def test_get_curated_clients_returns_empty_set_with_missing_attribute(caplog):
    """An item with no (or an empty) client_cwids attribute is also normal —
    it must not warn either."""
    import logging
    from pipeline_cores import persist

    with caplog.at_level(logging.WARNING):
        out = persist.get_curated_clients(
            "2", client=_FakeGetItemDynamo(item={"client_cwids": {"L": []}}),
        )
    assert out == set()
    assert not [r for r in caplog.records if "get_curated_clients" in r.message]


def test_get_curated_clients_returns_empty_set_and_warns_on_error(caplog):
    import logging
    from pipeline_cores import persist

    with caplog.at_level(logging.WARNING):
        out = persist.get_curated_clients(
            "2", client=_FakeGetItemDynamo(raise_exc=RuntimeError("throttled")),
        )
    assert out == set()
    assert "get_curated_clients" in caplog.text
    assert "RuntimeError" in caplog.text


def test_get_curated_clients_returns_empty_set_and_warns_on_client_error(caplog):
    """A botocore ClientError (e.g. throttling) is the same error path as any
    other exception: warn and degrade to an empty set."""
    import logging
    from botocore.exceptions import ClientError
    from pipeline_cores import persist

    exc = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "GetItem",
    )
    with caplog.at_level(logging.WARNING):
        out = persist.get_curated_clients("2", client=_FakeGetItemDynamo(raise_exc=exc))
    assert out == set()
    assert "get_curated_clients" in caplog.text
    assert "ClientError" in caplog.text


# --- put_core_usage must not clobber the attributes batch_screen owns ------
class _FakeUpdateDynamo:
    """Applies SET/REMOVE UpdateExpressions to an in-memory store; explodes on a Put.

    A whole-item Put is the bug under test, so the double refuses one outright rather
    than letting the assertions below decide.
    """
    def __init__(self, store=None):
        self.store = dict(store or {})

    def batch_write_item(self, **kw):
        raise AssertionError("put_core_usage must not write whole items")

    put_item = batch_write_item

    def update_item(self, TableName, Key, UpdateExpression,
                    ExpressionAttributeNames, ExpressionAttributeValues):
        item = dict(self.store.get((Key["PK"]["S"], Key["SK"]["S"]), {}))
        sets, _, removes = UpdateExpression.partition(" REMOVE ")
        for pair in sets[len("SET "):].split(","):
            n, v = (s.strip() for s in pair.split("="))
            item[ExpressionAttributeNames[n]] = ExpressionAttributeValues[v]
        for n in (s.strip() for s in removes.split(",") if s.strip()):
            item.pop(ExpressionAttributeNames[n], None)
        self.store[(Key["PK"]["S"], Key["SK"]["S"])] = item
        return {}


_SCREENED_ROW = {
    "PK": {"S": "PUB#7"}, "SK": {"S": "CORE#2"}, "status": {"S": STATUS_CANDIDATE},
    "prefilter_prior": {"N": "0.31"}, "screen_confidence": {"N": "8"},
    "screen_band": {"S": "candidate"}, "screen_version": {"S": "sv"},
    "prefilter_version": {"S": "pv"}, "run_mode": {"S": "batch_screen"},
}


def _run_py_write(seed, signals):
    from pipeline_cores.models import CoreUsageRecord
    from pipeline_cores.persist import put_core_usage

    db = _FakeUpdateDynamo({("PUB#7", "CORE#2"): dict(seed)})
    rec = CoreUsageRecord(pmid="7", core_id="2", likelihood=0.9,
                          status=STATUS_CONFIRMED, signals=signals, scored_at="t")
    assert put_core_usage([rec], client=db) == 1
    return db.store[("PUB#7", "CORE#2")]


def test_put_core_usage_leaves_batch_screen_attributes_alone():
    """run.py owns 12 attributes; the other six on the item belong to batch_screen.
    While this was a BatchWriteItem PutRequest (a full-item replace) a run.py write
    over a screened pair destroyed all six — including prefilter_prior, which SPS
    renders as the review queue's topicalPrior chip on 8,656 live rows."""
    item = _run_py_write(_SCREENED_ROW, SignalResult())
    assert item["prefilter_prior"] == {"N": "0.31"}         # the chip survives
    assert item["screen_confidence"] == {"N": "8"}
    assert item["screen_band"] == {"S": "candidate"}
    assert item["screen_version"] == {"S": "sv"}
    assert item["prefilter_version"] == {"S": "pv"}
    assert item["run_mode"] == {"S": "batch_screen"}
    assert item["status"] == {"S": STATUS_CONFIRMED}        # ...and run.py's own land
    assert item["likelihood"] == {"N": "0.9"}


def test_put_core_usage_clears_an_optional_it_did_not_produce_this_run():
    """The other half: a run.py-owned optional that is absent THIS run must go, not
    linger. A previous run's llm_rationale left on a pair scored without the LLM is
    stale evidence reading in the queue as fresh."""
    seed = dict(_SCREENED_ROW, llm_score={"N": "8"},
                llm_rationale={"S": "used 3T MRI at the core"},
                ack_alias={"S": "CBIC"}, author_affinity={"N": "0.4"})
    item = _run_py_write(seed, SignalResult())
    assert "llm_score" not in item and "llm_rationale" not in item
    assert "ack_alias" not in item and "author_affinity" not in item
    assert item["prefilter_prior"] == {"N": "0.31"}         # still not ours to clear


# ---------------------------------------------------------------------------
# suggest_aliases — alias discovery from confirmed papers (inverse of signal 3)
# ---------------------------------------------------------------------------
def test_suggest_aliases_finds_the_recurring_facility_phrase():
    from pipeline_cores.suggest_aliases import ack_text, phrases, rank

    xml = (
        "<article><body>methods here</body>"
        "<ack><title>Acknowledgements</title><p>We thank the Research Informatics "
        "Core (RIC) at Weill Cornell Medicine, funded by the National Institutes "
        "of Health.</p></ack></article>"
    )
    text = ack_text(xml)
    assert "Research Informatics Core" in text
    assert "methods here" not in text          # body is NOT mined, only the <ack>

    found = phrases(text)
    assert "Research Informatics Core" in found
    assert "RIC" in found                       # parenthesised acronym
    assert "National Institutes of Health" not in found   # generic boilerplate dropped

    # document frequency is the gate: 1 paper is boilerplate, 2 is a pattern
    docs = {"Research Informatics Core": {"1", "2"}, "One Off Center": {"1"}}
    ranked = rank(docs, min_docs=2, foreign_aliases=set())
    assert [r[0] for r in ranked] == ["Research Informatics Core"]

    # a phrase owned by another core in the dictionary is not this core's alias
    assert rank(docs, 2, {"research informatics core"}) == []


def test_suggest_aliases_collapses_nested_subspans():
    from pipeline_cores.suggest_aliases import rank

    # the miner emits every window; same doc set => only the longest survives
    docs = {
        "Citigroup Biomedical Imaging Center": {"1", "2", "3"},
        "Biomedical Imaging Center": {"1", "2", "3"},
        "Imaging Center": {"1", "2", "3", "4"},   # MORE papers: a real short form
    }
    out = [r[0] for r in rank(docs, 2, set())]
    assert out == ["Imaging Center", "Citigroup Biomedical Imaging Center"]


def test_suggest_aliases_caps_phrase_length_on_a_long_run():
    from pipeline_cores.suggest_aliases import phrases, MAX_PHRASE_WORDS

    # an <ack> that swallowed an affiliations block: one very long capitalised run
    text = " ".join(["Center"] + [f"Word{i}" for i in range(60)])
    out = phrases(text)
    assert out, "should still emit something"
    assert max(len(p.split()) for p in out) <= MAX_PHRASE_WORDS


# ---------------------------------------------------------------------------
# pmc_search — signal 3 without the corpus prefetch
# ---------------------------------------------------------------------------
def test_alias_loader_returns_text_only_for_alias_hits():
    """The loader is the whole point: full text for the papers PMC says name the
    core, "" for everyone else — so run_core scans 25 papers, not 80,203."""
    from pipeline_cores.models import CoreDefinition
    from pipeline_cores.pmc_search import make_alias_loader

    class FakeClient:
        def __init__(self):
            self.asked = []

        def get(self, pmid):
            self.asked.append(pmid)
            return f"body of {pmid}"

    client = FakeClient()
    core = CoreDefinition(core_id="14", name="RI", aliases=["Architecture for Research Computing"])
    loader = make_alias_loader(core, client=client, hits={"111", "222"})

    assert loader("111") == "body of 111"
    assert loader("999") == ""                 # not an alias hit -> never fetched
    assert client.asked == ["111"]             # one fetch, not two


def test_pmids_naming_skips_acronym_aliases(monkeypatch):
    """esearch has no case-sensitive mode, so an acronym alias would return the
    exact noise signals.py's word-boundary rule exists to prevent."""
    from pipeline_cores import pmc_search
    from pipeline_cores.models import CoreDefinition

    searched = []
    monkeypatch.setattr(pmc_search, "esearch_pmc", lambda p, **kw: searched.append(p) or [])
    monkeypatch.setattr(pmc_search, "pmcids_to_pmids", lambda ids, **kw: set())

    core = CoreDefinition(core_id="14", name="RI",
                          aliases=["Architecture for Research Computing", "ARCH", "RIC"])
    pmc_search.pmids_naming(core)
    assert searched == ["Architecture for Research Computing"]


def test_pmids_naming_survives_an_ncbi_failure(monkeypatch):
    """Signal 3 is a bonus confirmer — one bad alias must not fail the run."""
    from pipeline_cores import pmc_search
    from pipeline_cores.models import CoreDefinition

    def flaky(phrase, **kw):
        if phrase == "Bad Alias":
            raise TimeoutError("ncbi hiccup")
        return ["7392233"]

    monkeypatch.setattr(pmc_search, "esearch_pmc", flaky)
    monkeypatch.setattr(pmc_search, "pmcids_to_pmids", lambda ids, **kw: {"32997716"})

    core = CoreDefinition(core_id="14", name="RI", aliases=["Bad Alias", "Good Alias"])
    assert pmc_search.pmids_naming(core) == {"32997716"}


def _esearch_page(ids, count):
    return {"esearchresult": {"count": str(count), "idlist": [str(i) for i in ids]}}


def test_esearch_paginates_to_the_true_count(monkeypatch):
    """esearch reports the real total in `count` and returns at most RETMAX ids.
    Returning page 1 and calling it the answer dropped 98% of "Flow Cytometry
    Core" (count=23544) — in NCBI's default sort order, so not even the same 98%
    on the next run."""
    from pipeline_cores import pmc_search

    starts = []

    def fake_get(url, params, timeout):
        starts.append(params["retstart"])
        return _esearch_page(range(params["retstart"],
                                   min(params["retstart"] + params["retmax"], 1200)), 1200)

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    ids = pmc_search.esearch_pmc("Epigenomics Core")
    assert starts == [0, 500, 1000]                 # walks retstart, stops at the count
    assert len(ids) == len(set(ids)) == 1200        # every page kept, none duplicated


def test_esearch_ceiling_trips_loudly_instead_of_looping(monkeypatch, caplog):
    """A pathological alias must stop, and must never stop QUIETLY: the warning
    names the alias and the count so a truncated signal 3 is visible in the log."""
    import logging
    from pipeline_cores import pmc_search

    starts = []

    def fake_get(url, params, timeout):
        starts.append(params["retstart"])
        return _esearch_page(range(params["retstart"], params["retstart"] + params["retmax"]), 23544)

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    monkeypatch.setattr(pmc_search, "MAX_IDS", 1000)
    with caplog.at_level(logging.WARNING):
        ids = pmc_search.esearch_pmc("Flow Cytometry Core")
    assert starts == [0, 500] and len(ids) == 1000  # capped, not spinning to 23544
    assert "Flow Cytometry Core" in caplog.text and "23544" in caplog.text


def test_esearch_count_refuses_a_phrase_pmc_could_not_run(monkeypatch):
    """esearch does not error on an unfindable phrase — it silently answers a
    term-ANDed/MeSH-expanded query instead and reports THAT count. Scoring it as
    alias specificity mispriced 9 of 55 real aliases, pushing 4 into `generic`
    (-5.07) and 5 into `distinctive` (+4.35) on a number measuring nothing.
    """
    from pipeline_cores import pmc_search

    def fake_get(url, params, timeout):
        return {"esearchresult": {"count": "7221", "warninglist": {
            "quotedphrasesnotfound": ['"Davis Cancer Immune Monitoring Core"']}}}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.esearch_count("Davis Cancer Immune Monitoring Core") is None


def test_esearch_count_returns_a_real_phrase_count(monkeypatch):
    from pipeline_cores import pmc_search
    monkeypatch.setattr(pmc_search, "_get_json",
                        lambda u, p, t: {"esearchresult": {"count": "197", "warninglist": {}}})
    assert pmc_search.esearch_count("Citigroup Biomedical Imaging Center") == 197


def test_refresh_alias_hits_leaves_an_unrunnable_phrase_uncached():
    """An uncached alias reads as ack.spec:unknown (0.00), not as a fabricated bucket."""
    from pipeline_cores import refresh_alias_hits
    from pipeline_cores.models import CoreDefinition

    core = CoreDefinition(core_id="9", name="X", aliases=["Real Core Name", "Unrunnable Core"])
    hits = refresh_alias_hits.fetch_hits(
        core, counter=lambda a: 42 if a == "Real Core Name" else None)
    assert hits == {"Real Core Name": 42}


def test_esearch_ceiling_respects_ncbis_own_retstart_limit(monkeypatch):
    """MAX_IDS must stay at or under NCBI's hard retstart<=9998 cap.

    A request past it returns HTTP 200 whose JSON body carries a raw newline, so
    json.load raises and pmids_naming's per-alias `except Exception` discards every
    id already collected -- 0 ids for an alias the old single-page code got 500 for.
    This fake serves indices 0..9998 and refuses beyond, exactly as NCBI does, and
    pins the SHIPPED MAX_IDS rather than monkeypatching it away.
    """
    import json as _json
    from pipeline_cores import pmc_search

    starts = []

    def fake_get(url, params, timeout):
        starts.append(params["retstart"])
        if params["retstart"] > 9998:
            raise _json.JSONDecodeError("Invalid control character", "", 0)
        hi = min(params["retstart"] + params["retmax"], 9999)  # NCBI serves 0..9998
        return _esearch_page(range(params["retstart"], hi), 23544)

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    ids = pmc_search.esearch_pmc("Flow Cytometry Core")  # real MAX_IDS, not patched

    assert max(starts) <= 9998, f"asked NCBI for retstart={max(starts)}; it refuses >9998"
    assert len(ids) == 9999, len(ids)  # everything NCBI will serve, not 0 and not 500


def test_esearch_single_page_result_is_one_call(monkeypatch):
    """The common case — a distinctive alias whose whole result set fits one page
    — must still cost exactly one request."""
    from pipeline_cores import pmc_search

    starts = []

    def fake_get(url, params, timeout):
        starts.append(params["retstart"])
        return _esearch_page(range(25), 25)

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.esearch_pmc("Architecture for Research Computing") == [str(i) for i in range(25)]
    assert starts == [0]


def test_pmcids_to_pmids_chunks_at_the_idconv_limit(monkeypatch):
    """idconv takes 200 ids per request; now that esearch can hand it thousands,
    one giant GET is a 414 that pmids_naming would swallow as "no hits"."""
    from pipeline_cores import pmc_search

    sizes = []

    def fake_get(url, params, timeout):
        ids = params["ids"].split(",")
        sizes.append(len(ids))
        return {"records": [{"pmid": i[len("PMC"):]} for i in ids]}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    out = pmc_search.pmcids_to_pmids([str(i) for i in range(500)])
    assert sizes == [200, 200, 100]
    assert out == {str(i) for i in range(500)}


def test_get_json_sends_the_api_key_and_paces(monkeypatch):
    """Unpaced and unkeyed, 2 requests per alias × 57 aliases blew past NCBI's
    3-req/s anonymous limit: 18 of 57 came back "HTTP Error 400" and each one was
    logged as a warning and dropped."""
    import io
    import urllib.request
    from pipeline_cores import pmc_search

    monkeypatch.setenv("PUBMED_API_KEY", "deadbeef")
    urls, slept = [], []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: urls.append(url) or io.BytesIO(b'{"records": []}'))
    monkeypatch.setattr(pmc_search.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(pmc_search, "_last_request", 0.0)

    pmc_search.pmcids_to_pmids(["1"])
    pmc_search.pmcids_to_pmids(["2"])
    assert all("api_key=deadbeef" in u for u in urls)
    assert slept and 0 < slept[0] <= 0.11    # the keyed 10 req/s cadence, not 0.34


def test_read_pmids_file_ignores_blanks_and_comments(tmp_path):
    """The pool file is hand-edited between runs — a stray comment or blank line
    must not become a PMID the corpus query then fails to match."""
    from pipeline_cores.run import read_pmids_file

    f = tmp_path / "pool.txt"
    f.write_text("# core 14 candidates, 2026-09-03\n39919677\n\n  38212178  \n33862230 # already claimed\n")
    assert read_pmids_file(str(f)) == ["39919677", "38212178", "33862230"]


def test_prior_affinity_fetches_bylines_for_papers_outside_this_run(monkeypatch):
    """A --pmids-file run scores a POOL, so every prior confirmed paper is outside
    it. Treating those as byline-less zeroes the entire repeat-user prior."""
    from pipeline_cores import ingest, run
    import pipeline_cores.persist as persist

    monkeypatch.setattr(persist, "scan_prior_core_usage",
                        lambda cid: [{"pmid": "999", "core_id": "14"}])
    fetched = []

    def fake_bylines(engine, pmids):
        fetched.append(list(pmids))
        return {"999": ["evs2008", "thc2015"]}

    monkeypatch.setattr(ingest, "fetch_author_bylines", fake_bylines)
    # The numerator is gated to the corpus before anything is counted; here everything
    # passes, so this test still measures only the out-of-scope byline fetch.
    monkeypatch.setattr(ingest, "filter_corpus_pmids", lambda engine, pmids: set(pmids))

    counts = run.load_prior_user_counts("14", {"111": ["someone"]}, enabled=True, engine=object())
    assert fetched == [["999"]]                 # the out-of-scope prior was fetched
    assert counts["evs2008"]["14"] == 1
    assert counts["thc2015"]["14"] == 1

    # without an engine it degrades to the old behaviour rather than crashing
    assert run.load_prior_user_counts("14", {}, enabled=True) == {}


def test_prior_affinity_counts_only_papers_inside_the_scored_corpus(monkeypatch):
    """The rate's NUMERATOR and DENOMINATOR must live on the same corpus.

    DynamoDB holds confirmed/claimed rows for papers this pipeline does not score
    (pre-2020, Letter, Preprint, Erratum, Case Report, Comment — 19 of core 14's 43).
    Counting those against a corpus-restricted total inflated the rate into `aff:core`,
    which alone clears the confirm bar: cwid `ccole` measured 8/11 = 0.727 where the
    same-corpus rate is 2/11 = 0.182. It also drove 7 of the 8 `aff:core` rows on the
    live core-14 queue, so this is the gate that keeps the shipped weights meaning what
    they were fitted to mean."""
    from pipeline_cores import ingest, run
    import pipeline_cores.persist as persist

    monkeypatch.setattr(persist, "scan_prior_core_usage", lambda cid: [
        {"pmid": "111", "core_id": "14"},        # in the corpus
        {"pmid": "222", "core_id": "14"},        # a 2015 Letter — scored by nothing
    ])
    monkeypatch.setattr(ingest, "filter_corpus_pmids", lambda engine, pmids: {"111"})
    monkeypatch.setattr(ingest, "fetch_author_bylines",
                        lambda engine, pmids: {p: ["ccole"] for p in pmids})

    counts = run.load_prior_user_counts("14", {}, enabled=True, engine=object())
    assert counts["ccole"]["14"] == 1            # not 2 — the out-of-corpus row is gated out


# ---------------------------------------------------------------------------
# Ack EVIDENCE — alias specificity / institution / section (SPEC phase 1).
# Extracted here, deliberately unpriced: combine() must score the same either way
# until the weights are fitted.
# ---------------------------------------------------------------------------
_SHARED = CoreDefinition(
    core_id="4", name="Flow Cytometry",
    aliases=["Flow Cytometry Core"],
    alias_hits={"Flow Cytometry Core": 23544},
)


def test_ack_records_alias_specificity_from_the_dictionary():
    """An alias's global PMC hit count predicts precision (r=-0.852 vs home%), so
    the match carries it. An uncached alias stays None — never a made-up number."""
    r = acknowledgement_signal("sorted in the Flow Cytometry Core at Weill Cornell", _SHARED)
    assert r.ack_alias_hits == 23544
    assert acknowledgement_signal("scanned at the CBIC core", _CORE).ack_alias_hits is None


def test_institution_home_for_wcm_and_for_a_tri_i_partner():
    """Cores are genuinely SHARED: requiring a Weill Cornell affiliation would
    discard ~47% of core 4's legitimate hits, so a partner name is home."""
    for text in (
        "...used the Flow Cytometry Core at Weill Cornell Medicine for sorting",
        "...used the Flow Cytometry Core of the Rockefeller University for sorting",
        "...used the Flow Cytometry Core at NYP for sorting",
    ):
        assert acknowledgement_signal(text, _SHARED).ack_institution == "home", text


def test_institution_partner_optout_makes_the_same_window_other():
    """The allowlist is per core, so a core that is NOT shared opts out with []."""
    solo = CoreDefinition(core_id="4", name="Flow", aliases=["Flow Cytometry Core"],
                          partner_institutions=[])
    text = "...used the Flow Cytometry Core of the Rockefeller University for sorting"
    assert acknowledgement_signal(text, solo).ack_institution == "other"


def test_institution_other_when_a_different_institution_is_named():
    text = "Cells were sorted at the Flow Cytometry Core, Stanford University School of Medicine."
    assert acknowledgement_signal(text, _SHARED).ack_institution == "other"


def test_institution_none_when_nobody_is_named():
    """'none' is NOT a synonym for 'other': ARCH is 43% none with 0% other, so the
    weighting conditions this bucket on alias specificity rather than flat-rating it."""
    text = "Samples were acquired on the Flow Cytometry Core sorter."
    assert acknowledgement_signal(text, _SHARED).ack_institution == "none"


def test_institution_ignores_funder_boilerplate():
    """The NIH is a funder, not a host. Counting it would read almost every
    acknowledgements window as 'a DIFFERENT institution'."""
    text = ("The Flow Cytometry Core is supported by the National Institutes of Health "
            "and the National Cancer Institute under award P30CA000000.")
    assert acknowledgement_signal(text, _SHARED).ack_institution == "none"


def test_institution_home_when_the_alias_itself_names_wcm():
    """Several cores list a spelled-out alias; that alias IS the affiliation."""
    core = CoreDefinition(core_id="4", name="Flow",
                          aliases=["Flow Cytometry Core Facility, Weill Cornell Medicine"])
    text = "Sorting used the Flow Cytometry Core Facility, Weill Cornell Medicine."
    assert acknowledgement_signal(text, core).ack_institution == "home"


def test_institution_ignores_institution_words_inside_the_alias_itself():
    """The core's own name is not somebody else's institution."""
    core = CoreDefinition(core_id="7", name="IPM", aliases=["Institute for Precision Medicine Biobank"])
    text = "Specimens came from the Institute for Precision Medicine Biobank."
    assert acknowledgement_signal(text, core).ack_institution == "none"


_JATS = (
    "<article><body>"
    "<sec><title>Materials and Methods</title>"
    "<p>Cells were sorted in the Flow Cytometry Core.</p></sec>"
    "<sec><title>Results</title><p>Data from the Epigenomics Core agreed.</p></sec>"
    "</body><back><ack><p>We thank the Genomics Core.</p></ack></back></article>"
)


def test_section_reads_ack_methods_and_body_out_of_jats():
    assert match_section(_JATS, "Genomics Core") == "ack"
    assert match_section(_JATS, "Flow Cytometry Core") == "methods"
    assert match_section(_JATS, "Epigenomics Core") == "body"


def test_section_degrades_to_blank_without_xml_and_changes_no_score():
    """Feature 3 is EXTRACTED, not priced — plain text alone leaves it unknown, and
    the likelihood is identical with and without the structure."""
    plain = "We thank the Flow Cytometry Core at Weill Cornell Medicine."
    xml = f"<article><back><ack><p>{plain}</p></ack></back></article>"
    assert acknowledgement_signal(plain, _SHARED).ack_section == ""
    assert acknowledgement_signal(plain, _SHARED, xml=xml).ack_section == "ack"
    assert (combine("1", "4", acknowledgement_signal(plain, _SHARED)).likelihood
            == combine("1", "4", acknowledgement_signal(plain, _SHARED, xml=xml)).likelihood)


# --- dictionary: both new keys are optional --------------------------------
def _write_dict(tmp_path, body: str):
    p = tmp_path / "core_dictionary.yaml"
    p.write_text(body)
    return p


def test_dictionary_defaults_when_the_new_keys_are_absent(tmp_path):
    from pipeline_cores.models import DEFAULT_PARTNER_INSTITUTIONS
    p = _write_dict(tmp_path, 'cores:\n  - core_id: "9"\n    name: Test\n    aliases: ["Test Core"]\n')
    c = load_cores(p)[0]
    assert c.alias_hits == {}                                   # nothing cached yet
    assert c.clients == []                                      # no curated client list
    assert c.partner_institutions == DEFAULT_PARTNER_INSTITUTIONS


def test_dictionary_reads_alias_hits_and_an_explicit_partner_optout(tmp_path):
    p = _write_dict(tmp_path,
                    'cores:\n  - core_id: "9"\n    name: Test\n    aliases: ["Test Core"]\n'
                    '    partner_institutions: []\n    alias_hits:\n      "Test Core": 17\n')
    c = load_cores(p)[0]
    assert c.alias_hits == {"Test Core": 17}
    assert c.partner_institutions == []                          # opted out, not re-defaulted


def test_dictionary_reads_a_curated_client_list_as_cwid_strings(tmp_path):
    """CWIDs only, never names and never surname-matched (511 resolved "Voss" rows are
    the oncologist mhv9001, not CBIC's hev2006). Coerced to str so a cwid that YAML
    happens to read as a number still compares against a byline."""
    p = _write_dict(tmp_path,
                    'cores:\n  - core_id: "9"\n    name: Test\n    aliases: ["Test Core"]\n'
                    '    clients: [cwid1, 4242]\n')
    assert load_cores(p)[0].clients == ["4242", "cwid1"]


def test_dictionary_normalises_hand_typed_client_cwids(tmp_path):
    """Unlike `staff`, this list is hand-typed into YAML, so a stray capital or a
    trailing space would match no byline, fire no signal, and raise nothing anywhere.
    Case-folded, stripped, de-duplicated and empties dropped at load."""
    p = _write_dict(tmp_path,
                    'cores:\n  - core_id: "9"\n    name: Test\n    aliases: ["Test Core"]\n'
                    '    clients: ["ABC1234", " abc1234 ", "abc1234", "", "  ", "XyZ9"]\n')
    assert load_cores(p)[0].clients == ["abc1234", "xyz9"]


# The nine aliases PMC cannot run as a phrase. esearch does not error on one: it
# silently answers a term-ANDed/MeSH-expanded query and reports THAT count, which
# measures nothing about the alias -- so refresh_alias_hits.fetch_hits leaves them
# uncached on purpose and they read as ack.spec:unknown (weight 0.00). Pinned rather
# than tolerated, so an alias that loses its count for any OTHER reason still fails.
# Probed 2026-09-04 via pipeline_cores.pmc_search.esearch_count; re-probe with
# `python3 -m pipeline_cores.refresh_alias_hits` if this list moves.
_PMC_UNRUNNABLE = frozenset({
    "Citigroup Biomedical Imaging Core Facility",
    "Applied Bioinformatics Core Facility",
    "Applied Bioinformatics Core (ABC)",
    "Institutional Biorepository Core (IBC)",
    "Metabolic Phenotyping Center (MPC)",
    "Advanced Biomolecular Analysis Core (ABAC)",
    "WCM Advanced Biomolecular Analysis Core",
    "Ellen and Gary Davis Immune Monitoring Core",
    "Davis Cancer Immune Monitoring Core",
})


def test_shipped_dictionary_carries_a_hit_count_for_every_searchable_alias():
    """`refresh_alias_hits --write` has been run, so specificity is live rather than
    everything falling back to the "unknown" bucket. Acronyms are absent by design
    (esearch has no case-sensitive mode), and so are the _PMC_UNRUNNABLE phrases."""
    from pipeline_cores.signals import _ACRONYM
    for c in load_cores():
        assert "Rockefeller" in c.partner_institutions
        assert c.confirm_threshold is None and c.triage_threshold is None   # no core opts out yet
        searchable = [a for a in c.aliases
                      if not _ACRONYM.match(a) and a not in _PMC_UNRUNNABLE]
        assert sorted(c.alias_hits) == sorted(searchable), c.core_id
        assert all(n >= 0 for n in c.alias_hits.values())


# --- refresh_alias_hits — cache the specificity counts, no network ---------
def test_fetch_hits_skips_acronyms_and_survives_a_bad_alias():
    """esearch has no case-sensitive mode, so an acronym's count would be the very
    noise the matcher's word-boundary rule exists to exclude."""
    from pipeline_cores import refresh_alias_hits as rah

    asked = []

    def counter(alias):
        asked.append(alias)
        if alias == "Bad Alias":
            raise TimeoutError("ncbi hiccup")
        return 42

    core = CoreDefinition(core_id="14", name="RI", aliases=["Good Alias", "ARCH", "Bad Alias"])
    assert rah.fetch_hits(core, counter=counter) == {"Good Alias": 42}
    assert asked == ["Good Alias", "Bad Alias"]      # the acronym was never searched


def test_refresh_writes_the_block_in_place_and_keeps_every_comment(tmp_path):
    """safe_dump would strip the dictionary's comments — the project's IP — so the
    write is a surgical line edit, and re-running replaces rather than stacks."""
    from pipeline_cores import refresh_alias_hits as rah

    text = ('# header comment\n'
            'cores:\n'
            '  - core_id: "4"\n'
            '    name: Flow Cytometry\n'
            '    aliases:\n'
            '      - "Flow Cytometry Core"\n'
            '    # a comment inside the entry\n'
            '    staff: []\n')
    out = rah.write_hits(text, {"4": {"Flow Cytometry Core": 23544}})
    assert "# header comment" in out and "# a comment inside the entry" in out
    assert load_cores(_write_dict(tmp_path, out))[0].alias_hits == {"Flow Cytometry Core": 23544}

    again = rah.write_hits(out, {"4": {"Flow Cytometry Core": 25000}})
    assert again.count("alias_hits:") == 1                       # replaced, not stacked
    assert load_cores(_write_dict(tmp_path, again))[0].alias_hits == {"Flow Cytometry Core": 25000}


def test_refresh_leaves_a_core_with_no_counts_untouched():
    from pipeline_cores import refresh_alias_hits as rah

    text = 'cores:\n  - core_id: "4"\n    name: Flow\n    aliases:\n      - "FCC"\n'
    assert rah.write_hits(text, {"4": {}}) == text                # all-acronym core: nothing to cache


def test_esearch_count_is_one_request_with_no_id_list(monkeypatch):
    from pipeline_cores import pmc_search

    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return {"esearchresult": {"count": "23544", "idlist": []}}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.esearch_count("Flow Cytometry Core") == 23544
    assert len(calls) == 1 and calls[0]["retmax"] == 0            # count only, no paging


# ---------------------------------------------------------------------------
# The log-odds combiner (evidence-scoring SPEC phase 2). What the weights BUY:
# an ordering the 0.98/0.95 constants could not express, and a score that spreads.
# ---------------------------------------------------------------------------
def _ack(hits, institution, **kw):
    return SignalResult(ack_matched=True, ack_alias="x", ack_alias_hits=hits,
                        ack_institution=institution, **kw)


def test_score_is_monotone_in_the_evidence():
    """More evidence, and better evidence, both score higher — the property the
    constants destroyed (0.98 for any match at all, whatever it said)."""
    nothing = score(SignalResult())
    llm_only = score(SignalResult(llm_score=6))
    llm_plus_affinity = score(SignalResult(llm_score=6, author_affinity=0.85))
    plus_staff = score(SignalResult(llm_score=6, author_affinity=0.85, coauthor_cwids=["a"]))
    plus_ack = score(_ack(20, "home", llm_score=6, author_affinity=0.85, coauthor_cwids=["a"]))
    assert nothing < llm_only < llm_plus_affinity < plus_staff < plus_ack

    # ...and monotone WITHIN a signal, not only across signals.
    assert score(SignalResult(llm_score=3)) < score(SignalResult(llm_score=7))
    assert score(SignalResult(author_affinity=0.45)) < score(SignalResult(author_affinity=0.85))
    assert score(_ack(23544, "home")) < score(_ack(20, "home"))          # specificity
    assert score(_ack(20, "other")) < score(_ack(20, "none")) < score(_ack(20, "home"))


def test_a_specific_alias_at_home_outranks_a_generic_one_elsewhere():
    """The headline case the old combiner scored IDENTICALLY at 0.98: an
    `Architecture for Research Computing in Health` (20 global PMC hits, 100%
    precision measured) match beside Weill Cornell, against a `Flow Cytometry Core`
    (23,544 hits, 24%) match beside a different institution."""
    ours = combine("1", "14", _ack(20, "home"))
    theirs = combine("2", "4", _ack(23544, "other"))
    assert ours.likelihood > 0.99 and ours.status == STATUS_CONFIRMED
    assert theirs.likelihood < 0.01 and theirs.status == STATUS_BELOW
    # ...and a generic alias is not condemned FOR being generic. Beside a home
    # institution it survives (measured: -5.07 for the alias, +3.47 for the window),
    # which is the shared-core case a hard WCM gate would have thrown away.
    assert score(_ack(23544, "home")) > score(_ack(23544, "none")) > 0
    assert combine("3", "4", _ack(23544, "home")).status == STATUS_CONFIRMED
    assert combine("4", "4", _ack(23544, "none")).status == STATUS_BELOW


def test_section_is_extracted_but_contributes_nothing_yet():
    """SPEC decision 3: <ack> vs methods vs body is unmeasured, so it must move no
    score until a labelling pass prices it. Delete this test when it is fitted."""
    base = _ack(20, "home")
    for section in ("ack", "methods", "body", ""):
        assert score(_ack(20, "home", ack_section=section)) == score(base), section
    from pipeline_cores.combine import WEIGHTS
    assert {WEIGHTS[k] for k in WEIGHTS if k.startswith("sec:")} == {0.0}


def test_a_curated_client_is_extracted_but_priced_at_nothing():
    """The curated list ASSERTS a core's users where the affinity prior only INFERS
    them, so it fires on a core with no confirmations at all — but its weight is
    UNFITTED (there is no curated list to fit against yet), so like sec:* it must move
    no score until scripts/fit_evidence_weights.py prices it, and pricing it needs the
    client x aff:* overlap measured first. This test is what goes red the day someone
    gives it a weight without meaning to."""
    from pipeline_cores.combine import WEIGHTS
    on_list = SignalResult(client_cwids=["cwid1"])
    assert evidence_features(on_list) == ["client"]
    assert evidence_features(SignalResult(client_cwids=["cwid1", "cwid2"])) == ["client"]
    assert evidence_features(SignalResult()) == []          # not on the list -> no key at all
    assert WEIGHTS["client"] == 0.0
    assert score(on_list) == score(SignalResult())          # inert, on its own...
    rich = SignalResult(author_affinity=0.85, llm_score=7, coauthor_cwids=["djb2001"])
    with_client = SignalResult(author_affinity=0.85, llm_score=7, coauthor_cwids=["djb2001"],
                               client_cwids=["cwid1"])
    assert score(with_client) == score(rich)                # ...and beside every other signal


def test_explain_accounts_for_the_whole_score():
    """The queue has to be able to say WHY, so the parts must sum to the whole."""
    sig = _ack(20, "home", ack_section="ack", llm_score=8, author_affinity=0.85,
               coauthor_cwids=["a", "b"])
    total = PRIOR_LOGIT + sum(w for _, w in explain(sig))
    assert abs(score(sig) - 1 / (1 + math.exp(-total))) < 1e-12
    assert [f for f, _ in explain(sig)] == sorted(
        [f for f, _ in explain(sig)], key=lambda f: -abs(dict(explain(sig))[f]))


def test_explain_shows_the_rate_behind_the_affinity_bucket():
    """The bucket is what moves the score; the rate is what tells a reviewer whether
    this is the core's heaviest user or someone who just crossed the 0.70 edge. The
    line carries both, and the WEIGHT still comes from the bare key."""
    from pipeline_cores.combine import WEIGHTS
    lines = dict(explain(SignalResult(author_affinity=0.833)))
    assert lines["aff:core=0.833"] == WEIGHTS["aff:core"]
    # 3 dp, because 2 dp would print a 0.699 rate as "0.70" — the edge it is under.
    assert "aff:regular=0.699" in dict(explain(SignalResult(author_affinity=0.699)))


def test_per_core_thresholds_override_the_defaults():
    """Status is a threshold now, and decision 1 made it configurable per core."""
    sig = SignalResult(llm_score=9)                       # candidate at the defaults
    assert combine("1", "9", sig).status == STATUS_CANDIDATE
    strict = CoreDefinition(core_id="9", name="T", aliases=["T Core"], triage_threshold=0.99)
    assert combine("1", "9", sig, core=strict).status == STATUS_BELOW
    loose = CoreDefinition(core_id="9", name="T", aliases=["T Core"], confirm_threshold=0.5)
    assert combine("1", "9", sig, core=loose).status == STATUS_CONFIRMED
    # an explicit argument still beats the core's own override
    assert combine("1", "9", sig, core=strict, triage_threshold=0.1).status == STATUS_CANDIDATE


def test_the_score_spreads_where_noisy_or_saturated():
    """The whole point. The evidence a candidate pool actually varies on — an LLM
    score and a repeat-user prior — piles up against the top of the range under
    noisy-OR (that is how core 14's 347 candidates all landed in 0.802-0.985). In
    log-odds the same combinations use the whole range instead."""
    from pipeline_cores.combine import noisy_or
    combos = [SignalResult(llm_score=s, author_affinity=a)
              for s in range(1, 10) for a in (0.0, 0.45, 0.6, 0.6975, 0.78, 0.85)]
    old = sorted(round(noisy_or((c.llm_score or 0) / 10.0, c.author_affinity), 4) for c in combos)
    new = sorted(round(score(c), 4) for c in combos)
    assert max(old) - min(old) < 0.9 and min(old) > 0.09      # noisy-OR never goes near 0
    assert max(new) - min(new) > max(old) - min(old)
    # noisy-OR crowds the top — its MEDIAN combination is already 0.84, which is why
    # the pool was unrankable. Log-odds keeps the median off the ceiling.
    assert statistics.median(old) > 0.8
    assert 0.3 < statistics.median(new) < 0.8
    # That upper bound was 0.7 (median 0.570) while affinity was a slope. Fitting the
    # rate into buckets moved this grid's median to 0.746, because five of its six
    # affinity values are >= 0.45 and land in the two most favourable cells — a grid of
    # legacy STRENGTHS over-represents heavy core users relative to a live queue, where
    # most bylines have no history with the core at all and earn no key.
    # NOT asserted: that log-odds yields MORE distinct values. It does not here (27 vs
    # 51) and the gap widened, because three fitted buckets deliberately collapse 39
    # observed rates. Distinct-count is the wrong yardstick for spread; WHERE the mass
    # sits is the right one, which is what the assertions above pin.
