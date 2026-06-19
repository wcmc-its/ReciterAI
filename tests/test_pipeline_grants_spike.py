from pipeline_grants.models import Opportunity
from pipeline_grants.spike import top_topics, render_markdown


def _opp():
    return Opportunity(opportunity_id="grants_gov:1", source="grants_gov", source_id="1",
                       source_url="http://x", sponsor="NIH", title="Cancer Informatics", synopsis="S")


def test_top_topics_sorts_and_limits():
    dense = {"a": {"score": 0.4, "rationale": "ra"},
             "breast_cancer": {"score": 0.95, "rationale": "rb"},
             "c": {"score": 0.7, "rationale": "rc"}}
    tops = top_topics(dense, limit=2)
    assert [t[0] for t in tops] == ["breast_cancer", "c"]
    assert tops[0][1] == 0.95


def test_render_markdown_includes_title_and_top_topic():
    md = render_markdown([(_opp(), {"breast_cancer": {"score": 0.95, "rationale": "rb"}})])
    assert "Cancer Informatics" in md
    assert "breast_cancer" in md
    assert md.startswith("| ")
