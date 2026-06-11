"""Tests for the durable id<->membership store + match-or-mint (#191, brick A).

Covers:
- pure record builders: keys, seed coercion, mutable vs mint-once fields, and
  byte-stability across reruns (the membership-sidecar posture);
- the read API (slug -> durable, durable -> row) over a dict-backed fake table;
- match-or-mint: mint writes both rows; attach refreshes mutable fields while
  preserving mint-once provenance and writes no new pointer;
- ``reconcile_durable_ids`` summary, 1:1 coverage, seedless-still-minted, and
  idempotency on a second pass;
- seed_pmids are stored as ``int`` (DynamoDB rejects float).
"""

from __future__ import annotations

import json
import random

from pipeline_hierarchy.bundler import MEMBERSHIP_KIND
from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    PTR_SK,
    STATUS_ACTIVE,
    SUBTOPIC_ID_PK_PREFIX,
    SUBTOPIC_SLUG_PK_PREFIX,
    ALIAS_SCHEMA_VERSION,
    MintContext,
    SubtopicIdStore,
    SubtopicMatch,
    build_alias_map,
    build_slug_pointer_record,
    build_subtopic_id_record,
    get_subtopic_row,
    load_slug_pointer_map,
    reconcile_durable_ids,
    resolve_durable_id,
    set_lineage,
)
from pipeline_hierarchy.subtopic_ids import SubtopicIdMinter, is_subtopic_id


