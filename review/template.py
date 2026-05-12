"""YAML template generator for the review approve workflow (Phase 11).

Generates a pre-populated YAML string that the operator fills in using
$EDITOR before the CLI validates and writes the REVIEW# DDB row.
"""
from __future__ import annotations

from typing import Optional

import yaml

_HEADER = """\
# REVIEW for {artifact_type} {version}
# Fill in `reviewer_cwid` (if not already set), `rationale`, and `decision`.
# decision must be exactly: approve | reject
# rationale must be >= 40 characters.
"""


def build_template(
    *,
    artifact_type: str,
    version: str,
    proposed_artifact_uri: str,
    summary_stats: dict,
    reviewer_cwid: Optional[str] = None,
) -> str:
    """Generate a pre-populated YAML template for the operator to review.

    Args:
        artifact_type: Type of artifact being reviewed (e.g. "hierarchy").
        version: Version string (e.g. "v2026-06-01").
        proposed_artifact_uri: Full S3 URI to the artifact.
        summary_stats: Dict of summary statistics to display to the operator.
        reviewer_cwid: Pre-filled reviewer CWID if resolvable; operator can
            edit if empty.

    Returns:
        A YAML string with a comment header and pre-populated fields.
        The operator must fill in reviewer_cwid (if empty), rationale,
        and decision before saving.
    """
    body = {
        "artifact_type": artifact_type,
        "version": version,
        "proposed_artifact_uri": proposed_artifact_uri,
        "summary_stats": dict(summary_stats),
        "reviewer_cwid": reviewer_cwid or "",
        "rationale": "",
        "decision": "",
    }
    header = _HEADER.format(artifact_type=artifact_type, version=version)
    return header + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)
