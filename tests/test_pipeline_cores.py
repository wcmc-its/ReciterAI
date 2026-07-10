"""Unit tests for the deterministic, dependency-light parts of pipeline_cores:
dictionary load, the acknowledgement matcher, and the combiner. No DB/Bedrock/AWS.
"""
from pipeline_cores.combine import (
    DEFAULT_TRIAGE_THRESHOLD,
    combine,
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
    affinity_strength,
    author_affinity,
    build_affinity_index,
    llm_triage,
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
def test_acknowledgement_auto_confirms():
    sig = SignalResult(ack_matched=True, ack_alias="CBIC", llm_score=1)
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_CONFIRMED and rec.likelihood >= 0.95


def test_staff_coauthor_auto_confirms():
    sig = SignalResult(coauthor_cwids=["djb2001"], llm_score=1)
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_CONFIRMED


def test_llm_only_is_candidate_via_noisy_or():
    sig = SignalResult(llm_score=9)
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_CANDIDATE
    assert abs(rec.likelihood - 0.9) < 1e-9


def test_low_signal_falls_below_threshold():
    sig = SignalResult(llm_score=2)            # 0.2 < default 0.30
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_BELOW


def test_affinity_lifts_a_weak_llm_score_over_threshold():
    sig = SignalResult(llm_score=2, author_affinity=0.5)   # noisy-OR(0.2,0.5)=0.6
    rec = combine("1", "2", sig)
    assert rec.status == STATUS_CANDIDATE and rec.likelihood > DEFAULT_TRIAGE_THRESHOLD


# --- full-text loader (pure + cache, no network) ---------------------------
def test_to_plain_text_strips_xml():
    from pipeline_cores.fulltext import to_plain_text
    assert to_plain_text("<sec><p>Citigroup  Biomedical\nImaging</p></sec>") == "Citigroup Biomedical Imaging"
    assert to_plain_text("") == ""


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


# --- author affinity (signal 1, repeat-user prior) -------------------------
def test_affinity_strength_scales_with_confirmed_count_and_caps():
    assert affinity_strength(0) == 0.0
    assert affinity_strength(1) == 0.45
    assert affinity_strength(2) == 0.60
    assert affinity_strength(99) == 0.85          # capped


def test_build_affinity_index_and_lookup():
    idx = build_affinity_index({"djb2001": {"2": 3}, "abc1001": {"5": 1}})
    assert author_affinity(idx, ["djb2001"], "2") == affinity_strength(3)   # single author: noisy-OR == strength
    assert author_affinity(idx, ["abc1001"], "2") == 0.0      # different core
    assert author_affinity(idx, ["nobody"], "2") == 0.0       # unknown author
    # one contributing author across the byline (the rest are zero) == that strength
    assert author_affinity(idx, ["nobody", "djb2001"], "2") == affinity_strength(3)


def test_author_affinity_noisy_or_combines_repeat_users():
    idx = build_affinity_index({"a": {"2": 1}, "b": {"2": 1}})    # each affinity_strength(1) = 0.45
    val = author_affinity(idx, ["a", "b"], "2")
    assert abs(val - (1 - 0.55 * 0.55)) < 1e-9                    # noisy-OR(0.45, 0.45) = 0.6975
    assert val > affinity_strength(1)                            # two repeat users beat either alone


def test_author_affinity_noisy_or_clamped_below_confirmer_ceiling():
    idx = build_affinity_index({"a": {"2": 9}, "b": {"2": 9}, "c": {"2": 9}})  # each capped at 0.85
    assert author_affinity(idx, ["a", "b", "c"], "2") == 0.85     # clamped to _AFFINITY_CAP, never >= 0.95


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

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None)}
    assert recs["100"].status == STATUS_CONFIRMED
    # 200 had no direct signal, but inherits djb2001's confirmed affinity (0.45 >= 0.30).
    assert recs["200"].status == STATUS_CANDIDATE
    assert recs["200"].likelihood >= 0.45


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
    monkeypatch.setattr(ingest, "fetch_publications", lambda e, limit=None: [{"pmid": "1"}])
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