class FakeTable:
    """Minimal dict-backed stand-in for a boto3 Table resource (get/put only)."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.put_calls = 0

    def get_item(self, Key):
        key = (Key["PK"], Key["SK"])
        return {"Item": self.items[key]} if key in self.items else {}

    def put_item(self, Item):
        self.put_calls += 1
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def metas(self):
        return [v for (_pk, sk), v in self.items.items() if sk == META_SK]

    def ptrs(self):
        return [v for (_pk, sk), v in self.items.items() if sk == PTR_SK]


def _ctx(run_id="run-1", created_at="2026-06-11T00:00:00Z", hv="v2026-06-11"):
    return MintContext(
        run_id=run_id,
        created_at=created_at,
        taxonomy_version="taxonomy_v2",
        hierarchy_version=hv,
    )


def _store(seed=7):
    return SubtopicIdStore(FakeTable(), SubtopicIdMinter(rand=random.Random(seed).random))


# ---------- record builders ----------


def test_subtopic_id_record_keys_and_fields():
    rec = build_subtopic_id_record(
        durable_id="st_abc",
        slug_id="aging_one",
        topic_id="aging",
        seed_pmids=["30", 10, 10, "20"],
        label_at_mint="One",
        taxonomy_version="taxonomy_v2",
        hierarchy_version="v2026-06-11",
        created_at="2026-06-11T00:00:00Z",
        first_run_id="run-1",
        last_seen_run_id="run-1",
    )
    assert rec["PK"] == f"{SUBTOPIC_ID_PK_PREFIX}st_abc"
    assert rec["SK"] == META_SK
    assert rec["seed_pmids"] == [10, 20, 30]  # int-coerced, de-duped, sorted
    assert all(isinstance(p, int) for p in rec["seed_pmids"])  # never float
    assert rec["membership_kind"] == MEMBERSHIP_KIND
    assert rec["status"] == STATUS_ACTIVE
    assert rec["label_at_mint"] == "One"


def test_build_alias_map_inverts_snapshot_to_slug_keyed_redirect_map():
    # snapshot shape mirrors load_id_store_snapshot: {durable_id: {slug_id, topic_id,
    # status, ...}}. build_alias_map inverts to slug -> {durable_id, parent, status}.
    snapshot = {
        "st_two": {"slug_id": "aging_two", "topic_id": "aging", "status": "active"},
        "st_one": {"slug_id": "aging_one", "topic_id": "aging", "status": "active"},
        "st_nodurable": {"slug_id": None, "topic_id": "aging", "status": "active"},  # skipped
    }
    out = build_alias_map(snapshot, hierarchy_version="v2026-06-11", taxonomy_version="taxonomy_v2")
    assert out["alias_schema_version"] == ALIAS_SCHEMA_VERSION
    assert out["hierarchy_version"] == "v2026-06-11"
    assert out["taxonomy_version"] == "taxonomy_v2"
    assert out["subtopic_count"] == 2  # the slug_id=None row is skipped
    # slug-keyed, slug-ordered, carrying durable + parent + status
    assert list(out["aliases"].keys()) == ["aging_one", "aging_two"]
    assert out["aliases"]["aging_one"] == {
        "durable_id": "st_one",
        "parent_topic_id": "aging",
        "status": "active",
    }


def test_build_alias_map_defaults_missing_status_to_active_and_is_byte_stable():
    snapshot = {"st_x": {"slug_id": "aging_x", "topic_id": "aging"}}  # no status key
    out = build_alias_map(snapshot, hierarchy_version="v1", taxonomy_version="t")
    assert out["aliases"]["aging_x"]["status"] == STATUS_ACTIVE
    # byte-stable under sort_keys (the publish serialization posture)
    assert json.dumps(out, sort_keys=True) == json.dumps(
        build_alias_map(snapshot, hierarchy_version="v1", taxonomy_version="t"),
        sort_keys=True,
    )


def test_build_alias_map_empty_snapshot():
    out = build_alias_map({}, hierarchy_version="v1", taxonomy_version="t")
    assert out["subtopic_count"] == 0 and out["aliases"] == {}


def test_slug_pointer_record_shape():
    rec = build_slug_pointer_record(
        slug_id="aging_one", durable_id="st_abc", created_at="2026-06-11T00:00:00Z"
    )
    assert rec["PK"] == f"{SUBTOPIC_SLUG_PK_PREFIX}aging_one"
    assert rec["SK"] == PTR_SK
    assert rec["durable_id"] == "st_abc"


def test_record_serialization_is_byte_stable_across_reruns():
    def serialize():
        rec = build_subtopic_id_record(
            durable_id="st_abc",
            slug_id="aging_one",
            topic_id="aging",
            seed_pmids=[3, 1, 2],
            label_at_mint="One",
            taxonomy_version="taxonomy_v2",
            hierarchy_version="v1",
            created_at="2026-06-11T00:00:00Z",
            first_run_id="run-1",
            last_seen_run_id="run-1",
        )
        return json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")

    assert serialize() == serialize()


# ---------- read API ----------


class _ScanTable:
    """Paginating scan stub (ignores FilterExpression, like the suite's other scan
    fakes — the function under test self-filters by record_type)."""

    def __init__(self, items, page=100):
        self._items = items
        self._page = page

    def scan(self, **kwargs):
        start = kwargs.get("ExclusiveStartKey", 0)
        chunk = self._items[start : start + self._page]
        resp = {"Items": chunk}
        nxt = start + self._page
        if nxt < len(self._items):
            resp["LastEvaluatedKey"] = nxt
        return resp


def test_load_slug_pointer_map_inverts_pointers_skips_meta_and_paginates():
    items = [
        build_slug_pointer_record(slug_id="aging_one", durable_id="st_one", created_at="t"),
        build_slug_pointer_record(slug_id="aging_two", durable_id="st_two", created_at="t"),
        # A META row that ALSO carries slug_id + durable_id: must be skipped (the map is
        # slug-keyed; a META row would invert the wrong direction).
        build_subtopic_id_record(
            durable_id="st_one", slug_id="aging_one", topic_id="aging", seed_pmids=[1],
            label_at_mint="One", taxonomy_version="t", hierarchy_version="v1",
            created_at="t", first_run_id="r", last_seen_run_id="r",
        ),
    ]
    m = load_slug_pointer_map(_ScanTable(items, page=1))  # page=1 forces pagination
    assert m == {"aging_one": "st_one", "aging_two": "st_two"}


def test_load_slug_pointer_map_empty():
    assert load_slug_pointer_map(_ScanTable([], page=10)) == {}


def test_resolve_and_get_over_fake_table():
    t = FakeTable()
    assert resolve_durable_id(t, slug_id="missing") is None
    assert get_subtopic_row(t, durable_id="st_missing") is None
    t.put_item(Item=build_slug_pointer_record(slug_id="s1", durable_id="st_x", created_at="t"))
    assert resolve_durable_id(t, slug_id="s1") == "st_x"


# ---------- match ----------


def test_match_returns_none_when_unminted_then_match_after_mint():
    store = _store()
    assert store.match(slug="aging_one", membership=[1, 2], topic_id="aging") is None
    store.match_or_mint(
        slug="aging_one", membership=[1, 2], topic_id="aging", label="One", ctx=_ctx()
    )
    hit = store.match(slug="aging_one", membership=[1, 2], topic_id="aging")
    assert isinstance(hit, SubtopicMatch)
    assert hit.reason == "exact_slug" and hit.score == 1.0
    assert is_subtopic_id(hit.durable_id)


# ---------- match_or_mint ----------


def test_mint_writes_primary_and_pointer_rows():
    store = _store()
    durable, action = store.match_or_mint(
        slug="aging_one", membership=[2, 1], topic_id="aging", label="One", ctx=_ctx()
    )
    assert action == "minted"
    t = store._table
    assert len(t.metas()) == 1 and len(t.ptrs()) == 1
    meta = t.metas()[0]
    assert meta["durable_id"] == durable and meta["slug_id"] == "aging_one"
    assert meta["seed_pmids"] == [1, 2] and meta["first_run_id"] == "run-1"
    assert t.ptrs()[0]["durable_id"] == durable


def test_attach_refreshes_mutable_and_preserves_mint_once():
    store = _store()
    durable, _ = store.match_or_mint(
        slug="aging_one", membership=[1], topic_id="aging", label="One", ctx=_ctx()
    )
    t = store._table
    pointer_rows_after_mint = len(t.ptrs())

    durable2, action = store.match_or_mint(
        slug="aging_one",
        membership=[9, 8],
        topic_id="aging_v2",
        label="One (relabelled)",  # a fresh label must NOT overwrite label_at_mint
        ctx=_ctx(run_id="run-2", created_at="2026-07-01T00:00:00Z", hv="v2026-07-01"),
    )
    assert action == "attached" and durable2 == durable
    meta = t.metas()[0]
    # mint-once preserved:
    assert meta["created_at"] == "2026-06-11T00:00:00Z"
    assert meta["first_run_id"] == "run-1"
    assert meta["label_at_mint"] == "One"
    # mutable refreshed:
    assert meta["seed_pmids"] == [8, 9]
    assert meta["topic_id"] == "aging_v2"
    assert meta["hierarchy_version"] == "v2026-07-01"
    assert meta["last_seen_run_id"] == "run-2"
    # no new pointer row on attach (slug unchanged in brick A):
    assert len(t.ptrs()) == pointer_rows_after_mint


# ---------- reconcile_durable_ids ----------


def _membership(seeds_by_slug, topic="aging"):
    return {
        "subtopics": {
            slug: {"topic_id": topic, "seed_pmids": seeds}
            for slug, seeds in seeds_by_slug.items()
        }
    }


def _hierarchy(slugs, topic="aging"):
    return {
        "topics": {
            topic: {"subtopics": [{"id": s, "label": s.title()} for s in slugs]}
        }
    }


def test_reconcile_mints_each_subtopic_including_seedless():
    store = _store()
    m = _membership({"aging_one": [1, 2], "aging_two": []})
    h = _hierarchy(["aging_one", "aging_two"])
    summary = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx())
    assert summary == {"subtopic_count": 2, "minted": 2, "attached": 0}
    assert len(store._table.metas()) == 2  # seedless subtopic still got a durable id


def test_dangling_pointer_remints_instead_of_fabricating_provenance():
    # A slug pointer that resolves to a durable id with no META row (external
    # corruption / a future brick's deletion) must NOT silently attach with
    # fabricated provenance — it re-mints an honest row and self-heals the pointer.
    store = _store()
    t = store._table
    t.put_item(
        Item=build_slug_pointer_record(
            slug_id="ghosted", durable_id="st_ghost", created_at="2020-01-01T00:00:00Z"
        )
    )  # pointer with no matching META row
    durable, action = store.match_or_mint(
        slug="ghosted", membership=[5], topic_id="aging", label="Ghost", ctx=_ctx()
    )
    assert action == "minted"
    assert durable != "st_ghost"  # a fresh, honest id
    meta = [m for m in t.metas() if m["slug_id"] == "ghosted"][0]
    assert meta["durable_id"] == durable and meta["label_at_mint"] == "Ghost"
    assert resolve_durable_id(t, slug_id="ghosted") == durable  # pointer self-healed


def test_attach_is_additive_and_preserves_unknown_future_fields():
    # A field a later brick (C lineage / ops backfill) persists onto the row must
    # survive an attach, not be erased by the full-row rebuild.
    store = _store()
    durable, _ = store.match_or_mint(
        slug="aging_one", membership=[1], topic_id="aging", label="One", ctx=_ctx()
    )
    t = store._table
    key = (f"{SUBTOPIC_ID_PK_PREFIX}{durable}", "META")
    t.items[key]["split_from"] = "st_parent"  # simulate a brick-C field
    store.match_or_mint(
        slug="aging_one", membership=[2], topic_id="aging", label="One", ctx=_ctx(run_id="run-2")
    )
    assert t.items[key]["split_from"] == "st_parent"  # survived the attach
    assert t.items[key]["seed_pmids"] == [2]  # mutable still refreshed


# ---------- brick C: set_lineage (split_from / merged_into edges) ----------


def test_set_lineage_writes_split_from_and_merged_into():
    store = _store()
    durable, _ = store.match_or_mint(
        slug="child", membership=[1], topic_id="aging", label="C", ctx=_ctx()
    )
    t = store._table
    key = (f"{SUBTOPIC_ID_PK_PREFIX}{durable}", META_SK)
    assert set_lineage(t, durable_id=durable, split_from="st_parent") is True
    assert t.items[key]["split_from"] == "st_parent"
    assert "merged_into" not in t.items[key]  # only the provided edge is written
    # a later merged_into edge is additive and leaves split_from intact
    assert set_lineage(t, durable_id=durable, merged_into="st_succ") is True
    assert t.items[key]["merged_into"] == "st_succ"
    assert t.items[key]["split_from"] == "st_parent"


def test_set_lineage_is_idempotent_and_never_touches_status():
    store = _store()
    durable, _ = store.match_or_mint(
        slug="x", membership=[1], topic_id="aging", label="X", ctx=_ctx()
    )
    t = store._table
    key = (f"{SUBTOPIC_ID_PK_PREFIX}{durable}", META_SK)
    assert set_lineage(t, durable_id=durable, split_from="st_p") is True
    puts_after_first = t.put_calls
    # same edge again -> no write
    assert set_lineage(t, durable_id=durable, split_from="st_p") is False
    assert t.put_calls == puts_after_first
    # no edges provided -> no write
    assert set_lineage(t, durable_id=durable) is False
    assert t.put_calls == puts_after_first
    # status is brick F's; brick C never mutates it
    assert t.items[key]["status"] == STATUS_ACTIVE


def test_set_lineage_returns_false_for_missing_row():
    store = _store()
    t = store._table
    assert set_lineage(t, durable_id="st_absent", split_from="st_p") is False
    assert t.put_calls == 0


def test_read_subtopic_row_int_coerces_decimal_seed_pmids():
    from decimal import Decimal

    from pipeline_hierarchy.subtopic_id_store import read_subtopic_row

    t = FakeTable()
    t.put_item(
        Item={
            "PK": f"{SUBTOPIC_ID_PK_PREFIX}st_x",
            "SK": "META",
            "durable_id": "st_x",
            "seed_pmids": [Decimal(30), Decimal(10), Decimal(20)],  # as DDB returns them
        }
    )
    row = read_subtopic_row(t, durable_id="st_x")
    assert row["seed_pmids"] == [10, 20, 30]
    assert all(isinstance(p, int) for p in row["seed_pmids"])
    assert read_subtopic_row(t, durable_id="st_missing") is None


def test_reconcile_empty_or_absent_membership_is_a_noop():
    store = _store()
    h = _hierarchy([])
    assert reconcile_durable_ids(store, membership={"subtopics": {}}, hierarchy=h, ctx=_ctx()) == {
        "subtopic_count": 0,
        "minted": 0,
        "attached": 0,
    }
    assert reconcile_durable_ids(store, membership={}, hierarchy=h, ctx=_ctx()) == {
        "subtopic_count": 0,
        "minted": 0,
        "attached": 0,
    }
    assert store._table.metas() == []


def test_reconcile_membership_id_absent_from_hierarchy_mints_with_empty_label():
    # A membership entry with no matching hierarchy subtopic still mints (durable
    # id is membership-keyed), with label_at_mint='' — pins the documented fallback.
    store = _store()
    m = _membership({"orphan_id": [1]})
    h = _hierarchy(["different_id"])  # orphan_id not present
    summary = reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx())
    assert summary["minted"] == 1
    assert store._table.metas()[0]["label_at_mint"] == ""


def test_reconcile_is_idempotent_on_second_pass():
    store = _store()
    m = _membership({"aging_one": [1, 2], "aging_two": [3]})
    h = _hierarchy(["aging_one", "aging_two"])
    reconcile_durable_ids(store, membership=m, hierarchy=h, ctx=_ctx())
    durables_pass1 = {r["slug_id"]: r["durable_id"] for r in store._table.metas()}

    summary2 = reconcile_durable_ids(
        store, membership=m, hierarchy=h, ctx=_ctx(run_id="run-2")
    )
    assert summary2 == {"subtopic_count": 2, "minted": 0, "attached": 2}
    durables_pass2 = {r["slug_id"]: r["durable_id"] for r in store._table.metas()}
    assert durables_pass1 == durables_pass2  # ids are durable across runs
    assert len(store._table.metas()) == 2  # no new rows minted
