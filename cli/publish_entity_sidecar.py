#!/usr/bin/env python3
"""Entity-only sidecar publish for the #1166 Methods Surface B entity layer.

Publishes ONLY ``entities.json`` + ``entity_context.json`` + an updated v4
``manifest.json`` to ``s3://wcmc-reciterai-artifacts/tools/{,latest/}``. The live
``tools.json`` / ``families.json`` / ``faculty.json`` / ``tool_context.json`` are
left BYTE-FOR-BYTE untouched (the analog of #239's ``republish_sidecar``), so this
can NOT regress the live #239 sentence-aligned ``tool_context.json`` the way a full
``build_tool_taxonomy_corpus.py --publish`` would (corpus emits the pre-#239
fragment context, 3.61MB, vs the live rebuilt 4.74MB).

SPS picks this up because ``etl/tools/index.ts`` short-circuits on a COMPOSITE
signature over EVERY manifest object's sha — adding the two new entity object shas
changes that signature, so the next ``etl:scholar-tool`` run full-replaces
``family_entity`` / ``family_entity_usage`` while leaving the (unchanged) tools /
tool_context data alone.

Two steps:

  1. build (dry-run; ~40min cache-warm corpus re-run, no new extraction):
       python cli/publish_entity_sidecar.py
     -> writes out/tools/a2/_sidecar/{entities.json,entity_context.json}, prints the
        exact upload plan + safety asserts, uploads NOTHING.

  2. publish (fast; reuses the persisted bodies, re-fetches + re-asserts live manifest):
       python cli/publish_entity_sidecar.py --publish --reuse-bodies
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import boto3  # noqa: E402

from pipeline_tools.publish import (  # noqa: E402
    build_publish_payload,
    _split_artifacts,
    S3_PREFIX,
    LATEST_CACHE_CONTROL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("publish_entity_sidecar")

BUCKET = "wcmc-reciterai-artifacts"
REGION = "us-east-1"
ENTITY_NAMES = ("entities.json", "entity_context.json")
PRESERVE_NAMES = ("tools.json", "families.json", "faculty.json", "tool_context.json")
SIDECAR_DIR = REPO_ROOT / "out" / "tools" / "a2" / "_sidecar"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def gather_entity_bodies(reuse: bool, live_descriptors: dict | None = None) -> tuple[bytes, bytes, dict]:
    """Return the exact (entities.json, entity_context.json) bytes the full publisher
    would emit, plus {entities, entity_context} counts.

    With ``reuse`` and a persisted _sidecar dir present, read them back (no corpus
    re-run). Otherwise re-run the cache-warm corpus and persist for the publish step.

    The corpus re-run is done with ``define=False`` (no Bedrock parent-descriptor pass):
    this is a publish tool, not a build tool, and an un-timed Bedrock call in the define
    pass can wedge the entire publish on a stalled socket. Parent descriptors are
    render-only + content-keyed (stable ids), so ``live_descriptors`` (read from the live
    entities.json) is re-attached instead — mirroring cli/rebuild_entity_context.
    """
    ent_p = SIDECAR_DIR / "entities.json"
    ctx_p = SIDECAR_DIR / "entity_context.json"
    cnt_p = SIDECAR_DIR / "counts.json"
    if reuse and ent_p.exists() and ctx_p.exists() and cnt_p.exists():
        log.info("reusing persisted entity bodies from %s", SIDECAR_DIR)
        return ent_p.read_bytes(), ctx_p.read_bytes(), json.loads(cnt_p.read_text())

    # Re-run the cache-warm corpus exactly as cli/build_tool_taxonomy_corpus does.
    from cli.build_tool_taxonomy_corpus import (
        load_mentions, DEFAULT_INPUT, DEFAULT_REGISTRY_DIR, DEFAULT_OUT_DIR,
    )
    from pipeline_tools.corpus_run import run_corpus
    from pipeline_tools.salience import load_force_c_terms
    from pipeline_tools.embeddings import EmbeddingCache, titan_embed
    from pipeline_tools.classify import make_classifier_call_json
    from pipeline_tools.registry import FamilyRegistry, ToolRegistry

    mentions = load_mentions(DEFAULT_INPUT, None)
    call_json = make_classifier_call_json()
    cache = EmbeddingCache(embed=titan_embed)
    reg = DEFAULT_REGISTRY_DIR
    tool_registry = ToolRegistry.load(reg / "tool_registry.json", reg / "tool_denylist.json", cache=cache)
    family_registry = FamilyRegistry.load(reg / "family_registry.json", cache=cache)
    log.info("loaded seed registries: %d tools, %d families", len(tool_registry), len(family_registry))
    result = run_corpus(
        mentions, call_json=call_json,
        tool_registry=tool_registry, family_registry=family_registry,
        force_c_terms=load_force_c_terms(),
        batch_size=50, relabel_batch_size=40,
        relabel=True, define=False, apply_family_overrides=True,
        apply_consolidation=True, apply_adhoc_dedup=True,
        checkpoint_dir=DEFAULT_OUT_DIR / "_checkpoint",
    )
    # define=False above -> parent_descriptor is None on every entity; re-attach the
    # render-only descriptors from the live artifact (stable, content-keyed parent ids).
    if live_descriptors:
        from pipeline_tools.entities import apply_parent_descriptors
        apply_parent_descriptors(result.entities, live_descriptors)
        log.info("preserved %d live parent descriptor(s) (Bedrock define pass skipped)", len(live_descriptors))
    provenance = {
        "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": str(DEFAULT_INPUT),
        "raw_mentions": len(mentions),
        "limit": None,
        "registry_dir": str(reg),
        "note": "entity-only sidecar publish (#1166) — tools/families/faculty/tool_context preserved from live",
    }
    payload = build_publish_payload(result, provenance=provenance)
    items = _split_artifacts(payload, prefix=S3_PREFIX)
    bodies: dict[str, bytes] = {}
    for it in items:
        name = it.key.rsplit("/", 1)[-1]
        if name in ENTITY_NAMES and it.key.startswith(f"{S3_PREFIX}latest/"):
            bodies[name] = it.body
    counts = {
        "entities": len(payload.get("entities", []) or []),
        "entity_context": len(payload.get("entity_context", {}) or {}),
    }
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    ent_p.write_bytes(bodies["entities.json"])
    ctx_p.write_bytes(bodies["entity_context.json"])
    cnt_p.write_text(json.dumps(counts))
    log.info("persisted entity bodies to %s (entities=%d, entity_context=%d)",
             SIDECAR_DIR, counts["entities"], counts["entity_context"])
    return bodies["entities.json"], bodies["entity_context.json"], counts


def fetch_live_manifest(s3) -> dict:
    obj = s3.get_object(Bucket=BUCKET, Key=f"{S3_PREFIX}latest/manifest.json")
    return json.loads(obj["Body"].read())


def fetch_live_parent_descriptors(s3) -> dict:
    """``{parent_entity_id: descriptor}`` from the live entities.json so the sidecar can
    preserve render-only parent prose without re-running the Bedrock define pass.
    Best-effort: a missing/unreadable live entities.json -> empty map (descriptors stay None)."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"{S3_PREFIX}latest/entities.json")
        live_entities = json.loads(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001 — preservation is best-effort
        log.warning("could not read live entities.json for descriptor preservation (%s); descriptors stay None", exc)
        return {}
    return {
        e["parent_entity_id"]: e["parent_descriptor"]
        for e in live_entities
        if e.get("parent_entity_id") and e.get("parent_descriptor")
    }


def build_v4_manifest(entities_body: bytes, ctx_body: bytes, counts: dict, live: dict) -> dict:
    """Live manifest + the two new entity objects; tools/families/faculty/tool_context
    objects (and the top-level tools.json sha/bytes) preserved verbatim."""
    for name in PRESERVE_NAMES:
        if name not in live.get("objects", {}):
            raise SystemExit(f"ABORT: live manifest is missing preserved object {name!r}")
    objects = dict(live["objects"])  # preserve the 4 existing entries verbatim
    objects["entities.json"] = {
        "key": f"{S3_PREFIX}latest/entities.json", "bytes": len(entities_body), "sha256": _sha(entities_body),
    }
    objects["entity_context.json"] = {
        "key": f"{S3_PREFIX}latest/entity_context.json", "bytes": len(ctx_body), "sha256": _sha(ctx_body),
    }
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": "tools-a2-v4",
        "version": f"v{generated_at[:10]}",
        "generated_at": generated_at,
        "sha256": live["sha256"],            # tools.json — UNCHANGED
        "artifact_bytes": live["artifact_bytes"],  # tools.json — UNCHANGED
        "objects": objects,
        "counts": {**live.get("counts", {}), **counts},
    }


