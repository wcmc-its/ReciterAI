"""Rebuild the `entity_context.json` sidecar with sentence-complete snippets (#254).

#238/#239 sentence-aligned the per-(tool, pmid) `tool_context` snippets (via
`cli/rebuild_tool_context.py`). The newer `entity_context.json` (the #1166
specific-cell-line Surface-B facts) never got that pass, so its snippets still
render as mid-sentence fragments in SPS — e.g. *"they both dimerize in the plasma
membrane of HEK293 cells ..."* (starts lowercase, no terminal punctuation).

The durable fix needs **no second LLM pass**: `entity_context` is a pure
PROJECTION of `tool_context` (`pipeline_tools.entities.build_entity_layer` reads
`tool_context[eid]`). So once `tool_context` is sentence-aligned (already true on
the live artifact post-#239), simply **re-projecting** the entity layer over the
live, aligned `tool_context` regenerates clean entity snippets AND recomputes
`span`/`centrality_score`/`sentence_complete` for free. A naive in-place text swap
would leave STALE span offsets (SPS uses `span` for its `<mark>`); re-projection
avoids that by recomputing them.

Safety postures copied verbatim from `rebuild_tool_context.republish_sidecar`:
  - **Dry-run by default** — no S3 write unless `--publish`.
  - **Byte-freeze of the siblings** — the would-write `tools.json` / `families.json`
    / `faculty.json` / `tool_context.json` bytes are asserted sha-identical to the
    live manifest before any upload; ONLY `entities.json` / `entity_context.json`
    may change. (entities.json normally stays identical too — only its `evidenced`
    flags can shift if alignment turned a prior fragment into a usable sentence.)
  - Parent DESCRIPTORS are preserved from the live `entities.json` (their ids are
    content-derived and stable across runs) so the re-projection needs no LLM
    define-pass.

Pure core (`reproject_entity_context`, `entity_fragment_metrics`) is I/O-free and
unit-tested; `republish_entity_context` does the freeze-check + publish.
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from pipeline_tools.entities import apply_parent_descriptors, build_entity_layer, load_generic_terms

logger = logging.getLogger(__name__)

# Siblings that MUST be byte-identical to live — this rebuild only ever changes the
# entity objects. (entities.json is intentionally NOT frozen: alignment can flip an
# entity's `evidenced` flag, a legitimate content change, never a schema change.)
FROZEN_ARTIFACTS = ("tools.json", "families.json", "faculty.json", "tool_context.json")


# ---------------------------------------------------------------------------
# Pure logic (no I/O — unit-tested)
# ---------------------------------------------------------------------------


def _entity_sentences(entity_context: dict) -> dict[str, dict[str, str]]:
    """Flatten {eid:{pmid:[usage,...]}} -> {eid:{pmid: first usage_sentence}} for metrics."""
    out: dict[str, dict[str, str]] = {}
    for eid, by_pmid in (entity_context or {}).items():
        row = {
            str(pmid): usages[0]["usage_sentence"]
            for pmid, usages in by_pmid.items()
            if usages and usages[0].get("usage_sentence")
        }
        if row:
            out[eid] = row
    return out


def entity_fragment_metrics(entity_context: dict) -> dict:
    """Fragment proxy over entity snippets — reuses the #239 tool-grain metric."""
    from cli.rebuild_tool_context import fragment_metrics

    return fragment_metrics(_entity_sentences(entity_context))


