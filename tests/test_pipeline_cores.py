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
