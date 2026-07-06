"""Offline eval harness for grant->researcher ranking quality.

The problem this solves: every quality judgment on the matcher today is human
eyeballing. You cannot iterate ("run loops") on ranking quality without a number
that goes up when a change helps. This produces that number.

A "ranking dump" is the unit of input, one per grant, JSON:

  {
    "grant": "worldquant",
    "gate": "esi" | "ft",
    "solicitation": "<the grant's focus text>",
    "ranked": [                       # in the ranker's order, best first
      {"cwid": "qiz4006", "name": "Qingyu Zhao", "rank": 1, "fit": 4.55,
       "evidence": [{"pmid","title","year","subtopic","relevance"}, ...]},
      ...
    ],
    "awardees": ["cwid", ...]         # optional past winners (back-test ground truth)
  }

Dumps come from the ranker (the SPS ECS `dump_rankings` harness, emitting JSON) or,
as a bootstrap, from `grant_eval_from_verification.py` which parses the existing
human-verification markdown into this shape.

TWO CROSS-VALIDATING METRICS
  A. LLM-judge nDCG / precision (every grant, scalable): a judge model, BLIND to the
     ranker's fit/rank/relevance, grades each shortlisted researcher's scientific fit
     0-3 from the grant text + the researcher's paper titles. We then score the
     ranker's ORDER against those grades (nDCG@k, precision@k, mean-fit). The judge is
     told to grade scientific substance, not keyword overlap, so it flags the ranker's
     lexical-leakage failures instead of rubber-stamping them.
  B. Historical-winner back-test (subset, honest): for the ~35 WCM funding-DB grants
     that carry past_recipients, map awardee email-prefix -> cwid and report hit@k /
     rank over awardees that are actually in the candidate pool. Partial by design
     (awardees skew pre-2020, corpus floor is 2020+) -- coverage is reported, never
     hidden.

A single aggregate score per run + a diff vs a saved baseline is what you loop on.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Metrics (pure -- unit-tested in tests/test_grant_eval.py)
# ---------------------------------------------------------------------------


def dcg(rels: list[float]) -> float:
    """Discounted cumulative gain with the standard (2^rel - 1) gain."""
    return sum((2.0 ** r - 1.0) / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg_at_k(order_rels: list[float], k: int) -> float:
    """nDCG@k of a ranking whose items have relevance `order_rels` in rank order."""
    ideal = sorted(order_rels, reverse=True)
    idcg = dcg(ideal[:k])
    if idcg == 0.0:
        return 0.0
    return dcg(order_rels[:k]) / idcg


def precision_at_k(order_rels: list[float], k: int, thresh: float = 2.0) -> float:
    """Fraction of the top-k with relevance >= thresh (2 = adjacent-or-better)."""
    top = order_rels[:k]
    if not top:
        return 0.0
    return sum(1 for r in top if r >= thresh) / len(top)


def backtest_ranks(pool_cwids: list[str], awardee_cwids: set[str]) -> dict:
    """Where do known past winners land in the ranked pool?

    `pool_cwids` is the ranker's order (may be truncated to top-N -- then hit@k is a
    lower bound and percentile is only meaningful over a full pool). Returns raw
    counts + the 1-based ranks of awardees found in the pool; the caller aggregates.
    """
    index = {cw: i + 1 for i, cw in enumerate(pool_cwids)}
    found = sorted(index[cw] for cw in awardee_cwids if cw in index)
    n = len(pool_cwids)
    return {
        "awardees": len(awardee_cwids),
        "in_pool": len(found),
        "ranks": found,
        "pool_size": n,
        "percentiles": [round(1 - (r - 1) / n, 3) for r in found] if n else [],
    }


# ---------------------------------------------------------------------------
# LLM judge (Bedrock Sonnet, OpenAI gpt-5.x fallback per operator preference)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You grade how well each researcher fits a specific funding opportunity.

Grade SCIENTIFIC SUBSTANCE: the actual field, biological system, and methodology.
Do NOT reward keyword overlap. A paper that merely shares a word ("genomics",
"imaging", "network") with the grant while being a different field is NOT a fit.

Scale (integers):
  3 = squarely in the grant's scientific scope AND approach; an obvious fit.
  2 = adjacent/plausible; overlaps the grant's area but is not centrally on it.
  1 = tangential; shares vocabulary or a tool but the science is different.
  0 = off-topic; wrong field, wrong system, or wrong methodology.

Judge only from the paper titles given. Reward depth on the grant's topic over a
single lucky keyword match. Return STRICT JSON, one entry per researcher:
{"ratings":[{"cwid":"<id>","fit":<0-3>,"reason":"<=12 words"}]}"""


