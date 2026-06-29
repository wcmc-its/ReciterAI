"""Unit tests for the grant->researcher matcher compiler (no Bedrock, no network)."""
import json

from pipeline_grants.match_compile import (
    SYS_QUERY,
    _parse_dsl,
    _parse_query,
    compile_match,
    compile_rel,
    match_attrs,
    norm_pool,
    pool_pmids,
    rel_attr,
)


class FakeBedrock:
    """Stands in for BedrockClient: returns canned JSON, branching on which prompt was sent."""

    def __init__(self, *, dsl_out=None, query_out=None, raises=False):
        self.dsl_out, self.query_out, self.raises = dsl_out, query_out, raises
        self.calls = 0
        self.dsl_call = None   # records the (system, user, cache_system) of the DSL call

    def call_json(self, *, model, system, messages, max_tokens, temperature, cache_system=False):
        self.calls += 1
        if self.raises:
            raise RuntimeError("bedrock boom")
        if system != SYS_QUERY:
            self.dsl_call = {"system": system, "user": messages[0]["content"], "cache_system": cache_system}
            return self.dsl_out
        return self.query_out


def test_parse_query_weights_lowercase_and_drops_blanks():
    out = {"queries": [
        {"q": "Deep Learning", "w": "core"},
        {"q": "  EHR  ", "w": "peripheral"},
        {"q": "", "w": "core"},          # blank -> dropped
        "single cell",                    # bare string -> defaults to core weight
    ]}
    res = _parse_query(out)
    assert {"q": "deep learning", "w": 1.0} in res
    assert {"q": "ehr", "w": 0.25} in res
    assert {"q": "single cell", "w": 1.0} in res
    assert all(r["q"] for r in res)       # the blank entry was dropped
    assert _parse_query({}) == [] and _parse_query(None) == []


def test_parse_dsl_shape_and_pediatric_defaults_false():
    out = {"require": ["a"], "penalize": ["b"], "pediatric_markers": ["c"], "pediatric_required": True}
    assert _parse_dsl(out) == out
    # No curated pediatric flag in production -> default False (not the scratch g['pediatric']).
    assert _parse_dsl({})["pediatric_required"] is False
    assert _parse_dsl({})["require"] == []
    assert _parse_dsl({"require": None})["require"] == []   # null-coalesced


def test_compile_match_happy_path():
    fb = FakeBedrock(
        dsl_out={"require": ["machine_learning"], "penalize": ["epidemiology"],
                 "pediatric_markers": [], "pediatric_required": False},
        query_out={"queries": [{"q": "deep learning", "w": "core"}]},
    )
    dsl, query = compile_match("Title", "Synopsis", ["machine_learning", "epidemiology"], bedrock=fb)
    assert dsl["require"] == ["machine_learning"]
    assert query == [{"q": "deep learning", "w": 1.0}]
    assert fb.calls == 2   # one dsl call + one query call


def test_compile_dsl_caches_vocab_in_system_prefix():
    # The candidate vocab must live in the (cacheable) system prefix, not the per-grant
    # user turn — that prefix reuse is what makes the corpus backfill ~4x cheaper.
    fb = FakeBedrock(
        dsl_out={"require": ["a"], "penalize": [], "pediatric_markers": [], "pediatric_required": False},
        query_out={"queries": [{"q": "x", "w": "core"}]},
    )
    compile_match("Title", "Synopsis text", ["machine_learning", "epidemiology"], bedrock=fb)
    assert fb.dsl_call["cache_system"] is True                      # opted into prompt caching
    assert "machine_learning" in fb.dsl_call["system"]              # vocab is in the cached prefix
    assert "machine_learning" not in fb.dsl_call["user"]            # NOT in the per-grant turn
    assert "Synopsis text" in fb.dsl_call["user"]                   # grant text stays in the user turn


def test_compile_match_fail_open_on_empty_require():
    fb = FakeBedrock(
        dsl_out={"require": [], "penalize": [], "pediatric_markers": [], "pediatric_required": False},
        query_out={"queries": [{"q": "x", "w": "core"}]},
    )
    assert compile_match("T", "S", ["v"], bedrock=fb) == (None, None)


