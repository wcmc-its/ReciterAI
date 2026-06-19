import pipeline_grants.scoring as scoring


class _Result:
    def __init__(self, status, dense_scores=None, error=None):
        self.status = status
        self.dense_scores = dense_scores or {}
        self.error = error


def test_score_grant_text_builds_pub_dict_and_returns_dense(monkeypatch):
    seen = {}

    def fake_score(pub, bedrock, taxonomy, dynamo_client, table_name, int_to_id, id_to_int,
                   *args, **kwargs):
        seen["pub"] = pub
        seen["table"] = table_name
        return _Result("complete", {"breast_cancer": {"score": 0.9, "rationale": "r"}})

    monkeypatch.setattr(scoring.sp, "score_one_publication", fake_score)
    out = scoring.score_grant_text(
        title="T", synopsis="S", opportunity_id="grants_gov:1",
        bedrock=object(), taxonomy={"topics": []}, int_to_id={}, id_to_int={},
    )
    assert out == {"breast_cancer": {"score": 0.9, "rationale": "r"}}
    assert seen["pub"] == {"pmid": "grants_gov:1", "title": "T", "synopsis": "S", "abstract": ""}
    assert seen["table"] == "reciterai"


def test_score_grant_text_raises_on_failure(monkeypatch):
    monkeypatch.setattr(scoring.sp, "score_one_publication",
                        lambda *a, **k: _Result("failed", error="boom"))
    try:
        scoring.score_grant_text(title="T", synopsis="S", opportunity_id="x",
                                 bedrock=object(), taxonomy={}, int_to_id={}, id_to_int={})
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)


def test_noop_client_absorbs_any_write():
    c = scoring._NoOpDynamoClient()
    assert c.put_item(TableName="reciterai", Item={}) == {}
    assert c.update_item(Key={}) == {}


def test_load_taxonomy_and_build_index_real():
    tax = scoring.load_taxonomy()
    assert tax.get("taxonomy_version")
    assert isinstance(tax.get("topics"), list) and len(tax["topics"]) > 0
    int_to_id, id_to_int = scoring.build_index(tax)
    assert isinstance(int_to_id, dict) and isinstance(id_to_int, dict)
    any_id = next(iter(id_to_int))
    assert int_to_id[id_to_int[any_id]] == any_id