def _call_judge(system: str, user: str) -> dict:
    """Bedrock Sonnet first; on any failure (content filter, transient) fall back to
    OpenAI gpt-5.1. Both return the parsed JSON dict."""
    try:
        from utils.bedrock_client import BedrockClient, SONNET_MODEL

        return BedrockClient().call_json(
            model=SONNET_MODEL,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001 -- deliberately broad; fall back on anything
        sys.stderr.write(f"[judge] Bedrock failed ({type(e).__name__}: {e}); OpenAI fallback\n")
        from utils.openai_client import create_gpt5_completion, get_default_client, GPT5_MODEL

        resp = create_gpt5_completion(
            client=get_default_client(),
            model=GPT5_MODEL,
            system_prompt=system,
            user_prompt=user,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        cleaned = re.sub(r"```json\n?|\n?```", "", content).strip()
        return json.loads(cleaned)


def judge_grant(dump: dict, k: int = 10) -> dict[str, tuple[int, str]]:
    """Grade the top-k researchers. Returns {cwid: (fit_0_3, reason)}.

    The judge sees ONLY cwid + paper titles/years -- never the ranker's fit, rank, or
    relevance -- so its grade is independent of what we are grading.
    """
    cands = dump["ranked"][:k]
    payload = {
        "funding_opportunity": dump["solicitation"],
        "researchers": [
            {
                "cwid": c["cwid"],
                "papers": [
                    f'{e.get("title", "").rstrip(".")} ({e.get("year", "n/a")})'
                    for e in c.get("evidence", [])
                ],
            }
            for c in cands
        ],
    }
    out = _call_judge(JUDGE_SYSTEM, json.dumps(payload))
    graded = {r["cwid"]: (int(r["fit"]), r.get("reason", "")) for r in out.get("ratings", [])}
    # Any candidate the judge skipped scores 0 (absent from a fit list = not a fit).
    return {c["cwid"]: graded.get(c["cwid"], (0, "no rating")) for c in cands}


# ---------------------------------------------------------------------------
# Back-test ground truth from the WCM funding DB
# ---------------------------------------------------------------------------


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load_funding_db_awardees(path: str) -> dict[str, set[str]]:
    """{normalized_title -> {cwid}} from funding-DB past_recipients (email prefix = cwid)."""
    data = json.load(open(path))
    out: dict[str, set[str]] = {}
    for rec in data:
        prs = rec.get("past_recipients") or []
        cwids = {
            e["email"].split("@")[0].strip().lower()
            for e in prs
            if e.get("email") and "@" in e["email"]
        }
        if cwids:
            out[_norm_title(rec.get("title", ""))] = cwids
    return out


def awardees_for(dump: dict, db_awardees: dict[str, set[str]]) -> set[str]:
    """Awardee cwids for a dump: explicit `awardees` field, else fuzzy title join."""
    if dump.get("awardees"):
        return set(dump["awardees"])
    key = _norm_title(dump.get("solicitation_title", "") or dump.get("grant", ""))
    if key in db_awardees:
        return db_awardees[key]
    # containment fallback (dump grant name is a slug; DB title is the full name)
    for t, cwids in db_awardees.items():
        if key and (key in t or t in key):
            return cwids
    return set()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def evaluate_dump(dump: dict, k: int, db_awardees: dict[str, set[str]], run_judge: bool) -> dict:
    res: dict = {"grant": dump["grant"], "gate": dump.get("gate"), "n_ranked": len(dump["ranked"])}

    if run_judge:
        grades = judge_grant(dump, k=k)
        order_rels = [grades[c["cwid"]][0] for c in dump["ranked"][:k]]
        res["judge"] = {
            "ndcg_at_k": round(ndcg_at_k(order_rels, k), 4),
            "precision_at_5": round(precision_at_k(order_rels, 5), 3),
            "mean_fit": round(sum(order_rels) / len(order_rels), 3) if order_rels else 0.0,
            "grades": {c["cwid"]: {"rank": i + 1, "fit": grades[c["cwid"]][0], "reason": grades[c["cwid"]][1]}
                       for i, c in enumerate(dump["ranked"][:k])},
        }

    aw = awardees_for(dump, db_awardees)
    if aw:
        res["backtest"] = backtest_ranks([c["cwid"] for c in dump["ranked"]], aw)
    return res


def aggregate(per_grant: list[dict]) -> dict:
    js = [g["judge"]["ndcg_at_k"] for g in per_grant if "judge" in g]
    ps = [g["judge"]["precision_at_5"] for g in per_grant if "judge" in g]
    bt = [g["backtest"] for g in per_grant if "backtest" in g]
    agg = {"grants": len(per_grant)}
    if js:
        agg["mean_ndcg_at_k"] = round(sum(js) / len(js), 4)
        agg["mean_precision_at_5"] = round(sum(ps) / len(ps), 3)
    if bt:
        tot_aw = sum(b["awardees"] for b in bt)
        tot_in = sum(b["in_pool"] for b in bt)
        agg["backtest"] = {
            "grants_with_awardees": len(bt),
            "awardees_total": tot_aw,
            "awardees_in_pool": tot_in,
            "in_pool_rate": round(tot_in / tot_aw, 3) if tot_aw else 0.0,
        }
    return agg


def main() -> None:
    ap = argparse.ArgumentParser(description="Grant->researcher ranking eval harness")
    ap.add_argument("dumps", nargs="+", help="ranking-dump JSON files (globs ok)")
    ap.add_argument("-k", type=int, default=10, help="top-k judged (default 10)")
    ap.add_argument("--funding-db", default=None, help="WCM funding-DB json (back-test ground truth)")
    ap.add_argument("--no-judge", action="store_true", help="back-test only (no LLM calls)")
    ap.add_argument("--baseline", default=None, help="baseline report json to diff against")
    ap.add_argument("--out", default=None, help="write full report json here")
    args = ap.parse_args()

    paths = sorted({p for g in args.dumps for p in glob.glob(g)})
    if not paths:
        sys.exit("no dump files matched")
    db_awardees = load_funding_db_awardees(args.funding_db) if args.funding_db else {}

    per_grant = []
    for p in paths:
        dump = json.load(open(p))
        if "grant" not in dump or "ranked" not in dump:
            continue  # not a ranking dump (e.g. a baseline/report json caught by the glob)
        r = evaluate_dump(dump, args.k, db_awardees, run_judge=not args.no_judge)
        per_grant.append(r)
        j = r.get("judge", {})
        b = r.get("backtest", {})
        line = f"{r['grant']:<16} "
        if j:
            line += f"nDCG@{args.k}={j['ndcg_at_k']:.3f} P@5={j['precision_at_5']:.2f} meanfit={j['mean_fit']:.2f} "
        if b:
            line += f"backtest={b['in_pool']}/{b['awardees']} in pool (ranks {b['ranks']})"
        print(line)

    agg = aggregate(per_grant)
    report = {"aggregate": agg, "grants": per_grant}
    print("\nAGGREGATE:", json.dumps(agg))

    if args.baseline and os.path.exists(args.baseline):
        base = json.load(open(args.baseline)).get("aggregate", {})
        for key in ("mean_ndcg_at_k", "mean_precision_at_5"):
            if key in agg and key in base:
                d = agg[key] - base[key]
                print(f"  Δ {key}: {d:+.4f}  ({base[key]} -> {agg[key]})")

    if args.out:
        json.dump(report, open(args.out, "w"), indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
