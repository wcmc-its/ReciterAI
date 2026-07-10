"""Prestige backfill: item reconstruction, dry-run vs apply, report aggregation."""
from unittest.mock import MagicMock

from pipeline_grants import backfill_prestige as bf


def _item(pk, *, title="X", mechanism=None, ceiling=None, est=None, sponsor="NIH"):
    it = {"PK": {"S": pk}, "SK": {"S": "META"},
          "opportunity_id": {"S": pk.split("#", 1)[1]}, "source": {"S": "grants_gov"},
          "source_url": {"S": "u"}, "sponsor": {"S": sponsor}, "title": {"S": title},
          "synopsis": {"S": "s"}}
    if mechanism:
        it["mechanism"] = {"S": mechanism}
    if ceiling is not None:
        it["award_ceiling"] = {"N": str(ceiling)}
    if est is not None:
        it["estimated_funding"] = {"N": str(est)}
    return it


def test_opp_from_item_round_trips_prestige_fields():
    opp = bf.opp_from_item(_item("GRANT#g1", title="The Wolf Prize", mechanism="R01", ceiling=500_000))
    assert opp.mechanism == "R01" and opp.award_ceiling == 500_000 and opp.title == "The Wolf Prize"
    attrs = bf.prestige_item_attrs(opp)
    assert "N" in attrs["prestige"]["M"]["score"] and attrs["is_honorific"]["BOOL"] is True


def test_opp_from_item_recovers_mechanism_from_title_when_unstored():
    # legacy items (pre-#275) have no `mechanism` attr -> recover it from the title
    opp = bf.opp_from_item(_item("GRANT#g2", title="Pilot Proteins (R01 Clinical Trial Not Allowed)"))
    assert opp.mechanism == "R01"
    # a curated prize has no code -> stays empty (correctly Standard, gated by is_honorific)
    assert bf.opp_from_item(_item("GRANT#g3", title="The Wolf Prize")).mechanism == ""


def _client_with(items):
    c = MagicMock()
    c.scan.return_value = {"Items": items}  # no LastEvaluatedKey -> single page
    return c


def test_dry_run_reports_without_writing():
    client = _client_with([
        _item("GRANT#a", title="R01 Cancer Research", mechanism="R01", ceiling=500_000),
        _item("GRANT#b", title="The Wolf Prize", mechanism=""),  # honorific, curated-style
    ])
    summary = bf.backfill(client, apply=False)
    assert summary["scanned"] == 2 and summary["written"] == 0
    assert summary["honorific"] == 1
    assert sum(summary["labels"].values()) == 2
    client.update_item.assert_not_called()


def test_apply_writes_only_the_two_attrs():
    client = _client_with([_item("GRANT#a", mechanism="R01", ceiling=500_000)])
    summary = bf.backfill(client, apply=True)
    assert summary["written"] == 1
    _, kwargs = client.update_item.call_args
    assert set(kwargs["ExpressionAttributeValues"]) == {":p", ":h"}
    assert "SET prestige" in kwargs["UpdateExpression"]
    assert kwargs["Key"]["PK"] == {"S": "GRANT#a"}


# --- mechanism backfill (#288) -------------------------------------------------
def test_apply_backfills_mechanism_recovered_from_title():
    # legacy item: no stored mechanism, title carries the activity code -> SET it
    client = _client_with([_item("GRANT#a", title="Pilot Proteins (R01 Clinical Trial Not Allowed)")])
    summary = bf.backfill(client, apply=True)
    assert summary["mechanism_backfilled"] == 1
    _, kwargs = client.update_item.call_args
    assert ", mechanism = :m" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeValues"][":m"] == {"S": "R01"}


def test_apply_never_overwrites_stored_mechanism():
    # stored K23 wins even though the title would recover R01
    client = _client_with([_item("GRANT#a", title="Something (R01)", mechanism="K23")])
    summary = bf.backfill(client, apply=True)
    assert summary["mechanism_backfilled"] == 0
    _, kwargs = client.update_item.call_args
    assert "mechanism" not in kwargs["UpdateExpression"]
    assert ":m" not in kwargs["ExpressionAttributeValues"]


def test_apply_omits_mechanism_when_nothing_recovered():
    # no stored mechanism AND no code in the title -> update stays prestige-only
    client = _client_with([_item("GRANT#a", title="The Wolf Prize")])
    summary = bf.backfill(client, apply=True)
    assert summary["mechanism_backfilled"] == 0
    _, kwargs = client.update_item.call_args
    assert "mechanism" not in kwargs["UpdateExpression"]
    assert set(kwargs["ExpressionAttributeValues"]) == {":p", ":h"}


def test_dry_run_counts_recoverable_mechanisms_without_writing():
    client = _client_with([
        _item("GRANT#a", title="Pilot Proteins (R01 Clinical Trial Not Allowed)"),
        _item("GRANT#b", title="Something (R01)", mechanism="K23"),
        _item("GRANT#c", title="The Wolf Prize"),
    ])
    summary = bf.backfill(client, apply=False)
    assert summary["mechanism_backfilled"] == 1
    client.update_item.assert_not_called()


def test_limit_caps_scan():
    client = _client_with([_item(f"GRANT#{i}", mechanism="R01") for i in range(5)])
    assert bf.backfill(client, apply=False, limit=2)["scanned"] == 2
