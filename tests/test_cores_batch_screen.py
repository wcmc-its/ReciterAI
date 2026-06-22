"""Unit tests for the batch_screen run-mode: bands, the Sonnet screen (mock Bedrock),
screen_core orchestration, and the idempotent never-downgrade DynamoDB write (fake client).
No network/DB/AWS.
"""
import pytest

from pipeline_cores.batch_screen import (
    band_for,
    screen_core,
    screen_likelihood,
)
from pipeline_cores.dictionary import load_core
from pipeline_cores.persist import put_candidate
from pipeline_cores.signals import batched_one_core_screen

_CORE = load_core("2")  # Biomedical Imaging
_PUBS = [{"pmid": str(i), "title": f"paper {i}", "abstract": ""} for i in range(1, 7)]


# --- bands (confidence 1-10 -> candidate/curator/drop) ---------------------
def test_band_boundaries():
    assert band_for(10) == "candidate"
    assert band_for(7) == "candidate"      # CANDIDATE_BAND_MIN
    assert band_for(6) == "curator"
    assert band_for(4) == "curator"        # CURATOR_BAND_MIN
    assert band_for(3) == "drop"
    assert band_for(1) == "drop"


def test_band_respects_overrides():
    assert band_for(5, candidate_min=5, curator_min=2) == "candidate"
    assert band_for(2, candidate_min=5, curator_min=2) == "curator"
    assert band_for(1, candidate_min=5, curator_min=2) == "drop"


# --- screen_likelihood (noisy-OR of screen + prior) ------------------------
def test_screen_likelihood_noisy_or():
    assert screen_likelihood(10, 0.0) == 1.0
    assert screen_likelihood(5, 0.0) == 0.5
    # noisy-OR(0.5, 0.4) = 1 - 0.5*0.6 = 0.7
    assert screen_likelihood(5, 0.4) == 0.7
    assert screen_likelihood(1, 0.76) == screen_likelihood(1, 0.76)  # deterministic


# --- batched one-core Sonnet screen (mock Bedrock) -------------------------
class _FakeScreenBedrock:
    """call_json returns a preset {pmid: confidence}; counts calls."""
    def __init__(self, preset, boom=False):
        self.preset, self.boom = preset, boom
        self.calls = 0

    def call_json(self, model, messages, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("simulated Bedrock failure")
        assert "sonnet" in model.lower()      # batch screen must use Sonnet
        return dict(self.preset)


def test_batched_screen_returns_confidence_per_pmid_and_batches():
    fb = _FakeScreenBedrock({str(i): i for i in range(1, 7)})
    out = batched_one_core_screen(fb, _CORE, _PUBS, batch_size=2, max_workers=1)
    assert out == {str(i): i for i in range(1, 7)}
    assert fb.calls == 3                       # 6 pubs / batch 2 = 3 calls


def test_batched_screen_missing_pmid_defaults_low():
    fb = _FakeScreenBedrock({"1": 9})          # reply omits 2..6
    out = batched_one_core_screen(fb, _CORE, _PUBS, batch_size=40, max_workers=1)
    assert out["1"] == 9 and all(out[str(i)] == 1 for i in range(2, 7))


def test_batched_screen_resilient_to_batch_error():
    fb = _FakeScreenBedrock({}, boom=True)
    out = batched_one_core_screen(fb, _CORE, _PUBS, batch_size=40, max_workers=1)
    assert all(v == 1 for v in out.values())   # a failed batch screens its pmids low, no crash


def test_batched_screen_empty_input():
    assert batched_one_core_screen(_FakeScreenBedrock({}), _CORE, [], max_workers=1) == {}


def test_batched_screen_non_dict_reply_screens_low_not_crash():
    """A valid-but-non-dict model reply (array/int) must screen low, never raise (B1) —
    exercised in the THREADED path where the original bug re-raised in the main thread."""
    class _NonDict:
        def call_json(self, model, messages, **kw):
            return [1, 2, 3]                    # valid JSON, but not an object
    out = batched_one_core_screen(_NonDict(), _CORE, _PUBS, batch_size=2, max_workers=2)
    assert set(out) == {str(i) for i in range(1, 7)} and all(v == 1 for v in out.values())


def test_batched_screen_none_bedrock_screens_low_no_calls():
    """Dry-run path: a None client screens everything low without touching Bedrock."""
    out = batched_one_core_screen(None, _CORE, _PUBS, max_workers=1)
    assert out == {str(i): 1 for i in range(1, 7)}


def test_screen_all_cores_non_dict_reply_defaults_all_low():
    """Sibling guard (S1): _parse_core_scores must survive a non-dict Haiku reply."""
    from pipeline_cores.dictionary import load_cores
    from pipeline_cores.signals import screen_all_cores

    class _NonDict:
        def call_json(self, model, messages, **kw):
            return 5                            # bare int, not an object

    cores = load_cores()[:2]
    out = screen_all_cores(_NonDict(), cores, [{"pmid": "7", "title": "t", "abstract": ""}])
    assert out["7"] == {c.core_id: 1 for c in cores}


# --- screen_core orchestration ---------------------------------------------
def test_screen_core_excludes_confirmed_and_bands_correctly():
    # confidences: 1->8(candidate) 2->5(curator) 3->2(drop) 4->9(candidate); 5 confirmed; 6 dropped by prior
    fb = _FakeScreenBedrock({"1": 8, "2": 5, "3": 2, "4": 9, "6": 7})
    results = screen_core(
        _CORE, _PUBS, bedrock=fb,
        mesh_pmids={"1"}, author_pmids={"4"},
        confirmed_pmids={"5"},                 # excluded from the pool
        drop_threshold=0.0, max_workers=1,
    )
    by = {r["pmid"]: r for r in results}
    assert "5" not in by                       # confirmed pub never screened
    assert by["1"]["band"] == "candidate" and by["1"]["write"] is True
    assert by["2"]["band"] == "curator" and by["2"]["write"] is True
    assert by["3"]["band"] == "drop" and by["3"]["write"] is False
    assert by["4"]["band"] == "candidate"
    # prior recorded: 1 has mesh signal (0.4), 4 has author signal (0.6)
    assert by["1"]["prior"] == 0.4 and by["4"]["prior"] == 0.6
    # likelihood lifts 4 (conf 9 -> 0.9, prior 0.6) above the bare screen
    assert by["4"]["likelihood"] == screen_likelihood(9, 0.6)


def test_screen_core_drop_threshold_prefilters_pool():
    # only pmid 4 has a non-zero prior (author signal 0.6); threshold 0.5 drops the rest
    fb = _FakeScreenBedrock({str(i): 9 for i in range(1, 7)})
    results = screen_core(
        _CORE, _PUBS, bedrock=fb,
        mesh_pmids=set(), author_pmids={"4"},
        drop_threshold=0.5, max_workers=1,
    )
    assert [r["pmid"] for r in results] == ["4"]   # zero-signal pairs filtered before the screen


# --- idempotent, never-downgrade conditional write -------------------------
class _FakeDynamo:
    """Minimal low-level DynamoDB double: stores items, enforces our ConditionExpression."""
    class exceptions:
        class ConditionalCheckFailedException(Exception):
            pass

    def __init__(self, store=None):
        self.store = dict(store or {})         # (PK,SK) -> attribute-map

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression,
                    ExpressionAttributeNames, ExpressionAttributeValues):
        k = (Key["PK"]["S"], Key["SK"]["S"])
        existing = self.store.get(k)
        if existing is not None:               # condition: absent OR status in (:st, :below)
            st = existing.get("status", {}).get("S")
            allowed = {ExpressionAttributeValues[":st"]["S"],
                       ExpressionAttributeValues[":below"]["S"]}
            if st not in allowed:
                raise self.exceptions.ConditionalCheckFailedException("protected status")
        item = dict(existing or {"PK": Key["PK"], "SK": Key["SK"]})
        for part in UpdateExpression[len("SET "):].split(","):
            attr, val = (s.strip() for s in part.split("="))
            item[ExpressionAttributeNames.get(attr, attr)] = ExpressionAttributeValues[val]
        self.store[k] = item
        return {}


