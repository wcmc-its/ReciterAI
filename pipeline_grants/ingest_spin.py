"""SPIN (InfoEd) ingest: targeted funder pull -> gate -> score -> judge -> persist.

Mirrors ``ingest_curated`` (the proven "second source" shape) for the third
source: for each resolved target funder in ``config/spin_target_funders.json``,
pull that sponsor's programs via ``[SOLR]spon_name:"<exact canonical name>"``,
normalize (``spin.normalize_spin``), dedup by ``opportunity_id``, apply the
exclusion list, run the **noise gates before any Bedrock spend**
(``spin.keep_opportunity`` — per-reason drop counts are logged and returned),
then score with the production topic scorer and persist via the same
``build_grant_item`` / ``put_grants`` / S3 artifact path — output is
indistinguishable downstream.

Two deliberate differences from the curated path (owner decisions, 2026-08):
  2. SPIN is *not* human-vetted, so the verdict is NOT synthesized: the
     grants_gov LLM judge (``judge_opportunity``) supplies ``is_research`` +
     ``appeal_by_stage`` (and its explicit ``is_biomedical_relevant=False``
     drops, same as the grants_gov path).
  4. Only the 113 resolved funders in ``target_funders`` are pulled; the
     config's ``_residual_for_human_pass`` block is deliberately ignored until
     the human pass resolves it.
  5. Manual CLI only — no scheduler/EventBridge until the SPIN ToS check on
     scheduled bulk pulls clears.

Run (writes to the ``reciterai`` table — use STAGING credentials first):
    python -m pipeline_grants.ingest_spin --dry-run
Add ``--funder helmsley`` to pull one funder, ``--limit 20`` for a bounded run.
"""
import argparse
import json
import logging
import os

from pipeline_grants import scoring, spin
from pipeline_grants.dedupe import load_corpus_key_index
from pipeline_grants.denoise import judge_opportunity
from pipeline_grants.exclusions import load_excluded_ids
from pipeline_grants.persist import build_grant_item, publish_opportunities_artifact, put_grants
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import get_dynamo_client
from utils.iso_clock import now_iso

log = logging.getLogger("pipeline_grants.ingest_spin")

_DEFAULT_TARGETS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "spin_target_funders.json")


def load_targets(path: str = _DEFAULT_TARGETS_PATH, *, funder: str = None) -> list:
    """The resolved target funders (``target_funders`` only) [decision #4].

    ``funder`` filters case-insensitively on a substring of either the funder
    or its canonical SPIN name (the ``--funder`` CLI flag).
    """
    with open(path) as f:
        data = json.load(f)
    targets = data.get("target_funders", [])
    if funder:
        needle = funder.lower()
        targets = [t for t in targets
                   if needle in (t.get("funder") or "").lower()
                   or needle in (t.get("spin_name") or "").lower()]
    return targets


