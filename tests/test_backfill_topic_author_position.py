"""Tests for cli/backfill_topic_author_position.py core logic (fake DDB, no AWS)."""

from __future__ import annotations

from botocore.exceptions import ClientError

from cli import backfill_topic_author_position as bp


class FakeClient:
    def __init__(self, fail_keys=()):
        self.updates: list[dict] = []
        self._fail = set(fail_keys)

    def update_item(self, **kw):
        if kw["Key"]["SK"]["S"] in self._fail:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        self.updates.append(kw)


def _row(topic, sk, pmid, cwid):
    return {"PK": {"S": f"TOPIC#{topic}"}, "SK": {"S": sk},
            "pmid": {"S": pmid}, "faculty_uid": {"S": f"cwid_{cwid}"}}


_MAP = bp.build_position_map({
    "1": [{"cwid": "a", "position": "first"}, {"cwid": "b", "position": "None"}],
    "2": [{"cwid": "a", "position": ""}, {"cwid": "a", "position": "last"}],
})


def test_position_map_matches_mint_resolution():
    assert _MAP == {("1", "a"): "first", ("1", "b"): "middle", ("2", "a"): "last"}


def test_writes_matched_rows_with_conditional_update_only():
    c = FakeClient()
    rows = [_row("neuro", "s1", "1", "a"), _row("neuro", "s2", "1", "b"),
            _row("pain", "s3", "2", "a"), _row("pain", "s4", "9", "zz")]
    res = bp.backfill(c, rows, _MAP, apply=True)
    assert (res["blank"], res["written"], res["unmatched"], res["raced"]) == (4, 3, 1, 0)
    assert [u["ExpressionAttributeValues"][":p"]["S"] for u in c.updates] == ["first", "middle", "last"]
    for u in c.updates:
        # SET only (never a Put), and never resurrects a deleted row or clobbers a value.
        assert u["UpdateExpression"] == "SET author_position = :p"
        assert u["ConditionExpression"].startswith("attribute_exists(PK) AND (")
        assert "attribute_not_exists(author_position)" in u["ConditionExpression"]
    assert res["by_topic"]["pain"]["unmatched"] == 1


def test_dry_run_writes_nothing():
    c = FakeClient()
    res = bp.backfill(c, [_row("neuro", "s1", "1", "a")], _MAP, apply=False)
    assert c.updates == [] and res["blank"] == 1 and res["written"] == 0


def test_conditional_failure_counted_as_raced():
    c = FakeClient(fail_keys={"s1"})
    res = bp.backfill(c, [_row("neuro", "s1", "1", "a")], _MAP, apply=True)
    assert (res["written"], res["raced"]) == (0, 1)
