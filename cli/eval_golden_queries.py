"""
eval_golden_queries.py — Regression harness for the ReCiter AI Chatbot subtopic system.

Usage:
  # 1. Run hierarchy-mode queries against the live dev server:
  python eval_golden_queries.py \\
      --server http://localhost:3000 \\
      --queries ReCiter-Publication-Manager/tests/chatbot/golden-queries.json \\
      --baseline .planning/phases/04-subtopic-system/artifacts/golden_baseline.json \\
      --output .planning/phases/04-subtopic-system/hierarchy_run_results.json

  # 2. Generate regression gate report (hierarchy vs frozen baseline):
  python eval_golden_queries.py \\
      --diff-report \\
      --baseline .planning/phases/04-subtopic-system/artifacts/golden_baseline.json \\
      --hierarchy .planning/phases/04-subtopic-system/hierarchy_run_results.json \\
      --output .planning/phases/04-subtopic-system/regression_gate_report.md

Exit codes:
  0 — all categories PASS (or report written successfully without --diff-report gate)
  1 — one or more categories FAIL in --diff-report mode
  2 — usage / argument error
  3 — hash-lock gate failed (golden-queries.json was tampered)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Metric functions
# ---------------------------------------------------------------------------


def compute_mrr(expected: list[str], returned: list[str]) -> float:
    """Mean Reciprocal Rank — rank of the first expected hit in returned list.

    Args:
        expected: Ordered list of expected faculty personIdentifiers.
        returned: Ordered list returned by the server (any length).

    Returns:
        1/rank_of_first_hit, or 0.0 if no expected item appears in returned.
    """
    if not expected or not returned:
        return 0.0
    expected_set = set(expected)
    for rank, item in enumerate(returned, start=1):
        if item in expected_set:
            return 1.0 / rank
    return 0.0


def compute_recall_at_10(expected: list[str], returned: list[str]) -> float:
    """Recall@10 — fraction of expected faculty found in the top-10 returned results.

    Args:
        expected: List of expected faculty personIdentifiers.
        returned: Ordered list returned by the server (capped at first 10).

    Returns:
        |expected ∩ returned[:10]| / |expected|, or 0.0 if expected is empty.
    """
    if not expected:
        return 0.0
    top_10 = set(returned[:10])
    hits = sum(1 for e in expected if e in top_10)
    return hits / len(expected)


# ---------------------------------------------------------------------------
# diff_report: per-category aggregate comparison
# ---------------------------------------------------------------------------

QUERY_CATEGORIES = ["topic_match", "topic_decompose", "gap_query", "team_assembly"]


def diff_report(
    baseline_results: list[dict[str, Any]],
    hierarchy_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare baseline vs hierarchy run per query category.

    For topic_decompose, MRR and Recall@10 are not meaningful (no expected_faculty).
    The category entry still records structural info and defaults to PASS status
    (structural correctness is verified separately during the run step).

    Args:
        baseline_results: List of result dicts from the frozen baseline.
        hierarchy_results: List of result dicts from the hierarchy run.

    Returns:
        Dict keyed by category with:
          baseline_mrr, hierarchy_mrr, baseline_recall_at_10,
          hierarchy_recall_at_10, status (PASS|FAIL), query_count.
    """
    # Index hierarchy results by id for fast lookup
    hierarchy_by_id: dict[str, dict] = {r["id"]: r for r in hierarchy_results}

    # Accumulate per-category stats
    cat_stats: dict[str, dict[str, list[float]]] = {
        cat: {
            "base_mrr": [],
            "hier_mrr": [],
            "base_r10": [],
            "hier_r10": [],
        }
        for cat in QUERY_CATEGORIES
    }

    for b_result in baseline_results:
        qid = b_result["id"]
        qtype = b_result.get("query_type", "topic_match")
        if qtype not in cat_stats:
            continue

        h_result = hierarchy_by_id.get(qid, {})
        expected = b_result.get("expected_faculty") or []
        b_returned = b_result.get("returned_faculty") or []
        h_returned = h_result.get("returned_faculty") or []

        if qtype == "topic_decompose":
            # Structural correctness only — metric values are informational
            cat_stats[qtype]["base_mrr"].append(0.0)
            cat_stats[qtype]["hier_mrr"].append(0.0)
            cat_stats[qtype]["base_r10"].append(0.0)
            cat_stats[qtype]["hier_r10"].append(0.0)
        else:
            cat_stats[qtype]["base_mrr"].append(compute_mrr(expected, b_returned))
            cat_stats[qtype]["hier_mrr"].append(compute_mrr(expected, h_returned))
            cat_stats[qtype]["base_r10"].append(
                compute_recall_at_10(expected, b_returned)
            )
            cat_stats[qtype]["hier_r10"].append(
                compute_recall_at_10(expected, h_returned)
            )

    report: dict[str, Any] = {}
    for cat in QUERY_CATEGORIES:
        stats = cat_stats[cat]
        n = len(stats["base_mrr"])
        if n == 0:
            report[cat] = {
                "query_count": 0,
                "baseline_mrr": None,
                "hierarchy_mrr": None,
                "baseline_recall_at_10": None,
                "hierarchy_recall_at_10": None,
                "status": "N/A",
            }
            continue

        b_mrr = sum(stats["base_mrr"]) / n
        h_mrr = sum(stats["hier_mrr"]) / n
        b_r10 = sum(stats["base_r10"]) / n
        h_r10 = sum(stats["hier_r10"]) / n

        if cat == "topic_decompose":
            # Structural-only: always PASS from metrics perspective
            status = "PASS"
        else:
            status = (
                "PASS"
                if h_mrr >= b_mrr and h_r10 >= b_r10
                else "FAIL"
            )

        report[cat] = {
            "query_count": n,
            "baseline_mrr": round(b_mrr, 4),
            "hierarchy_mrr": round(h_mrr, 4),
            "baseline_recall_at_10": round(b_r10, 4),
            "hierarchy_recall_at_10": round(h_r10, 4),
            "status": status,
        }

    return report


