"""Snapshot tests for pipeline_enrichment.prompts.

These tests exist to catch accidental edits to the ported prompts. They
do NOT make any LLM calls. The expected hashes were captured at port
time (2026-05-13) from the POC source-of-truth versions.

If a test in this file fails after a prompt edit, the change is
deliberate and the expected hash needs updating in the same commit.
That edit should also trigger re-running the equivalence check
(scripts/equivalence_check.py) before merging to main.
"""
from __future__ import annotations

import hashlib

from pipeline_enrichment.prompts import (
    IMPACT_PROMPT_DEFAULT_VERSION,
    IMPACT_PROMPT_V1,
    IMPACT_PROMPT_V2,
    IMPACT_SCHEMA,
    SYNOPSIS_SCHEMA,
    SYNOPSIS_SYSTEM,
    build_impact_user_content,
    build_synopsis_user_content,
    get_impact_system_prompt,
)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------- synopsis ----------


def test_synopsis_system_prompt_byte_stable():
    """If this fails, someone edited SYNOPSIS_SYSTEM. Re-run the equivalence
    check (scripts/equivalence_check.py) and update the expected hash in
    the same commit if the edit is deliberate."""
    expected = "17ee07fec6a288a7fbd71ab1b017d1370afc960ec7177df810bd539a36b74a6a"
    assert _hash(SYNOPSIS_SYSTEM) == expected, (
        f"SYNOPSIS_SYSTEM has drifted from the 2026-05-13 ported version. "
        f"Got: {_hash(SYNOPSIS_SYSTEM)}"
    )


def test_synopsis_system_prompt_structural_invariants():
    """Survives small whitespace edits; catches semantic damage."""
    assert "scientific writing assistant" in SYNOPSIS_SYSTEM
    assert "<= 95 characters" in SYNOPSIS_SYSTEM
    assert "40+ characters" in SYNOPSIS_SYSTEM
    assert ">= 5 words" in SYNOPSIS_SYSTEM


def test_impact_v2_byte_stable():
    """If this fails, someone edited IMPACT_PROMPT_V2. Re-run equivalence check."""
    expected = "5be0bb9f29b5e75d4826c4e9e09a424c7ff39d0d2405070108d205668936dc39"
    assert _hash(IMPACT_PROMPT_V2) == expected, (
        f"IMPACT_PROMPT_V2 has drifted from the 2026-05-13 ported version. "
        f"Got: {_hash(IMPACT_PROMPT_V2)}"
    )


def test_impact_v1_byte_stable():
    """v1 is the rollback target; keep it byte-stable so rollback is meaningful."""
    expected = "992a39bfa3ccf9e69f4c2c914dcf62c384ebc807592eca2d189a5aa525781ee0"
    assert _hash(IMPACT_PROMPT_V1) == expected


def test_synopsis_schema_shape():
    assert SYNOPSIS_SCHEMA["name"] == "synopsis_schema"
    assert SYNOPSIS_SCHEMA["schema"]["required"] == ["synopsis"]
    syn_prop = SYNOPSIS_SCHEMA["schema"]["properties"]["synopsis"]
    assert syn_prop["type"] == "string"
    # Added 2026-05-14 (#50): schema-level cap mirrors the SYNOPSIS_SYSTEM
    # plain-language "<= 95 characters" rule.
    assert syn_prop["maxLength"] == 95


def test_synopsis_user_content_format():
    out = build_synopsis_user_content(
        title="A novel finding",
        journal="Cell",
        year=2024,
        abstract="We show that X.",
    )
    assert out.startswith("Title: A novel finding\n")
    assert "Journal: Cell" in out
    assert "Year: 2024" in out
    assert "Abstract: We show that X." in out
    assert out.endswith("Return JSON only.")


def test_synopsis_user_content_handles_missing_fields():
    out = build_synopsis_user_content(title="T", journal=None, year=None, abstract=None)
    assert "Journal: N/A" in out
    assert "Year: N/A" in out
    assert "Abstract: (no abstract)" in out


# ---------- impact ----------


def test_impact_default_version_is_v2():
    """The 2025-12-28 v2 prompt is the production default; v1 only kept for rollback."""
    assert IMPACT_PROMPT_DEFAULT_VERSION == "v2"


def test_impact_v2_structural_invariants():
    """v2 prompt must keep the parity constraint + counterfactual check that
    were the whole point of the 2025-12-28 revision. If either disappears
    the prompt has been damaged."""
    assert "PARITY CONSTRAINT" in IMPACT_PROMPT_V2
    assert "counterfactual check" in IMPACT_PROMPT_V2
    assert "0–100" in IMPACT_PROMPT_V2
    assert '"impactScore"' in IMPACT_PROMPT_V2
    assert '"justification"' in IMPACT_PROMPT_V2


def test_impact_v1_lacks_parity_constraint():
    """v1 predates the parity constraint; v2 added it. Sanity-check the
    versions haven't been collapsed."""
    assert "PARITY CONSTRAINT" not in IMPACT_PROMPT_V1


def test_get_impact_system_prompt_default_returns_v2():
    assert get_impact_system_prompt() == IMPACT_PROMPT_V2


def test_get_impact_system_prompt_unknown_version_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown impact prompt version"):
        get_impact_system_prompt("v99")


def test_impact_schema_shape():
    schema = IMPACT_SCHEMA["schema"]
    assert set(schema["required"]) == {"impactScore", "justification"}
    assert schema["properties"]["impactScore"]["minimum"] == 0
    assert schema["properties"]["impactScore"]["maximum"] == 100
    assert schema["properties"]["justification"]["maxLength"] == 120


def test_impact_user_content_builds_from_mariadb_columns():
    """build_impact_user_content takes a dict shaped like the MariaDB row.
    Verify the expected fields land in the prompt body."""
    pub = {
        "pmid": "12345",
        "articleTitle": "Title here",
        "journalTitleVerbose": "Nature",
        "articleYear": 2024,
        "citationCountNIH": 42,
        "percentileNIH": 87,
        "relativeCitationRatioNIH": 1.5,
        "datePublicationAddedToEntrez": "2024-01-15",
        "abstractVarchar": "We discovered something.",
    }
    out = build_impact_user_content(pub)
    assert "Title: Title here" in out
    assert "Journal: Nature (2024)" in out
    assert "Citation Count (NIH): 42" in out
    assert "NIH iCite Percentile: 87" in out
    assert "Relative Citation Ratio: 1.50" in out
    assert "Publication Date: 2024-01-15" in out
    assert "Abstract:\nWe discovered something." in out


def test_impact_user_content_truncates_long_abstract():
    """POC truncates abstracts at 3000 chars; mirror exactly."""
    long_abstract = "x" * 5000
    out = build_impact_user_content({
        "articleTitle": "T",
        "abstractVarchar": long_abstract,
    })
    # 3000 char body + "..." marker
    assert "x" * 3000 + "..." in out
    assert "x" * 3001 not in out


def test_impact_user_content_omits_missing_optionals():
    """Optional bibliometric fields shouldn't appear when None."""
    out = build_impact_user_content({
        "articleTitle": "T",
        "abstractVarchar": "A",
    })
    assert "Citation Count" not in out
    assert "iCite Percentile" not in out
    assert "Relative Citation Ratio" not in out
    assert "Publication Date" not in out
