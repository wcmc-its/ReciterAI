"""Tests for the Brick F PR-2 policy (#191): mint-persistence gate + retire transitions.

PR-1 (test_subtopic_lifecycle.py) captures the substrate; PR-2 ACTS on it. These tests
pin the policy half:
  - LifecyclePolicy.from_config: the new policy flag + thresholds, fail-loud
    (policy-on-substrate-off, missing/bad thresholds, non-bool flag), policy-off
    tolerance, two-arg backward-compat;
  - MintPersistenceGate.admit: below-floor hold, single-run-floor mint, two-run
    hold-then-mint + candidate consume, seen_slugs protection;
  - delete_candidate / set_status: consume + status-only-write idempotent mutator;
  - sweep_retire: off no-op, demote/archive thresholds, un-demote (demoted AND archived),
    monotone-forward guard, counter untouched;
  - reconcile_durable_ids + mint_gate: held leaves NO half-state (no id/pointer), mints
    next run + consumes, attach stays ungated, and mint_gate=None is byte-identical to PR-1.

Everything runs offline over a dict-backed fake table (no AWS).
"""

from __future__ import annotations

import json
import random

import pytest

from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    PTR_SK,
    RECORD_TYPE_SUBTOPIC_CANDIDATE,
    RECORD_TYPE_SUBTOPIC_ID,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DEMOTED,
    SUBTOPIC_CANDIDATE_PK_PREFIX,
    SUBTOPIC_ID_PK_PREFIX,
    SUBTOPIC_SLUG_PK_PREFIX,
    MintContext,
    SubtopicIdStore,
    reconcile_durable_ids,
    set_status,
)
from pipeline_hierarchy.subtopic_ids import SubtopicIdMinter
from pipeline_hierarchy.subtopic_lifecycle import (
    LifecyclePolicy,
    MintPersistenceGate,
    delete_candidate,
    gc_candidates,
    sweep_retire,
    upsert_candidate,
)

HV1 = "v2026-06-11"
HV2 = "v2026-06-12"

# Policy presets (substrate must be on whenever policy is on).
P2 = LifecyclePolicy(  # two-run persistence, demote 12 / archive 12 (defaults)
    substrate_enabled=True, mint_floor_papers=5, policy_enabled=True, mint_persistence_runs=2
)
P1 = LifecyclePolicy(  # single-run floor (annual)
    substrate_enabled=True, mint_floor_papers=5, policy_enabled=True, mint_persistence_runs=1
)
P_OFF = LifecyclePolicy(substrate_enabled=True, mint_floor_papers=5, policy_enabled=False)
RETIRE = LifecyclePolicy(  # small thresholds for readable retire tests
    substrate_enabled=True, mint_floor_papers=5, policy_enabled=True,
    mint_persistence_runs=2, demote_quiet_runs=3, archive_quiet_runs=2,
)  # demote at quiet>=3, archive at quiet>=5


class FakeTable:
    """Dict-backed boto3 Table stand-in: get/put/delete + paginating scan."""

    def __init__(self, page=100):
        self.items: dict[tuple[str, str], dict] = {}
        self.put_calls = 0
        self.delete_calls = 0
        self._page = page

    def get_item(self, Key):
        key = (Key["PK"], Key["SK"])
        return {"Item": self.items[key]} if key in self.items else {}

    def put_item(self, Item):
        self.put_calls += 1
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def delete_item(self, Key):
        self.delete_calls += 1
        self.items.pop((Key["PK"], Key["SK"]), None)

    def scan(self, **kwargs):
        rows = list(self.items.values())
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = rows[start : start + self._page]
        resp = {"Items": chunk}
        nxt = start + self._page
        if nxt < len(rows):
            resp["LastEvaluatedKey"] = nxt
        return resp

    # helpers
    def candidate(self, slug):
        return self.items.get((f"{SUBTOPIC_CANDIDATE_PK_PREFIX}{slug}", META_SK))

    def id_rows(self):
        return [v for v in self.items.values() if v.get("record_type") == RECORD_TYPE_SUBTOPIC_ID]

    def ptr_for(self, slug):
        return self.items.get((f"{SUBTOPIC_SLUG_PK_PREFIX}{slug}", PTR_SK))


def _ctx(hv=HV1):
    return MintContext(run_id="run-1", created_at="2026-06-11T00:00:00Z",
                       taxonomy_version="taxonomy_v2", hierarchy_version=hv)


def _store():
    return SubtopicIdStore(FakeTable(), SubtopicIdMinter(rand=random.Random(7).random))


