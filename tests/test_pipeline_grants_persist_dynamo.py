from decimal import Decimal
from pipeline_grants.models import Opportunity
from pipeline_grants.persist import PRESERVED_ATTRS, build_grant_item, put_grants


def _opp():
    return Opportunity(opportunity_id="grants_gov:359855", source="grants_gov", source_id="359855",
                       source_url="http://x", sponsor="NIH", title="T", synopsis="S",
                       award_ceiling=600000, due_date="2026-10-19", status="posted",
                       ingested_at="2026-06-19T00:00:00Z")


def test_build_grant_item_keys_and_types():
    dense = {"breast_cancer": {"score": 0.95, "rationale": "rb"},
             "biomedical_informatics": {"score": 0.6, "rationale": "ri"}}
    item = build_grant_item(_opp(), dense, taxonomy_version="taxonomy_v2",
                            judge={"is_research": True, "appeal_by_stage": {"early": 0.9}})
    assert item["PK"] == {"S": "GRANT#grants_gov:359855"}
    assert item["SK"] == {"S": "META"}
    assert item["sponsor"] == {"S": "NIH"}
    assert item["award_ceiling"] == {"N": "600000"}
    assert item["primary_topic_id"] == {"S": "breast_cancer"}  # highest score
    tv = item["topic_vector"]["L"]
    assert tv[0]["M"]["topic_id"]["S"] == "breast_cancer"
    assert tv[0]["M"]["score"]["N"] == str(Decimal("0.95"))


def test_build_grant_item_canonicalizes_sponsor():
    # The persist choke point every source flows through (incl. spin/curated) applies the
    # sponsor canonical map, so a fragmenting variant lands as one facet label. Refs #294 item 3.
    opp = _opp()
    opp.sponsor = "American Cancer Society, Inc."
    item = build_grant_item(opp, {}, taxonomy_version="taxonomy_v2", judge={})
    assert item["sponsor"] == {"S": "American Cancer Society"}


def test_build_grant_item_handles_none_award():
    opp = _opp()
    opp.award_ceiling = None
    item = build_grant_item(opp, {}, taxonomy_version="taxonomy_v2", judge={})
    assert "award_ceiling" not in item  # omit nulls rather than write empty
    assert item["primary_topic_id"] == {"S": ""}
    assert "eligibility" not in item  # no structured block on the judge -> attr omitted (#290)


def test_build_grant_item_serializes_structured_eligibility():
    # A validated eligibility block persists as a NATIVE DynamoDB map (not a JSON string) so the
    # SPS mapper reads it directly: lists -> L of S, booleans -> BOOL, scalars -> S.
    judge = {"is_research": True, "eligibility": {
        "applicant_org_types": ["higher_ed", "small_business"],
        "career_stages": ["early_career_faculty"],
        "degree_required": [],
        "citizenship_requirement": "us_citizen_or_permanent_resident_required",
        "esi_targeted": True, "limited_submission": False,
        "cost_sharing_required": False, "individual_award": True,
        "extracted_by": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "extracted_at": "2026-07-10T00:00:00Z",
    }}
    item = build_grant_item(_opp(), {}, taxonomy_version="taxonomy_v2", judge=judge)
    m = item["eligibility"]["M"]
    assert m["applicant_org_types"] == {"L": [{"S": "higher_ed"}, {"S": "small_business"}]}
    assert m["degree_required"] == {"L": []}
    assert m["citizenship_requirement"] == {"S": "us_citizen_or_permanent_resident_required"}
    assert m["esi_targeted"] == {"BOOL": True}
    assert m["limited_submission"] == {"BOOL": False}
    assert m["extracted_by"] == {"S": "us.anthropic.claude-haiku-4-5-20251001-v1:0"}


# --- preserve-on-put (#292): re-ingest must not clobber backfill-only attrs ---------------

