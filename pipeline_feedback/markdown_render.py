"""Deterministic markdown rendering for feedback-sweep findings.

Same rows in → same bytes out. No generated_at in the body (G-29 lesson,
D-03). The caller owns the filename's timestamp (e.g.
feedback_sweep_{run_id}_{date}.md).
"""

from __future__ import annotations
import io
from typing import Any


def render_sweep_markdown(run_id: str, rows: list[dict[str, Any]]) -> bytes:
    """Render finding-record rows as deterministic markdown bytes.

    Sort order: within each section, records are sorted by PK ascending.
    No generated_at in body — timestamp belongs in the filename (D-03).
    """
    candidates = sorted(
        (r for r in rows if r.get("record_type") == "CANDIDATE_TOPIC"),
        key=lambda r: r["PK"],
    )
    reclusters = sorted(
        (r for r in rows if r.get("record_type") == "RECLUSTER_RECOMMENDATION"),
        key=lambda r: r["PK"],
    )
    diagnostics = sorted(
        (r for r in rows if r.get("record_type") == "SPOTLIGHT_DIAGNOSTIC"),
        key=lambda r: r["PK"],
    )

    buf = io.StringIO()
    buf.write(f"# Feedback Sweep — run_id `{run_id}`\n\n")

    buf.write(f"## Candidate Topics ({len(candidates)})\n\n")
    for r in candidates:
        buf.write(f"### `{r['slug']}` — {r['proposed_label']}\n")
        buf.write(f"- evidence PMIDs: {', '.join(r['source_pmids'][:10])}\n")
        buf.write(f"- rationale: {r['sonnet_rationale']}\n")
        if r.get("truncated"):
            buf.write(
                f"- _truncated: {r.get('total_unprocessed_remaining')} PMIDs unprocessed_\n"
            )
        buf.write("\n")

    buf.write(f"## Recluster Recommendations ({len(reclusters)})\n\n")
    for r in reclusters:
        buf.write(f"### `{r['topic_id']}`\n")
        for ev in r["evaluation_history"]:
            buf.write(f"- {ev['date']}: count={ev['count']}\n")
        buf.write("\n")

    buf.write(f"## Spotlight Diagnostics ({len(diagnostics)})\n\n")
    for r in diagnostics:
        # D-08: keyed by subtopic_id (not cwid)
        buf.write(f"### `{r['subtopic_id']}` (subtopic_id)\n")
        buf.write(
            f"- distinct rejected (publish, pmid_set) pairs: {r['distinct_pmid_set_count']}\n"
        )
        buf.write(
            f"- reason_code distribution: {sorted(r['reason_code_distribution'].items())}\n"
        )
        buf.write(f"- window_days: {r['window_days']}\n")
        ut = r.get("underlying_rejects_truncated", False)
        tail = f" (truncated; total={r.get('total_underlying')})" if ut else ""
        buf.write(f"- underlying_rejects: {len(r['underlying_rejects'])}{tail}\n")
        buf.write("\n")

    return buf.getvalue().encode("utf-8")
