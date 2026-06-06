"""
A1 — seed canonicalization for the Axis 2 tools/methods taxonomy.

Reads raw research-tool names (the committed POC seed `tools_to_canonicalize.json`
by default, or the `reciterai_tools` RDS table with --source db) and canonicalizes
them via AWS Bedrock Sonnet into the `TOOL#` schema (docs/tools-producer-model.md
§#8): distinct canonical entries with kind, functional_category, a curated
supercategory, a provisional salience tier (S/A/B/C), merged aliases, and provenance.

Two passes mirror cli/generate_taxonomy.py:
  Pass 1: batch-canonicalize raw names (partial-failure tolerant).
  Pass 2: consolidate cross-batch duplicate canonicals + finalize fields.
Then deterministic post-processing: suppress-list re-application, pub_count
rollup from the seed, RRID-candidate flagging, schema validation.

Output: tool_taxonomy_v1.json — written for HUMAN REVIEW before it feeds any
downstream stage (D-07 gate). Salience tiers here are LLM-provisional and get
recalibrated against cross-corpus frequency after A2.

Usage:
    python -m pipeline_tools.canonicalize [--source json|db] [--input PATH]
        [--output PATH] [--batch-size N] [--limit N]

Cost: A1 over the 230-name seed is ~6 Sonnet calls (bounded). The corpus-wide
A2 extraction is the expensive fan-out and MUST go through cost_guard (Policy 5);
A1 does not, because it runs over a bounded seed list.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path (mirrors cli/generate_taxonomy.py).
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.bedrock_client import BedrockClient, SONNET_MODEL, MODEL_IDS_BY_STAGE  # noqa: E402
from prompts.tool_canonicalize import (  # noqa: E402
    CANONICALIZE_SYSTEM_PROMPT,
    CONSOLIDATE_SYSTEM_PROMPT,
    BUILD_CANONICALIZE_USER_MESSAGE,
    BUILD_CONSOLIDATE_USER_MESSAGE,
    KINDS,
    SUPERCATEGORIES,
    RRID_CANDIDATE_KINDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_INPUT = REPO_ROOT / "tools_to_canonicalize.json"
DEFAULT_OUTPUT = REPO_ROOT / "tool_taxonomy_v1.json"
SUPPRESS_PATH = REPO_ROOT / "config" / "tool_suppress_list.json"

DEFAULT_BATCH_SIZE = 50
# Max entries per consolidation LLM call. Above this, consolidation buckets by
# kind and chunks within a bucket so each call's JSON stays under the token cap
# (a single call over ~177 entries truncates → invalid JSON).
CONSOLIDATE_SINGLE_MAX = 80

VALID_TIERS = {"S", "A", "B", "C"}
VALID_KINDS = set(KINDS)
VALID_SUPERCATEGORIES = {s["id"] for s in SUPERCATEGORIES}
STAGE_MODEL = MODEL_IDS_BY_STAGE.get("tool_canonicalize", SONNET_MODEL)


# ---------------------------------------------------------------------------
# Pure helpers (no AWS, no file I/O) — unit-tested in tests/test_tool_canonicalize.py
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Lowercase alphanumeric + underscore slug for canonical_tool_id."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unknown"


def norm_name(name: str) -> str:
    """Normalize a raw/alias name for matching (case/space/punct-insensitive)."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def build_raw_lookup(raw_tools: list[dict]) -> dict[str, dict]:
    """Map normalized raw_name -> raw record, for alias matching + pub_count rollup."""
    return {norm_name(t.get("raw_name", "")): t for t in raw_tools if t.get("raw_name")}


def matches_suppress(text: str, suppress_terms: list[str]) -> bool:
    """True if `text` contains any suppress term as a whole word (case-insensitive)."""
    hay = f" {norm_name(text)} "
    for term in suppress_terms:
        needle = norm_name(term)
        if needle and f" {needle} " in hay:
            return True
    return False


