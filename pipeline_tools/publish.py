"""Step G — assemble + publish the A2 tools taxonomy artifact (tools.json).

The corpus run (``pipeline_tools.corpus_run``) produces the registries + §9
records + faculty rollup in memory; this module shapes them into the single
SPS-facing artifact and uploads it to ``s3://wcmc-reciterai-artifacts/tools/``
(the same bucket the topic hierarchy + spotlight already publish to). The
Scholars Profile System reads this artifact to render the Methods lens (families,
Lead/Senior-scoped, beside the MeSH Subjects lens), the per-family pages, and the
``/tools`` browser.

Two deliberate safety postures, both per the handoff + repo conventions:

  - **Dry-run by default.** ``publish_artifacts(dry_run=True)`` validates and
    reports the keys + byte sizes it WOULD write, but performs no S3 PutObject —
    the D-07 gate: the artifact is reviewed before anything downstream consumes
    it. The operator opts in to the real upload explicitly (``--publish``).
  - **The legacy-DynamoDB supersede is intentionally NOT in this module.** The
    stale ``TOOL#`` / ``TOOL_INDEX#`` items (the paused-chatbot extraction) must
    be retired once A2 ships, but that is a DESTRUCTIVE op against ~14.7k items
    whose replacement DDB schema is not pinned by the classifier spec. It is
    isolated to its own reviewed step rather than bundled into the publish path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

# v4 over v3 (all additive — v3 consumers ignore the new objects / fields):
#   - two NEW sidecar objects for the Methods Surface-B specific-entity layer (#1166):
#     `entities.json` (the per-family specific-entity DIMENSION — normalized id,
#     parent nesting + descriptor, usage_count) and `entity_context.json` (the
#     per-(publication x entity) FACTS — usage sentence, matched_span, centrality).
#     Both are split OUT of the tools.json bundle like tool_context.json.
# v3 over v2 / v2 over v1 (all additive):
#   - faculty rollup tool/family rows carry `pmids` (the distinct set `pub_count`
#     counts; len(pmids) == pub_count) — #175.
#   - families carry a `display` tier ∈ {feature, standard, suppressed}.
#   - the family set is the 820 consolidation (within-supercategory merges + relabels).
PUBLISH_SCHEMA_VERSION = "tools-a2-v4"
S3_PREFIX = "tools/"
# #193 sidecar provenance stamp — names WHAT the snippets are (per-publication
# tool-usage context), so a consumer never mistakes them for a definitional gloss
# or the (separate) context_evidence list. Mirrors membership.json's membership_kind.
TOOL_CONTEXT_KIND = "tool_usage_snippet"
# #1166 entity-facts provenance stamp — names WHAT the per-(pub x entity) snippets
# are, distinct from the per-tool tool_context. Mirrors TOOL_CONTEXT_KIND.
ENTITY_CONTEXT_KIND = "entity_usage_snippet"
# #253 per-(tool, pmid) usage SIGNAL sidecar — informativeness_score + mention_class
# for each tool_context snippet. Kept OUT of tool_context.json so that file's value
# stays a flat string (the overview generator / scholar-tool-mapper / #239 rebuild
# freeze-check all depend on the string shape); SPS joins this map by (cid, pmid).
TOOL_CONTEXT_META_KIND = "tool_usage_signal"
# Latest/manifest gets a short cache so SPS picks up a republish quickly; the
# immutable versioned copies (if any) can be cached long. Mirrors the hierarchy
# publisher's CacheControl posture.
LATEST_CACHE_CONTROL = "max-age=60, must-revalidate"
# Shrink guard: refuse to overwrite live latest/ when the new run lost more than
# this fraction of its tools vs the prior manifest. Same default as the spotlight
# publisher's shrink guard.
PUBLISH_SHRINK_MAX_FRACTION = 0.34


class PublishShrinkGuardError(RuntimeError):
    """Raised when a real publish would shrink the live tools artifact past the guard."""


def _family_record(fam: dict) -> dict:
    return {
        "family_id": fam["family_id"],
        "label": fam.get("label"),
        "supercategory": fam.get("supercategory"),
        "dominant_kind": fam.get("dominant_kind"),
        "status": fam.get("status"),
        # Consolidation display tier ∈ {feature, standard, suppressed} (null if the
        # consolidation batch was not applied). Drives the SPS Methods-lens prominence.
        "display": fam.get("display"),
        # Render-only, capability-framed definition (#879). Null until the define pass
        # generates it; existing consumers ignore the new field, only an updated SPS
        # renders it. `definition_source` lets SPS attach an AI-generated disclaimer.
        "definition": fam.get("definition"),
        "definition_source": "generated" if fam.get("definition") else None,
        "member_tool_ids": list(fam.get("member_tool_ids", [])),
        "exemplar_tool_ids": list(fam.get("exemplar_tool_ids", [])),
    }


def build_publish_payload(result, *, provenance: dict | None = None) -> dict:
    """Assemble the SPS-facing ``tools.json`` payload from a ``CorpusResult``.

    Carries the canonical-tool records (§9a), the family registry, the compact
    review hierarchy (§9c), the per-faculty rollup (Step F), the separate grant
    signal, the calibrated salience thresholds, and run telemetry — plus a
    ``provenance`` block (corpus sizes, run timestamp, code sha) the caller
    supplies. Pure: no I/O, fully serializable.
    """
    th = result.thresholds
    payload = {
        "schema_version": PUBLISH_SCHEMA_VERSION,
        "provenance": provenance or {},
        "salience_thresholds": {
            "s_spread_min": getattr(th, "s_spread_min", None),
            "a_pub_floor": getattr(th, "a_pub_floor", None),
            "a_spread_floor": getattr(th, "a_spread_floor", None),
            "percentile": getattr(th, "percentile", None),
        },
        "tools": result.records,
        "families": [_family_record(f) for f in result.family_registry.records()],
        "hierarchy": result.hierarchy,
        "faculty": result.faculty_rollup,
        "grant_signal": result.grant_signal,
        "telemetry": result.telemetry,
        "exceptions_summary": result.telemetry.get("exceptions_by_type", {}),
        # #193 per-publication usage context (cid -> {pmid: snippet}). Carried on the
        # payload so it reaches the publisher + local review, but split OUT of the
        # SPS-facing tools.json bundle by ``_split_artifacts`` into its own sidecar.
        "tool_context": getattr(result, "tool_context", {}) or {},
        # #1166 specific-entity layer (Methods Surface B). The DIMENSION (one record
        # per specific cell line, with parent nesting + usage_count) and the per-(pub
        # x entity) FACTS (usage sentence + matched_span + centrality). Both are split
        # out of the tools.json bundle into their own sidecars. Empty until the entity
        # stage runs (pre-#1166 runs publish a v4 manifest with empty entity objects).
        "entities": getattr(result, "entities", []) or [],
        "entity_context": getattr(result, "entity_context", {}) or {},
    }
    return payload


@dataclass(frozen=True)
class PublishItem:
    key: str
    body: bytes
    cache_control: str | None = None

    @property
    def size(self) -> int:
        return len(self.body)


def _build_tool_context_meta(tool_context: dict, tools: list[dict]) -> dict:
    """Derive the #253 per-(tool, pmid) usage signal from the tool_context snippets.

    ``{canonical_tool_id: {pmid: {informativeness_score, mention_class}}}``. Forms are
    the tool's display name + aliases (so the score's centrality term resolves the same
    span SPS marks). Pure + deterministic, computed HERE so every publish path — the
    forward corpus run and the #239 / #254 backfills, which all call this function —
    emits a meta map consistent with the tool_context it ships. Never mutates the flat
    ``tool_context.json`` string values (the overview generator depends on that shape).
    """
    from pipeline_tools.context_quality import (
        _INFORM_USAGE_THRESHOLD,
        informativeness_score,
        salient_name_forms,
    )

    forms_by_cid: dict[str, list[str]] = {
        cid: salient_name_forms(t.get("display_name") or "", list(t.get("aliases") or []))
        for t in tools
        if (cid := t.get("canonical_tool_id"))
    }
    out: dict[str, dict] = {}
    for cid, by_pmid in (tool_context or {}).items():
        forms = forms_by_cid.get(cid, [])
        row = {}
        for pmid, snippet in (by_pmid or {}).items():
            if not snippet:
                continue
            score = informativeness_score(snippet, forms)
            row[str(pmid)] = {
                "informativeness_score": score,
                "mention_class": "usage" if score >= _INFORM_USAGE_THRESHOLD else "mention",
            }
        if row:
            out[cid] = row
    return out


def _split_artifacts(payload: dict, *, prefix: str) -> list[PublishItem]:
    """The objects to publish: the consolidated tools.json + split faculty/families/context.

    SPS reads ``tools.json`` for the global Methods lens; the faculty rollup and
    family registry are also split out so a per-profile or per-family surface can
    fetch just what it needs without the whole artifact. The ``tool_context.json``
    sidecar (#193) carries the per-publication usage snippets the AI overview
    generator joins onto each scholar's pmids — kept out of the bundle so only that
    generator pays for it.

    Each object is emitted under BOTH a flat key (``tools/<name>`` — the existing
    contract, kept for transition) and a ``tools/latest/<name>`` key, from the
    SAME compact bytes. The ``latest/`` prefix + ``latest/manifest.json`` (added
    in :func:`publish_artifacts`) mirror the spotlight/hierarchy publishers, so a
    consumer can poll one manifest, short-circuit on its sha256, and verify each
    fetched object byte-for-byte.
    """
    def _bytes(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # The per-publication context map (#193) ships as its own sidecar — keep it OUT
    # of the tools.json bundle (the Methods lens loads that; a {pmid: snippet} map
    # over ~18k tools would bloat it). The #1166 entity objects are likewise split
    # out (Surface B loads them per-family). The bundle is byte-identical to v3 when
    # the entity objects are absent/empty.
    _SPLIT_OUT = ("tool_context", "entities", "entity_context")
    tool_context = payload.get("tool_context", {})
    entities = payload.get("entities", []) or []
    entity_context = payload.get("entity_context", {}) or {}
    tool_context_meta = _build_tool_context_meta(tool_context, payload.get("tools", []))
    tools_body = _bytes({k: v for k, v in payload.items() if k not in _SPLIT_OUT})
    families_body = _bytes({
        "schema_version": payload["schema_version"],
        "provenance": payload["provenance"],
        "families": payload["families"],
        "hierarchy": payload["hierarchy"],
    })
    faculty_body = _bytes({
        "schema_version": payload["schema_version"],
        "provenance": payload["provenance"],
        "faculty": payload["faculty"],
    })
    # sort_keys=True makes the sidecar byte-stable across content-identical reruns
    # (recursively sorts tool ids AND the inner pmid keys) — the membership.json
    # discipline (#192). Loaded only by the overview generator, not by SPS.
    tool_context_body = json.dumps(
        {
            "schema_version": payload["schema_version"],
            "provenance": payload["provenance"],
            "tool_context_kind": TOOL_CONTEXT_KIND,
            "tool_context": tool_context,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    # #253 per-(tool, pmid) usage signal sidecar (informativeness_score + mention_class).
    # sort_keys for byte-stability, exactly like tool_context.json. Empty {} when the
    # payload has no tool_context (the file is still emitted, byte-stable).
    tool_context_meta_body = json.dumps(
        {
            "schema_version": payload["schema_version"],
            "provenance": payload["provenance"],
            "tool_context_meta_kind": TOOL_CONTEXT_META_KIND,
            "tool_context_meta": tool_context_meta,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    # #1166 entity DIMENSION (entities.json) — the entity list is already sorted
    # deterministically by build_entity_layer; emit compactly (a stable list does
    # not need sort_keys). FACTS (entity_context.json) use sort_keys so the nested
    # {entity_id: {pmid: [...]}} map is byte-stable across content-identical reruns
    # (the tool_context.json / membership.json discipline).
    entities_body = _bytes({
        "schema_version": payload["schema_version"],
        "provenance": payload["provenance"],
        "entities": entities,
    })
    entity_context_body = json.dumps(
        {
            "schema_version": payload["schema_version"],
            "provenance": payload["provenance"],
            "entity_context_kind": ENTITY_CONTEXT_KIND,
            "entity_context": entity_context,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")

    items: list[PublishItem] = []
    for name, body in (("tools.json", tools_body),
                       ("families.json", families_body),
                       ("faculty.json", faculty_body),
                       ("tool_context.json", tool_context_body),
                       ("tool_context_meta.json", tool_context_meta_body),
                       ("entities.json", entities_body),
                       ("entity_context.json", entity_context_body)):
        # Flat keys: preserve the existing posture exactly — tools.json carried
        # LATEST_CACHE_CONTROL, families/faculty carried none. Do not change the
        # flat contract while it is still the published surface.
        flat_cc = LATEST_CACHE_CONTROL if name == "tools.json" else None
        items.append(PublishItem(f"{prefix}{name}", body, flat_cc))
        # latest/ copies: identical bytes; all get the short cache so SPS picks up
        # a republish quickly (parity with spotlight/hierarchy's latest/ posture).
        items.append(PublishItem(f"{prefix}latest/{name}", body, LATEST_CACHE_CONTROL))
    return items


def _build_manifest(items: list[PublishItem], payload: dict, *, prefix: str) -> dict:
    """The small freshness/integrity anchor a consumer polls before fetching.

    sha256/bytes are computed over the EXACT compact bytes uploaded under
    ``latest/`` (never a re-serialization), so a consumer's post-fetch integrity
    check matches byte-for-byte. Top-level ``sha256``/``artifact_bytes`` point at
    ``tools.json`` (the superset bundle) so a single-artifact manifest reader
    works unchanged; ``objects`` extends it for the split files. No
    ``taxonomy_version`` is emitted — the tools payload carries only
    ``schema_version`` (a fake value would be worse than its absence).
    """
    by_name = {
        it.key.rsplit("/", 1)[-1]: it
        for it in items
        if it.key.startswith(f"{prefix}latest/")
    }
    tools_it = by_name["tools.json"]
    objects = {
        name: {"key": it.key, "bytes": it.size, "sha256": hashlib.sha256(it.body).hexdigest()}
        for name, it in by_name.items()
    }
    generated_at = now_iso()
    return {
        "schema_version": PUBLISH_SCHEMA_VERSION,
        "version": f"v{generated_at[:10]}",  # YYYY-MM-DD, from the single clock read
        "generated_at": generated_at,
        # §4.4 content identity: the artifact's REAL corpus run time (from the
        # producing run's provenance), distinct from `version`/`generated_at`,
        # which are publish-wall-clock and advance even on a zero-content
        # republish. Lets SPS surface the true "data as of" moment instead of
        # the publish date. None if the caller supplied no provenance.
        "run_at_utc": (payload.get("provenance") or {}).get("run_at_utc"),
        "sha256": hashlib.sha256(tools_it.body).hexdigest(),
        "artifact_bytes": tools_it.size,
        "objects": objects,
        "counts": {
            "tools": len(payload["tools"]),
            "families": len(payload["families"]),
            "faculty": len(payload["faculty"]),
            "tool_context": len(payload.get("tool_context", {})),
            # #253 sibling signal map — one entry per tool that has any snippet
            # (same cid cardinality as tool_context); integrity rides `objects`.
            "tool_context_meta": len(payload.get("tool_context", {})),
            "entities": len(payload.get("entities", []) or []),
            "entity_context": len(payload.get("entity_context", {}) or {}),
        },
    }


def _prior_published_tool_count(s3_client, *, prefix: str) -> int | None:
    """Best-effort tool count from the live ``latest/manifest.json``.

    Returns None on any error (no prior, S3 hiccup, parse failure) so a missing or
    corrupt prior never blocks a legitimate publish — the guard fails open.
    """
    try:
        key = f"{prefix}latest/manifest.json"
        if not s3_client.key_exists(key):
            return None
        prior = json.loads(s3_client.get_object_bytes(key))
        return prior.get("counts", {}).get("tools")
    except Exception:
        return None


def publish_artifacts(
    payload: dict,
    *,
    s3_client=None,
    prefix: str = S3_PREFIX,
    dry_run: bool = True,
    force: bool = False,
) -> list[dict]:
    """Publish (or dry-run) the tools artifact set to S3. Returns a per-object report.

    ``dry_run=True`` (default) performs NO upload — it returns the keys + byte
    sizes that WOULD be written, for the pre-publish review. ``dry_run=False``
    uploads each object via the injected/constructed ``S3HierarchyClient``
    (ARTIFACTS_BUCKET). The S3 client is built lazily only on a real publish, so
    a dry-run never touches AWS.

    A real publish is gated by a shrink guard (raises ``PublishShrinkGuardError``
    before any PutObject when the tool count dropped past ``PUBLISH_SHRINK_MAX_FRACTION``
    vs the live manifest — the symptom of a degraded corpus run). ``force=True``
    overrides it; the guard fails open on a missing/corrupt prior.
    """
    items = _split_artifacts(payload, prefix=prefix)
    manifest = _build_manifest(items, payload, prefix=prefix)
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    items.append(PublishItem(f"{prefix}latest/manifest.json", manifest_bytes, LATEST_CACHE_CONTROL))
    report = [{"key": it.key, "bytes": it.size, "uploaded": False} for it in items]
    if dry_run:
        for r in report:
            logger.info("DRY-RUN would upload s3://wcmc-reciterai-artifacts/%s (%d bytes)", r["key"], r["bytes"])
        return report

    if s3_client is None:
        from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient
        s3_client = S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
    if not force:
        prev_tools = _prior_published_tool_count(s3_client, prefix=prefix)
        new_tools = len(payload["tools"])
        if prev_tools and new_tools < prev_tools * (1.0 - PUBLISH_SHRINK_MAX_FRACTION):
            raise PublishShrinkGuardError(
                f"tools artifact shrank from {prev_tools} to {new_tools} tools "
                f"(>{PUBLISH_SHRINK_MAX_FRACTION:.0%} drop) — refusing to overwrite "
                f"live latest/. Re-run with force=True to override."
            )
    for it, r in zip(items, report):
        s3_client.put_object(it.key, it.body, content_type="application/json", cache_control=it.cache_control)
        r["uploaded"] = True
    logger.info("published %d tools artifact object(s) to s3://wcmc-reciterai-artifacts/%s", len(items), prefix)
    return report
