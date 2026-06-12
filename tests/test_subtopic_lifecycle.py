"""Tests for the Brick F PR-1 substrate (#191): forward-only lifecycle data capture.

PR-1 is the SUBSTRATE only — it records the data PR-2's mint-persistence gate and
retire transitions will later read. These tests pin the PR-1/PR-2 boundary: the
module ++a quiet COUNTER on unclaimed priors, stamps last_active on claimed priors,
and accrues seen_runs on SUBTOPIC_CANDIDATE# staging rows — but writes NO status
transition and gates NO mint. Everything runs offline over a dict-backed fake table.

Covered:
- LifecyclePolicy.from_config: real-config parse, fail-soft (absent flag), fail-loud
  (enabled-but-misconfigured), non-boolean flag rejection;
- sweep_quiet_substrate: OFF no-op; ++quiet on unclaimed, reset+stamp on claimed;
  never mutates status/lineage/mint-once; idempotent;
- stage_candidates: OFF no-op; create+accrue seen_runs; below-floor exclusion; no
  mint interaction;
- gc_candidates: burst-filter delete of unseen slugs; OFF no-op; churn reset.
"""

from __future__ import annotations

import json

import pytest

from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    RECORD_TYPE_SUBTOPIC_CANDIDATE,
    RECORD_TYPE_SUBTOPIC_ID,
    STATUS_ACTIVE,
    SUBTOPIC_CANDIDATE_PK_PREFIX,
    SUBTOPIC_ID_PK_PREFIX,
    set_quiet_substrate,
)
from pipeline_hierarchy.subtopic_lifecycle import (
    LifecyclePolicy,
    gc_candidates,
    stage_candidates,
    sweep_quiet_substrate,
)

HV1 = "v2026-06-11"
HV2 = "v2026-06-12"
HV3 = "v2026-06-13"


class FakeTable:
    """Dict-backed boto3 Table stand-in: get/put/delete + paginating scan.

    Extends the suite's other fakes with delete_item(Key=) and a record_type-aware
    scan so the lifecycle GC pass (SUBTOPIC_CANDIDATE# Scan + delete) runs offline.
    """

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
        # Filter-agnostic paginating scan (function-under-test self-filters by
        # record_type, mirroring the suite's _ScanTable convention).
        rows = list(self.items.values())
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = rows[start : start + self._page]
        resp = {"Items": chunk}
        nxt = start + self._page
        if nxt < len(rows):
            resp["LastEvaluatedKey"] = nxt
        return resp

    # helpers
    def candidate_rows(self):
        return [
            v
            for v in self.items.values()
            if v.get("record_type") == RECORD_TYPE_SUBTOPIC_CANDIDATE
        ]


def _prior_row(durable_id, *, slug, topic="aging", status=STATUS_ACTIVE, extra=None):
    row = {
        "PK": f"{SUBTOPIC_ID_PK_PREFIX}{durable_id}",
        "SK": META_SK,
        "record_type": RECORD_TYPE_SUBTOPIC_ID,
        "durable_id": durable_id,
        "slug_id": slug,
        "topic_id": topic,
        "seed_pmids": [1, 2, 3],
        "status": status,
        "label_at_mint": slug.title(),
        "created_at": "2026-01-01T00:00:00Z",
        "first_run_id": "run-0",
    }
    if extra:
        row.update(extra)
    return row


def _seed_prior(table, durable_id, **kw):
    row = _prior_row(durable_id, **kw)
    table.put_item(Item=row)
    return row


ON = LifecyclePolicy(substrate_enabled=True, mint_floor_papers=5)
OFF = LifecyclePolicy(substrate_enabled=False, mint_floor_papers=5)


# ---------- LifecyclePolicy.from_config ----------


def test_from_config_parses_real_thresholds():
    p = LifecyclePolicy.from_config()
    assert isinstance(p.substrate_enabled, bool)
    assert isinstance(p.mint_floor_papers, int) and p.mint_floor_papers >= 1


def test_from_config_fail_soft_when_flag_absent(tmp_path):
    # A pre-PR-1 config that omits the substrate flag entirely still loads, inert.
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"some_other_key": 1}))
    pol = LifecyclePolicy.from_config(p)
    assert pol.substrate_enabled is False  # fail-soft to disabled, no raise


