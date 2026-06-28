"""Grants.gov ingest orchestrator: fetch -> normalize -> denoise -> score -> persist.

Run: python -m pipeline_grants.ingest --rows 200 --keyword ""
"""
import argparse
import logging

from pipeline_grants import grants_gov, scoring
from pipeline_grants.denoise import judge_opportunity, regex_gate
from pipeline_grants.exclusions import load_excluded_ids
from pipeline_grants.match_compile import compile_match as _compile_match, load_vocab_or_disable
from pipeline_grants.normalize import normalize_grantsgov
from pipeline_grants.persist import build_grant_item, publish_opportunities_artifact, put_grants
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client

log = logging.getLogger("pipeline_grants.ingest")


def run(rows: int, keyword: str, *, flush_every: int = 25, compile_match: bool = False) -> dict:
    taxonomy = scoring.load_taxonomy()
    taxonomy_version = taxonomy.get("taxonomy_version", "taxonomy_v2")
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    # Short read timeout: the default 900s lets one hung Bedrock socket read stall this
    # serial loop for up to ~45min (3 retry attempts). 90s is ~18x a normal call; a truly
    # hung item times out fast and is skipped by the per-item guard below.
    bedrock = BedrockClient(read_timeout=90)
    dynamo = get_dynamo_client()
    # Off by default: compiling the matcher DSL+query is 2 extra Sonnet calls/grant for data
    # nothing reads until the SPS consumer ships. An empty/failed vocab load leaves vocab=[],
    # which disables compilation for the run (no abort, no wasted Bedrock call).
    vocab = load_vocab_or_disable(log) if compile_match else []

    data = grants_gov.search_opportunities(keyword=keyword, statuses="posted", rows=rows, start=0)
    hits = data.get("oppHits", [])
    excluded = load_excluded_ids()
    pending, artifact = [], []
    kept = failed = persisted = 0
    for hit in hits:
        # Isolate each opportunity: one transient Bedrock/network failure skips that
        # item, it does not abort the whole run (a 1000-row run hits the occasional
        # non-JSON Bedrock reply, which otherwise raised and lost every prior item).
        try:
            opp = normalize_grantsgov({"data": grants_gov.fetch_opportunity(hit["id"])})
            if opp.opportunity_id in excluded:
                log.info("excluded %s (held out of matching)", opp.opportunity_id)
                continue
            ok, reason = regex_gate(opp)
            if not ok:
                log.info("regex-drop %s: %s", opp.opportunity_id, reason)
                continue
            verdict = judge_opportunity(opp, bedrock)
            if not verdict["is_research"]:
                log.info("llm-drop %s: %s", opp.opportunity_id, verdict["reason"])
                continue
            dense = scoring.score_grant_text(
                title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
                bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
            )
            m_dsl, m_query = _compile_match(opp.title, opp.synopsis, vocab, bedrock=bedrock) if vocab else (None, None)
            item = build_grant_item(opp, dense, taxonomy_version=taxonomy_version, judge=verdict,
                                    match_dsl=m_dsl, match_query=m_query)
        except Exception as exc:  # noqa: BLE001 - skip-and-continue is the point
            failed += 1
            log.warning("skip %s: %s", hit.get("id"), exc)
            continue
        kept += 1
        pending.append(item)
        artifact.append({
            "opportunity_id": opp.opportunity_id, "title": opp.title, "sponsor": opp.sponsor,
            "due_date": opp.due_date, "primary_topic_id": item["primary_topic_id"]["S"],
        })
        # ponytail: flush in batches so a late crash on a long run keeps the items already
        # scored (DDB is the source of truth). Drop to a single end-of-loop put_grants if
        # batch churn ever matters.
        if len(pending) >= flush_every:
            persisted += put_grants(dynamo, pending)
            pending = []

    persisted += put_grants(dynamo, pending)
    manifest = publish_opportunities_artifact(artifact)
    summary = {"fetched": len(hits), "kept": kept, "failed": failed, "persisted": persisted,
               "artifact_version": manifest.get("version")}
    log.info("ingest summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Grants.gov opportunity ingest")
    p.add_argument("--rows", type=int, default=200)
    p.add_argument("--keyword", default="")
    p.add_argument("--compile-match", action="store_true",
                   help="compile + cache the grant->researcher matcher DSL+query on each GRANT# "
                        "(2 extra Sonnet calls/grant; off by default until the SPS consumer ships)")
    args = p.parse_args(argv)
    run(args.rows, args.keyword, compile_match=args.compile_match)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
