"""Phase 0 de-risk spike: pull N Grants.gov opportunities, topic-score them with the
existing scorer, and emit a Markdown review table for human go/no-go eyeballing.

Run: python -m pipeline_grants.spike --rows 50 --keyword "" --out spike_review.md
"""
import argparse
import logging

from pipeline_grants import grants_gov, scoring
from pipeline_grants.denoise import regex_gate
from pipeline_grants.normalize import normalize_grantsgov

log = logging.getLogger("pipeline_grants.spike")


def top_topics(dense_scores: dict, limit: int = 5) -> list:
    items = [(tid, d.get("score", 0.0), d.get("rationale", "")) for tid, d in dense_scores.items()]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]


def render_markdown(scored: list) -> str:
    lines = ["| Title | Sponsor | Top topics (id:score) | Top rationale |",
             "|---|---|---|---|"]
    for opp, dense in scored:
        tops = top_topics(dense, limit=5)
        topic_str = ", ".join(f"{tid}:{score:.2f}" for tid, score, _ in tops) or "(none)"
        rationale = tops[0][2] if tops else ""
        title = (opp.title or "")[:80].replace("|", "/")
        lines.append(f"| {title} | {opp.sponsor} | {topic_str} | {rationale.replace('|', '/')} |")
    return "\n".join(lines)


def run(rows: int, keyword: str, out_path: str) -> str:
    from utils.bedrock_client import BedrockClient

    taxonomy = scoring.load_taxonomy()
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    bedrock = BedrockClient()

    data = grants_gov.search_opportunities(keyword=keyword, statuses="posted", rows=rows, start=0)
    hits = data.get("oppHits", [])
    scored = []
    for hit in hits:
        detail = grants_gov.fetch_opportunity(hit["id"])
        opp = normalize_grantsgov({"data": detail})
        kept, reason = regex_gate(opp)
        if not kept:
            log.info("skip %s: %s", opp.opportunity_id, reason)
            continue
        dense = scoring.score_grant_text(
            title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
            bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
        )
        scored.append((opp, dense))
        log.info("scored %s (%d topics)", opp.opportunity_id, len(dense))

    md = render_markdown(scored)
    with open(out_path, "w") as f:
        f.write(md + "\n")
    log.info("wrote %d scored opportunities to %s", len(scored), out_path)
    return md


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Phase 0 grant-scoring spike")
    p.add_argument("--rows", type=int, default=50)
    p.add_argument("--keyword", default="")
    p.add_argument("--out", default="spike_review.md")
    args = p.parse_args(argv)
    run(args.rows, args.keyword, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