def normalize_entry(entry: dict, raw_lookup: dict[str, dict]) -> dict | None:
    """
    Validate + shape one LLM entry into the #8 TOOL# schema. Returns None if the
    entry is unusable (no id / no display_name / no aliases).

    pub_count is rolled up from the seed pub_counts of the aliases that resolve
    against raw_lookup (approximate by design — the seed pub_count is not the
    salience denominator; cross-corpus frequency replaces it after A2).
    """
    raw_aliases = entry.get("aliases") or []
    aliases = sorted({a.strip() for a in raw_aliases if isinstance(a, str) and a.strip()})
    display_name = (entry.get("display_name") or "").strip()
    cid = slugify(entry.get("canonical_tool_id") or display_name)
    # Idempotent: normalize_entry runs in Pass 1 AND on consolidation output, so
    # strip an already-applied prefix instead of doubling it.
    if cid.startswith("wcm_tool_"):
        cid = cid[len("wcm_tool_"):]
    if not display_name or not aliases or cid in ("", "unknown"):
        return None

    kind = entry.get("kind") if entry.get("kind") in VALID_KINDS else "method"
    tier = entry.get("salience_tier") if entry.get("salience_tier") in VALID_TIERS else "B"
    supercat = entry.get("supercategory") if entry.get("supercategory") in VALID_SUPERCATEGORIES else "other"

    matched, pub_count = [], 0
    for a in aliases:
        rec = raw_lookup.get(norm_name(a))
        if rec is not None:
            matched.append(norm_name(a))
            pub_count += int(rec.get("pub_count") or 0)

    try:
        conf = float(entry.get("source_confidence"))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    return {
        "canonical_tool_id": f"wcm_tool_{cid}",
        "display_name": display_name,
        "kind": kind,
        "functional_category": slugify(entry.get("functional_category") or "uncategorized"),
        "supercategory": supercat,
        "salience_tier": tier,
        "salience_tier_basis": "llm_provisional",
        "aliases": aliases,
        "parent_tool_ids": [],  # families discovered globally later (#7 / step E)
        "method_family_hint": (entry.get("method_family_hint") or "").strip(),
        "source": "wcm_curated",  # #5: RRID resolution is a separate later task
        "source_uri": None,
        "source_confidence": round(conf, 3),
        "rrid_candidate": kind in RRID_CANDIDATE_KINDS,
        "pub_count": pub_count,
        "description": (entry.get("description") or "").strip(),
        "_matched_alias_keys": matched,  # internal QC; stripped before write
    }


def merge_by_id(entries: list[dict], raw_lookup: dict[str, dict]) -> list[dict]:
    """
    Merge entries sharing a canonical_tool_id: union aliases, recompute pub_count
    from the unioned aliases (avoids double-counting), keep best confidence, prefer
    a non-empty description / family hint. Deterministic.
    """
    by_id: dict[str, dict] = {}
    for e in entries:
        cid = e["canonical_tool_id"]
        if cid not in by_id:
            by_id[cid] = dict(e)
            continue
        cur = by_id[cid]
        cur["aliases"] = sorted(set(cur["aliases"]) | set(e["aliases"]))
        cur["source_confidence"] = max(cur["source_confidence"], e["source_confidence"])
        # Prefer the more "signature" tier (S < A < B < C in priority for display).
        order = {"S": 0, "A": 1, "B": 2, "C": 3}
        cur["salience_tier"] = min(cur["salience_tier"], e["salience_tier"], key=lambda t: order[t])
        cur["description"] = cur["description"] or e["description"]
        cur["method_family_hint"] = cur["method_family_hint"] or e["method_family_hint"]
        cur["rrid_candidate"] = cur["rrid_candidate"] or e["rrid_candidate"]

    for cur in by_id.values():
        matched, pub_count = [], 0
        for a in cur["aliases"]:
            rec = raw_lookup.get(norm_name(a))
            if rec is not None:
                matched.append(norm_name(a))
                pub_count += int(rec.get("pub_count") or 0)
        cur["pub_count"] = pub_count
        cur["_matched_alias_keys"] = matched
    return list(by_id.values())


