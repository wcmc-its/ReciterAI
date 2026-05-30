"""
Tests for eval_golden_queries.py regression harness.

Test plan:
  Test 1 (unit): compute_mrr returns 0.5 when first expected hit is at rank 2
  Test 2 (unit): compute_recall_at_10 returns 3/5 when 3 of 5 expected appear in top 10
  Test 3 (unit): MRR and Recall@10 return 0.0 when no expected match appears in returned list
  Test 4 (unit): diff_report aggregates per-category MRR/Recall@10 from two result sets
  Test 5 (integration, mocked HTTP): full harness run with 2 fake queries writes regression_gate_report.md
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Test 1 — compute_mrr
# ---------------------------------------------------------------------------


class TestComputeMRR(unittest.TestCase):
    def setUp(self):
        from cli.eval_golden_queries import compute_mrr
        self.compute_mrr = compute_mrr

    def test_hit_at_rank_2_returns_half(self):
        """a found at rank 2 -> MRR = 1/2 = 0.5"""
        result = self.compute_mrr(
            expected=["a", "b", "c"],
            returned=["x", "a", "y"],
        )
        self.assertAlmostEqual(result, 0.5)

    def test_hit_at_rank_1_returns_one(self):
        result = self.compute_mrr(expected=["a"], returned=["a", "b"])
        self.assertAlmostEqual(result, 1.0)

    def test_hit_at_rank_3_returns_third(self):
        result = self.compute_mrr(expected=["c"], returned=["x", "y", "c"])
        self.assertAlmostEqual(result, 1 / 3)


# ---------------------------------------------------------------------------
# Test 2 — compute_recall_at_10
# ---------------------------------------------------------------------------


class TestComputeRecallAt10(unittest.TestCase):
    def setUp(self):
        from cli.eval_golden_queries import compute_recall_at_10
        self.compute_recall_at_10 = compute_recall_at_10

    def test_three_of_five_returns_point_six(self):
        """3 of 5 expected in returned top-10 -> Recall@10 = 3/5 = 0.6"""
        result = self.compute_recall_at_10(
            expected=["a", "b", "c", "d", "e"],
            returned=["a", "b", "x", "y", "z", "c", "w", "v", "u", "t"],
        )
        self.assertAlmostEqual(result, 0.6)

    def test_all_expected_in_returned(self):
        result = self.compute_recall_at_10(
            expected=["a", "b"],
            returned=["a", "b", "c"],
        )
        self.assertAlmostEqual(result, 1.0)

    def test_only_top_10_counted(self):
        """Hit at position 11 should not count."""
        returned = ["x"] * 10 + ["a"]
        result = self.compute_recall_at_10(expected=["a"], returned=returned)
        self.assertAlmostEqual(result, 0.0)


# ---------------------------------------------------------------------------
# Test 3 — zero score when no expected match appears
# ---------------------------------------------------------------------------


class TestZeroScores(unittest.TestCase):
    def setUp(self):
        from cli.eval_golden_queries import compute_mrr, compute_recall_at_10
        self.compute_mrr = compute_mrr
        self.compute_recall_at_10 = compute_recall_at_10

    def test_mrr_zero_on_no_match(self):
        result = self.compute_mrr(expected=["a", "b"], returned=["x", "y", "z"])
        self.assertEqual(result, 0.0)

    def test_recall_zero_on_no_match(self):
        result = self.compute_recall_at_10(
            expected=["a", "b"], returned=["x", "y", "z"]
        )
        self.assertEqual(result, 0.0)

    def test_mrr_zero_on_empty_returned(self):
        result = self.compute_mrr(expected=["a"], returned=[])
        self.assertEqual(result, 0.0)

    def test_recall_zero_on_empty_expected(self):
        result = self.compute_recall_at_10(expected=[], returned=["a", "b"])
        self.assertEqual(result, 0.0)


# ---------------------------------------------------------------------------
# Test 4 — diff_report per-category aggregates
# ---------------------------------------------------------------------------


class TestDiffReport(unittest.TestCase):
    def setUp(self):
        from cli.eval_golden_queries import diff_report
        self.diff_report = diff_report

    def _make_result(self, query_id, query_type, returned_faculty, expected_faculty):
        return {
            "id": query_id,
            "query_type": query_type,
            "returned_faculty": returned_faculty,
            "expected_faculty": expected_faculty,
        }

    def test_aggregates_per_category(self):
        """diff_report returns per-category MRR and Recall@10 for all 4 categories."""
        baseline_results = [
            self._make_result("gq-01", "topic_match", ["a", "b"], ["a"]),
            self._make_result("gq-09", "topic_decompose", [], []),
            self._make_result("gq-12", "gap_query", ["x"], ["x"]),
            self._make_result("gq-14", "team_assembly", ["y"], ["y"]),
        ]
        hierarchy_results = [
            self._make_result("gq-01", "topic_match", ["a", "b"], ["a"]),
            self._make_result("gq-09", "topic_decompose", [], []),
            self._make_result("gq-12", "gap_query", ["x"], ["x"]),
            self._make_result("gq-14", "team_assembly", ["y"], ["y"]),
        ]
        report = self.diff_report(baseline_results, hierarchy_results)
        self.assertIn("topic_match", report)
        self.assertIn("topic_decompose", report)
        self.assertIn("gap_query", report)
        self.assertIn("team_assembly", report)
        for cat in ["topic_match", "gap_query", "team_assembly"]:
            self.assertIn("baseline_mrr", report[cat])
            self.assertIn("hierarchy_mrr", report[cat])
            self.assertIn("baseline_recall_at_10", report[cat])
            self.assertIn("hierarchy_recall_at_10", report[cat])
            self.assertIn("status", report[cat])

    def test_pass_when_hierarchy_not_worse(self):
        baseline = [self._make_result("gq-01", "topic_match", ["a"], ["a"])]
        hierarchy = [self._make_result("gq-01", "topic_match", ["a", "b"], ["a"])]
        report = self.diff_report(baseline, hierarchy)
        self.assertEqual(report["topic_match"]["status"], "PASS")

    def test_fail_when_hierarchy_worse_mrr(self):
        baseline = [self._make_result("gq-01", "topic_match", ["a"], ["a"])]
        hierarchy = [self._make_result("gq-01", "topic_match", ["x", "y", "a"], ["a"])]
        report = self.diff_report(baseline, hierarchy)
        # hierarchy MRR (1/3) < baseline MRR (1/1)
        self.assertEqual(report["topic_match"]["status"], "FAIL")


# ---------------------------------------------------------------------------
# Test 5 — integration: full harness run with mocked HTTP
# ---------------------------------------------------------------------------


FAKE_GOLDEN_QUERIES = {
    "version": 1,
    "authored_by": "Test",
    "authored_at": "2026-04-22",
    "regression_baseline": "flat_topic_v2_phase2_complete",
    "queries": [
        {
            "id": "gq-01",
            "query_type": "topic_match",
            "query": "Who works on aging?",
            "expected_faculty": ["mslachs", "sjc7004"],
            "expected_tier": 3,
            "expected_behavior": "cascade_hit_tier3_fallback",
        },
        {
            "id": "gq-09",
            "query_type": "topic_decompose",
            "query": "What are subtopics of aging?",
            "expected_faculty": [],
            "expected_tier": None,
            "expected_behavior": "topic_decompose",
        },
    ],
}

# Minimal SSE stream simulating the /api/chat response
FAKE_SSE_TOPIC_MATCH = (
    b'data: {"type":"retrieved","candidates":['
    b'{"personIdentifier":"mslachs","rankingScore":4.0},'
    b'{"personIdentifier":"sjc7004","rankingScore":3.5}'
    b']}\n\n'
    b'data: {"type":"done","totalLatencyMs":1000,"costUsd":0.05,'
    b'"inputTokens":100,"outputTokens":50,"guardrail":null}\n\n'
)

FAKE_SSE_TOPIC_DECOMPOSE = (
    b'data: {"type":"retrieved","subtopics":['
    b'{"id":"aging_cellular_senescence","label":"Cellular Senescence"}'
    b']}\n\n'
    b'data: {"type":"done","totalLatencyMs":500,"costUsd":0.01,'
    b'"inputTokens":50,"outputTokens":20,"guardrail":null}\n\n'
)


def _make_fake_response(content: bytes):
    """Return a mock requests.Response with iter_content returning the SSE bytes."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_content = MagicMock(
        return_value=iter([content])
    )
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestIntegrationHarnessRun(unittest.TestCase):
    def test_full_run_writes_report(self):
        """Full harness run with 2 fake queries writes regression_gate_report.md."""
        import importlib
        import cli.eval_golden_queries as egq

        with tempfile.TemporaryDirectory() as tmpdir:
            queries_path = os.path.join(tmpdir, "golden-queries.json")
            baseline_path = os.path.join(tmpdir, "golden_baseline.json")
            output_path = os.path.join(tmpdir, "hierarchy_run_results.json")
            report_path = os.path.join(tmpdir, "regression_gate_report.md")

            # Write fake golden-queries.json
            with open(queries_path, "w") as f:
                json.dump(FAKE_GOLDEN_QUERIES, f)

            # Compute its sha256 and write fake baseline
            import hashlib
            with open(queries_path, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()

            baseline_data = {
                "queries_sha256": sha,
                "results": [
                    {
                        "id": "gq-01",
                        "query_type": "topic_match",
                        "returned_faculty": ["mslachs"],
                        "expected_faculty": ["mslachs", "sjc7004"],
                    },
                    {
                        "id": "gq-09",
                        "query_type": "topic_decompose",
                        "returned_faculty": [],
                        "expected_faculty": [],
                    },
                ],
            }
            with open(baseline_path, "w") as f:
                json.dump(baseline_data, f)

            # Mock HTTP post to return fake SSE responses
            call_count = [0]
            def fake_post(url, **kwargs):
                idx = call_count[0]
                call_count[0] += 1
                if idx == 0:
                    return _make_fake_response(FAKE_SSE_TOPIC_MATCH)
                return _make_fake_response(FAKE_SSE_TOPIC_DECOMPOSE)

            with patch("requests.post", side_effect=fake_post):
                # Run hierarchy-mode query capture
                egq.run_queries(
                    server="http://localhost:3000",
                    queries_path=queries_path,
                    baseline_path=baseline_path,
                    output_path=output_path,
                    skip_hash_check=False,
                )

            # Verify output JSON was written
            self.assertTrue(os.path.exists(output_path))
            with open(output_path) as f:
                results = json.load(f)
            self.assertEqual(len(results["results"]), 2)

            # Run diff-report
            egq.write_diff_report(
                baseline_path=baseline_path,
                hierarchy_path=output_path,
                report_path=report_path,
            )

            # Verify report was written and contains expected structure
            self.assertTrue(os.path.exists(report_path))
            with open(report_path) as f:
                content = f.read()
            self.assertIn("PASS", content)
            self.assertIn("topic_match", content.lower())


if __name__ == "__main__":
    unittest.main()
