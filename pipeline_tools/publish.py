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

PUBLISH_SCHEMA_VERSION = "tools-a2-v1"
S3_PREFIX = "tools/"
# Latest/manifest gets a short cache so SPS picks up a republish quickly; the
# immutable versioned copies (if any) can be cached long. Mirrors the hierarchy
# publisher's CacheControl posture.
LATEST_CACHE_CONTROL = "max-age=60, must-revalidate"


def _family_record(fam: dict) -> dict:
    return {
        "family_id": fam["family_id"],
        "label": fam.get("label"),
        "supercategory": fam.get("supercategory"),
        "dominant_kind": fam.get("dominant_kind"),
        "status": fam.get("status"),
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


def _split_artifacts(payload: dict, *, prefix: str) -> list[PublishItem]:
    """The objects to publish: the consolidated tools.json + split faculty/families.

    SPS reads ``tools.json`` for the global Methods lens; the faculty rollup and
    family registry are also split out so a per-profile or per-family surface can
    fetch just what it needs without the whole artifact.

    Each object is emitted under BOTH a flat key (``tools/<name>`` — the existing
    contract, kept for transition) and a ``tools/latest/<name>`` key, from the
    SAME compact bytes. The ``latest/`` prefix + ``latest/manifest.json`` (added
    in :func:`publish_artifacts`) mirror the spotlight/hierarchy publishers, so a
    consumer can poll one manifest, short-circuit on its sha256, and verify each
    fetched object byte-for-byte.
    """
    def _bytes(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    tools_body = _bytes(payload)
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

    items: list[PublishItem] = []
    for name, body in (("tools.json", tools_body),
                       ("families.json", families_body),
                       ("faculty.json", faculty_body)):
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
        "sha256": hashlib.sha256(tools_it.body).hexdigest(),
        "artifact_bytes": tools_it.size,
        "objects": objects,
        "counts": {
            "tools": len(payload["tools"]),
            "families": len(payload["families"]),
            "faculty": len(payload["faculty"]),
        },
    }


def publish_artifacts(
    payload: dict,
    *,
    s3_client=None,
    prefix: str = S3_PREFIX,
    dry_run: bool = True,
) -> list[dict]:
    """Publish (or dry-run) the tools artifact set to S3. Returns a per-object report.

    ``dry_run=True`` (default) performs NO upload — it returns the keys + byte
    sizes that WOULD be written, for the pre-publish review. ``dry_run=False``
    uploads each object via the injected/constructed ``S3HierarchyClient``
    (ARTIFACTS_BUCKET). The S3 client is built lazily only on a real publish, so
    a dry-run never touches AWS.
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
    for it, r in zip(items, report):
        s3_client.put_object(it.key, it.body, content_type="application/json", cache_control=it.cache_control)
        r["uploaded"] = True
    logger.info("published %d tools artifact object(s) to s3://wcmc-reciterai-artifacts/%s", len(items), prefix)
    return report
