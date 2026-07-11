"""Checks for cli/retire_topic.py — the #307 topic-retirement delete tool.

The load-bearing safety properties: the slug boundary (never touch the separate
benign `hematology` topic), the taxonomy-presence ordering guard, and that a
dry-run deletes nothing.
"""
import json

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