def _membership(seeds_by_slug, topic="aging"):
    return {"subtopics": {s: {"topic_id": topic, "seed_pmids": seeds}
                          for s, seeds in seeds_by_slug.items()}}


def _hierarchy(slugs, topic="aging"):
    return {"topics": {topic: {"subtopics": [{"id": s, "label": s.title()} for s in slugs]}}}


def _prior_row(durable_id, *, slug, topic="aging", status=STATUS_ACTIVE, quiet=None):
    row = {
        "PK": f"{SUBTOPIC_ID_PK_PREFIX}{durable_id}", "SK": META_SK,
        "record_type": RECORD_TYPE_SUBTOPIC_ID, "durable_id": durable_id,
        "slug_id": slug, "topic_id": topic, "seed_pmids": [1, 2, 3], "status": status,
        "label_at_mint": slug.title(), "created_at": "2026-01-01T00:00:00Z", "first_run_id": "r0",
    }
    if quiet is not None:
        row["consecutive_quiet_runs"] = quiet
    return row


def _snap_row(*, slug="s", topic="aging", status=STATUS_ACTIVE, quiet=0):
    """A load_id_store_snapshot-shaped row (what sweep_retire consumes)."""
    return {"slug_id": slug, "topic_id": topic, "seed_pmids": {1, 2, 3},
            "label_at_mint": slug.title(), "status": status, "consecutive_quiet_runs": quiet}


# ====================== LifecyclePolicy.from_config (policy) ======================


def test_policy_defaults_off_in_real_config():
    p = LifecyclePolicy.from_config()
    assert p.policy_enabled is False
    assert p.mint_persistence_runs >= 1 and p.demote_quiet_runs >= 1 and p.archive_quiet_runs >= 1


def test_two_arg_construction_back_compat():
    # PR-1 call sites construct with two fields; the PR-2 fields default (policy off).
    p = LifecyclePolicy(substrate_enabled=True, mint_floor_papers=5)
    assert p.policy_enabled is False and p.mint_persistence_runs == 2
    assert p.demote_quiet_runs == 12 and p.archive_quiet_runs == 12


def test_policy_enabled_requires_substrate(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": False,
        "subtopic_lifecycle_policy_enabled": True,
        "subtopic_lifecycle_mint_persistence_runs": 2,
        "subtopic_lifecycle_demote_quiet_runs": 12,
        "subtopic_lifecycle_archive_quiet_runs": 12,
    }))
    with pytest.raises(ValueError, match="requires"):
        LifecyclePolicy.from_config(p)


def test_policy_enabled_parses(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 5,
        "subtopic_lifecycle_policy_enabled": True,
        "subtopic_lifecycle_mint_persistence_runs": 2,
        "subtopic_lifecycle_demote_quiet_runs": 9,
        "subtopic_lifecycle_archive_quiet_runs": 6,
    }))
    pol = LifecyclePolicy.from_config(p)
    assert pol.policy_enabled is True and pol.substrate_enabled is True
    assert (pol.mint_persistence_runs, pol.demote_quiet_runs, pol.archive_quiet_runs) == (2, 9, 6)


@pytest.mark.parametrize("missing", [
    "subtopic_lifecycle_mint_persistence_runs",
    "subtopic_lifecycle_demote_quiet_runs",
    "subtopic_lifecycle_archive_quiet_runs",
])
def test_policy_fail_loud_when_threshold_missing(tmp_path, missing):
    cfg = {
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 5,
        "subtopic_lifecycle_policy_enabled": True,
        "subtopic_lifecycle_mint_persistence_runs": 2,
        "subtopic_lifecycle_demote_quiet_runs": 12,
        "subtopic_lifecycle_archive_quiet_runs": 12,
    }
    cfg.pop(missing)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match=missing.replace("subtopic_lifecycle_", "")):
        LifecyclePolicy.from_config(p)


def test_policy_fail_loud_threshold_below_one(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 5,
        "subtopic_lifecycle_policy_enabled": True,
        "subtopic_lifecycle_mint_persistence_runs": 0,
        "subtopic_lifecycle_demote_quiet_runs": 12,
        "subtopic_lifecycle_archive_quiet_runs": 12,
    }))
    with pytest.raises(ValueError, match="mint_persistence_runs"):
        LifecyclePolicy.from_config(p)


def test_policy_non_bool_flag_rejected(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 5,
        "subtopic_lifecycle_policy_enabled": "false",  # string, must not coerce to enabled
    }))
    with pytest.raises(ValueError, match="must be a JSON boolean"):
        LifecyclePolicy.from_config(p)


