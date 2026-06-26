"""SPIN ingest: per target funder -> structured gate -> regex gate -> LLM judge ->
score -> persist GRANT# items. Mirrors ``ingest_curated`` but for the SPIN API source.

PRECISION over recall (deliberate): an opportunity must clear ALL of
``spin.keep_opportunity`` (structured project_type/geographic/status), ``regex_gate``
(title type + expired deadline), and ``judge_opportunity`` (LLM is_research) before it is
scored and written. Every drop is counted by reason and logged — no silent recall loss.

PROVENANCE / BACK-OUT: items are ``source="spin"`` -> ``PK="GRANT#spin:<id>"``. This run
writes ONLY to DynamoDB (the source of truth SPS consumes via etl:dynamodb). It does NOT
publish the S3 ``grants/latest`` artifact — that publisher REPLACES the whole artifact, so a
SPIN-only publish would clobber the grants_gov + curated opps. A full-corpus artifact rebuild
(scan all GRANT#) is a separate concern. To remove SPIN: delete ``GRANT#spin:*`` and re-etl.

Run (writes to the ``reciterai`` table — use STAGING credentials first):
    python -m pipeline_grants.ingest_spin --dry-run        # build + report, no writes
    python -m pipeline_grants.ingest_spin                  # write to DynamoDB
    python -m pipeline_grants.ingest_spin --limit-funders 5 --max-per-funder 10  # smoke
"""
import argparse
import json
import logging
import os
from collections import Counter

from pipeline_grants import scoring, spin
from pipeline_grants.denoise import judge_opportunity, regex_gate
from pipeline_grants.exclusions import load_excluded_ids
from pipeline_grants.persist import build_grant_item, put_grants
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client
from utils.iso_clock import now_iso

log = logging.getLogger("pipeline_grants.ingest_spin")

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "spin_target_funders.json")


def load_target_funders(path: str = _CONFIG_PATH) -> list:
    """Target funders resolved to canonical SPIN sponsor names (config/spin_target_funders.json)."""
    with open(path) as f:
        return json.load(f).get("target_funders", [])


def build_items(funders: list, *, bedrock, ingested_at: str = None, max_per_funder: int = None):
    """Pull -> gate -> judge -> score each funder's programs. Returns (items, artifact, drops)."""
    taxonomy = scoring.load_taxonomy()
    taxonomy_version = taxonomy.get("taxonomy_version", "taxonomy_v2")
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    ingested_at = ingested_at or now_iso()
    excluded = load_excluded_ids()

    items, artifact = [], []
    seen, drops = set(), Counter()
    for f in funders:
        name = f.get("spin_name")
        if not name:
            continue
        try:
            rows = spin.search_sponsor(name, max_results=max_per_funder)
        except Exception as exc:  # noqa: BLE001 - one bad funder must not abort the run
            log.warning("funder fetch failed %s: %s", name, exc)
            drops["fetch_failed"] += 1
            continue
        for row in rows:
            # Stage A — structured gate (cheap, before any Bedrock spend)
            keep, reason = spin.keep_opportunity(row)
            if not keep:
                drops[reason] += 1
                continue
            opp = spin.normalize_spin(row, ingested_at=ingested_at)
            if opp.opportunity_id in seen:
                drops["duplicate"] += 1
                continue
            seen.add(opp.opportunity_id)
            if opp.opportunity_id in excluded:
                drops["excluded"] += 1
                continue
            ok, rg = regex_gate(opp)
            if not ok:
                drops[f"regex:{rg}"] += 1
                continue
            # Stage B — LLM judge (per-item isolated; one failure skips just this item)
            try:
                verdict = judge_opportunity(opp, bedrock)
                if not verdict["is_research"]:
                    drops["llm:not-research"] += 1
                    continue
                dense = scoring.score_grant_text(
                    title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
                    bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
                )
                item = build_grant_item(opp, dense, taxonomy_version=taxonomy_version, judge=verdict)
            except Exception as exc:  # noqa: BLE001
                log.warning("skip %s: %s", opp.opportunity_id, exc)
                drops["score_failed"] += 1
                continue
            items.append(item)
            artifact.append({
                "opportunity_id": opp.opportunity_id, "title": opp.title, "sponsor": opp.sponsor,
                "due_date": opp.due_date, "primary_topic_id": item["primary_topic_id"]["S"],
            })
    log.info("spin gate drops by reason: %s", dict(drops.most_common()))
    return items, artifact, drops


def run(*, dry_run: bool = False, limit_funders: int = None, max_per_funder: int = None) -> dict:
    funders = load_target_funders()
    if limit_funders:
        funders = funders[:limit_funders]
    bedrock = BedrockClient(read_timeout=90)
    items, artifact, drops = build_items(funders, bedrock=bedrock, max_per_funder=max_per_funder)
    if dry_run:
        summary = {"funders": len(funders), "built": len(items), "persisted": 0, "dry_run": True,
                   "dropped": sum(drops.values()), "sample": [a["opportunity_id"] for a in artifact[:5]]}
        log.info("spin ingest (dry-run): %s", summary)
        return summary
    persisted = put_grants(get_dynamo_client(), items)
    summary = {"funders": len(funders), "built": len(items), "persisted": persisted,
               "dropped": sum(drops.values())}
    log.info("spin ingest summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="SPIN (InfoEd) funding-opportunity ingest")
    p.add_argument("--dry-run", action="store_true", help="build + report without writing to DynamoDB")
    p.add_argument("--limit-funders", type=int, default=None, help="cap number of target funders (smoke)")
    p.add_argument("--max-per-funder", type=int, default=None, help="cap programs pulled per funder (smoke)")
    args = p.parse_args(argv)
    run(dry_run=args.dry_run, limit_funders=args.limit_funders, max_per_funder=args.max_per_funder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
