"""Grants.gov ingest orchestrator: fetch -> normalize -> denoise -> score -> persist.

Run: python -m pipeline_grants.ingest --rows 200 --keyword ""
"""
import argparse
import logging
from itertools import islice

from pipeline_grants import grants_gov, scoring
from pipeline_grants.dedupe import load_corpus_key_index
from pipeline_grants.denoise import judge_opportunity, regex_gate
from pipeline_grants.exclusions import load_excluded_ids
from pipeline_grants.match_compile import compile_match as _compile_match, load_vocab_or_disable
from pipeline_grants.normalize import normalize_grantsgov
from pipeline_grants.persist import build_grant_item, publish_opportunities_artifact, put_grants
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client
from utils.event_records import load_thresholds

log = logging.getLogger("pipeline_grants.ingest")

# Off-domain force-fit guard: `persist.build_grant_item` picks primary_topic_id = argmax, so an
# off-domain grant always lands on SOME biomedical topic. Drop it when even its best topic cannot
# clear the same relevance bar the scorer screens topics on (config/thresholds.json score_floor) —
# i.e. nothing really fit. Pairs with the is_biomedical_relevant judge gate. See #293.
_PRIMARY_TOPIC_FLOOR = load_thresholds()["score_floor"]


def run(rows: int, keyword: str, *, flush_every: int = 25, compile_match: bool = False,
        limit: int | None = None) -> dict:
    taxonomy = scoring.load_taxonomy()
    taxonomy_version = taxonomy.get("taxonomy_version", "taxonomy_v2")
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    # Short read timeout: the default 900s lets one hung Bedrock socket read stall this
    # serial loop for up to ~45min (3 retry attempts). 90s is ~18x a normal call; a truly
    # hung item times out fast and is skipped by the per-item guard below.
    bedrock = BedrockClient(read_timeout=90)
    dynamo = get_dynamo_client()
    # Cross-source duplicate guard: normalized-key index over the persisted corpus so
    # an opportunity already held by an equal-or-higher-priority source is skipped
    # before any Bedrock spend (see pipeline_grants/dedupe.py).
    corpus_index = load_corpus_key_index(dynamo)
    # Off by default: compiling the matcher DSL+query is 2 extra Sonnet calls/grant for data
    # nothing reads until the SPS consumer ships. An empty/failed vocab load leaves vocab=[],
    # which disables compilation for the run (no abort, no wasted Bedrock call).
    vocab = load_vocab_or_disable(log) if compile_match else []

    # Paginate to hitCount (was one page of `rows`) and include forecasted NOFOs. `rows` is
    # now the page size; `limit` caps total opportunities fetched (None = all) so a run's
    # per-item Bedrock cost stays boundable now that a full sweep is ~2-3k opportunities.
    hits = list(islice(grants_gov.search_all_opportunities(keyword=keyword, rows=rows), limit))
    excluded = load_excluded_ids()
    pending, artifact = [], []
    kept = failed = persisted = no_synopsis = 0
    for hit in hits:
        # Isolate each opportunity: one transient Bedrock/network failure skips that
        # item, it does not abort the whole run (a 1000-row run hits the occasional
        # non-JSON Bedrock reply, which otherwise raised and lost every prior item).
        try:
            opp = normalize_grantsgov({"data": grants_gov.fetch_opportunity(hit["id"])})
            if opp.opportunity_id in excluded:
                log.info("excluded %s (held out of matching)", opp.opportunity_id)
                continue
            duplicate_of = corpus_index.blocking_id(
                opportunity_id=opp.opportunity_id, source=opp.source,
                title=opp.title, sponsor=opp.sponsor)
            if duplicate_of:
                log.info("cross-dup %s: key already held by %s", opp.opportunity_id, duplicate_of)
                continue
            ok, reason = regex_gate(opp)
            if not ok:
                log.info("regex-drop %s: %s", opp.opportunity_id, reason)
                continue
            # An empty body cannot be screened: the model answers "I don't see the publication
            # content" and json.loads dies at char 0, so three Bedrock calls buy a failure
            # indistinguishable from a network blip. Counted, not silent — a rise here means a
            # source changed shape again, the way forecasts did in #269.
            # ponytail: strictly empty, no char floor. A thin-but-real body still gets screened
            # and the existing topic-score floor drops it if it says nothing.
            if not opp.synopsis.strip():
                no_synopsis += 1
                log.info("no-synopsis-drop %s: empty body", opp.opportunity_id)
                continue
            verdict = judge_opportunity(opp, bedrock)
            if not verdict["is_research"]:
                log.info("llm-drop %s: %s", opp.opportunity_id, verdict["reason"])
                continue
            if not verdict.get("is_biomedical_relevant", True):
                log.info("offdomain-drop %s: %s", opp.opportunity_id, verdict["reason"])
                continue
            dense = scoring.score_grant_text(
                title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
                bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
            )
            top_score = max((d.get("score", 0.0) for d in dense.values()), default=0.0)
            if top_score < _PRIMARY_TOPIC_FLOOR:
                log.info("floor-drop %s: top topic score %.3f < %.2f",
                         opp.opportunity_id, top_score, _PRIMARY_TOPIC_FLOOR)
                continue
            m_dsl, m_query = _compile_match(opp.title, opp.synopsis, vocab, bedrock=bedrock) if vocab else (None, None)
            item = build_grant_item(opp, dense, taxonomy_version=taxonomy_version, judge=verdict,
                                    match_dsl=m_dsl, match_query=m_query)
        except Exception as exc:  # noqa: BLE001 - skip-and-continue is the point
            failed += 1
            log.warning("skip %s: %s", hit.get("id"), exc)
            continue
        kept += 1
        pending.append(item)
        corpus_index.add(opportunity_id=opp.opportunity_id, source=opp.source,
                         title=opp.title, sponsor=opp.sponsor)
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
               "no_synopsis": no_synopsis, "artifact_version": manifest.get("version")}
    log.info("ingest summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Grants.gov opportunity ingest")
    p.add_argument("--rows", type=int, default=200, help="page size (the ingest paginates to hitCount)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap total opportunities fetched (default: all; use for bounded/cost-limited runs)")
    p.add_argument("--keyword", default="")
    p.add_argument("--compile-match", action="store_true",
                   help="compile + cache the grant->researcher matcher DSL+query on each GRANT# "
                        "(2 extra Sonnet calls/grant; off by default until the SPS consumer ships)")
    args = p.parse_args(argv)
    run(args.rows, args.keyword, compile_match=args.compile_match, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