def test_from_config_fail_loud_when_enabled_but_floor_missing(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({"subtopic_lifecycle_substrate_enabled": True}))
    with pytest.raises(ValueError, match="mint_floor_papers"):
        LifecyclePolicy.from_config(p)


def test_from_config_fail_loud_when_enabled_but_floor_non_int(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": "5",  # string, not int
    }))
    with pytest.raises(ValueError, match="mint_floor_papers"):
        LifecyclePolicy.from_config(p)


def test_from_config_fail_loud_when_enabled_but_floor_below_one(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 0,
    }))
    with pytest.raises(ValueError, match="mint_floor_papers"):
        LifecyclePolicy.from_config(p)


def test_from_config_rejects_non_boolean_flag(tmp_path):
    # mirror ReconcileThresholds' non-boolean arbiter-flag guard: a "false" string
    # must NOT coerce to enabled (bool("false") is True).
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": "false",
        "subtopic_lifecycle_mint_floor_papers": 5,
    }))
    with pytest.raises(ValueError, match="must be a JSON boolean"):
        LifecyclePolicy.from_config(p)


def test_from_config_enabled_true_parses(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps({
        "subtopic_lifecycle_substrate_enabled": True,
        "subtopic_lifecycle_mint_floor_papers": 7,
    }))
    pol = LifecyclePolicy.from_config(p)
    assert pol.substrate_enabled is True and pol.mint_floor_papers == 7


# ---------- sweep_quiet_substrate ----------


def test_sweep_off_is_a_noop():
    t = FakeTable()
    _seed_prior(t, "st_a", slug="aging_a")
    t.put_calls = 0  # ignore the seed put; we only care the sweep writes nothing
    out = sweep_quiet_substrate(
        t, claimed_ids=set(), all_prior_ids={"st_a"}, hierarchy_version=HV1, policy=OFF
    )
    assert out == {"enabled": False}
    assert t.put_calls == 0  # zero writes
    assert "consecutive_quiet_runs" not in t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_a", META_SK)]


def test_sweep_increments_unclaimed_and_resets_claimed():
    t = FakeTable()
    _seed_prior(t, "st_claimed", slug="aging_c")
    _seed_prior(t, "st_quiet", slug="aging_q")

    # run 1: st_claimed matched, st_quiet not.
    out = sweep_quiet_substrate(
        t,
        claimed_ids={"st_claimed"},
        all_prior_ids={"st_claimed", "st_quiet"},
        hierarchy_version=HV1,
        policy=ON,
    )
    assert out["enabled"] is True and out["claimed"] == 1 and out["quiet_incremented"] == 1
    claimed = t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_claimed", META_SK)]
    quiet = t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_quiet", META_SK)]
    assert claimed["consecutive_quiet_runs"] == 0
    assert claimed["last_active_hierarchy_version"] == HV1
    assert quiet["consecutive_quiet_runs"] == 1

    # run 2 + run 3: st_quiet still unclaimed -> 2 -> 3.
    sweep_quiet_substrate(
        t, claimed_ids={"st_claimed"}, all_prior_ids={"st_claimed", "st_quiet"},
        hierarchy_version=HV2, policy=ON,
    )
    sweep_quiet_substrate(
        t, claimed_ids={"st_claimed"}, all_prior_ids={"st_claimed", "st_quiet"},
        hierarchy_version=HV3, policy=ON,
    )
    assert t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_quiet", META_SK)]["consecutive_quiet_runs"] == 3
    # a previously-quiet id reclaimed resets to 0 + restamps last-active.
    sweep_quiet_substrate(
        t, claimed_ids={"st_quiet"}, all_prior_ids={"st_claimed", "st_quiet"},
        hierarchy_version=HV3, policy=ON,
    )
    requiet = t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_quiet", META_SK)]
    assert requiet["consecutive_quiet_runs"] == 0
    assert requiet["last_active_hierarchy_version"] == HV3


