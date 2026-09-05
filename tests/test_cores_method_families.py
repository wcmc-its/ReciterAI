"""Unit tests for the A2 method-family signal (ReciterAI #394): index build, tier
selection, the one-key rule, the inertness claim, and what reaches DynamoDB. No S3.
"""
import json

import pytest

from pipeline_cores.combine import evidence_features, score
from pipeline_cores.dictionary import load_core, load_cores
from pipeline_cores.method_families import load_family_index
from pipeline_cores.models import METHOD_FAMILY_TIERS, CoreDefinition, SignalResult
from pipeline_cores.signals import method_family_signal

# One tool per family, plus the two shapes that must NOT produce a chip: a tool with a
# null method_family_label, and a tool with a family but no context entries anywhere.
_TOOLS = {
    "tools": [
        {"canonical_tool_id": "t1", "display_name": "Epic Clarity",
         "method_family_label": "Electronic health record datasets"},
        {"canonical_tool_id": "t2", "display_name": "logistic regression",
         "method_family_label": "Regression modeling"},
        {"canonical_tool_id": "t3", "display_name": "random forest",
         "method_family_label": "Machine learning classification"},
        {"canonical_tool_id": "t4", "display_name": "a nameless thing",
         "method_family_label": None},                  # dropped: no family, no chip
        {"canonical_tool_id": "t5", "display_name": "never mentioned",
         "method_family_label": "Clinical text mining"},  # no context entries
    ]
}
_CONTEXT = {
    "tool_context": {
        "t1": {"100": "We queried the Epic Clarity data warehouse for the cohort."},
        "t2": {"100": "Associations were tested with logistic regression.",
               "200": "Outcomes were modelled with logistic regression."},
        "t3": {"100": "A random forest classifier was trained on the features."},
        "t4": {"100": "This sentence belongs to a tool with no family."},
    }
}

_CORE = CoreDefinition(
    core_id="14", name="Research Informatics", aliases=["Architecture for Research Computing"],
    method_families={
        "strong": ["electronic health record datasets", "clinical text mining"],
        "moderate": ["machine learning classification"],
        "weak": ["regression modeling"],
    },
)


def _index(tmp_path, tools=None, context=None) -> dict:
    (tmp_path / "tools.json").write_text(json.dumps(tools or _TOOLS), encoding="utf-8")
    (tmp_path / "ctx.json").write_text(json.dumps(context or _CONTEXT), encoding="utf-8")
    return load_family_index(tools_path=tmp_path / "tools.json",
                             context_path=tmp_path / "ctx.json")


# --- the index -------------------------------------------------------------
def test_index_joins_tools_to_pmids(tmp_path):
    idx = _index(tmp_path)
    assert set(idx) == {"100", "200"}
    assert ("Regression modeling", "logistic regression",
            "Outcomes were modelled with logistic regression.") in idx["200"]


def test_index_drops_a_tool_with_a_null_family(tmp_path):
    """method_family_label is nullable (891 of 18,405 tools live). No family, no chip —
    and nothing downstream should have to carry a None through."""
    idx = _index(tmp_path)
    assert all(family for family, _tool, _sentence in idx["100"])
    assert not any(tool == "a nameless thing" for _f, tool, _s in idx["100"])


def test_index_ignores_a_tool_with_no_context_entries(tmp_path):
    """t5 has a family but appears in no paper's context: it contributes no pmid."""
    idx = _index(tmp_path)
    assert not any("Clinical text mining" == f for rows in idx.values() for f, _t, _s in rows)


def test_a_failed_read_raises_instead_of_returning_an_empty_index(tmp_path):
    """The whole point of the module. put_core_usage REMOVEs every owned attribute the
    run did not produce, so an empty index would strip method_* off every previously
    scored row — a wipe on a green run. Both a missing file and a moved schema raise."""
    with pytest.raises(OSError):
        load_family_index(tools_path=tmp_path / "gone.json", context_path=tmp_path / "gone.json")
    (tmp_path / "tools.json").write_text(json.dumps({"toolz": []}), encoding="utf-8")
    (tmp_path / "ctx.json").write_text(json.dumps(_CONTEXT), encoding="utf-8")
    with pytest.raises(KeyError):
        load_family_index(tools_path=tmp_path / "tools.json", context_path=tmp_path / "ctx.json")