class _FakeDynamo:
    """Stub for the two calls put_grants makes: batch_get_item + batch_write_item.

    ``existing`` = {(PK, SK): full stored item}. batch_get_item honors the request's
    ProjectionExpression/ExpressionAttributeNames (so a projection that misses an attr fails
    the test) and can defer the last key of each chunk as UnprocessedKeys for N rounds."""

    def __init__(self, existing=None, unprocessed_rounds=0):
        self.existing = existing or {}
        self.written = []
        self.get_requests = []
        self._unprocessed_rounds = unprocessed_rounds

    def batch_get_item(self, RequestItems):
        (table, spec), = RequestItems.items()
        keys = spec["Keys"]
        assert 0 < len(keys) <= 100  # DynamoDB BatchGetItem hard limit
        self.get_requests.append(keys)
        served = keys
        deferred = []
        if self._unprocessed_rounds > 0:
            self._unprocessed_rounds -= 1
            served, deferred = keys[:-1], keys[-1:]
        names = spec.get("ExpressionAttributeNames", {})
        attrs = [names.get(p.strip(), p.strip()) for p in spec["ProjectionExpression"].split(",")]
        responses = []
        for key in served:
            stored = self.existing.get((key["PK"]["S"], key["SK"]["S"]))
            if stored is not None:
                responses.append({a: stored[a] for a in attrs if a in stored})
        out = {"Responses": {table: responses}}
        if deferred:
            out["UnprocessedKeys"] = {table: dict(spec, Keys=deferred)}
        return out

    def batch_write_item(self, RequestItems):
        (_, requests), = RequestItems.items()
        self.written.extend(r["PutRequest"]["Item"] for r in requests)
        return {"UnprocessedItems": {}}


def _stored(oid, **attrs):
    base = {"PK": {"S": f"GRANT#{oid}"}, "SK": {"S": "META"}, "title": {"S": "old"}}
    base.update({k: {"S": v} for k, v in attrs.items()})
    return {(base["PK"]["S"], base["SK"]["S"]): base}


def test_put_grants_preserves_backfilled_match_attrs_on_replot():
    # Routine re-ingest: compile_match OFF -> new item carries NO match_*; all three survive.
    client = _FakeDynamo(existing=_stored("grants_gov:359855", match_dsl='{"require":["x"]}',
                                          match_query='[{"q":"y","w":1.0}]', match_rel='{"1":0.9}'))
    item = build_grant_item(_opp(), {}, taxonomy_version="taxonomy_v2", judge={})
    assert put_grants(client, [item]) == 1
    (written,) = client.written
    assert written["match_dsl"] == {"S": '{"require":["x"]}'}
    assert written["match_query"] == {"S": '[{"q":"y","w":1.0}]'}
    assert written["match_rel"] == {"S": '{"1":0.9}'}
    assert written["title"] == {"S": "T"}  # everything else is the FRESH ingest value


def test_put_grants_fresh_compile_wins_but_rel_still_preserved():
    # compile_match ON: the new item's own match_dsl/match_query win; match_rel (never
    # compiled at ingest) is still carried over.
    client = _FakeDynamo(existing=_stored("grants_gov:359855", match_dsl='{"require":["old"]}',
                                          match_query='[{"q":"old","w":1.0}]', match_rel='{"1":0.9}'))
    item = build_grant_item(_opp(), {}, taxonomy_version="taxonomy_v2", judge={},
                            match_dsl={"require": ["new"]}, match_query=[{"q": "new", "w": 1.0}])
    put_grants(client, [item])
    (written,) = client.written
    assert written["match_dsl"] == {"S": '{"require":["new"]}'}
    assert written["match_query"] == {"S": '[{"q":"new","w":1.0}]'}
    assert written["match_rel"] == {"S": '{"1":0.9}'}


def test_put_grants_new_item_and_unmatched_existing_untouched():
    # A first-time item, and an existing row with none of the preserved attrs: no merge.
    client = _FakeDynamo(existing=_stored("grants_gov:359855"))
    item = build_grant_item(_opp(), {}, taxonomy_version="taxonomy_v2", judge={})
    put_grants(client, [item])
    (written,) = client.written
    assert all(attr not in written for attr in PRESERVED_ATTRS)


def test_put_grants_chunks_lookups_and_retries_unprocessed_keys():
    # 120 unique keys -> chunks of 100 + 20; the first chunk's last key is deferred once
    # (UnprocessedKeys) and re-fetched before the next chunk.
    existing = {}
    items = []
    for n in range(120):
        opp = _opp()
        opp.opportunity_id = f"grants_gov:{n}"
        items.append(build_grant_item(opp, {}, taxonomy_version="taxonomy_v2", judge={}))
        existing.update(_stored(opp.opportunity_id, match_rel=f'{{"p":{n}}}'))
    client = _FakeDynamo(existing=existing, unprocessed_rounds=1)
    assert put_grants(client, items) == 120
    assert [len(k) for k in client.get_requests] == [100, 1, 20]
    assert len(client.written) == 120
    assert all(w["match_rel"] == {"S": f'{{"p":{n}}}'} for n, w in enumerate(client.written))


def test_put_grants_empty_is_a_noop():
    client = _FakeDynamo()
    assert put_grants(client, []) == 0
    assert client.get_requests == [] and client.written == []