def test_sweep_never_mutates_status_lineage_or_mint_once():
    t = FakeTable()
    _seed_prior(
        t, "st_x", slug="aging_x",
        extra={"split_from": "st_parent", "merged_into": "st_succ"},
    )
    before = dict(t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_x", META_SK)])
    sweep_quiet_substrate(
        t, claimed_ids=set(), all_prior_ids={"st_x"}, hierarchy_version=HV1, policy=ON
    )
    after = t.items[(f"{SUBTOPIC_ID_PK_PREFIX}st_x", META_SK)]
    for k in ("status", "slug_id", "split_from", "merged_into", "seed_pmids",
              "label_at_mint", "created_at", "first_run_id"):
        assert after[k] == before[k], f"{k} must be untouched by the quiet sweep"
    assert after["status"] == STATUS_ACTIVE  # PR-1 never transitions status
    assert after["consecutive_quiet_runs"] == 1  # only the counter changed


def test_sweep_is_idempotent_for_already_zero_claimed_row():
    t = FakeTable()
    _seed_prior(t, "st_a", slug="aging_a")
    sweep_quiet_substrate(
        t, claimed_ids={"st_a"}, all_prior_ids={"st_a"}, hierarchy_version=HV1, policy=ON
    )
    puts_after_first = t.put_calls
    # second identical sweep: counter already 0 AND last-active already HV1 -> no write.
    out = sweep_quiet_substrate(
        t, claimed_ids={"st_a"}, all_prior_ids={"st_a"}, hierarchy_version=HV1, policy=ON
    )
    assert t.put_calls == puts_after_first  # nothing re-written
    assert out["rows_written"] == 0


def test_sweep_skips_missing_prior_row_without_fabricating():
    t = FakeTable()  # no rows
    out = sweep_quiet_substrate(
        t, claimed_ids={"st_ghost"}, all_prior_ids={"st_ghost"},
        hierarchy_version=HV1, policy=ON
    )
    assert t.put_calls == 0  # set_quiet_substrate returned False for the missing row
    assert out["rows_written"] == 0


def test_set_quiet_substrate_returns_false_for_missing_row():
    t = FakeTable()
    assert set_quiet_substrate(t, durable_id="st_absent", reset=False) is False
    assert t.put_calls == 0


# ---------- stage_candidates ----------


def _mint_bound(*pairs):
    """pairs: (slug, n_papers) -> the mint_bound contract list of dicts."""
    return [{"slug": s, "n_papers": n} for s, n in pairs]


def test_stage_off_is_a_noop():
    t = FakeTable()
    out = stage_candidates(
        t, mint_bound=_mint_bound(("new_area", 9)), hierarchy_version=HV1, policy=OFF
    )
    assert out == {"enabled": False, "seen_slugs": set()}
    assert t.candidate_rows() == []


def test_stage_creates_then_accrues_seen_runs_and_preserves_first_seen():
    t = FakeTable()
    out1 = stage_candidates(
        t, mint_bound=_mint_bound(("new_area", 9)), hierarchy_version=HV1, policy=ON
    )
    assert out1["enabled"] is True and out1["staged"] == 1
    assert out1["seen_slugs"] == {"new_area"}
    row = t.items[(f"{SUBTOPIC_CANDIDATE_PK_PREFIX}new_area", META_SK)]
    assert row["record_type"] == RECORD_TYPE_SUBTOPIC_CANDIDATE
    assert row["seen_runs"] == 1
    assert row["first_seen_hierarchy_version"] == HV1
    assert row["last_seen_hierarchy_version"] == HV1
    assert row["n_papers_last"] == 9

    # re-run: same slug reappears mint-bound -> seen_runs ++ to 2, first_seen preserved.
    out2 = stage_candidates(
        t, mint_bound=_mint_bound(("new_area", 11)), hierarchy_version=HV2, policy=ON
    )
    assert out2["seen_slugs"] == {"new_area"}
    row = t.items[(f"{SUBTOPIC_CANDIDATE_PK_PREFIX}new_area", META_SK)]
    assert row["seen_runs"] == 2
    assert row["first_seen_hierarchy_version"] == HV1  # mint-once preserved
    assert row["last_seen_hierarchy_version"] == HV2  # refreshed
    assert row["n_papers_last"] == 11  # refreshed


def test_stage_below_floor_is_not_staged_and_not_in_seen_slugs():
    t = FakeTable()
    out = stage_candidates(
        t,
        mint_bound=_mint_bound(("big", 9), ("tiny", 3)),  # floor=5
        hierarchy_version=HV1,
        policy=ON,
    )
    assert out["staged"] == 1 and out["below_floor"] == 1
    assert out["seen_slugs"] == {"big"}
    assert (f"{SUBTOPIC_CANDIDATE_PK_PREFIX}tiny", META_SK) not in t.items


def test_stage_does_not_touch_subtopic_id_rows():
    # PR-1 staging records CANDIDATE# rows only; it never gates/holds a mint, so it
    # writes nothing under the SUBTOPIC_ID# / SUBTOPIC_SLUG# namespaces.
    t = FakeTable()
    stage_candidates(
        t, mint_bound=_mint_bound(("new_area", 9)), hierarchy_version=HV1, policy=ON
    )
    assert all(
        not pk.startswith(SUBTOPIC_ID_PK_PREFIX) for (pk, _sk) in t.items
    )


def test_stage_at_floor_boundary_is_inclusive():
    t = FakeTable()
    out = stage_candidates(
        t, mint_bound=_mint_bound(("exactly_floor", 5)), hierarchy_version=HV1, policy=ON
    )
    assert out["seen_slugs"] == {"exactly_floor"}  # n_papers >= floor is inclusive


# ---------- gc_candidates ----------


def test_gc_off_is_a_noop():
    t = FakeTable()
    stage_candidates(t, mint_bound=_mint_bound(("a", 9)), hierarchy_version=HV1, policy=ON)
    assert gc_candidates(t, seen_slugs={"a"}, policy=OFF) == 0
    assert t.delete_calls == 0


def test_gc_deletes_unseen_and_keeps_seen():
    t = FakeTable()
    stage_candidates(
        t, mint_bound=_mint_bound(("keep", 9), ("drop", 9)), hierarchy_version=HV1, policy=ON
    )
    deleted = gc_candidates(t, seen_slugs={"keep"}, policy=ON)
    assert deleted == 1
    assert (f"{SUBTOPIC_CANDIDATE_PK_PREFIX}keep", META_SK) in t.items
    assert (f"{SUBTOPIC_CANDIDATE_PK_PREFIX}drop", META_SK) not in t.items


def test_gc_never_touches_subtopic_id_rows():
    t = FakeTable()
    _seed_prior(t, "st_a", slug="aging_a")  # a real durable row
    stage_candidates(t, mint_bound=_mint_bound(("c", 9)), hierarchy_version=HV1, policy=ON)
    gc_candidates(t, seen_slugs=set(), policy=ON)  # GC everything candidate-side
    assert (f"{SUBTOPIC_ID_PK_PREFIX}st_a", META_SK) in t.items  # durable row survives


def test_candidate_slug_churn_resets_the_count():
    # A slug staged run-1, ABSENT run-2 (GC'd), reappearing run-3 starts back at 1
    # — the burst filter (a one-run blip can never accrue seen_runs >= 2).
    t = FakeTable()
    # run 1: 'oscillator' appears
    stage_candidates(t, mint_bound=_mint_bound(("oscillator", 9)), hierarchy_version=HV1, policy=ON)
    gc_candidates(t, seen_slugs={"oscillator"}, policy=ON)
    assert t.items[(f"{SUBTOPIC_CANDIDATE_PK_PREFIX}oscillator", META_SK)]["seen_runs"] == 1
    # run 2: 'oscillator' absent (some other slug mint-bound) -> GC removes it
    stage_candidates(t, mint_bound=_mint_bound(("other", 9)), hierarchy_version=HV2, policy=ON)
    gc_candidates(t, seen_slugs={"other"}, policy=ON)
    assert (f"{SUBTOPIC_CANDIDATE_PK_PREFIX}oscillator", META_SK) not in t.items
    # run 3: 'oscillator' reappears -> seen_runs back to 1, not 2
    stage_candidates(t, mint_bound=_mint_bound(("oscillator", 9)), hierarchy_version=HV3, policy=ON)
    assert t.items[(f"{SUBTOPIC_CANDIDATE_PK_PREFIX}oscillator", META_SK)]["seen_runs"] == 1