def apply_suppress(entries: list[dict], suppress_terms: list[str]) -> list[dict]:
    """Force tier C (deterministically) on any entry whose DISPLAY_NAME matches the
    suppress list — belt-and-suspenders over the LLM's own judgement. Matches the
    display_name only, NOT aliases: verbose alias variants ("...microscopy",
    "magnetic resonance imaging...") would false-positive and suppress distinctive
    instruments (MRI, cryo-EM). Generic bare words live in the LLM prompt, not here."""
    for e in entries:
        if matches_suppress(e["display_name"], suppress_terms) and e["salience_tier"] != "C":
            e["salience_tier"] = "C"
            e["salience_tier_basis"] = "suppress_list"
    return entries


def coverage_stats(entries: list[dict], raw_lookup: dict[str, dict]) -> dict:
    """QC for the review gate: how many raw names landed in a canonical entry."""
    matched_keys: set[str] = set()
    for e in entries:
        matched_keys.update(e.get("_matched_alias_keys", []))
    all_keys = set(raw_lookup.keys())
    unmatched = sorted(all_keys - matched_keys)
    return {
        "raw_total": len(all_keys),
        "raw_matched": len(all_keys) - len(unmatched),
        "raw_unmatched": len(unmatched),
        "coverage_pct": round((len(all_keys) - len(unmatched)) / len(all_keys), 3) if all_keys else 0.0,
        "unmatched_sample": [raw_lookup[k].get("raw_name", k) for k in unmatched[:25]],
    }