def test_compile_match_fail_open_on_empty_query():
    fb = FakeBedrock(
        dsl_out={"require": ["a"], "penalize": [], "pediatric_markers": [], "pediatric_required": False},
        query_out={"queries": []},
    )
    assert compile_match("T", "S", ["v"], bedrock=fb) == (None, None)


def test_compile_match_fail_open_on_exception():
    assert compile_match("T", "S", ["v"], bedrock=FakeBedrock(raises=True)) == (None, None)


def test_match_attrs_encodes_compact_json_and_omits_when_none():
    dsl = {"require": ["a"], "penalize": [], "pediatric_markers": [], "pediatric_required": False}
    attrs = match_attrs(dsl, [{"q": "x", "w": 1.0}])
    assert json.loads(attrs["match_dsl"]["S"]) == dsl
    assert json.loads(attrs["match_query"]["S"]) == [{"q": "x", "w": 1.0}]
    assert "," in attrs["match_dsl"]["S"] and ", " not in attrs["match_dsl"]["S"]  # compact separators
    # Omitted entirely when absent -> the SPS matcher stays fail-closed.
    assert match_attrs(None, None) == {}
    assert "match_query" not in match_attrs(dsl, None)
    assert "match_dsl" not in match_attrs(None, [{"q": "x", "w": 1.0}])


# --- dense relevance (§3) -------------------------------------------------------------------

def _fake_embed(texts):
    """Canned 2-D vectors keyed by a marker in the text: 'ON' aligns with the grant, 'OFF' is
    orthogonal. texts[0] is the grant (also 'ON'-aligned)."""
    return [[0.0, 1.0] if "OFF" in t else [1.0, 0.0] for t in texts]


def test_norm_pool_max_to_one_floors_negatives():
    assert norm_pool([0.6, 0.3, -0.1, 0.0]) == [1.0, 0.5, 0.0, 0.0]  # max->1, neg floored, scaled
    assert norm_pool([]) == []
    assert norm_pool([0.0, 0.0]) == [0.0, 0.0]                       # no positive -> all 0, no div0
    assert norm_pool([-0.4, -0.2]) == [0.0, 0.0]                     # all negative -> all 0


def test_pool_pmids_substring_match_over_subtopic_ids():
    idx = {"1": "machine_learning_imaging", "2": "clinical_epidemiology", "3": "deep_learning"}
    assert set(pool_pmids(idx, ["learning"])) == {"1", "3"}
    assert pool_pmids(idx, []) == []                  # no require -> empty pool (matcher fail-closed)
    assert pool_pmids({"4": None}, ["x"]) == []       # null subtopic id skipped


def test_compile_rel_pool_max_norm_and_floor():
    # cos(grant,'ON')=1 -> normalized 1.0 (kept); cos(grant,'OFF')=0 -> floored out.
    rel = compile_rel("grant text", {"11": "ON abstract", "22": "OFF abstract"}, embed=_fake_embed, floor=0.1)
    assert rel == {"11": 1.0}


def test_compile_rel_empty_pool_and_blank_abstracts_never_embed():
    calls = []

    def spy(texts):
        calls.append(texts)
        return [[1.0, 0.0]] * len(texts)

    assert compile_rel("g", {}, embed=spy) == {}            # empty pool
    assert compile_rel("g", {"1": "   "}, embed=spy) == {}  # only-blank pool -> dropped pre-embed
    assert calls == []                                       # never reached the embedder


def test_rel_attr_compact_json_and_omits_when_empty():
    attrs = rel_attr({"123": 1.0, "456": 0.5})
    assert json.loads(attrs["match_rel"]["S"]) == {"123": 1.0, "456": 0.5}
    assert ", " not in attrs["match_rel"]["S"]               # compact separators
    assert rel_attr({}) == {} and rel_attr(None) == {}       # nothing to write -> matcher BM25 fallback