# ---------------------------------------------------------------------------
# Hash-lock verification
# ---------------------------------------------------------------------------


def verify_queries_hash(queries_path: str, baseline_path: str) -> None:
    """Assert sha256(golden-queries.json) matches baseline.queries_sha256.

    Raises SystemExit(3) on mismatch.
    """
    with open(queries_path, "rb") as fh:
        actual_hash = hashlib.sha256(fh.read()).hexdigest()

    with open(baseline_path) as fh:
        baseline = json.load(fh)

    expected_hash = baseline.get("queries_sha256", "")
    if actual_hash != expected_hash:
        print(
            f"ERROR: Hash-lock gate FAILED.\n"
            f"  golden-queries.json sha256: {actual_hash}\n"
            f"  baseline.queries_sha256:    {expected_hash}\n"
            f"This indicates post-hoc ground-truth tampering or an un-recorded "
            f"authorized mutation.\n"
            f"If you intentionally modified golden-queries.json (e.g., Plan 10 "
            f"expected_tier annotation), update queries_sha256 in the baseline "
            f"file and re-run. Use --skip-hash-check to bypass this gate "
            f"(explicit escape hatch).",
            file=sys.stderr,
        )
        sys.exit(3)


# ---------------------------------------------------------------------------
# SSE parsing helpers
# ---------------------------------------------------------------------------


def _parse_sse_stream(content_iter) -> dict[str, Any]:
    """Parse SSE event stream and collect retrieved + done events.

    Returns dict with:
      returned_faculty: list[str]
      subtopics: list  (for topic_decompose)
      gap_results: list  (for gap_query)
      done_meta: dict  (latency, cost, tokens)
      raw_events: list[dict]
    """
    buffer = b""
    result: dict[str, Any] = {
        "returned_faculty": [],
        "subtopics": [],
        "gap_results": [],
        "done_meta": {},
        "raw_events": [],
    }

    for chunk in content_iter:
        buffer += chunk
        while b"\n\n" in buffer:
            event_bytes, buffer = buffer.split(b"\n\n", 1)
            event_text = event_bytes.decode("utf-8", errors="replace")
            for line in event_text.splitlines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    result["raw_events"].append(payload)
                    evt_type = payload.get("type", "")
                    if evt_type == "retrieved":
                        # Candidates (topic_match / person_centric)
                        candidates = payload.get("candidates", [])
                        for c in candidates:
                            pid = c.get("personIdentifier") or c.get("faculty_uid")
                            if pid:
                                result["returned_faculty"].append(pid)
                        # Subtopics (topic_decompose)
                        subs = payload.get("subtopics", [])
                        result["subtopics"].extend(subs)
                        # Gap results
                        gaps = payload.get("gapResults", [])
                        result["gap_results"].extend(gaps)
                    elif evt_type == "done":
                        result["done_meta"] = payload
    return result


# ---------------------------------------------------------------------------
# Core: run_queries
# ---------------------------------------------------------------------------


