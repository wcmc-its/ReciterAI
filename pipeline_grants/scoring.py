"""Topic-score grant text by reusing the production scorer (score_publications).

score_one_publication() writes TOPIC#/processing rows to DynamoDB as a side effect.
We only want its returned dense_scores, so we pass a no-op sink client and persist
our own GRANT# record elsewhere (pipeline_grants.persist)."""
import json
from pathlib import Path

import score_publications as sp
from utils.bedrock_client import BedrockClient

_TAXONOMY_PATH = Path(sp.__file__).parent / "taxonomy_v2.json"


class _NoOpDynamoClient:
    """Stand-in DynamoDB client whose write calls are intentionally discarded."""

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return {}
        return _noop


def load_taxonomy() -> dict:
    with open(_TAXONOMY_PATH) as f:
        return json.load(f)


def build_index(taxonomy: dict) -> "tuple[dict, dict]":
    return sp.build_topic_index(taxonomy)


def score_grant_text(*, title: str, synopsis: str, opportunity_id: str,
                     bedrock: BedrockClient, taxonomy: dict,
                     int_to_id: dict, id_to_int: dict) -> dict:
    """Return dense_scores {topic_id: {'score': float, 'rationale': str}} for a grant."""
    pub = {"pmid": opportunity_id, "title": title, "synopsis": synopsis, "abstract": ""}
    result = sp.score_one_publication(
        pub, bedrock, taxonomy, _NoOpDynamoClient(), "reciterai", int_to_id, id_to_int,
    )
    if result.status != "complete":
        raise RuntimeError(f"grant scoring failed for {opportunity_id}: {result.error}")
    return result.dense_scores
