"""Unit tests for the grant->researcher matcher compiler (no Bedrock, no network)."""
import json

from pipeline_grants.match_compile import (
    SYS_QUERY,
    _parse_dsl,
    _parse_query,
    compile_match,
    match_attrs,
)


class FakeBedrock:
    """Stands in for BedrockClient: returns canned JSON, branching on which prompt was sent."""

    def __init__(self, *, dsl_out=None, query_out=None, raises=False):
        self.dsl_out, self.query_out, self.raises = dsl_out, query_out, raises
        self.calls = 0

    def call_json(self, *, model, system, messages, max_tokens, temperature):
        self.calls += 1
        if self.raises:
            raise RuntimeError("bedrock boom")
        return self.query_out if system == SYS_QUERY else self.dsl_out


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