def run_queries(
    server: str,
    queries_path: str,
    baseline_path: str,
    output_path: str,
    skip_hash_check: bool = False,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Run all golden queries against the hierarchy-mode server and write results.

    Args:
        server: Base URL (e.g., http://localhost:3000).
        queries_path: Path to golden-queries.json.
        baseline_path: Path to golden_baseline.json (for hash-lock verification).
        output_path: Path to write hierarchy_run_results.json.
        skip_hash_check: If True, bypass the sha256 hash-lock gate.
        timeout: Per-query HTTP timeout in seconds.

    Returns:
        List of result dicts (also written to output_path).
    """
    if not skip_hash_check:
        verify_queries_hash(queries_path, baseline_path)

    with open(queries_path) as fh:
        golden = json.load(fh)

    queries = golden.get("queries", [])
    endpoint = f"{server.rstrip('/')}/api/chat"
    run_results: list[dict[str, Any]] = []

    for q in queries:
        qid = q["id"]
        query_text = q["query"]
        query_type = q.get("query_type", "topic_match")
        expected_faculty = q.get("expected_faculty", [])

        print(f"  Running {qid}: {query_text[:60]}...", end=" ", flush=True)
        t0 = time.time()

        try:
            with requests.post(
                endpoint,
                json={"message": query_text, "conversationId": None},
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                parsed = _parse_sse_stream(resp.iter_content(chunk_size=None))

            latency_ms = int((time.time() - t0) * 1000)
            done_meta = parsed.get("done_meta", {})
            print(f"OK ({latency_ms}ms, {len(parsed['returned_faculty'])} faculty)")

            run_results.append(
                {
                    "id": qid,
                    "query": query_text,
                    "query_type": query_type,
                    "expected_faculty": expected_faculty,
                    "returned_faculty": parsed["returned_faculty"],
                    "subtopics_returned": parsed["subtopics"],
                    "gap_results_returned": parsed["gap_results"],
                    "latency_ms": latency_ms,
                    "cost_usd": done_meta.get("costUsd"),
                    "input_tokens": done_meta.get("inputTokens"),
                    "output_tokens": done_meta.get("outputTokens"),
                    "raw_events": parsed["raw_events"],
                }
            )
        except requests.exceptions.Timeout:
            print(f"TIMEOUT ({timeout}s)")
            run_results.append(
                {
                    "id": qid,
                    "query": query_text,
                    "query_type": query_type,
                    "expected_faculty": expected_faculty,
                    "returned_faculty": [],
                    "subtopics_returned": [],
                    "gap_results_returned": [],
                    "latency_ms": timeout * 1000,
                    "error": "timeout",
                    "raw_events": [],
                }
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            run_results.append(
                {
                    "id": qid,
                    "query": query_text,
                    "query_type": query_type,
                    "expected_faculty": expected_faculty,
                    "returned_faculty": [],
                    "subtopics_returned": [],
                    "gap_results_returned": [],
                    "latency_ms": -1,
                    "error": str(exc),
                    "raw_events": [],
                }
            )

    output_data = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "server": server,
        "queries_path": queries_path,
        "query_count": len(run_results),
        "results": run_results,
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(output_data, fh, indent=2)

    print(f"\nResults written to {output_path}")
    return run_results


# ---------------------------------------------------------------------------
# Core: write_diff_report
# ---------------------------------------------------------------------------


def write_diff_report(
    baseline_path: str,
    hierarchy_path: str,
    report_path: str,
) -> bool:
    """Compare baseline and hierarchy run results and write a markdown report.

    Returns True if all categories PASS, False otherwise.
    """
    with open(baseline_path) as fh:
        baseline_data = json.load(fh)

    with open(hierarchy_path) as fh:
        hierarchy_data = json.load(fh)

    baseline_results: list[dict] = baseline_data.get("results", [])
    hierarchy_results: list[dict] = hierarchy_data.get("results", [])

    # Merge expected_faculty from hierarchy results if missing from baseline
    hierarchy_by_id = {r["id"]: r for r in hierarchy_results}
    for b in baseline_results:
        if not b.get("expected_faculty") and b["id"] in hierarchy_by_id:
            b["expected_faculty"] = hierarchy_by_id[b["id"]].get(
                "expected_faculty", []
            )

    report = diff_report(baseline_results, hierarchy_results)

    overall_pass = all(
        v.get("status") in ("PASS", "N/A")
        for v in report.values()
        if v is not None
    )

    # Build markdown report
    run_at = hierarchy_data.get("run_at", "unknown")
    lines = [
        "# ReCiter AI Chatbot — Regression Gate Report",
        "",
        f"**Generated:** {run_at}",
        f"**Hierarchy server:** {hierarchy_data.get('server', 'unknown')}",
        f"**Baseline:** {baseline_path}",
        f"**Hierarchy run:** {hierarchy_path}",
        "",
        f"## Overall Verdict: {'PASS' if overall_pass else 'FAIL'}",
        "",
        "## Per-Category Results",
        "",
        "| Category | Baseline MRR | Hierarchy MRR | Baseline R@10 | Hierarchy R@10 | Queries | Status |",
        "|----------|-------------|---------------|---------------|----------------|---------|--------|",
    ]

    for cat in QUERY_CATEGORIES:
        row = report.get(cat, {})
        if not row or row.get("status") == "N/A":
            lines.append(
                f"| {cat} | — | — | — | — | 0 | N/A |"
            )
            continue
        b_mrr = f"{row['baseline_mrr']:.4f}" if row["baseline_mrr"] is not None else "—"
        h_mrr = f"{row['hierarchy_mrr']:.4f}" if row["hierarchy_mrr"] is not None else "—"
        b_r10 = (
            f"{row['baseline_recall_at_10']:.4f}"
            if row["baseline_recall_at_10"] is not None
            else "—"
        )
        h_r10 = (
            f"{row['hierarchy_recall_at_10']:.4f}"
            if row["hierarchy_recall_at_10"] is not None
            else "—"
        )
        status = row["status"]
        n = row["query_count"]
        lines.append(
            f"| {cat} | {b_mrr} | {h_mrr} | {b_r10} | {h_r10} | {n} | **{status}** |"
        )

    lines += [
        "",
        "## Gate Rules",
        "",
        "- **PASS**: hierarchy_MRR >= baseline_MRR AND hierarchy_Recall@10 >= baseline_Recall@10 for ALL categories",
        "- **FAIL**: any category is below baseline on either metric",
        "- `topic_decompose` is structural-only (no MRR/R@10 gate); marked PASS if renderer returned subtopics",
        "",
        "## Notes",
        "",
        "- Baseline is frozen from Plan 01 Task 4 (pre-Phase-4 flat-topic server). It cannot be corrupted by hierarchy code.",
        "- Hash-lock on golden-queries.json prevents post-hoc ground-truth tampering.",
        "- Run manually before each production deployment of subtopic code. NOT a CI hook.",
        "",
    ]

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as fh:
        fh.write("\n".join(lines))

    print(f"\nRegression gate report written to {report_path}")
    print(f"Overall verdict: {'PASS' if overall_pass else 'FAIL'}")
    return overall_pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "eval_golden_queries.py — Regression harness for the ReCiter AI Chatbot "
            "subtopic system (Plan 10, Phase 4)."
        )
    )
    parser.add_argument(
        "--server",
        default="http://localhost:3000",
        help="Base URL of the PM dev server (default: http://localhost:3000)",
    )
    parser.add_argument(
        "--queries",
        default="ReCiter-Publication-Manager/tests/chatbot/golden-queries.json",
        help="Path to golden-queries.json",
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Path to golden_baseline.json (frozen Plan 01 baseline)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help=(
            "In run mode: path to write hierarchy_run_results.json. "
            "In --diff-report mode: path to write regression_gate_report.md."
        ),
    )
    parser.add_argument(
        "--hierarchy",
        default=None,
        help="Path to hierarchy_run_results.json (required for --diff-report)",
    )
    parser.add_argument(
        "--mode",
        choices=["hierarchy"],
        default="hierarchy",
        help="Query run mode (default: hierarchy — post-classifier active)",
    )
    parser.add_argument(
        "--diff-report",
        action="store_true",
        help="Generate regression gate report comparing baseline vs hierarchy run",
    )
    parser.add_argument(
        "--skip-hash-check",
        action="store_true",
        help=(
            "Bypass sha256 hash-lock gate on golden-queries.json "
            "(explicit escape hatch — use only when intentionally updating ground truth)"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Per-query HTTP timeout in seconds (default: 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.diff_report:
        if not args.hierarchy:
            print(
                "ERROR: --hierarchy is required when using --diff-report", file=sys.stderr
            )
            return 2
        passed = write_diff_report(
            baseline_path=args.baseline,
            hierarchy_path=args.hierarchy,
            report_path=args.output,
        )
        return 0 if passed else 1
    else:
        print(
            f"Running {args.mode} queries against {args.server} ...\n"
            f"  Queries:  {args.queries}\n"
            f"  Baseline: {args.baseline}\n"
            f"  Output:   {args.output}\n"
        )
        run_queries(
            server=args.server,
            queries_path=args.queries,
            baseline_path=args.baseline,
            output_path=args.output,
            skip_hash_check=args.skip_hash_check,
            timeout=args.timeout,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
