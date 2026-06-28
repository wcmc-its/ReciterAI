"""Curated WCM awards ingest: enriched CSV -> score -> persist GRANT# items.

Mirrors ``ingest`` but for the human-curated awards source. Two deliberate
differences from the Grants.gov path:

  * **No denoise gates.** ``regex_gate`` would drop these on "prize"/"medal" and
    ``judge_opportunity`` is prompted to mark prizes ``is_research=false``. The
    list is human-vetted, so we synthesize the verdict (``is_research=True``).
  * **Curated career stage.** ``appeal_by_stage`` comes from the curated
    ``Career Stage`` column (``wcm_curated.appeal_for_row``), not the LLM judge.

Still runs the production topic scorer (``score_grant_text``) so topic_ids share
the publication taxonomy, and writes via the same ``build_grant_item`` /
``put_grants`` / S3 artifact path — output is indistinguishable downstream.

Run (writes to the ``reciterai`` table — use STAGING credentials first):
    python -m pipeline_grants.ingest_curated --csv pipeline_grants/data/wcm_curated_opportunities_enriched.csv
Add ``--dry-run`` to build + report without writing to DynamoDB/S3.
"""
import argparse
import logging

from pipeline_grants import scoring, wcm_curated
from pipeline_grants.exclusions import load_excluded_ids
from pipeline_grants.match_compile import compile_match as _compile_match, load_vocab_or_disable
from pipeline_grants.persist import build_grant_item, publish_opportunities_artifact, put_grants
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client
from utils.iso_clock import now_iso

log = logging.getLogger("pipeline_grants.ingest_curated")


def build_items(csv_path: str, *, bedrock, ingested_at=None, compile_match: bool = False):
    """Read the enriched CSV and return (dynamodb_items, artifact_rows). No writes."""
    taxonomy = scoring.load_taxonomy()
    taxonomy_version = taxonomy.get("taxonomy_version", "taxonomy_v2")
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    ingested_at = ingested_at or now_iso()
    vocab = load_vocab_or_disable(log) if compile_match else []   # [] => compilation disabled for the run

    rows = wcm_curated.read_curated_csv(csv_path)
    excluded = load_excluded_ids()
    items, artifact = [], []
    seen, dup, skipped = set(), 0, 0
    for row in rows:
        synopsis = (row.get("synopsis") or "").strip() or wcm_curated.fallback_synopsis(row)
        opp = wcm_curated.make_curated_opportunity(row, synopsis=synopsis, ingested_at=ingested_at)
        # Collapse genuine duplicate rows (same name+sponsor) BEFORE scoring so we
        # neither pay Bedrock twice nor silently overwrite on write.
        if opp.opportunity_id in seen:
            dup += 1
            continue
        seen.add(opp.opportunity_id)
        # Held out of reverse-matching (non-topical awards) — skip-persist, no Bedrock spend.
        if opp.opportunity_id in excluded:
            skipped += 1
            continue
        dense = scoring.score_grant_text(
            title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
            bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
        )
        # Synthesized verdict — curation IS the vetting; stage from the curated column.
        verdict = {
            "is_research": True,
            "reason": "wcm_curated",
            "appeal_by_stage": wcm_curated.appeal_for_row(row),
        }
        m_dsl, m_query = _compile_match(opp.title, opp.synopsis, vocab, bedrock=bedrock) if vocab else (None, None)
        item = build_grant_item(opp, dense, taxonomy_version=taxonomy_version, judge=verdict,
                                match_dsl=m_dsl, match_query=m_query)
        items.append(item)
        artifact.append({
            "opportunity_id": opp.opportunity_id, "title": opp.title, "sponsor": opp.sponsor,
            "due_date": opp.due_date, "primary_topic_id": item["primary_topic_id"]["S"],
        })
    if dup:
        log.info("dropped %d duplicate curated row(s) (same name+sponsor)", dup)
    if skipped:
        log.info("held out %d excluded curated award(s) (see config/excluded_opportunities.json)", skipped)
    return items, artifact


def run(csv_path: str, *, dry_run: bool = False, compile_match: bool = False) -> dict:
    bedrock = BedrockClient()
    items, artifact = build_items(csv_path, bedrock=bedrock, compile_match=compile_match)
    if dry_run:
        sample = [a["opportunity_id"] for a in artifact[:5]]
        summary = {"built": len(items), "persisted": 0, "dry_run": True, "sample_ids": sample}
        log.info("curated ingest (dry-run): %s", summary)
        return summary
    dynamo = get_dynamo_client()
    persisted = put_grants(dynamo, items)
    manifest = publish_opportunities_artifact(artifact)
    summary = {"built": len(items), "persisted": persisted, "artifact_version": manifest.get("version")}
    log.info("curated ingest summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="WCM-curated awards ingest")
    p.add_argument("--csv", default="pipeline_grants/data/wcm_curated_opportunities_enriched.csv")
    p.add_argument("--dry-run", action="store_true", help="build + report without writing to DynamoDB/S3")
    p.add_argument("--compile-match", action="store_true",
                   help="compile + cache the grant->researcher matcher DSL+query on each GRANT# "
                        "(2 extra Sonnet calls/grant; off by default until the SPS consumer ships)")
    args = p.parse_args(argv)
    run(args.csv, dry_run=args.dry_run, compile_match=args.compile_match)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