def test_policy_off_tolerates_bad_thresholds(tmp_path):
    # Policy off -> the thresholds are inert; a bad value must not block an unrelated publish.
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 5,
        "subtopic_lifecycle_policy_enabled": False,
        "subtopic_lifecycle_mint_persistence_runs": -3,
        "subtopic_lifecycle_demote_quiet_runs": "x",
    }))
    pol = LifecyclePolicy.from_config(p)  # no raise
    assert pol.policy_enabled is False
    assert pol.mint_persistence_runs == 2 and pol.demote_quiet_runs == 12  # fall back to defaults


# ====================== MintPersistenceGate.admit ======================


def test_gate_below_floor_holds_without_candidate():
    t = FakeTable()
    g = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    assert g.admit(slug="tiny", n_papers=4) is False  # < floor 5
    assert (g.held, g.below_floor, g.minted) == (1, 1, 0)
    assert t.candidate("tiny") is None  # not staged -> GC resets any streak
    assert g.seen_slugs == set()


def test_gate_single_run_floor_mints_first_sighting_no_churn():
    t = FakeTable()
    g = MintPersistenceGate(t, P1, hierarchy_version=HV1)  # persistence 1
    assert g.admit(slug="big", n_papers=9) is True
    assert (g.minted, g.held, g.consumed) == (1, 0, 0)
    assert t.candidate("big") is None  # no create-then-consume churn
    assert t.put_calls == 0 and t.delete_calls == 0


def test_upsert_candidate_idempotent_per_hierarchy_version():
    # seen_runs counts DISTINCT runs (versions), NOT --publish invocations: a same-version
    # re-upsert (the skip path re-runs step-10 with the prior version) must NOT advance.
    t = FakeTable()
    assert upsert_candidate(t, slug="x", n_papers=6, hierarchy_version=HV1) == 1
    assert upsert_candidate(t, slug="x", n_papers=6, hierarchy_version=HV1) == 1  # same version
    assert t.candidate("x")["seen_runs"] == 1
    assert upsert_candidate(t, slug="x", n_papers=6, hierarchy_version=HV2) == 2  # new version


def test_gate_two_run_holds_then_mints_and_consumes():
    t = FakeTable()
    # run 1: first sighting -> seen_runs=1 < 2 -> HOLD, candidate accrues, protected from GC.
    g1 = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    assert g1.admit(slug="area", n_papers=8) is False
    assert g1.held == 1 and g1.seen_slugs == {"area"}
    assert t.candidate("area")["seen_runs"] == 1
    # run 2: reappears -> seen_runs=2 >= 2 -> MINT + consume the candidate row.
    g2 = MintPersistenceGate(t, P2, hierarchy_version=HV2)
    assert g2.admit(slug="area", n_papers=8) is True
    assert (g2.minted, g2.consumed, g2.held) == (1, 1, 0)
    assert t.candidate("area") is None  # consumed
    assert "area" not in g2.seen_slugs  # minted slug is NOT protected (its row is gone)


# ====================== delete_candidate / set_status ======================


def test_delete_candidate_removes_and_is_idempotent():
    t = FakeTable()
    upsert_candidate(t, slug="x", n_papers=6, hierarchy_version=HV1)
    assert t.candidate("x") is not None
    assert delete_candidate(t, slug="x") is True
    assert t.candidate("x") is None
    assert delete_candidate(t, slug="x") is True  # absent-key delete is a no-op, still True


def test_set_status_writes_status_only_and_preserves_fields():
    t = FakeTable()
    row = _prior_row("st_a", slug="aging_a", quiet=7)
    row["split_from"] = "st_parent"  # a brick-C lineage edge must survive
    t.put_item(Item=row)
    assert set_status(t, durable_id="st_a", status=STATUS_DEMOTED) is True
    after = t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_a", META_SK)]
    assert after["status"] == STATUS_DEMOTED
    assert after["consecutive_quiet_runs"] == 7  # counter untouched
    assert after["split_from"] == "st_parent" and after["label_at_mint"] == "Aging_A"


def test_set_status_idempotent_and_missing_row():
    t = FakeTable()
    t.put_item(Item=_prior_row("st_a", slug="aging_a", status=STATUS_DEMOTED))
    assert set_status(t, durable_id="st_a", status=STATUS_DEMOTED) is False  # already matches
    assert set_status(t, durable_id="ghost", status=STATUS_DEMOTED) is False  # absent row


# ====================== sweep_retire ======================


def test_retire_off_is_a_noop():
    t = FakeTable()
    snap = {"st_q": _snap_row(status=STATUS_ACTIVE, quiet=99)}
    out = sweep_retire(t, snapshot=snap, claimed_ids=set(), policy=P_OFF)
    assert out == {"enabled": False}
    assert t.put_calls == 0


