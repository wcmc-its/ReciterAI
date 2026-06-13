"""Guards for the subtopic-assignment system prompt.

The substrate-matching gate stops the classifier from assigning a publication
to a substrate-defined subtopic (e.g. "EHR-Based Outcome Prediction Models")
purely on a method match (machine learning / prediction) when the paper's
actual data substrate is something else (proteomics, flow cytometry, imaging,
genomics, survey). Validated on the live Haiku model against the 85 members of
``biomedical_ml_ehr_prediction``: 23/41 non-EHR papers rerouted off the EHR
subtopic with ~0 collateral damage to genuine EHR papers.

These tests pin the gate's presence so a future prompt edit can't silently
drop it without a deliberate, reviewed change.
"""

from __future__ import annotations

from prompts.subtopic_assignment import (
    ASSIGNMENT_SYSTEM_PROMPT,
    BUILD_ASSIGNMENT_USER_MESSAGE,
)


def test_substrate_gate_present():
    """The substrate-matching gate must be in the assignment system prompt."""
    p = ASSIGNMENT_SYSTEM_PROMPT
    assert "SUBSTRATE MATCHING" in p
    # The core instruction: a method match alone is not sufficient.
    assert "method" in p.lower() and "substrate" in p.lower()
    assert "ACTUALLY USE that substrate" in p


def test_gate_instructs_reroute_or_empty():
    """The gate must offer the two safe outcomes: reroute, or empty assignment —
    never force a method-only match."""
    p = ASSIGNMENT_SYSTEM_PROMPT
    assert "assign it there instead" in p
    assert "empty assignments" in p


def test_user_message_builder_unaffected():
    """The gate lives in the system prompt only; the user-message builder still
    renders the activity + candidate subtopics exactly as before."""
    msg = BUILD_ASSIGNMENT_USER_MESSAGE(
        activity={"pmid": 123, "title": "T", "synopsis": "S"},
        topic_meta={"id": "topic_x", "label": "Topic X", "description": "desc"},
        subtopic_defs=[{"id": "sub_a", "label": "Sub A", "description": "d"}],
    )
    assert "pmid: 123" in msg
    assert "Candidate subtopics (1):" in msg
    assert "sub_a" in msg
    # The substrate gate is NOT injected into the per-activity user message.
    assert "SUBSTRATE MATCHING" not in msg
