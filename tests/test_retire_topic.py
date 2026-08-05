"""Checks for cli/retire_topic.py — the #307 topic-retirement delete tool.

The load-bearing safety properties: the slug boundary (never touch the separate
benign `hematology` topic), the taxonomy-presence ordering guard, and that a
dry-run deletes nothing.
"""
import json
import pytest
from pathlib import Path
import sys

from cli.retire_topic import (
    belongs_to_topic,
    delete_topic_partition,
    delete_subtopic_score_partitions,
    topic_in_taxonomy,
)

HEME = "hematology_medical_oncology"


def test_slug_boundary_never_matches_benign_hematology():
    # the topic being retired
    assert belongs_to_topic(f"SUBTOPIC_SCORE#{HEME}#leukemia", HEME)
    assert belongs_to_topic(f"SUBTOPIC_SCORE_INCLUSIVE#{HEME}#lymphoma", HEME)
    # the SEPARATE benign topic must never be swept in
    assert not belongs_to_topic("SUBTOPIC_SCORE#hematology#anemia", HEME)
    assert not belongs_to_topic("SUBTOPIC_SCORE_INCLUSIVE#hematology#hemostasis", HEME)
    # and the reverse: retiring benign hematology must not hit heme/onc
    assert not belongs_to_topic(f"SUBTOPIC_SCORE#{HEME}#x", "hematology")


def test_taxonomy_presence_guard(tmp_path):
    p = tmp_path / "tax.json"
    p.write_text(json.dumps({"topics": [{"id": "cancer_biology_general"}, {"id": HEME}]}))
    assert topic_in_taxonomy(HEME, p)
    p.write_text(json.dumps({"topics": [{"id": "cancer_biology_general"}]}))
    assert not topic_in_taxonomy(HEME, p)


class _FakeTable:
    def __init__(self, items):
        self._items = items
        self.deleted = []

    def query(self, **kw):
        pk = kw["KeyConditionExpression"]._values[1]  # Key('PK').eq(<pk>)
        return {"Items": [i for i in self._items if i["PK"] == pk]}

    def scan(self, **kw):
        return {"Items": list(self._items)}

    def batch_writer(self):
        table = self

        class _BW:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def delete_item(self_, Key): table.deleted.append((Key["PK"], Key["SK"]))
        return _BW()


def test_dry_run_deletes_nothing_execute_deletes():
    items = [
        {"PK": f"TOPIC#{HEME}", "SK": "SCORE#0900#ACTIVITY#pmid_1#cwid_a"},
        {"PK": f"TOPIC#{HEME}", "SK": "SCORE#0800#ACTIVITY#pmid_2#cwid_b"},
        {"PK": "TOPIC#hematology", "SK": "SCORE#0700#ACTIVITY#pmid_3#cwid_c"},  # benign — must survive
    ]
    t = _FakeTable(items)
    assert delete_topic_partition(t, HEME, dry_run=True) == 2
    assert t.deleted == []  # dry-run touches nothing

    t2 = _FakeTable(items)
    assert delete_topic_partition(t2, HEME, dry_run=False) == 2
    assert all(pk == f"TOPIC#{HEME}" for pk, _ in t2.deleted)  # only heme rows deleted
    assert ("TOPIC#hematology", "SCORE#0700#ACTIVITY#pmid_3#cwid_c") not in t2.deleted


# ---------------------------------------------------------------------------
# TAXONOMY#{version}/META refresh
# ---------------------------------------------------------------------------


def _taxonomy_file(tmp_path, ids):
    p = tmp_path / "taxonomy_test.json"
    p.write_text(json.dumps({
        "taxonomy_version": "taxonomy_v2",
        "topics": [
            {"id": i, "label": i.title(), "description": f"desc {i}"} for i in ids
        ],
    }))
    return p