def test_retire_demotes_at_threshold_not_before():
    t = FakeTable()
    t.put_item(Item=_prior_row("st_just", slug="a", status=STATUS_ACTIVE, quiet=2))   # +1 = 3 >= demote 3
    t.put_item(Item=_prior_row("st_under", slug="b", status=STATUS_ACTIVE, quiet=1))  # +1 = 2 < 3
    snap = {
        "st_just": _snap_row(slug="a", quiet=2),
        "st_under": _snap_row(slug="b", quiet=1),
    }
    out = sweep_retire(t, snapshot=snap, claimed_ids=set(), policy=RETIRE)
    assert out == {"enabled": True, "demoted": 1, "archived": 0, "undemoted": 0}
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_just", META_SK)]["status"] == STATUS_DEMOTED
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_under", META_SK)]["status"] == STATUS_ACTIVE


def test_retire_archives_when_past_archive_band():
    t = FakeTable()
    t.put_item(Item=_prior_row("st_old", slug="a", status=STATUS_DEMOTED, quiet=4))  # +1 = 5 >= 3+2
    snap = {"st_old": _snap_row(slug="a", status=STATUS_DEMOTED, quiet=4)}
    out = sweep_retire(t, snapshot=snap, claimed_ids=set(), policy=RETIRE)
    assert out["archived"] == 1 and out["demoted"] == 0
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_old", META_SK)]["status"] == STATUS_ARCHIVED


def test_retire_undemotes_claimed_demoted_and_archived():
    t = FakeTable()
    t.put_item(Item=_prior_row("st_d", slug="a", status=STATUS_DEMOTED, quiet=9))
    t.put_item(Item=_prior_row("st_x", slug="b", status=STATUS_ARCHIVED, quiet=9))
    t.put_item(Item=_prior_row("st_a", slug="c", status=STATUS_ACTIVE, quiet=0))  # claimed-active: no-op
    snap = {
        "st_d": _snap_row(slug="a", status=STATUS_DEMOTED, quiet=9),
        "st_x": _snap_row(slug="b", status=STATUS_ARCHIVED, quiet=9),
        "st_a": _snap_row(slug="c", status=STATUS_ACTIVE, quiet=0),
    }
    claimed = {"st_d", "st_x", "st_a"}
    out = sweep_retire(t, snapshot=snap, claimed_ids=claimed, policy=RETIRE)
    assert out["undemoted"] == 2  # demoted AND archived both revive; active is a no-op
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_d", META_SK)]["status"] == STATUS_ACTIVE
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_x", META_SK)]["status"] == STATUS_ACTIVE
    # un-demote writes status only; the counter reset is sweep_quiet_substrate's job.
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_d", META_SK)]["consecutive_quiet_runs"] == 9


def test_retire_monotone_forward_never_regresses_archived():
    # An archived row still quiet but only in the DEMOTE band (e.g. a lowered threshold)
    # must NOT be re-demoted; the rank guard keeps it archived.
    t = FakeTable()
    t.put_item(Item=_prior_row("st_x", slug="a", status=STATUS_ARCHIVED, quiet=2))  # +1 = 3 (demote band)
    snap = {"st_x": _snap_row(slug="a", status=STATUS_ARCHIVED, quiet=2)}
    out = sweep_retire(t, snapshot=snap, claimed_ids=set(), policy=RETIRE)
    assert out == {"enabled": True, "demoted": 0, "archived": 0, "undemoted": 0}
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_x", META_SK)]["status"] == STATUS_ARCHIVED


def test_retire_idempotent_no_write_when_already_in_target_band():
    t = FakeTable()
    t.put_item(Item=_prior_row("st_d", slug="a", status=STATUS_DEMOTED, quiet=2))  # +1 = 3, demote band == status
    snap = {"st_d": _snap_row(slug="a", status=STATUS_DEMOTED, quiet=2)}
    t.put_calls = 0
    out = sweep_retire(t, snapshot=snap, claimed_ids=set(), policy=RETIRE)
    assert out == {"enabled": True, "demoted": 0, "archived": 0, "undemoted": 0}
    assert t.put_calls == 0  # no status churn on a content-identical row


# ====================== reconcile_durable_ids + mint_gate ======================


