"""parent_prefix gate — bundle-level rescan for D-19 editorial rule.

The relabel pass in `relabel_subtopics.py` already runs the same check
at generation time and retries on violations (`ca3a7ed`). This gate
runs the check again at publish time as cheap insurance — catches the
case where someone hand-edits an augmented file with a violation and
runs publish without re-relabeling.

The actual violation logic is `relabel_subtopics.find_parent_prefix_violation`,
imported and reused. The gate is a thin iterator wrapper.

Topic labels come from `taxonomy_v2.json` because the published
hierarchy doesn't carry topic-level labels — only subtopic ones. The
gate accepts an explicit `topic_labels: dict[str, str]` kwarg so tests
and the integrating stage can pass a pre-loaded map without re-reading
the taxonomy file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gates.registry import GateResult, SEVERITY_BLOCK, register_gate
from relabel_subtopics import find_parent_prefix_violation

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY_PATH = REPO_ROOT / "taxonomy_v2.json"


def _load_topic_labels(taxonomy_path: Path = DEFAULT_TAXONOMY_PATH) -> dict[str, str]:
    """Read taxonomy_v2.json and return {topic_id: topic_label}."""
    data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    return {t["id"]: t["label"] for t in data.get("topics", [])}


@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="parent_prefix")
def parent_prefix_gate(
    *,
    hierarchy: dict[str, Any],
    topic_labels: dict[str, str] | None = None,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    **_: object,
) -> GateResult:
    """Reject hierarchy bundles where any subtopic `display_name` starts
    with a parent-topic word (see `relabel_subtopics.find_parent_prefix_violation`).

    `topic_labels`: optional pre-loaded {topic_id: label}. Loaded from
    `taxonomy_path` if not provided.
    """
    if topic_labels is None:
        topic_labels = _load_topic_labels(taxonomy_path)

    violations: list[dict[str, str]] = []
    for tid, topic in hierarchy.get("topics", {}).items():
        label = topic_labels.get(tid, tid)
        for sub in topic.get("subtopics", []):
            display_name = sub.get("display_name", "")
            reason = find_parent_prefix_violation(tid, label, display_name)
            if reason:
                violations.append(
                    {
                        "topic_id": tid,
                        "subtopic_id": sub.get("id", "?"),
                        "display_name": display_name,
                        "reason": reason,
                    }
                )

    if violations:
        # Cap details payload size — 1,526 subtopics × worst-case all-violating
        # would still fit in DDB's 400KB limit, but no need to dump unbounded.
        return GateResult(
            name="parent_prefix",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=(
                f"{len(violations)} subtopic display_name(s) start with a "
                f"parent-topic word"
            ),
            details={
                "violation_count": len(violations),
                "violations": violations[:50],
                "truncated": len(violations) > 50,
            },
        )

    return GateResult(
        name="parent_prefix",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="no parent-prefix violations across the bundle",
    )
