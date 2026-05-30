#!/usr/bin/env python3
"""Bedrock spike for #37 — characterize Claude (Haiku + Sonnet) on the
synopsis + impact prompts vs the stored gpt-5.1 baseline.

Feeds `37-bedrock-cloud-PLAN.md`. Measures, per model:
  - output drift     — synopsis side-by-side + impact numeric delta vs gpt-5.1
  - robustness       — content-filter hits, JSON-parse failures, >95-char synopses
  - cost/latency     — per-call tokens, USD (config/llm_prices.yaml), wall latency
  - concurrency      — parallel throughput, to size the Lambda-vs-Fargate backfill fork
  - backlog          — live missing-synopsis / missing-impact corpus count

Read-only: SELECTs from ReciterDB, calls Bedrock Converse. No writes anywhere.

Run:  python3 scripts/debug/bedrock_synopsis_impact_spike.py --n 15
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import boto3
from botocore.config import Config
from sqlalchemy import text

from pipeline_enrichment.prompts import (
    SYNOPSIS_SYSTEM,
    build_impact_user_content,
    build_synopsis_user_content,
    get_impact_system_prompt,
)
from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL
from utils.db import get_engine
from utils.llm_cost import cost_for

SYNOPSIS_MAX_CHARS = 95
CONTENT_FILTER_STOP = {"content_filtered", "guardrail_intervened"}
MODELS = {"haiku": HAIKU_MODEL, "sonnet": SONNET_MODEL}
IMPACT_USER_PREFIX = "Please analyze the following publication and provide an impact score:\n\n"

# Baseline sample — papers that already have a stored gpt-5.1 synopsis + impact.
# Copied from scripts/equivalence_check.py:_SAMPLE_SQL (importing that module
# pulls in the openai package via pipeline_enrichment.synopsis/impact).
SAMPLE_SQL = """
SELECT a.pmid, a.articleTitle, a.journalTitleVerbose, a.articleYear,
       a.datePublicationAddedToEntrez, a.citationCountNIH, a.percentileNIH,
       a.relativeCitationRatioNIH, r.abstractVarchar,
       s.synopsis AS old_synopsis,
       i.impactScore AS old_impact_score,
       i.justification AS old_impact_justification
FROM reciterai_synopsis s
JOIN reciterai_impact i ON i.external_id = s.external_id AND i.entity_type = s.entity_type
JOIN analysis_summary_article a ON a.pmid = s.external_id
LEFT JOIN reporting_abstracts r ON r.pmid = a.pmid
WHERE s.entity_type = 'publication'
  AND s.synopsis IS NOT NULL AND s.synopsis != ''
  AND a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
ORDER BY s.external_id DESC
LIMIT :n
"""

# Backlog — mirrors utils.sql_queries.FACULTY_GAP_SCAN_SQL's joins/filters
# (first/last full-time-faculty Academic Articles, year >= 2020) and adds the
# reciterai_impact LEFT JOIN so missing-synopsis and missing-impact are counted
# under one identical corpus definition.
BACKLOG_SQL = """
SELECT
  COUNT(DISTINCT a.pmid) AS corpus_pmids,
  COUNT(DISTINCT CASE WHEN s.external_id IS NULL THEN a.pmid END) AS missing_synopsis,
  COUNT(DISTINCT CASE WHEN i.external_id IS NULL THEN a.pmid END) AS missing_impact,
  COUNT(DISTINCT CASE WHEN s.external_id IS NULL OR i.external_id IS NULL
                      THEN a.pmid END) AS missing_either
FROM analysis_summary_author au
JOIN identity id ON id.cwid = au.personIdentifier
JOIN analysis_summary_article a ON a.pmid = au.pmid
LEFT JOIN reciterai_synopsis s
  ON s.external_id = CAST(a.pmid AS CHAR) COLLATE utf8mb4_unicode_ci
  AND s.entity_type = 'publication' AND s.synopsis IS NOT NULL AND s.synopsis != ''
LEFT JOIN reciterai_impact i
  ON i.external_id = CAST(a.pmid AS CHAR) COLLATE utf8mb4_unicode_ci
  AND i.entity_type = 'publication' AND i.impactScore IS NOT NULL
WHERE id.fullTimeFaculty = 'yes'
  AND au.authorPosition IN ('first', 'last')
  AND a.publicationTypeCanonical = 'Academic Article'
  AND a.articleYear >= 2020