def build_items(targets: list, *, client, bedrock, ingested_at=None, limit=None,
                corpus_index=None):
    """Pull + gate + score every target funder; return (items, artifact, counts). No writes.

    ``counts`` carries the run accounting: ``fetched``, per-reason
    ``gate_drops`` (noise gates, applied BEFORE scoring), ``unknown_type``
    (kept-but-unclassified project_type [decision #1]), ``dup`` / ``excluded``
    / ``crossdup`` skips, per-reason ``llm_drops``, and per-item ``failed``.
    """
    taxonomy = scoring.load_taxonomy()
    taxonomy_version = taxonomy.get("taxonomy_version", "taxonomy_v2")
    int_to_id, id_to_int = scoring.build_index(taxonomy)
    ingested_at = ingested_at or now_iso()
    excluded = load_excluded_ids()

    items, artifact = [], []
    seen = set()
    counts = {"fetched": 0, "unknown_type": 0, "dup": 0, "excluded": 0,
              "crossdup": 0, "failed": 0, "gate_drops": {}, "llm_drops": {}}

    def _capped() -> bool:
        return limit is not None and counts["fetched"] >= limit

    for target in targets:
        if _capped():
            break
        query = f'[SOLR]spon_name:"{target["spin_name"]}"'
        log.info("pulling %s (%s)", target.get("funder"), target["spin_name"])
        for row in client.search(query):
            if _capped():
                break
            counts["fetched"] += 1
            opp = spin.normalize_spin(row, ingested_at=ingested_at)
            # Collapse duplicate rows (synonym queries / re-listed programs)
            # BEFORE scoring so we never pay Bedrock twice.
            if opp.opportunity_id in seen:
                counts["dup"] += 1
                continue
            seen.add(opp.opportunity_id)
            # Held out of reverse-matching — skip-persist, no Bedrock spend.
            if opp.opportunity_id in excluded:
                counts["excluded"] += 1
                continue
            if corpus_index is not None:
                duplicate_of = corpus_index.blocking_id(
                    opportunity_id=opp.opportunity_id, source=opp.source,
                    title=opp.title, sponsor=opp.sponsor)
                if duplicate_of:
                    counts["crossdup"] += 1
                    log.info("cross-dup %s: key already held by %s", opp.opportunity_id, duplicate_of)
                    continue
            # Noise gates BEFORE scoring so we never pay Bedrock for junk.
            kept, reason = spin.keep_opportunity(row)
            if not kept:
                counts["gate_drops"][reason] = counts["gate_drops"].get(reason, 0) + 1
                log.info("gate-drop %s: %s", opp.opportunity_id, reason)
                continue
            if reason == "unknown_type":
                counts["unknown_type"] += 1
                log.info("unknown project_type kept %s: %r", opp.opportunity_id,
                         row.get("project_type"))
            # Isolate each opportunity: one transient Bedrock failure skips that
            # item, it does not abort the whole run (same posture as ingest).
            try:
                dense = scoring.score_grant_text(
                    title=opp.title, synopsis=opp.synopsis, opportunity_id=opp.opportunity_id,
                    bedrock=bedrock, taxonomy=taxonomy, int_to_id=int_to_id, id_to_int=id_to_int,
                )
                # LLM judge, not a synthesized verdict — SPIN is not human-vetted [decision #2].
                verdict = judge_opportunity(opp, bedrock)
                if not verdict["is_research"]:
                    counts["llm_drops"]["not_research"] = counts["llm_drops"].get("not_research", 0) + 1
                    log.info("llm-drop %s: %s", opp.opportunity_id, verdict["reason"])
                    continue
                if not verdict.get("is_biomedical_relevant", True):
                    counts["llm_drops"]["off_domain"] = counts["llm_drops"].get("off_domain", 0) + 1
                    log.info("offdomain-drop %s: %s", opp.opportunity_id, verdict["reason"])
                    continue
                item = build_grant_item(opp, dense, taxonomy_version=taxonomy_version, judge=verdict)
            except Exception as exc:  # noqa: BLE001 - skip-and-continue is the point
                counts["failed"] += 1
                log.warning("skip %s: %s", opp.opportunity_id, exc)
                continue
            items.append(item)
            if corpus_index is not None:
                corpus_index.add(opportunity_id=opp.opportunity_id, source=opp.source,
                                 title=opp.title, sponsor=opp.sponsor)
            artifact.append({
                "opportunity_id": opp.opportunity_id, "title": opp.title, "sponsor": opp.sponsor,
                "due_date": opp.due_date, "primary_topic_id": item["primary_topic_id"]["S"],
            })
    for reason, count in sorted(counts["gate_drops"].items()):
        log.info("gate drops — %s: %d", reason, count)
    if counts["unknown_type"]:
        log.info("kept %d row(s) with an unclassified project_type (unknown_type)",
                 counts["unknown_type"])
    return items, artifact, counts


def run(*, dry_run: bool = False, funder: str = None, limit: int = None) -> dict:
    targets = load_targets(funder=funder)
    if not targets:
        log.warning("no target funders matched %r — nothing to do", funder)
        return {"targets": 0, "built": 0, "persisted": 0}
    client = spin.SpinClient()
    # Short read timeout: same rationale as ingest — one hung Bedrock socket
    # must not stall this serial per-item loop for the default 900s.
    bedrock = BedrockClient(read_timeout=90)
    dynamo = get_dynamo_client()
    # Read-only corpus scan (also under --dry-run) feeding the cross-source dup guard.
    corpus_index = load_corpus_key_index(dynamo)
    items, artifact, counts = build_items(
        targets, client=client, bedrock=bedrock, limit=limit, corpus_index=corpus_index)
    if dry_run:
        sample = [a["opportunity_id"] for a in artifact[:5]]
        summary = {"targets": len(targets), "built": len(items), "persisted": 0,
                   "dry_run": True, "sample_ids": sample, **counts}
        log.info("spin ingest (dry-run): %s", summary)
        return summary
    persisted = put_grants(dynamo, items)
    manifest = publish_opportunities_artifact(artifact)
    summary = {"targets": len(targets), "built": len(items), "persisted": persisted,
               "artifact_version": manifest.get("version"), **counts}
    log.info("spin ingest summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="SPIN (InfoEd) targeted funder ingest")
    p.add_argument("--dry-run", action="store_true", help="build + report without writing to DynamoDB/S3")
    p.add_argument("--funder", default=None,
                   help="only pull target funders whose name or SPIN name contains this (case-insensitive)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap total programs processed (default: all; use for bounded/cost-limited runs)")
    args = p.parse_args(argv)
    run(dry_run=args.dry_run, funder=args.funder, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