def _put(client, pmid="100", core_id="2", **kw):
    base = dict(confidence=8, band="candidate", prior=0.4, likelihood=0.88,
                scored_at="t", screen_version="sv", prefilter_version="pv")
    base.update(kw)
    return put_candidate(pmid, core_id, client=client, **base)


def test_put_candidate_fresh_write():
    db = _FakeDynamo()
    assert _put(db) is True
    item = db.store[("PUB#100", "CORE#2")]
    assert item["status"]["S"] == "candidate" and item["screen_band"]["S"] == "candidate"
    assert item["pmid"]["S"] == "100" and item["core_id"]["S"] == "2"


def test_put_candidate_overwrites_engine_owned_and_is_idempotent():
    db = _FakeDynamo({("PUB#100", "CORE#2"): {"PK": {"S": "PUB#100"}, "SK": {"S": "CORE#2"},
                                              "status": {"S": "candidate"}}})
    assert _put(db, confidence=6, band="curator") is True
    first = dict(db.store[("PUB#100", "CORE#2")])
    assert first["screen_band"]["S"] == "curator"
    assert _put(db, confidence=6, band="curator") is True          # re-run
    assert db.store[("PUB#100", "CORE#2")] == first                # same state -> idempotent


def test_put_candidate_overwrites_below_threshold():
    db = _FakeDynamo({("PUB#100", "CORE#2"): {"PK": {"S": "PUB#100"}, "SK": {"S": "CORE#2"},
                                              "status": {"S": "below_threshold"}}})
    assert _put(db) is True


@pytest.mark.parametrize("protected", ["confirmed", "claimed", "rejected"])
def test_put_candidate_never_downgrades_protected_status(protected):
    db = _FakeDynamo({("PUB#100", "CORE#2"): {"PK": {"S": "PUB#100"}, "SK": {"S": "CORE#2"},
                                              "status": {"S": protected}}})
    assert _put(db) is False                                       # condition protected it
    assert db.store[("PUB#100", "CORE#2")]["status"]["S"] == protected   # untouched


def test_put_candidate_real_condition_on_moto():
    """Exercise the REAL ConditionExpression against moto (proves the condition string, not
    just the fake). Skips if moto isn't installed."""
    pytest.importorskip("moto")
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="reciterai",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                       {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.put_item(TableName="reciterai", Item={
            "PK": {"S": "PUB#900"}, "SK": {"S": "CORE#2"}, "status": {"S": "confirmed"}})

        # never downgrade a confirmed row (real condition rejects)
        assert _put(client, pmid="900") is False
        kept = client.get_item(TableName="reciterai",
                               Key={"PK": {"S": "PUB#900"}, "SK": {"S": "CORE#2"}})["Item"]
        assert kept["status"]["S"] == "confirmed"

        # fresh key writes a candidate
        assert _put(client, pmid="901") is True
        wrote = client.get_item(TableName="reciterai",
                                Key={"PK": {"S": "PUB#901"}, "SK": {"S": "CORE#2"}})["Item"]
        assert wrote["status"]["S"] == "candidate" and wrote["screen_band"]["S"] == "candidate"