"""

_bedrock = None


def bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client(
            "bedrock-runtime",
            config=Config(read_timeout=300, retries={"max_attempts": 4, "mode": "standard"}),
        )
    return _bedrock


def converse(model: str, system: str, user: str, max_tokens: int = 1024) -> dict:
    """One Bedrock Converse call. Never raises — failures come back in the dict."""
    t0 = time.monotonic()
    try:
        resp = bedrock().converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": user}]}],
            system=[{"text": system}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "latency_s": time.monotonic() - t0,
                "text": None, "in_tok": 0, "out_tok": 0, "stop": "exception"}
    latency = time.monotonic() - t0
    stop = resp.get("stopReason", "unknown")
    blocks = resp.get("output", {}).get("message", {}).get("content") or []
    text_out = blocks[0]["text"] if blocks else None
    usage = resp.get("usage", {})
    return {"error": None if text_out else f"empty content (stopReason={stop})",
            "latency_s": latency, "text": text_out,
            "in_tok": int(usage.get("inputTokens", 0)),
            "out_tok": int(usage.get("outputTokens", 0)), "stop": stop}


def parse_json(raw: str | None):
    """Lenient JSON parse — strip ```json fences, else grab the first {...} block."""
    if not raw:
        return None
    cleaned = re.sub(r"```json\s*|\s*```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def run_pmid(model_id: str, row: dict) -> dict:
    """Run synopsis + impact for one paper through one model; record everything."""
    syn = converse(model_id, SYNOPSIS_SYSTEM, build_synopsis_user_content(
        title=row["articleTitle"] or "", journal=row.get("journalTitleVerbose"),
        year=row.get("articleYear"), abstract=row.get("abstractVarchar")))
    syn_payload = parse_json(syn["text"])
    if isinstance(syn_payload, dict):
        new_syn = (syn_payload.get("synopsis") or "").strip()
    elif isinstance(syn_payload, str):
        new_syn = syn_payload.strip()
    else:
        new_syn = None

    imp = converse(model_id, get_impact_system_prompt(),
                   IMPACT_USER_PREFIX + build_impact_user_content(row))
    imp_payload = parse_json(imp["text"])
    new_score = None
    new_justif = None
    if isinstance(imp_payload, dict) and "impactScore" in imp_payload:
        try:
            new_score = max(0, min(100, int(imp_payload["impactScore"])))
        except (TypeError, ValueError):
            new_score = None
        new_justif = (str(imp_payload.get("justification") or "").strip()) or None

    old_score = int(row.get("old_impact_score") or 0)
    return {
        "pmid": str(row["pmid"]),
        "title": (row["articleTitle"] or "")[:90],
        "old_synopsis": row.get("old_synopsis") or "",
        "new_synopsis": new_syn,
        "new_synopsis_len": len(new_syn) if new_syn else None,
        "synopsis_filtered": syn["stop"] in CONTENT_FILTER_STOP,
        "synopsis_error": syn["error"] if new_syn is None else None,
        "old_impact": old_score,
        "new_impact": new_score,
        "impact_delta": abs(new_score - old_score) if new_score is not None else None,
        "old_justif": row.get("old_impact_justification") or "",
        "new_justif": new_justif,
        "impact_filtered": imp["stop"] in CONTENT_FILTER_STOP,
        "impact_error": imp["error"] if new_score is None else None,
        "latency_s": round(syn["latency_s"] + imp["latency_s"], 2),
        "in_tok": syn["in_tok"] + imp["in_tok"],
        "out_tok": syn["out_tok"] + imp["out_tok"],
    }


def summarize(model_id: str, results: list[dict]) -> dict:
    n = len(results)
    syn_ok = [r for r in results if r["new_synopsis"]]
    imp_ok = [r for r in results if r["new_impact"] is not None]
    deltas = [r["impact_delta"] for r in results if r["impact_delta"] is not None]
    in_tok = sum(r["in_tok"] for r in results)
    out_tok = sum(r["out_tok"] for r in results)
    return {
        "n": n,
        "synopsis_ok": len(syn_ok),
        "synopsis_filtered": sum(1 for r in results if r["synopsis_filtered"]),
        "synopsis_over_95": sum(1 for r in syn_ok if (r["new_synopsis_len"] or 0) > SYNOPSIS_MAX_CHARS),
        "impact_ok": len(imp_ok),
        "impact_filtered": sum(1 for r in results if r["impact_filtered"]),
        "impact_delta_mean_abs": round(sum(deltas) / len(deltas), 1) if deltas else None,
        "impact_delta_max": max(deltas) if deltas else None,
        "impact_within_5": sum(1 for d in deltas if d <= 5),
        "latency_p50_s": round(pct([r["latency_s"] for r in results], 0.50), 2),
        "latency_p90_s": round(pct([r["latency_s"] for r in results], 0.90), 2),
        "total_in_tok": in_tok,
        "total_out_tok": out_tok,
        "total_cost_usd": float(cost_for(model_id, in_tok, out_tok)),
        "cost_per_paper_usd": round(float(cost_for(model_id, in_tok, out_tok)) / n, 6) if n else 0,
    }


def concurrency_probe(model_id: str, rows: list[dict], width: int) -> dict:
    """Fire `width` synopsis calls in parallel; measure wall-time + throttling."""
    sample = rows[:width]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=width) as ex:
        out = list(ex.map(
            lambda r: converse(model_id, SYNOPSIS_SYSTEM, build_synopsis_user_content(
                title=r["articleTitle"] or "", journal=r.get("journalTitleVerbose"),
                year=r.get("articleYear"), abstract=r.get("abstractVarchar"))),
            sample))
    wall = time.monotonic() - t0
    throttles = sum(1 for o in out if o["error"] and "throttl" in o["error"].lower())
    errs = sum(1 for o in out if o["error"])
    seq = sum(o["latency_s"] for o in out)
    return {"width": len(sample), "wall_s": round(wall, 2), "summed_seq_s": round(seq, 2),
            "speedup": round(seq / wall, 1) if wall else 0,
            "throttles": throttles, "errors": errs,
            "throughput_calls_per_min": round(len(sample) / wall * 60, 1) if wall else 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=15, help="baseline PMIDs to sample (default 15)")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel-call probe width")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).with_name("bedrock_spike_results.json"))
    args = ap.parse_args(argv)

    engine = get_engine()
    with engine.connect() as conn:
        backlog = dict(conn.execute(text(BACKLOG_SQL)).mappings().one())
        rows = [dict(r) for r in conn.execute(text(SAMPLE_SQL), {"n": args.n}).mappings().all()]
    if not rows:
        print("No baseline PMIDs returned — check DB connectivity / SAMPLE_SQL.")
        return 1

    print("=" * 78)
    print("Bedrock spike for #37 — synopsis + impact, Claude vs gpt-5.1 baseline")
    print("=" * 78)
    print(f"\nBacklog (first/last full-time-faculty Academic Articles, year >= 2020):")
    print(f"  corpus PMIDs        : {backlog['corpus_pmids']}")
    print(f"  missing synopsis    : {backlog['missing_synopsis']}")
    print(f"  missing impact      : {backlog['missing_impact']}")
    print(f"  missing EITHER      : {backlog['missing_either']}   <- backfill volume")
    print(f"\nBaseline sample: {len(rows)} PMIDs with stored gpt-5.1 synopsis + impact\n")

    all_results: dict[str, list[dict]] = {}
    summaries: dict[str, dict] = {}
    for key, model_id in MODELS.items():
        print(f"--- {key} ({model_id}) ---", flush=True)
        results = []
        for i, row in enumerate(rows, 1):
            r = run_pmid(model_id, row)
            flag = ""
            if r["synopsis_filtered"] or r["impact_filtered"]:
                flag = "  [CONTENT-FILTERED]"
            elif r["synopsis_error"] or r["impact_error"]:
                flag = "  [error]"
            print(f"  [{i}/{len(rows)}] pmid={r['pmid']} {r['latency_s']}s"
                  f" Δimpact={r['impact_delta']}{flag}", flush=True)
            results.append(r)
        all_results[key] = results
        summaries[key] = summarize(model_id, results)

    print(f"\n--- concurrency probe (sonnet, {args.concurrency}-wide synopsis) ---", flush=True)
    conc = concurrency_probe(SONNET_MODEL, rows, args.concurrency)
    print(f"  {conc['width']} parallel calls: wall={conc['wall_s']}s "
          f"(summed-sequential={conc['summed_seq_s']}s, speedup {conc['speedup']}x), "
          f"throttles={conc['throttles']}, throughput={conc['throughput_calls_per_min']}/min")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for key in MODELS:
        s = summaries[key]
        print(f"\n[{key}]")
        print(f"  synopsis : {s['synopsis_ok']}/{s['n']} ok, "
              f"{s['synopsis_filtered']} filtered, {s['synopsis_over_95']} over-95-char")
        print(f"  impact   : {s['impact_ok']}/{s['n']} ok, {s['impact_filtered']} filtered, "
              f"within +/-5 of gpt-5.1: {s['impact_within_5']}/{len(rows)}, "
              f"mean|Δ|={s['impact_delta_mean_abs']}, maxΔ={s['impact_delta_max']}")
        print(f"  latency  : p50={s['latency_p50_s']}s  p90={s['latency_p90_s']}s (synopsis+impact)")
        print(f"  cost     : ${s['total_cost_usd']:.4f} for {s['n']} papers "
              f"= ${s['cost_per_paper_usd']:.6f}/paper")
        proj = s["cost_per_paper_usd"] * backlog["missing_either"]
        print(f"  -> {backlog['missing_either']}-paper backfill: ~${proj:.2f}")

    # A few synopsis side-by-sides for eyeball drift.
    print("\n--- synopsis drift (sonnet, first 4) ---")
    for r in all_results["sonnet"][:4]:
        print(f"\nPMID {r['pmid']}: {r['title']}")
        print(f"  gpt-5.1: {r['old_synopsis']}")
        print(f"  sonnet : {r['new_synopsis'] or '<' + str(r['synopsis_error']) + '>'}")

    args.out.write_text(json.dumps(
        {"backlog": {k: int(v) for k, v in backlog.items()},
         "summaries": summaries, "concurrency": conc, "results": all_results},
        indent=2, default=str), encoding="utf-8")
    print(f"\nFull results: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