def reproject_entity_context(
    tools: list[dict],
    families: list[dict],
    tool_context: dict,
    *,
    live_entities: list[dict] | None = None,
    generic_terms: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """Re-run build_entity_layer over the (aligned) tool_context; preserve descriptors.

    Returns ``(entities, entity_context)`` with clean snippets + recomputed
    span/centrality/sentence_complete, the #252 is_generic flag + 0-count
    suppression re-applied. Descriptors from ``live_entities`` are re-attached by
    their content-stable ``parent_entity_id`` (no LLM call).
    """
    entities, entity_context, _parents = build_entity_layer(
        tools, families, tool_context, generic_terms=generic_terms,
    )
    if live_entities:
        descriptors = {
            e["parent_entity_id"]: e["parent_descriptor"]
            for e in live_entities
            if e.get("parent_entity_id") and e.get("parent_descriptor")
        }
        if descriptors:
            apply_parent_descriptors(entities, descriptors)
    return entities, entity_context


# ---------------------------------------------------------------------------
# Republish (freeze-checked, dry-run by default)
# ---------------------------------------------------------------------------


def republish_entity_context(
    live_tools_path: str,
    live_tool_context_path: str,
    *,
    live_entities_path: str | None = None,
    live_entity_context_path: str | None = None,
    live_manifest_path: str | None = None,
    out_dir: str | None = None,
    publish: bool = False,
) -> dict:
    """Re-project entity_context from live aligned tool_context and republish it.

    The live ``tools.json`` bundle IS the publish payload minus the split-out
    sidecars (`publish._split_artifacts`); we re-attach the live `tool_context`
    and the freshly re-projected `entities`/`entity_context`, then re-emit the full
    set + manifest. Before any upload, the would-write `tools.json`/`families.json`/
    `faculty.json`/`tool_context.json` bytes are asserted sha-identical to the live
    manifest. Default is a dry run — no S3 write.
    """
    import hashlib
    from pipeline_tools.publish import _build_manifest, _split_artifacts, publish_artifacts, S3_PREFIX

    bundle = json.load(open(live_tools_path))
    # The live tools.json bundle never carries the split-out sidecars; drop any if present.
    for k in ("tool_context", "entities", "entity_context"):
        bundle.pop(k, None)
    tool_context = json.load(open(live_tool_context_path)).get("tool_context", {})
    live_entities = (
        json.load(open(live_entities_path)).get("entities", []) if live_entities_path else []
    )

    before = None
    if live_entity_context_path:
        before_ctx = json.load(open(live_entity_context_path)).get("entity_context", {})
        before = entity_fragment_metrics(before_ctx)
        logger.info("BEFORE (live entity_context): %s", before)

    entities, entity_context = reproject_entity_context(
        bundle["tools"], bundle["families"], tool_context,
        live_entities=live_entities, generic_terms=load_generic_terms(),
    )
    after = entity_fragment_metrics(entity_context)
    logger.info("AFTER  (re-projected)        : %s", after)

    payload = dict(bundle)
    payload["tool_context"] = tool_context
    payload["entities"] = entities
    payload["entity_context"] = entity_context

    items = _split_artifacts(payload, prefix=S3_PREFIX)
    shas = {
        it.key.rsplit("/", 1)[-1]: hashlib.sha256(it.body).hexdigest()
        for it in items
        if it.key.startswith(f"{S3_PREFIX}latest/")
    }
    if live_manifest_path:
        live = json.load(open(live_manifest_path)).get("objects", {})
        drift = [n for n in FROZEN_ARTIFACTS if n in live and live[n]["sha256"] != shas.get(n)]
        if drift:
            raise SystemExit(
                f"ABORT: {drift} would change — entity_context rebuild must touch ONLY "
                "entities.json / entity_context.json"
            )
        logger.info("safety check: tools/families/faculty/tool_context byte-identical to live ✓")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        json.dump(entity_context, open(os.path.join(out_dir, "entity_context.rebuilt.json"), "w"),
                  ensure_ascii=False, sort_keys=True, indent=0)
        json.dump(entities, open(os.path.join(out_dir, "entities.rebuilt.json"), "w"),
                  ensure_ascii=False, indent=0)

    manifest = _build_manifest(items, payload, prefix=S3_PREFIX)
    report = publish_artifacts(payload, dry_run=not publish)
    logger.info(
        "%s: %d objects; entity_context.json sha=%s; manifest counts.entities=%d counts.entity_context=%d",
        "PUBLISHED" if publish else "DRY-RUN (no upload)",
        len(report), shas["entity_context.json"][:12],
        manifest["counts"]["entities"], manifest["counts"]["entity_context"],
    )
    return {"report": report, "manifest": manifest, "before": before, "after": after, "published": publish}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild entity_context.json with sentence-complete snippets by re-projection (#254)."
    )
    ap.add_argument("--live-dir", help="dir holding tools.json / tool_context.json / entities.json / "
                    "entity_context.json / latest/manifest.json (used to default the paths below)")
    ap.add_argument("--live-tools", help="path to the live tools.json bundle")
    ap.add_argument("--live-tool-context", help="path to the live (sentence-aligned) tool_context.json")
    ap.add_argument("--live-entities", help="path to the live entities.json (preserves parent descriptors)")
    ap.add_argument("--live-entity-context", help="path to the live entity_context.json (before-metrics)")
    ap.add_argument("--live-manifest", help="path to the live latest/manifest.json (freeze safety check)")
    ap.add_argument("--out-dir", default="out/tools/a2", help="where to write the rebuilt artifacts for review")
    ap.add_argument("--publish", action="store_true", help="ACTUALLY upload to S3 (default: dry-run)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    def _d(name: str) -> str | None:
        return os.path.join(args.live_dir, name) if args.live_dir else None

    live_tools = args.live_tools or _d("tools.json")
    live_tool_context = args.live_tool_context or _d("tool_context.json")
    if not live_tools or not live_tool_context:
        ap.error("need --live-tools and --live-tool-context (or --live-dir with both)")

    republish_entity_context(
        live_tools,
        live_tool_context,
        live_entities_path=args.live_entities or _d("entities.json"),
        live_entity_context_path=args.live_entity_context or _d("entity_context.json"),
        live_manifest_path=args.live_manifest or _d("manifest.json"),
        out_dir=args.out_dir,
        publish=args.publish,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