def distribution(entries: list[dict], field: str) -> dict:
    out: dict[str, int] = {}
    for e in entries:
        out[e.get(field, "?")] = out.get(e.get(field, "?"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def finalize(entries: list[dict]) -> list[dict]:
    """Strip internal QC fields and sort for a stable, reviewable artifact."""
    order = {"S": 0, "A": 1, "B": 2, "C": 3}
    clean = []
    for e in sorted(entries, key=lambda x: (order[x["salience_tier"]], -x["pub_count"], x["canonical_tool_id"])):
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        clean.append(e)
    return clean


# ---------------------------------------------------------------------------
# I/O + LLM
# ---------------------------------------------------------------------------

def load_suppress_terms(path: Path = SUPPRESS_PATH) -> list[str]:
    if not path.exists():
        logger.warning("Suppress list not found at %s; proceeding with none.", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("suppress_terms", []))


def load_raw_tools(source: str, input_path: Path, limit: int | None) -> list[dict]:
    """Load raw tool records {raw_name, tool_category, pub_count} from JSON or RDS."""
    if source == "json":
        data = json.loads(input_path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("tools", [])
        logger.info("Loaded %d raw tool records from %s", len(rows), input_path)
    elif source == "db":
        # Full 5,153-name run: requires VPN + DB_* env. Aggregates per raw_name.
        from sqlalchemy import text
        from utils.db import get_engine
        from utils import sql_queries

        logger.info("Querying reciterai_tools (RDS) for distinct raw tool names...")
        agg: dict[str, dict] = {}
        with get_engine().connect() as conn:
            for r in conn.execute(text(sql_queries.TOOL_EXTRACTION_SQL)).mappings():
                key = (r["tool_name"] or "").strip()
                if not key:
                    continue
                rec = agg.setdefault(key, {"raw_name": key, "tool_category": r["tool_category"], "pub_count": 0})
                rec["pub_count"] += 1  # one row per (pmid, tool) → pub_count = distinct pubs
        rows = list(agg.values())
        logger.info("Aggregated %d distinct raw tool names from reciterai_tools", len(rows))
    else:
        raise ValueError(f"Unknown --source {source!r} (expected 'json' or 'db')")

    rows = [r for r in rows if (r.get("raw_name") or "").strip()]
    if limit:
        rows = sorted(rows, key=lambda r: -(r.get("pub_count") or 0))[:limit]
        logger.info("Limited to top %d raw names by pub_count", limit)
    return rows


def canonicalize_batch(client: BedrockClient, batch: list[dict], suppress_terms: list[str]) -> list[dict]:
    """One Sonnet canonicalization call for a batch of raw names."""
    resp = client.call_json(
        model=STAGE_MODEL,
        system=CANONICALIZE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": BUILD_CANONICALIZE_USER_MESSAGE(batch, suppress_terms)}],
        max_tokens=8192,
    )
    return resp.get("tools", []) if isinstance(resp, dict) else []


def consolidate(client: BedrockClient, entries: list[dict], raw_lookup: dict[str, dict]) -> list[dict]:
    """
    Cross-batch consolidation. Buckets by kind (near-dups are almost always
    same-kind) and chunks within a bucket so each LLM call's JSON stays under the
    token cap. Per-chunk resilient (a failed chunk keeps its pre-consolidation
    entries). Always finished by a deterministic merge_by_id safety net. Chunk
    splits are logged — no silent caps.
    """
    def _one_call(items: list[dict]) -> list[dict]:
        resp = client.call_json(
            model=STAGE_MODEL,
            system=CONSOLIDATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": BUILD_CONSOLIDATE_USER_MESSAGE(items)}],
            max_tokens=16384,
        )
        shaped = [normalize_entry(e, raw_lookup) for e in (resp.get("tools", []) if isinstance(resp, dict) else [])]
        return [e for e in shaped if e]

    def _chunked(items: list[dict], label: str) -> list[dict]:
        n = CONSOLIDATE_SINGLE_MAX
        chunks = [items[i:i + n] for i in range(0, len(items), n)]
        if len(chunks) > 1:
            logger.warning(
                "%s: %d entries in %d chunks (>%d) — cross-chunk dups within this group may survive "
                "the LLM pass (deterministic merge_by_id still applies).", label, len(items), len(chunks), n
            )
        out: list[dict] = []
        for ci, chunk in enumerate(chunks, 1):
            try:
                out.extend(_one_call(chunk))
            except Exception as e:  # noqa: BLE001 — keep this chunk's entries un-consolidated
                logger.error("%s chunk %d/%d consolidation failed (%s); keeping pre-consolidation entries.",
                             label, ci, len(chunks), e)
                out.extend(chunk)
        return out

    if len(entries) <= CONSOLIDATE_SINGLE_MAX:
        consolidated = _chunked(entries, "all")
    else:
        logger.info("Consolidating %d entries by kind bucket (>%d).", len(entries), CONSOLIDATE_SINGLE_MAX)
        buckets: dict[str, list[dict]] = {}
        for e in entries:
            buckets.setdefault(e["kind"], []).append(e)
        consolidated = []
        for kind in sorted(buckets):
            items = sorted(buckets[kind], key=lambda x: x["canonical_tool_id"])
            consolidated.extend(_chunked(items, f"kind={kind}"))

    return merge_by_id(consolidated, raw_lookup)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(source: str, input_path: Path, output_path: Path, batch_size: int, limit: int | None) -> dict:
    raw_tools = load_raw_tools(source, input_path, limit)
    if not raw_tools:
        logger.error("No raw tool names loaded — nothing to canonicalize.")
        sys.exit(1)
    raw_lookup = build_raw_lookup(raw_tools)
    suppress_terms = load_suppress_terms()

    client = BedrockClient()

    # Pass 1 — batch canonicalization (partial-failure tolerant).
    chunks = [raw_tools[i:i + batch_size] for i in range(0, len(raw_tools), batch_size)]
    logger.info("Pass 1: canonicalizing %d raw names in %d batches of %d", len(raw_tools), len(chunks), batch_size)
    shaped: list[dict] = []
    for i, chunk in enumerate(chunks, 1):
        try:
            raw_entries = canonicalize_batch(client, chunk, suppress_terms)
            for e in raw_entries:
                ne = normalize_entry(e, raw_lookup)
                if ne:
                    shaped.append(ne)
            logger.info("  batch %d/%d: %d raw -> %d entries (running %d)", i, len(chunks), len(chunk), len(raw_entries), len(shaped))
        except Exception as e:  # noqa: BLE001 — continue; partial results feed consolidation
            logger.error("  batch %d/%d FAILED: %s", i, len(chunks), e)

    if not shaped:
        logger.error("Pass 1 produced no entries — check Bedrock connectivity.")
        sys.exit(1)

    merged = merge_by_id(shaped, raw_lookup)
    logger.info("Pass 1 complete: %d entries pre-consolidation (%d after intra-pass merge)", len(shaped), len(merged))

    # Pass 2 — consolidate cross-batch duplicates.
    logger.info("Pass 2: consolidating %d entries", len(merged))
    consolidated = consolidate(client, merged, raw_lookup)
    consolidated = apply_suppress(consolidated, suppress_terms)
    logger.info("Pass 2 complete: %d canonical entries", len(consolidated))

    cov = coverage_stats(consolidated, raw_lookup)
    tools = finalize(consolidated)
    taxonomy = {
        "taxonomy_version": "tool_taxonomy_v1",
        "tools": tools,
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "input": str(input_path) if source == "json" else "reciterai_tools (RDS)",
            "model": STAGE_MODEL,
            "batch_size": batch_size,
            "raw_count": len(raw_tools),
            "canonical_count": len(tools),
            "coverage": cov,
            "tier_distribution": distribution(tools, "salience_tier"),
            "kind_distribution": distribution(tools, "kind"),
            "supercategory_distribution": distribution(tools, "supercategory"),
            "rrid_candidates": sum(1 for t in tools if t["rrid_candidate"]),
        },
    }
    output_path.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False), encoding="utf-8")
    return taxonomy