def test_a_well_formed_but_empty_artifact_raises_too(tmp_path):
    """The read succeeded, the schema is right, and the result is still unusable.

    This is the case a plain try/except would never see: no error to catch, just zero
    rows. run.py would write that empty index straight into put_core_usage's REMOVE
    clause and strip method_* off every previously scored row, on a green run.
    """
    # Written directly rather than through _index(): its `tools or _TOOLS` default
    # would swap an intentionally empty list back for the fixture.
    (tmp_path / "tools.json").write_text(json.dumps({"tools": []}), encoding="utf-8")
    (tmp_path / "ctx.json").write_text(json.dumps(_CONTEXT), encoding="utf-8")
    with pytest.raises(ValueError, match="EMPTY"):
        load_family_index(tools_path=tmp_path / "tools.json", context_path=tmp_path / "ctx.json")
    # ...and a full tool list whose context is empty is the same failure.
    (tmp_path / "tools.json").write_text(json.dumps(_TOOLS), encoding="utf-8")
    (tmp_path / "ctx.json").write_text(json.dumps({"tool_context": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="EMPTY"):
        load_family_index(tools_path=tmp_path / "tools.json", context_path=tmp_path / "ctx.json")


def test_index_reads_s3_by_default_through_a_duck_typed_backend():
    """Default path is S3 — duck-typed on get_object_bytes, exactly like fulltext's."""
    class _S3:
        def __init__(self):
            self.keys = []

        def get_object_bytes(self, key):
            self.keys.append(key)
            return json.dumps(_TOOLS if key.endswith("tools.json") else _CONTEXT).encode()

    s3 = _S3()
    assert set(load_family_index(s3=s3)) == {"100", "200"}
    assert s3.keys == ["tools/latest/tools.json", "tools/latest/tool_context.json"]


# --- the signal ------------------------------------------------------------
def test_strongest_tier_wins_when_a_paper_carries_several(tmp_path):
    """PMID 100 carries a strong, a moderate and a weak family. The reported tier is
    the strongest, and the tool + sentence belong to the family that won."""
    families, tier, tool, snippet = method_family_signal(_index(tmp_path), "100", _CORE)
    assert tier == "strong"
    assert families[0] == "Electronic health record datasets"
    assert set(families) == {"Electronic health record datasets", "Regression modeling",
                             "Machine learning classification"}
    assert tool == "Epic Clarity" and "Epic Clarity" in snippet


def test_a_weak_only_paper_reports_weak(tmp_path):
    families, tier, tool, _s = method_family_signal(_index(tmp_path), "200", _CORE)
    assert (families, tier, tool) == (["Regression modeling"], "weak", "logistic regression")


def test_no_curation_and_no_index_entry_both_fire_nothing(tmp_path):
    idx = _index(tmp_path)
    assert method_family_signal(idx, "999", _CORE) == ([], "", "", "")
    assert method_family_signal(idx, "100", load_core("2")) == ([], "", "", "")


# --- the one-key rule and the inertness claim ------------------------------
def test_evidence_features_emits_exactly_one_method_key(tmp_path):
    """One key, not one per tier and not one per family: the tiers are correlated, and
    816 families would be an unfittable weight table."""
    families, tier, tool, snippet = method_family_signal(_index(tmp_path), "100", _CORE)
    keys = evidence_features(SignalResult(method_families=families, method_tier=tier,
                                          method_tool=tool, method_snippet=snippet))
    assert [k for k in keys if k.startswith("method:")] == ["method:strong"]


def test_every_tier_has_a_weights_cell():
    from pipeline_cores.combine import WEIGHTS
    assert all(WEIGHTS[f"method:{t}"] == 0.00 for t in METHOD_FAMILY_TIERS)


def test_the_signal_is_inert(tmp_path):
    """The claim, mechanically checked: at weight 0.00 the score is identical with and
    without the method fields, for a pair with other evidence and one with none."""
    families, tier, tool, snippet = method_family_signal(_index(tmp_path), "100", _CORE)
    for base in (SignalResult(),
                 SignalResult(ack_matched=True, ack_alias="a", ack_alias_hits=25,
                              ack_institution="home", coauthor_cwids=["abc1001"],
                              llm_score=7, author_affinity=0.42)):
        withm = SignalResult(**{**base.__dict__, "method_families": families,
                                "method_tier": tier, "method_tool": tool,
                                "method_snippet": snippet})
        assert score(withm) == score(base)


# --- persistence -----------------------------------------------------------
def test_build_core_item_emits_the_four_attributes_and_owns_them(tmp_path):
    from pipeline_cores.models import CoreUsageRecord
    from pipeline_cores.persist import _OWNED_ATTRS, build_core_item

    families, tier, tool, snippet = method_family_signal(_index(tmp_path), "100", _CORE)
    sig = SignalResult(method_families=families, method_tier=tier, method_tool=tool,
                       method_snippet=snippet)
    item = build_core_item(CoreUsageRecord("100", "14", 0.4, "candidate", sig, "t"))
    assert item["method_tier"] == {"S": "strong"}
    assert item["method_families"]["L"][0] == {"S": "Electronic health record datasets"}
    assert item["method_tool"] == {"S": "Epic Clarity"}
    assert "Epic Clarity" in item["method_snippet"]["S"]
    # Outside _OWNED_ATTRS an attribute is never swept into REMOVE, so a stale family
    # would survive on a row the run no longer supports.
    assert {"method_families", "method_tier", "method_tool", "method_snippet"} <= _OWNED_ATTRS
    # ...and a pair with no family emits none of the four.
    bare = build_core_item(CoreUsageRecord("200", "14", 0.1, "candidate", SignalResult(), "t"))
    assert not [k for k in bare if k.startswith("method_")]


def test_method_snippet_is_truncated_like_ack_snippet(tmp_path):
    from pipeline_cores.models import CoreUsageRecord
    from pipeline_cores.persist import build_core_item

    sig = SignalResult(method_families=["Electronic health record datasets"],
                       method_tier="strong", method_tool="Epic Clarity",
                       method_snippet="x" * 900)
    item = build_core_item(CoreUsageRecord("100", "14", 0.4, "candidate", sig, "t"))
    assert len(item["method_snippet"]["S"]) == 500


# --- the dictionary --------------------------------------------------------
def test_load_cores_casefolds_and_strips_hand_typed_labels(tmp_path):
    """Hand-typed YAML against an 816-label taxonomy: a stray capital or trailing space
    would otherwise match nothing, fire nothing and raise nothing."""
    (tmp_path / "d.yaml").write_text(
        'cores:\n  - core_id: "14"\n    name: RI\n    aliases: ["ARCH x"]\n'
        '    method_families:\n      strong: ["  Electronic Health Record Datasets  "]\n',
        encoding="utf-8")
    assert load_cores(tmp_path / "d.yaml")[0].method_families == {
        "strong": ["electronic health record datasets"]}


def test_load_cores_raises_on_an_unknown_tier(tmp_path):
    """A typo'd tier is a silent no-op otherwise: combine() looks up "method:medium",
    finds nothing, and the core carries curation that can never fire."""
    (tmp_path / "d.yaml").write_text(
        'cores:\n  - core_id: "14"\n    name: RI\n    aliases: ["ARCH x"]\n'
        '    method_families:\n      medium: ["Regression modeling"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="medium"):
        load_cores(tmp_path / "d.yaml")


def test_load_cores_raises_on_a_tier_written_as_a_string(tmp_path):
    """`strong: "Regression modeling"` instead of a one-item list. A string ITERATES,
    so without the guard this loads ten one-character labels, matches nothing, and
    raises nothing — curation that can never fire, on a green load."""
    (tmp_path / "d.yaml").write_text(
        'cores:\n  - core_id: "14"\n    name: RI\n    aliases: ["ARCH x"]\n'
        '    method_families:\n      strong: "Regression modeling"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        load_cores(tmp_path / "d.yaml")


def test_a_dictionary_without_the_key_still_loads():
    """Same contract as clients/alias_hits: absent = {}, no core is required to carry it."""
    assert all(c.method_families == {} for c in load_cores() if c.core_id != "14")


def test_shipped_core_14_carries_the_three_tiers():
    fams = load_core("14").method_families
    assert set(fams) == set(METHOD_FAMILY_TIERS)
    assert fams["strong"][0] == "clinical data warehouse/cohort platforms"
    assert len(fams["strong"]) == 3 and len(fams["moderate"]) == 2 and len(fams["weak"]) == 2


# --- run_core wiring -------------------------------------------------------
def test_run_core_sets_the_method_fields(monkeypatch, tmp_path):
    """Wiring, not scoring. Every weight is 0.00, so no likelihood or status assertion
    anywhere can notice this signal never being connected."""
    from pipeline_cores import ingest, run, signals

    core = load_core("14")
    pubs = [{"pmid": "100", "title": "ehr paper", "abstract": ""},
            {"pmid": "999", "title": "not in the artifact", "abstract": ""}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda e, c, p: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {})

    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=None, dry_run=True,
                                            family_index=_index(tmp_path))}
    assert recs["100"].signals.method_tier == "strong"
    assert recs["100"].signals.method_tool == "Epic Clarity"
    assert recs["999"].signals.method_families == []
    # ...and without the flag (no index) nothing is set, on the same pubs.
    off = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                           scored_at="t", engine=None, dry_run=True)}
    assert off["100"].signals.method_families == [] and off["100"].signals.method_tier == ""