def assert_safe(upload_keys: list[str], manifest: dict, live: dict) -> None:
    allowed = {
        f"{S3_PREFIX}entities.json", f"{S3_PREFIX}latest/entities.json",
        f"{S3_PREFIX}entity_context.json", f"{S3_PREFIX}latest/entity_context.json",
        f"{S3_PREFIX}latest/manifest.json",
    }
    bad = [k for k in upload_keys if k not in allowed]
    if bad:
        raise SystemExit(f"ABORT: upload set contains non-allowed keys: {bad}")
    for k in upload_keys:
        leaf = k.rsplit("/", 1)[-1]
        if leaf in PRESERVE_NAMES:
            raise SystemExit(f"ABORT: refusing to write a preserved object: {k}")
    # The v4 manifest must carry the live objects byte-identically.
    for name in PRESERVE_NAMES:
        if manifest["objects"][name] != live["objects"][name]:
            raise SystemExit(f"ABORT: manifest object {name!r} diverges from live (would change a preserved artifact)")
    if manifest["sha256"] != live["sha256"] or manifest["artifact_bytes"] != live["artifact_bytes"]:
        raise SystemExit("ABORT: top-level tools.json sha/bytes diverge from live")
    log.info("SAFE: upload touches only entity sidecars + manifest; 4 live objects preserved byte-for-byte")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Entity-only sidecar publish (#1166).")
    ap.add_argument("--publish", action="store_true", help="upload to S3 (default: dry-run plan only)")
    ap.add_argument("--reuse-bodies", action="store_true",
                    help="reuse persisted out/tools/a2/_sidecar bodies (skip the corpus re-run)")
    args = ap.parse_args(argv)

    s3 = boto3.client("s3", region_name=REGION)
    live = fetch_live_manifest(s3)
    log.info("live manifest: schema=%s version=%s tools.json sha=%s…",
             live.get("schema_version"), live.get("version"), live.get("sha256", "")[:12])
    # Preserve render-only parent descriptors from live (only needed on a fresh build;
    # reused bodies already carry them). Keeps the corpus re-run Bedrock-define-free.
    live_descriptors = {} if args.reuse_bodies else fetch_live_parent_descriptors(s3)
    entities_body, ctx_body, counts = gather_entity_bodies(
        reuse=args.reuse_bodies, live_descriptors=live_descriptors,
    )
    manifest = build_v4_manifest(entities_body, ctx_body, counts, live)
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    uploads = [
        (f"{S3_PREFIX}entities.json", entities_body, None),
        (f"{S3_PREFIX}latest/entities.json", entities_body, LATEST_CACHE_CONTROL),
        (f"{S3_PREFIX}entity_context.json", ctx_body, None),
        (f"{S3_PREFIX}latest/entity_context.json", ctx_body, LATEST_CACHE_CONTROL),
        (f"{S3_PREFIX}latest/manifest.json", manifest_bytes, LATEST_CACHE_CONTROL),  # manifest LAST
    ]
    assert_safe([k for k, _, _ in uploads], manifest, live)

    print("\n=== entity-only sidecar publish plan ===")
    print(f"  schema: {live.get('schema_version')} -> {manifest['schema_version']}   counts: "
          f"entities={counts['entities']} entity_context={counts['entity_context']}")
    print(f"  preserved (NOT written): {', '.join(PRESERVE_NAMES)}  (tools.json sha {live['sha256'][:12]}… unchanged)")
    for k, b, cc in uploads:
        print(f"    {'UPLOAD' if args.publish else 'would-upload'} s3://{BUCKET}/{k}  ({len(b):,} B  sha {_sha(b)[:12]}…  cc={cc})")

    if not args.publish:
        print("\n  DRY-RUN — re-run with --publish --reuse-bodies to upload.")
        return 0

    for k, b, cc in uploads:
        kw = dict(Bucket=BUCKET, Key=k, Body=b, ContentType="application/json")
        if cc:
            kw["CacheControl"] = cc
        s3.put_object(**kw)
        log.info("UPLOADED s3://%s/%s (%d B)", BUCKET, k, len(b))
    print(f"\n  PUBLISHED {len(uploads)} object(s). Manifest now schema_version=tools-a2-v4.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