def test_reconcile_gate_holds_then_mints_next_run():
    store = _store()
    t = store._table
    m = _membership({"area": [1, 2, 3, 4, 5, 6]})  # 6 >= floor 5
    h = _hierarchy(["area"])
    # run 1: held (persistence 2, seen_runs hits 1)
    g1 = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    s1 = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV1), mint_gate=g1)
    assert s1["minted"] == 0 and s1["attached"] == 0 and s1["minted_held"] == 1
    # NO half-state: no durable id row, no slug pointer; only the candidate accrues.
    assert t.id_rows() == [] and t.ptr_for("area") is None
    assert t.candidate("area")["seen_runs"] == 1
    # run 2: persists -> mints + consumes candidate
    g2 = MintPersistenceGate(t, P2, hierarchy_version=HV2)
    s2 = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV2), mint_gate=g2)
    assert s2["minted"] == 1 and s2["minted_held"] == 0
    assert len(t.id_rows()) == 1 and t.ptr_for("area") is not None
    assert t.candidate("area") is None  # consumed


def test_reconcile_gate_below_floor_leaves_no_halfstate():
    store = _store()
    t = store._table
    m = _membership({"tiny": [1, 2]})  # 2 < floor 5
    h = _hierarchy(["tiny"])
    g = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    s = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV1), mint_gate=g)
    assert s["minted"] == 0 and s["minted_held"] == 1
    assert t.id_rows() == [] and t.ptr_for("tiny") is None and t.candidate("tiny") is None


def test_reconcile_no_gate_is_byte_identical_to_pr1():
    store = _store()
    t = store._table
    m = _membership({"a": [1, 2], "b": []})
    h = _hierarchy(["a", "b"])
    s = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx())  # mint_gate=None
    assert s["minted"] == 2 and s["attached"] == 0
    assert "minted_held" not in s  # PR-1 event shape preserved
    assert len(t.id_rows()) == 2
    assert not [v for v in t.items.values() if v.get("record_type") == RECORD_TYPE_SUBTOPIC_CANDIDATE]


def test_gate_seen_slugs_protects_accruing_candidate_through_gc():
    # The publish-level seam: gc_candidates(seen_slugs=gate.seen_slugs) must KEEP a
    # still-accruing held candidate while reclaiming one that did not reappear this run.
    t = FakeTable()
    g = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    assert g.admit(slug="held", n_papers=8) is False  # accruing -> in seen_slugs
    upsert_candidate(t, slug="vanished", n_papers=8, hierarchy_version=HV1)  # stale, not re-seen
    deleted = gc_candidates(t, seen_slugs=g.seen_slugs, policy=P2)
    assert deleted == 1
    assert t.candidate("held") is not None   # protected by seen_slugs
    assert t.candidate("vanished") is None   # reclaimed (burst-filter reset)


def test_reconcile_gate_same_version_republish_does_not_prematurely_mint():
    # A skip-path / same-content re-publish (same hierarchy_version) must NOT advance
    # persistence: the held cluster stays held, no premature mint, no half-state.
    store = _store()
    t = store._table
    m = _membership({"area": [1, 2, 3, 4, 5, 6]})
    h = _hierarchy(["area"])
    g1 = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV1), mint_gate=g1)
    assert t.candidate("area")["seen_runs"] == 1 and t.id_rows() == []  # held run 1
    # SAME version again -> still held, count NOT advanced, still no durable id
    g2 = MintPersistenceGate(t, P2, hierarchy_version=HV1)
    s2 = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV1), mint_gate=g2)
    assert s2["minted"] == 0 and s2["minted_held"] == 1
    assert t.candidate("area")["seen_runs"] == 1 and t.id_rows() == []
    # a genuinely NEW run (new version) advances to 2 -> mints + consumes
    g3 = MintPersistenceGate(t, P2, hierarchy_version=HV2)
    s3 = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV2), mint_gate=g3)
    assert s3["minted"] == 1 and t.candidate("area") is None


def test_policy_directly_constructed_incoherent_raises():
    # __post_init__ defense-in-depth: policy_enabled without substrate_enabled is rejected
    # even when bypassing from_config.
    with pytest.raises(ValueError, match="requires substrate_enabled"):
        LifecyclePolicy(substrate_enabled=False, mint_floor_papers=5, policy_enabled=True)


def test_reconcile_gate_does_not_gate_an_attach():
    store = _store()
    t = store._table
    m = _membership({"area": [1, 2, 3, 4, 5, 6]})
    h = _hierarchy(["area"])
    reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV1))  # mint it (no gate)
    # second pass with a gate: the slug now matches a prior -> attach, ungated.
    g = MintPersistenceGate(t, P2, hierarchy_version=HV2)
    s = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx(HV2), mint_gate=g)
    assert s["attached"] == 1 and s["minted"] == 0 and s["minted_held"] == 0
    assert g.held == 0  # admit() never consulted for a match
    assert t.candidate("area") is None  # no candidate created for an attach