def main():
    parser = argparse.ArgumentParser(description="A1 seed canonicalization for the Axis 2 tools taxonomy")
    parser.add_argument("--source", choices=["json", "db"], default="json",
                        help="json = committed seed (default); db = full reciterai_tools (RDS, needs VPN)")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input JSON path (--source json)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output taxonomy path")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None, help="Cap raw names (top-N by pub_count) for a cheap test run")
    args = parser.parse_args()

    taxonomy = run(args.source, args.input, args.output, args.batch_size, args.limit)
    meta = taxonomy["_meta"]

    print(f"\n{'='*64}\nTOOL TAXONOMY v1 — SUMMARY\n{'='*64}")
    print(f"Raw names in:      {meta['raw_count']}")
    print(f"Canonical out:     {meta['canonical_count']}")
    print(f"Alias coverage:    {meta['coverage']['coverage_pct']*100:.0f}% "
          f"({meta['coverage']['raw_matched']}/{meta['coverage']['raw_total']} raw names mapped)")
    print(f"Tiers:             {meta['tier_distribution']}")
    print(f"Kinds:             {meta['kind_distribution']}")
    print(f"Supercategories:   {meta['supercategory_distribution']}")
    print(f"RRID candidates:   {meta['rrid_candidates']}")
    if meta['coverage']['raw_unmatched']:
        print(f"Unmatched (sample): {meta['coverage']['unmatched_sample']}")
    print(
        "\n*** REVIEW REQUIRED (D-07) ***\n"
        f"Review {DEFAULT_OUTPUT.name} before it feeds A2 / family discovery.\n"
        "Salience tiers are LLM-provisional — recalibrated against cross-corpus frequency after A2.\n"
        "This run used the 230-name committed seed; the full 5,153-name run needs --source db (VPN)."
    )


if __name__ == "__main__":
    main()