def test_meta_refresh_writes_the_current_topic_set(tmp_path, monkeypatch):
    """The catalog record must reflect the taxonomy AFTER the retirement.

    Regression guard: retire_topic used to delete a topic's rows and leave
    TAXONOMY#.../META still advertising it, which is how SPS ended up listing
    68 research areas against a published 67.
    """
    import boto3
    from cli.retire_topic import refresh_taxonomy_meta

    written = {}

    class _FakeClient:
        def put_item(self, TableName, Item):
            written["table"] = TableName
            written["item"] = Item

        def get_item(self, **kw):
            # The write is now verified by reading back; echo what was stored.
            return {"Item": {"topics": written["item"]["topics"]}}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _FakeClient())

    tax = _taxonomy_file(tmp_path, ["a", "b"])  # retired topic already removed
    out = refresh_taxonomy_meta("reciterai", "us-east-1", taxonomy_path=tax, dry_run=False)

    assert out == {"record": "TAXONOMY#taxonomy_v2/META", "topic_count": 2,
                   "written": True, "verified": True}
    assert written["table"] == "reciterai"
    item = written["item"]
    assert item["PK"]["S"] == "TAXONOMY#taxonomy_v2"
    assert item["SK"]["S"] == "META"
    assert item["topic_count"]["N"] == "2"
    assert {e["M"]["id"]["S"] for e in item["topics"]["L"]} == {"a", "b"}


def test_meta_refresh_writes_nothing_on_dry_run(tmp_path, monkeypatch):
    import boto3
    from cli.retire_topic import refresh_taxonomy_meta

    calls = []

    class _FakeClient:
        def put_item(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _FakeClient())

    tax = _taxonomy_file(tmp_path, ["a"])
    out = refresh_taxonomy_meta("reciterai", "us-east-1", taxonomy_path=tax, dry_run=True)

    assert calls == []
    assert out["written"] is False
    assert out["topic_count"] == 1


def test_script_runs_as_a_script_not_just_under_pytest():
    """Guard the import path when invoked directly.

    pytest puts the repo root on sys.path, so `from cli.load_dynamodb import ...`
    resolves in-process even when it would fail for a real operator running
    `cli/retire_topic.py`. Only a subprocess sees the difference.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, str(repo_root / "cli" / "retire_topic.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"--help failed:\n{r.stderr}"
    # The ordering is the point of the tool; --help must surface it (#352).
    assert "DELETING ROWS IS NOT THE LAST STEP" in r.stdout
    assert "TAXONOMY#{version}/META" in r.stdout


def test_meta_refresh_verifies_the_write_and_raises_on_a_silent_noop(tmp_path, monkeypatch):
    """A put_item that returns but does not persist must fail loudly.

    On 2026-08-04 this tool exited 1 with no output across three attempts while
    the record stayed stale, cause unidentified. Read-back verification cannot
    prevent that, but it turns a silent no-op into a visible failure.
    """
    import boto3
    from cli.retire_topic import refresh_taxonomy_meta

    class _NoOpClient:
        def put_item(self, TableName, Item):
            pass  # accepted, never persisted

        def get_item(self, **kw):
            return {"Item": {"topics": {"L": [{"M": {"id": {"S": "stale_topic"}}}]}}}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _NoOpClient())
    tax = _taxonomy_file(tmp_path, ["a", "b"])

    with pytest.raises(SystemExit) as e:
        refresh_taxonomy_meta("reciterai", "us-east-1", taxonomy_path=tax, dry_run=False)
    assert "VERIFY FAILED" in str(e.value)
    assert "stale_topic" in str(e.value)


def test_meta_refresh_raises_when_the_record_vanishes(tmp_path, monkeypatch):
    import boto3
    from cli.retire_topic import refresh_taxonomy_meta

    class _GhostClient:
        def put_item(self, TableName, Item):
            pass

        def get_item(self, **kw):
            return {}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _GhostClient())
    tax = _taxonomy_file(tmp_path, ["a"])

    with pytest.raises(SystemExit) as e:
        refresh_taxonomy_meta("reciterai", "us-east-1", taxonomy_path=tax, dry_run=False)
    assert "does not read back" in str(e.value)


def test_meta_refresh_reports_verified_on_success(tmp_path, monkeypatch):
    import boto3
    from cli.retire_topic import refresh_taxonomy_meta

    stored = {}

    class _GoodClient:
        def put_item(self, TableName, Item):
            stored["item"] = Item

        def get_item(self, **kw):
            assert kw.get("ConsistentRead") is True  # a stale read would defeat the check
            return {"Item": {"topics": stored["item"]["topics"]}}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _GoodClient())
    tax = _taxonomy_file(tmp_path, ["a", "b"])

    out = refresh_taxonomy_meta("reciterai", "us-east-1", taxonomy_path=tax, dry_run=False)
    assert out["written"] is True
    assert out["verified"] is True
