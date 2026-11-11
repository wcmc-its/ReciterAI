"""Tests for pipeline_tools.maintain — the incremental, guard-gated tools refresh.

Every side effect is stubbed: the extractor and classifier LLM seams, the embedding seam,
the artifact S3 client, the DynamoDB ledger table and the Teams alert. The "live" artifact
is rendered through the real publish path and the state is reconciled from it with the
real ``pipeline_tools.state.reconcile``, so the zero-delta identity Phase 0 proved holds here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from pipeline_tools import maintain as mt
from pipeline_tools import state as st
from pipeline_tools.checkpoint import ExtractionCheckpoint
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.entities import build_entity_layer
from pipeline_tools.publish import S3_PREFIX, _family_record, build_publish_items
from pipeline_tools.registry import FamilyRegistry, ToolRegistry
from pipeline_tools.seed import _build_hierarchy, _enriched_records

GENERATED_AT = "2026-06-23T17:56:59Z"
STARTED = "2026-11-17T03:30:00Z"
ATTRS = {"delivery": None, "provenance": None, "license": None, "consumable": False, "rrid_candidate": False}


# ---------------------------------------------------------------------------
# Fixture: a small live artifact + the state reconciled from it
# ---------------------------------------------------------------------------


def _tool(cid, name, pub_ids, cbp, *, fam, kind, sc, tier="A", disp="method_tool"):
    return {
        "canonical_tool_id": cid, "display_name": name, "aliases": [name], "disposition": disp,
        "kind": kind, "supercategory": sc, "member_of_family": fam, "salience_tier": tier,
        "salience_tier_basis": "grounded" if tier else None, "pub_ids": list(pub_ids),
        "pub_count": len(pub_ids), "context_by_pub": dict(cbp), "context_evidence": [],
        "classified_by": "model-x", "classified_by_assumed": False, "flags": [], "attributes": dict(ATTRS),
    }


def _registry():
    cells = {"fam": "fam_0001", "kind": "organism_or_cells", "sc": "animal_cell_models"}
    seq = {"fam": "fam_0002", "kind": "method", "sc": "genomics_sequencing"}
    tools = [
        _tool("tool_000001", "HEK293 cells", ["101", "102"],
              {"101": "HEK293 cells were transfected with the construct.", "102": "We used HEK293 cells."}, **cells),
        _tool("tool_000002", "MCF-7 cells", ["103"], {"103": "MCF-7 cells were treated with drug."}, **cells),
        {**_tool("tool_000003", "Z cells", [], {}, tier="B", **cells), "pub_count": 4},   # seed-count tool
        _tool("tool_000004", "RNA-seq", ["101", "104"], {"104": "RNA-seq was performed on all samples."}, **seq),
        _tool("tool_000005", "ChIP-seq", ["105"], {"105": "ChIP-seq profiled the marks."}, tier="C", **seq),
        _tool("tool_000006", "REDCap", ["104"], {}, fam=None, kind=None, sc=None, tier=None, disp="infrastructure"),
    ]
    fams = [
        {"family_id": "fam_0001", "label": "Cell lines", "supercategory": "animal_cell_models",
         "dominant_kind": "organism_or_cells", "status": "active", "display": "feature",
         "definition": "Immortalized cultured cells.", "member_tool_ids": ["tool_000001", "tool_000002", "tool_000003"],
         "member_display_names": ["HEK293 cells", "MCF-7 cells", "Z cells"],
         "exemplar_tool_ids": ["tool_000001", "tool_000002"]},
        {"family_id": "fam_0002", "label": "Sequencing assays", "supercategory": "genomics_sequencing",
         "dominant_kind": "method", "status": "active", "display": "standard",
         "definition": "Reading nucleic acid sequence.", "member_tool_ids": ["tool_000004", "tool_000005"],
         "member_display_names": ["RNA-seq", "ChIP-seq"], "exemplar_tool_ids": ["tool_000004"]},
    ]
    return tools, fams


def _faculty():
    return {
        "a1": {"cwid": "a1", "tools": [
            {"canonical_tool_id": "tool_000001", "display_name": "HEK293 cells", "pub_count": 2, "pmids": ["101", "102"]},
            {"canonical_tool_id": "tool_000004", "display_name": "RNA-seq", "pub_count": 2, "pmids": ["101", "104"]},
        ], "families": [
            {"family_id": "fam_0001", "label": "Cell lines", "supercategory": "animal_cell_models", "pub_count": 2,
             "pmids": ["101", "102"], "exemplar_tool_ids": ["tool_000001"]},
            {"family_id": "fam_0002", "label": "Sequencing assays", "supercategory": "genomics_sequencing",
             "pub_count": 2, "pmids": ["101", "104"], "exemplar_tool_ids": ["tool_000004"]},
        ]},
        "b2": {"cwid": "b2", "tools": [
            {"canonical_tool_id": "tool_000002", "display_name": "MCF-7 cells", "pub_count": 1, "pmids": ["103"]},
        ], "families": [
            {"family_id": "fam_0001", "label": "Cell lines", "supercategory": "animal_cell_models", "pub_count": 1,
             "pmids": ["103"], "exemplar_tool_ids": ["tool_000002"]},
        ]},
    }


def _no_embed(texts):
    raise AssertionError("embedding seam must not be called here")


def _build_live() -> dict[str, bytes]:
    tools, fams = _registry()
    tr = ToolRegistry(copy.deepcopy(tools), cache=EmbeddingCache(embed=_no_embed))
    fr = FamilyRegistry(copy.deepcopy(fams), cache=EmbeddingCache(embed=_no_embed))
    records = _enriched_records(tr, fr)
    tool_context = {r["canonical_tool_id"]: dict(sorted(r["context_by_pub"].items()))
                    for r in tr.records() if r["context_by_pub"]}
    entities, entity_context, _ = build_entity_layer(records, fr.records(), tool_context, generic_terms=[])
    payload = {
        "schema_version": "tools-a2-v3",
        "provenance": {"run_at_utc": "2026-06-13T22:08:21Z", "raw_mentions": 9},
        "salience_thresholds": {"s_spread_min": 5, "a_pub_floor": 3, "a_spread_floor": 2, "percentile": 0.88},
        "tools": records,
        "families": [_family_record(f) for f in fr.records()],
        "hierarchy": _build_hierarchy(tr, fr),
        "faculty": _faculty(),
        "grant_signal": {"tool_000004": {"appl_ids": ["g1"], "investigator_cwids": ["a1"]}},
        "telemetry": {"canonical_tools": len(records)},
        "exceptions_summary": {},
        "tool_context": tool_context,
        "entities": entities,
        "entity_context": entity_context,
    }
    items, _ = build_publish_items(payload, prefix=S3_PREFIX, generated_at=GENERATED_AT)
    return {it.key.rsplit("/", 1)[-1]: it.body for it in items if it.key.startswith(f"{S3_PREFIX}latest/")}


BASE_PMIDS = ["101", "102", "103", "104", "105"]


def _state(live_raw, *, incorporated=BASE_PMIDS):
    tools, fams = _registry()
    s = st.reconcile(tools, fams, ["junk widget"], st.parse_live(live_raw), merges=[])
    s.incorporated_pmids = list(incorporated) if incorporated is not None else None
    return s


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class MemStore:
    """In-memory checkpoint store (read_lines / append / flush)."""

    strict = True
    location = "mem://checkpoint"

    def __init__(self, lines=None):
        self.lines = list(lines or [])

    def read_lines(self):
        return list(self.lines) if self.lines else None

    def append(self, line):
        self.lines.append(line)

    def flush(self):
        pass


def _ckpt_line(pmid, mentions):
    return json.dumps({"pmid": pmid, "mentions": mentions,
                       "usage": {"model": "", "input_tokens": 0, "output_tokens": 0}})


def _m(name, pmid, ctx=None, cwids=("a1",), kind="publication"):
    return {"raw_name": name, "tool_category": None, "context": ctx, "confidence": "high", "pmid": pmid,
            "source_kind": kind, "authors": [{"cwid": c, "author_role": "senior"} for c in cwids]}


BASE_LINES = [_ckpt_line(p, [_m("HEK293 cells", p)]) for p in ("101", "102")] + \
             [_ckpt_line(p, [_m("RNA-seq", p)] * (2 if p == "104" else 1)) for p in ("103", "104")] + \
             [_ckpt_line("105", [_m("ChIP-seq", "105"), _m("REDCap", "105")])]
# raw mentions in BASE_LINES = 2 + 1 + 2 + 2 = 7 ... matched to provenance below where needed.


class Result:
    def __init__(self, text, model="stub-haiku", it=100, ot=50):
        self.text, self.model, self.input_tokens, self.output_tokens = text, model, it, ot


class Extractor:
    """Returns canned mentions per pmid, read out of the extraction user message."""

    def __init__(self, by_pmid, *, tokens=(100, 50)):
        self.by_pmid, self.tokens, self.calls = by_pmid, tokens, []

    def __call__(self, system, user):
        pmid = re.search(r'"pmid": "([^"]+)"', user).group(1)
        self.calls.append(pmid)
        mentions = [{k: v for k, v in m.items() if k in ("raw_name", "context")} for m in self.by_pmid.get(pmid, [])]
        return Result(json.dumps({"mentions": mentions}), model="us.anthropic.claude-haiku-5-5",
                      it=self.tokens[0], ot=self.tokens[1])


CLS = {
    "Sanger sequencing": {"disposition": "method_tool", "kind": "method", "supercategory": "genomics_sequencing"},
    "Foo widget": {"disposition": "method_tool", "kind": "instrument", "supercategory": "imaging_image_analysis"},
    "the patients": {"disposition": "excluded"},
    "LabArchives": {"disposition": "infrastructure"},
}


class Classifier:
    """Classify / relabel stub keyed by raw_name. ``fail`` names are omitted from the
    response, so classify_batch returns its ``missing`` placeholder (disposition None)."""

    def __init__(self, cls=None, *, fail=(), relabel=None):
        self.cls, self.fail, self.relabel = dict(CLS if cls is None else cls), set(fail), relabel or {}
        self.classify_calls, self.relabel_calls = 0, 0

    def __call__(self, system, user):
        from prompts.tool_classify import CLASSIFY_SYSTEM_PROMPT
        if system == CLASSIFY_SYSTEM_PROMPT:
            self.classify_calls += 1
            names = re.findall(r'"raw_name": "([^"]+)"', user)
            out = []
            for n in names:
                c = self.cls.get(n)
                if c and n not in self.fail:
                    out.append({"raw_name": n, "confidence": "high", "attributes": {}, **c})
            return {"classifications": out, "_model": "stub-sonnet"}
        self.relabel_calls += 1
        ids = re.findall(r'"family_id": "([^"]+)"', user)
        return {"families": [{"family_id": i, "label": self.relabel[i], "confidence": "high"}
                             for i in ids if i in self.relabel], "_model": "stub-sonnet"}


def make_embed(aliases: dict[str, str] | None = None):
    """Deterministic pseudo-random unit vectors; texts mapped to the same alias key share one."""
    aliases = aliases or {}

    def embed(texts):
        out = []
        for t in texts:
            key = aliases.get(t, t)
            seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).standard_normal(64)
            out.append((v / np.linalg.norm(v)).tolist())
        return out

    return embed


class FakeS3:
    def __init__(self):
        self.objects, self.puts = {}, []

    def put_object(self, key, body, content_type=None, cache_control=None):
        self.objects[key] = body
        self.puts.append(key)

    def get_object_bytes(self, key):
        return self.objects[key]

    def key_exists(self, key):
        return key in self.objects


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


class Alerts:
    def __init__(self):
        self.sent = []

    def __call__(self, severity, title, message, context=None):
        self.sent.append((severity, title, context))
        return True


def _corpus(pmids, cwids=("a1",)):
    return [{"pmid": p, "title": f"Paper {p}", "abstract": ABSTRACTS.get(p, ""), "source_kind": "publication",
             "authors": [{"cwid": c, "author_role": "senior"} for c in cwids]} for p in pmids]


ABSTRACTS = {
    "201": ("Background text here. HEK293 cells were transfected with the new reporter construct. "
            "Sanger sequencing confirmed every clone. A Foo widget imaged the plates."),
    "202": "Z cells were cultured for ten days. The patients were enrolled.",
}
NEW_MENTIONS = {
    "201": [
        {"raw_name": "HEK293 cells", "context": "HEK293 cells were transfected with the new reporter construct."},
        {"raw_name": "Sanger sequencing", "context": "Sanger sequencing confirmed every clone."},
        {"raw_name": "Foo widget", "context": "A Foo widget imaged the plates."},
        {"raw_name": "junk widget", "context": None},
    ],
    "202": [
        {"raw_name": "Z cells", "context": "Z cells were cultured for ten days."},
        {"raw_name": "the patients", "context": "The patients were enrolled."},
    ],
}
FAMILY_EMBED = {"Sanger sequencing": "SEQFAM", "Sequencing assays": "SEQFAM"}


class Env:
    """A full maintain environment over a tmp local state store."""

    def __init__(self, tmp_path, *, extractor=None, classifier=None, corpus=None, ckpt_lines=None,
                 embed_aliases=None, incorporated=BASE_PMIDS, live_raw=None):
        self.live = live_raw or _build_live()
        self.store = st.LocalStateStore(tmp_path / "state")
        st.save_state(self.store, _state(self.live, incorporated=incorporated), "v0", created_at="2026-11-11T00:00:00Z")
        self.ckpt_store = MemStore(BASE_LINES if ckpt_lines is None else ckpt_lines)
        self.s3, self.table, self.alerts = FakeS3(), FakeTable(), Alerts()
        self.extractor = extractor if extractor is not None else Extractor(NEW_MENTIONS)
        self.classifier = classifier if classifier is not None else Classifier()
        self.corpus = corpus if corpus is not None else _corpus(["101", "102", "103", "104", "105", "201", "202"])
        self.embed = make_embed(FAMILY_EMBED if embed_aliases is None else embed_aliases)

    def seams(self):
        return mt.Seams(
            state_store=self.store, read_live=lambda: dict(self.live), load_corpus=lambda: list(self.corpus),
            open_checkpoint=lambda: ExtractionCheckpoint.load(store=self.ckpt_store),
            call_llm=self.extractor, call_json=self.classifier, embed=self.embed, s3=self.s3,
            ledger_table=self.table, alert=self.alerts)

    def run(self, **cfg):
        cfg.setdefault("force_c_terms", [])
        cfg.setdefault("generic_terms", [])
        cfg.setdefault("alias_map", False)
        started_at = cfg.pop("started_at", STARTED)
        return mt.run(mt.Config(**cfg), self.seams(), started_at=started_at)

    def latest_version(self):
        return st.resolve_latest(self.store)


def _published(s3):
    def J(name):
        return json.loads(s3.objects[f"{S3_PREFIX}latest/{name}"])
    return {n: J(n) for n in st.ARTIFACT_NAMES + ("manifest.json",)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fixture_state_reemits_live_byte_identically():
    live = _build_live()
    items, _ = st.reemit(_state(live))
    assert st.compare_to_live(items, live)["all_objects_identical"]


def test_zero_new_pmids_publishes_nothing_and_writes_skipped(tmp_path):
    env = Env(tmp_path, extractor=Extractor({}), corpus=_corpus(BASE_PMIDS))
    res = env.run(publish=True)
    assert res.status == "skipped"
    assert env.s3.puts == []
    assert env.extractor.calls == []
    assert env.latest_version() == "v0"                     # no new state version
    [row] = env.table.items
    assert row["PK"] == "STAGE#tools_maintain#GLOBAL" and row["SK"] == f"RUN#{STARTED}"
    assert row["status"] == "skipped" and row["skip_reason"] == "no_new_pmids"
    assert row["new_pmid_count"] == 0 and row["cost_observed_usd"] == Decimal(0)
    for k in ("started_at", "completed_at", "duration_ms", "new_mention_count", "new_tool_count",
              "held_for_review_count", "record_type", "run_id", "input_hash"):
        assert k in row


def test_delta_publish_end_to_end(tmp_path):
    env = Env(tmp_path)
    res = env.run(publish=True)
    assert res.status == "complete" and res.published
    assert set(env.extractor.calls) == {"201", "202"}      # only rows the checkpoint lacked
    pub = _published(env.s3)
    live = st.parse_live(env.live)

    # upload order: versioned copies first, latest/manifest.json last; manifest gains run_started_at
    run_id = mt._run_id(STARTED)
    assert env.s3.puts[0].startswith(f"tools/versions/{run_id}/")
    assert env.s3.puts[-1] == "tools/latest/manifest.json" or env.s3.puts[-1].startswith("tools/_review/")
    manifest_idx = env.s3.puts.index("tools/latest/manifest.json")
    assert all(not k.startswith("tools/latest/") for k in env.s3.puts[manifest_idx + 1:])
    assert pub["manifest.json"]["run_started_at"] == STARTED
    assert pub["tools.json"]["provenance"]["run_at_utc"] == STARTED
    assert pub["tools.json"]["provenance"]["base_run_at_utc"] == "2026-06-13T22:08:21Z"

    tools = {t["canonical_tool_id"]: t for t in pub["tools.json"]["tools"]}
    # existing tool gained the new pub + a sentence-aligned snippet for it only
    assert tools["tool_000001"]["pub_count"] == 3
    assert pub["tool_context.json"]["tool_context"]["tool_000001"]["201"].startswith("HEK293 cells were")
    # seed-count tool keeps its seed count as a base: 4 + 1, not 1
    assert tools["tool_000003"]["pub_count"] == 5
    # new method tool attached to an EXISTING family; ids/families never minted
    new = [c for c in tools if c not in {t["canonical_tool_id"] for t in live["bundle"]["tools"]}]
    assert len(new) == 1
    sanger = tools[new[0]]
    assert sanger["display_name"] == "Sanger sequencing" and sanger["method_family_id"] == "fam_0002"
    assert sanger["salience_tier"] == "B" and sanger["salience_tier_basis"] == "grounded"
    fams = {f["family_id"]: f for f in pub["families.json"]["families"]}
    assert set(fams) == {"fam_0001", "fam_0002"}
    assert fams["fam_0002"]["member_tool_ids"] == ["tool_000004", "tool_000005", new[0]]
    # Foo widget matched no family -> held: not published, in the review queue + Teams card
    assert not any(t["display_name"] == "Foo widget" for t in tools.values())
    review = json.loads(env.s3.objects[f"tools/_review/{run_id}.json"])
    assert [h["display_name"] for h in review["held"]] == ["Foo widget"]
    assert review["held"][0]["reason"] == mt.HOLD_NO_FAMILY
    assert env.alerts.sent[0][0] == "INFO"
    # faculty: the touched rows appended 201/202; the untouched scholar is unchanged
    a1 = {r["canonical_tool_id"]: r for r in pub["faculty.json"]["faculty"]["a1"]["tools"]}
    assert a1["tool_000001"]["pmids"] == ["101", "102", "201"]
    assert pub["faculty.json"]["faculty"]["b2"] == live["bundle"]["faculty"]["b2"]
    # entity layer: existing HEK293 entity gained usage + a row for the new pmid only
    ents = {e["normalized_entity_id"]: e for e in pub["entities.json"]["entities"]}
    assert ents["tool_000001"]["usage_count"] == 3
    ec = pub["entity_context.json"]["entity_context"]["tool_000001"]
    assert set(ec) == {"101", "102", "201"}
    assert ec["101"] == live["entity_context"]["tool_000001"]["101"]

    # new state version written after the publish; incorporated advanced; held kept in state
    assert env.latest_version() == run_id
    s1 = st.load_state(env.store)
    assert s1.parent_version == "v0" and {"201", "202"} <= set(s1.incorporated_pmids)
    assert any(r.get("review_hold") for r in s1.tool_records)
    assert "the patients" in s1.denylist
    assert s1.baseline_manifest["run_started_at"] == STARTED
    # ledger row
    [row] = env.table.items
    assert row["status"] == "complete" and row["published"] is True
    assert row["new_pmid_count"] == 2 and row["new_tool_count"] == 1 and row["held_for_review_count"] == 1
    assert row["cost_observed_usd"] > 0


def test_existing_records_untouched_after_delta(tmp_path):
    env = Env(tmp_path)
    env.run(publish=True)
    pub, live = _published(env.s3), st.parse_live(env.live)
    tools = {t["canonical_tool_id"]: t for t in pub["tools.json"]["tools"]}
    for t in live["bundle"]["tools"]:
        n = tools[t["canonical_tool_id"]]
        assert {k: v for k, v in n.items() if k not in ("pub_count", "aliases")} == \
               {k: v for k, v in t.items() if k not in ("pub_count", "aliases")}
    for cid, row in live["tool_context"].items():
        for pmid, snip in row.items():
            assert pub["tool_context.json"]["tool_context"][cid][pmid] == snip
            assert pub["tool_context_meta.json"]["tool_context_meta"][cid][pmid] == live["tool_context_meta"][cid][pmid]
    for eid, row in live["entity_context"].items():
        for pmid, rows in row.items():
            assert pub["entity_context.json"]["entity_context"][eid][pmid] == rows
    lf = {f["family_id"]: f for f in live["bundle"]["families"]}
    for f in pub["families.json"]["families"]:
        assert {k: v for k, v in f.items() if k != "member_tool_ids"} == \
               {k: v for k, v in lf[f["family_id"]].items() if k != "member_tool_ids"}
    assert pub["tools.json"]["hierarchy"] == live["bundle"]["hierarchy"]


def test_second_run_after_publish_is_zero_delta(tmp_path):
    env = Env(tmp_path)
    env.run(publish=True)
    env.live = {n: env.s3.objects[f"tools/latest/{n}"] for n in st.ARTIFACT_NAMES + ("manifest.json",)}
    env.table.items.clear()
    n_puts = len(env.s3.puts)
    res = env.run(publish=True, started_at="2026-11-24T03:30:00Z")
    assert res.status == "skipped" and len(env.s3.puts) == n_puts
    assert env.table.items[0]["status"] == "skipped"


def test_dry_run_writes_nothing(tmp_path):
    env = Env(tmp_path)
    res = env.run(publish=False, out_dir=tmp_path / "out")
    assert res.status == "dry_run"
    assert env.s3.puts == [] and env.table.items == [] and env.alerts.sent == []
    assert env.latest_version() == "v0"
    rep = json.loads((tmp_path / "out" / "maintain_report.json").read_text())
    assert rep["guard_failures"] == [] and rep["diff"]["delta"]["new_tools_published"] == 1
    assert json.loads((tmp_path / "out" / "review.json").read_text())["held"][0]["display_name"] == "Foo widget"


def test_state_out_of_sync_with_live_fails_closed(tmp_path):
    env = Env(tmp_path)
    live = dict(env.live)
    body = json.loads(live["tools.json"])
    body["telemetry"]["x"] = 1
    live["tools.json"] = json.dumps(body).encode()
    man = json.loads(live["manifest.json"])
    man["objects"]["tools.json"]["sha256"] = hashlib.sha256(live["tools.json"]).hexdigest()
    live["manifest.json"] = json.dumps(man).encode()
    env.live = live
    with pytest.raises(mt.MaintainError, match="no longer matches state"):
        env.run(publish=True)
    assert env.s3.puts == [] and env.table.items[0]["status"] == "failed"


def test_seed_incorporated_requires_matching_mention_count(tmp_path):
    env = Env(tmp_path, incorporated=None, extractor=Extractor({}), corpus=_corpus(BASE_PMIDS))
    with pytest.raises(mt.MaintainError, match="--seed-incorporated-from-checkpoint"):
        env.run(publish=False)
    with pytest.raises(mt.MaintainError, match="mentions"):   # provenance says 9, checkpoint holds 7
        env.run(publish=False, seed_incorporated_from_checkpoint=True)
    env.ckpt_store.lines.append(_ckpt_line("106", [_m("RNA-seq", "106"), _m("RNA-seq", "106")]))
    env.corpus = _corpus(BASE_PMIDS + ["106"])
    res = env.run(publish=False, seed_incorporated_from_checkpoint=True)
    assert res.status == "skipped"   # every checkpoint PMID seeded as already incorporated


def test_cold_checkpoint_refused(tmp_path):
    env = Env(tmp_path, ckpt_lines=[])
    with pytest.raises(mt.MaintainError, match="checkpoint is empty"):
        env.run(publish=False)


def test_hard_cap_halts_extraction_and_defers_the_rest(tmp_path):
    big = Extractor(NEW_MENTIONS, tokens=(40_000_000, 10_000_000))   # one call blows a $1 cap
    env = Env(tmp_path, extractor=big)
    res = env.run(publish=False, hard_cap_usd=Decimal(1))
    assert res.report["halted_on_cost_cap"] is True
    assert len(big.calls) == 1
    assert res.report["diff"]["delta"]["new_pmids"] == 1        # the extracted one is still folded in


def test_llm_error_defers_pmid_and_is_never_cached(tmp_path):
    cache = tmp_path / "classify.jsonl"
    env = Env(tmp_path, classifier=Classifier(fail={"Sanger sequencing"}))
    res = env.run(publish=True, classify_cache=cache)
    assert res.status == "complete"
    s1 = st.load_state(env.store)
    assert "201" not in s1.incorporated_pmids and "202" in s1.incorporated_pmids
    assert not cache.exists() or "Sanger" not in cache.read_text()
    # retry next run: the form classifies, the pmid's earlier attachments are idempotent
    env.live = {n: env.s3.objects[f"tools/latest/{n}"] for n in st.ARTIFACT_NAMES + ("manifest.json",)}
    env.classifier = Classifier()
    res2 = env.run(publish=True, classify_cache=cache, started_at="2026-11-24T03:30:00Z")
    assert res2.status == "complete" and res2.report["guard_failures"] == []
    pub = _published(env.s3)
    tools = {t["display_name"]: t for t in pub["tools.json"]["tools"]}
    assert "Sanger sequencing" in tools and tools["HEK293 cells"]["pub_count"] == 3   # not double counted
    assert "201" in st.load_state(env.store).incorporated_pmids


def test_classify_budget_exhausted_defers_forms(tmp_path):
    env = Env(tmp_path)
    res = env.run(publish=False, hard_cap_usd=Decimal(0))
    d = res.report["diff"]["delta"]
    assert d["classified_forms"] == 0 and d["deferred_forms"] >= 2
    assert env.classifier.classify_calls == 0


def test_held_tool_stays_held_on_later_runs(tmp_path):
    env = Env(tmp_path)
    env.run(publish=True)
    env.live = {n: env.s3.objects[f"tools/latest/{n}"] for n in st.ARTIFACT_NAMES + ("manifest.json",)}
    env.extractor = Extractor({"203": [{"raw_name": "Foo widget", "context": "The Foo widget scanned slides."}]})
    env.corpus = env.corpus + _corpus(["203"])
    env.classifier = Classifier()
    res = env.run(publish=True, started_at="2026-11-24T03:30:00Z")
    assert env.classifier.classify_calls == 0                    # resolved by name: no LLM
    assert res.report["diff"]["delta"]["held_for_review"] == 1
    assert res.published is False                                # artifact unchanged -> no republish
    s2 = st.load_state(env.store)
    held = next(r for r in s2.tool_records if r.get("review_hold"))
    assert held["pub_ids"] == ["201", "203"] and "203" in s2.incorporated_pmids


def test_zero_artifact_delta_advances_state_without_publishing(tmp_path):
    env = Env(tmp_path, extractor=Extractor({"201": [{"raw_name": "junk widget", "context": None}]}),
              corpus=_corpus(BASE_PMIDS + ["201"]))
    res = env.run(publish=True)
    assert res.status == "complete" and not res.published
    assert not any(k.startswith("tools/latest/") for k in env.s3.puts)
    assert "201" in st.load_state(env.store).incorporated_pmids
    assert env.table.items[0]["published"] is False


def test_relabel_is_opt_in(tmp_path):
    env = Env(tmp_path, classifier=Classifier(relabel={"fam_0002": "DNA and RNA sequencing"}))
    env.run(publish=False)
    assert env.classifier.relabel_calls == 0
    env2 = Env(tmp_path / "b", classifier=Classifier(relabel={"fam_0002": "DNA and RNA sequencing"}))
    res = env2.run(publish=True, relabel=True)
    assert env2.classifier.relabel_calls == 1 and res.report["guard_failures"] == []
    pub = _published(env2.s3)
    fams = {f["family_id"]: f for f in pub["families.json"]["families"]}
    assert fams["fam_0002"]["label"] == "DNA and RNA sequencing"
    a1 = {r["family_id"]: r for r in pub["faculty.json"]["faculty"]["a1"]["families"]}
    assert a1["fam_0002"]["label"] == "DNA and RNA sequencing"


def test_guard_failure_publishes_nothing_and_writes_failed_row(tmp_path, monkeypatch):
    env = Env(tmp_path)
    monkeypatch.setattr(mt, "run_guards", lambda live, items, delta: ["tool tool_000001 field 'kind' changed"])
    with pytest.raises(mt.GuardError):
        env.run(publish=True)
    assert env.s3.puts == []
    assert env.latest_version() == "v0"
    [row] = env.table.items
    assert row["status"] == "failed" and row["error_code"] == "GuardError"
    assert row["failure_details"]["guard_failures"] == ["tool tool_000001 field 'kind' changed"]
    assert env.alerts.sent[-1][0] == "ERROR"


# --- each guard trips on a crafted violation --------------------------------


@pytest.fixture(scope="module")
def baseline():
    live = _build_live()
    state = _state(live)
    mentions = [{**m, "pmid": p, "source_kind": "publication", "tool_category": None,
                 "authors": [{"cwid": "a1", "author_role": "senior"}]}
                for p, ms in NEW_MENTIONS.items() for m in ms]
    s2, delta = mt.build_delta(state, mentions, ["201", "202"], call_json=Classifier(), embed=make_embed(FAMILY_EMBED),
                               classify_budget_usd=Decimal(5), abstracts=ABSTRACTS, force_c_terms=[],
                               generic_terms=[], run_id="r")
    payload, items, _ = mt.render(s2, generated_at="2026-11-17T04:00:00Z", run_started_at=STARTED)
    assert mt.run_guards(live, items, delta) == []
    return live, payload, delta


def _trip(baseline, mutate):
    live, payload, delta = baseline
    p = copy.deepcopy(payload)
    mutate(p)
    items, _ = build_publish_items(p, generated_at="2026-11-17T04:00:00Z")
    return mt.run_guards(live, items, delta)


def _tool_of(p, cid):
    return next(t for t in p["tools"] if t["canonical_tool_id"] == cid)


@pytest.mark.parametrize("name,mutate,expect", [
    ("tool_dropped", lambda p: p["tools"].pop(0), "missing"),
    ("tool_label", lambda p: _tool_of(p, "tool_000002").update(display_name="MCF7"), "display_name"),
    ("tier_moved", lambda p: _tool_of(p, "tool_000002").update(salience_tier="S"), "salience_tier"),
    ("pub_count", lambda p: _tool_of(p, "tool_000002").update(pub_count=9), "pub_count"),
    ("family_id_changed", lambda p: p["families"][1].update(family_id="fam_0099"), "family_id set"),
    ("family_label", lambda p: p["families"][0].update(label="Cells"), "field 'label'"),
    ("membership_reordered", lambda p: p["families"][0]["member_tool_ids"].reverse(), "membership"),
    ("context_changed", lambda p: p["tool_context"]["tool_000002"].update({"103": "Changed sentence."}),
     "tool_context (tool_000002, 103)"),
    ("context_removed", lambda p: p["tool_context"]["tool_000001"].pop("102"), "tool_context (tool_000001, 102)"),
    ("meta_rescored", lambda p: p["tool_context_meta"]["tool_000002"]["103"].update(informativeness_score=0.0),
     "tool_context_meta"),
    ("fragment_regressed", lambda p: p["tool_context"]["tool_000002"].update(
        {"201": "used as a control without end", "202": "another fragment here"}), "fragment metric"),
    ("context_for_old_pmid", lambda p: p["tool_context"]["tool_000002"].update({"101": "An old pub."}),
     "non-new pmids"),
    ("faculty_row_lost", lambda p: p["faculty"].pop("b2"), "faculty b2 missing"),
    ("faculty_old_pmid", lambda p: p["faculty"]["b2"]["tools"][0].update(pmids=["103", "104"], pub_count=2),
     "append-only-new"),
    ("entity_dropped", lambda p: p["entities"].pop(), "shrink: entities.json"),
    ("entity_row_changed", lambda p: p["entity_context"]["tool_000002"]["103"][0].update(usage_sentence="X."),
     "entity_context (tool_000002, 103)"),
    ("hierarchy_changed", lambda p: p["hierarchy"].clear(), "hierarchy"),
    ("grant_signal_shrank", lambda p: p["grant_signal"].pop("tool_000004"), "grant_signal"),
    ("faculty_shrinks", lambda p: [p["faculty"].pop(k) for k in list(p["faculty"])], "shrink: faculty.json"),
])
def test_each_guard_trips(baseline, name, mutate, expect):
    failures = _trip(baseline, mutate)
    assert any(expect in f for f in failures), (name, failures)


def test_unexpected_new_tool_trips(baseline):
    def add(p):
        p["tools"].append({**_tool_of(p, "tool_000002"), "canonical_tool_id": "tool_000999"})
    assert any("not minted+published" in f for f in _trip(baseline, add))


# --- smaller units ------------------------------------------------------------


def test_accept_new_snippet():
    ab = "Methods were standard. HEK293 cells were transfected with the construct. Results followed."
    assert mt.accept_new_snippet("HEK293 cells were transfected with the construct.", "HEK293 cells", ab)
    assert not mt.accept_new_snippet("cells were transfected with the construct.", "HEK293 cells", ab)
    assert not mt.accept_new_snippet("HEK293 cells were made up by the model.", "HEK293 cells", ab)  # not verbatim
    assert mt.accept_new_snippet("HEK293 cells were transfected.", "HEK293 cells", None)
    assert not mt.accept_new_snippet("HEK293 cells were transfected", "HEK293 cells", None)


def test_registry_seed_count_base_and_surface_match():
    r = ToolRegistry([{**_registry()[0][2]}], cache=EmbeddingCache(embed=_no_embed))
    assert r.match_surface("z cells").key == "tool_000003"
    assert r.match_surface("unknown thing") is None
    rec = r.get("tool_000003")
    rec["pub_count_base"] = 4
    r.attach("tool_000003", pub_ids=["9"])
    assert ToolRegistry._serialize(rec)["pub_count"] == 5


def test_stage_row_shape():
    row = mt.stage_row(status="complete", started_at=STARTED, run_id="r",
                       counts={"new_pmids": 3, "new_mentions": 7, "new_tools": 1, "held_for_review": 2},
                       cost_usd=Decimal("1.23456"))
    assert row["PK"] == "STAGE#tools_maintain#GLOBAL" and row["SK"] == f"RUN#{STARTED}"
    assert row["cost_observed_usd"] == Decimal("1.2346") and row["new_tool_count"] == 1
    assert isinstance(row["duration_ms"], int)
