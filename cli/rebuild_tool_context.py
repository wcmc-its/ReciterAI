"""Rebuild the `tool_context.json` sidecar with sentence-aligned usage snippets (#238).

The live sidecar's per-(canonical_tool, pmid) `context` snippets were emitted by
the extractor as "verbatim-ish" fragments — ~43% begin mid-clause. SPS surfaces
each one standalone ("How X is used") AND grounds its bio generator on it with no
abstract fallback (SPS #1119 / #879 D-19), so a broken fragment is both a bad
display and bad grounding. PR #239 fixes this at *extraction* time, but only for
the next cold-run; this script repairs the **live artifact now**.

It is a CONTEXT-ONLY rebuild: it regenerates only the snippet text, keyed by the
existing `(canonical_tool_id, pmid)` pairs in `tool_registry.json`. The tool set,
canonicalization, families, salience, and faculty rollup are untouched — only the
`context_by_pub` strings change, so the blast radius is the one sidecar.

For each publication it makes one Haiku call: given the abstract + the tools that
have a snippet for that pmid, return each tool's COMPLETE enclosing sentence,
copied verbatim. Every returned sentence is checked to be an actual (normalized)
substring of the abstract — a hard **verbatim guard** that keeps the field
"extracted, not generated" (the SPS contract). A tool the model can't place, or a
sentence that fails the guard, **keeps its existing snippet** — the rebuild only
ever upgrades a fragment to a full sentence, never drops coverage or invents text.

The LLM call is behind an injected ``call_llm(system, user) -> result`` seam, so
the pure logic (grouping, guard, apply, sidecar build, metrics) unit-tests with a
stub and never touches AWS.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from pipeline_tools.cost_guard import CostCeiling, CostCeilingExceeded
from pipeline_tools.context_quality import accept_snippet

logger = logging.getLogger(__name__)

CallLLM = Callable[[str, str], "object"]

REBUILD_SYSTEM_PROMPT = """You align research-tool usage snippets to complete sentences. \
Given ONE publication's ABSTRACT and a numbered list of tools/methods it used, return, for EACH tool, \
the SINGLE COMPLETE sentence from the abstract in which that tool is used — copied VERBATIM and IN FULL: \
the entire sentence, from its first word to its terminal punctuation, so it reads correctly on its own.

RULES:
- Copy EXACTLY from the abstract. Never paraphrase, summarise, or invent — the returned text must appear \
verbatim in the abstract.
- The sentence MUST mention or clearly refer to that specific tool. Pick the sentence where the tool is \
actually named/used, not a nearby sentence about something else.
- Return ONE WHOLE sentence. Do NOT shorten, truncate, or clip it; do NOT begin mid-clause; do NOT merge \
two sentences (if the abstract runs two together, return only the one that names the tool).
- If a tool is NOT mentioned in the abstract, return null for it (do not guess).

Respond with VALID JSON ONLY, keyed by the tool's number: {"1": "<verbatim sentence or null>", "2": "..."}.
"""


def build_user_message(abstract: str, names: list[str]) -> str:
    lines = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    return f"ABSTRACT:\n{abstract.strip()}\n\nTOOLS:\n{lines}"


# ---------------------------------------------------------------------------
# Pure logic (no I/O — unit-tested)
# ---------------------------------------------------------------------------


def group_by_pmid(records: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """{pmid: [(canonical_tool_id, display_name), ...]} over every snippet pair."""
    by_pmid: dict[str, list[tuple[str, str]]] = {}
    for r in records:
        cbp = r.get("context_by_pub") or {}
        if not cbp:
            continue
        cid = r["canonical_tool_id"]
        name = r.get("display_name") or cid
        for pmid in cbp:
            by_pmid.setdefault(str(pmid), []).append((cid, name))
    # Stable order (by cid) so a run is reproducible and prompts are deterministic.
    for pmid in by_pmid:
        by_pmid[pmid].sort()
    return by_pmid


def apply_regen(
    records: list[dict],
    regen: dict[str, dict[str, str]],
) -> dict:
    """Overwrite `context_by_pub[pmid]` where a valid sentence was regenerated.

    `regen` is {pmid: {canonical_tool_id: new_snippet}}. Records are mutated in
    place. Returns counts. A pair absent from `regen` (model gave null / failed the
    guard / pmid unprocessed) is left exactly as-is — never dropped.
    """
    upgraded = unchanged = 0
    for r in records:
        cbp = r.get("context_by_pub") or {}
        if not cbp:
            continue
        cid = r["canonical_tool_id"]
        for pmid, old in list(cbp.items()):
            new = regen.get(str(pmid), {}).get(cid)
            if new and new != old:
                cbp[pmid] = new
                upgraded += 1
            else:
                unchanged += 1
    return {"upgraded": upgraded, "unchanged": unchanged}


def build_sidecar(records: list[dict]) -> dict[str, dict[str, str]]:
    """Mirror corpus_run.py: {canonical_tool_id: {pmid: snippet}}, key-sorted."""
    return {
        r["canonical_tool_id"]: {p: cbp[p] for p in sorted(cbp)}
        for r in records
        if (cbp := r.get("context_by_pub"))
    }


def fragment_metrics(sidecar: dict[str, dict[str, str]]) -> dict:
    """Cheap proxy for the fragment problem: share starting mid-clause / no end punct."""
    snips = [s for cbp in sidecar.values() for s in cbp.values() if s]
    n = len(snips) or 1
    start_lower = sum(1 for s in snips if s[:1].islower())
    no_end = sum(1 for s in snips if s.rstrip()[-1:] not in ".!?")
    lens = [len(s) for s in snips]
    return {
        "snippets": len(snips),
        "start_lowercase_pct": round(100 * start_lower / n, 1),
        "no_terminal_punct_pct": round(100 * no_end / n, 1),
        "median_len": sorted(lens)[len(lens) // 2] if lens else 0,
        "max_len": max(lens) if lens else 0,
    }


def parse_regen_response(text: str, names: list[str], cids: list[str], abstract: str) -> dict[str, str]:
    """{canonical_tool_id: verbatim_snippet} for the tools the model placed in the abstract."""
    from pipeline_enrichment.llm_call import parse_json_lenient

    try:
        parsed = parse_json_lenient(text or "")
    except Exception:  # noqa: BLE001 — a malformed response just yields no upgrades
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for i, cid in enumerate(cids):
        cand = parsed.get(str(i + 1))
        cand = cand.strip().strip('"').strip() if isinstance(cand, str) else None
        # accept only a verbatim, single, tool-naming sentence; else the caller
        # keeps the existing snippet (never an off-topic or run-on upgrade).
        if cand and accept_snippet(cand, abstract, names[i]):
            out[cid] = cand
    return out


# ---------------------------------------------------------------------------
# Run (checkpointed, cost-bounded, concurrent)
# ---------------------------------------------------------------------------


@dataclass
class RebuildResult:
    n_pmids: int = 0
    n_done: int = 0
    n_skipped: int = 0
    n_failed: int = 0
    regen: dict = field(default_factory=dict)   # pmid -> {cid: snippet}
    halted: bool = False


def _load_checkpoint(path: str | None) -> dict:
    if not path:
        return {}
    out: dict = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    out[str(row["pmid"])] = row.get("regen", {})
    except FileNotFoundError:
        pass
    return out


def run_rebuild(
    by_pmid: dict[str, list[tuple[str, str]]],
    abstracts: dict[str, str],
    *,
    call_llm: CallLLM,
    ceiling: CostCeiling,
    checkpoint_path: str | None = None,
    workers: int = 8,
) -> RebuildResult:
    done = _load_checkpoint(checkpoint_path)
    result = RebuildResult(n_pmids=len(by_pmid), regen=dict(done))
    result.n_skipped = sum(1 for p in by_pmid if p in done)

    todo = [p for p in by_pmid if p not in done and abstracts.get(p, "").strip()]
    lock = threading.Lock()
    ckpt_fh = open(checkpoint_path, "a") if checkpoint_path else None

    def work(pmid: str) -> tuple[str, dict | None, int, int, str]:
        pairs = by_pmid[pmid]
        cids = [c for c, _ in pairs]
        names = [n for _, n in pairs]
        ab = abstracts[pmid]
        try:
            res = call_llm(REBUILD_SYSTEM_PROMPT, build_user_message(ab, names))
        except Exception as exc:  # noqa: BLE001
            return pmid, None, 0, 0, f"llm:{exc}"
        regen = parse_regen_response(getattr(res, "text", "") or "", names, cids, ab)
        return (pmid, regen,
                int(getattr(res, "input_tokens", 0) or 0),
                int(getattr(res, "output_tokens", 0) or 0),
                getattr(res, "model", "") or "")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(work, p): p for p in todo}
            for fut in as_completed(futures):
                pmid, regen, in_tok, out_tok, model_or_err = fut.result()
                if regen is None:
                    result.n_failed += 1
                    continue
                with lock:
                    result.regen[pmid] = regen
                    result.n_done += 1
                    if ckpt_fh:
                        ckpt_fh.write(json.dumps({"pmid": pmid, "regen": regen}) + "\n")
                        ckpt_fh.flush()
                    if in_tok or out_tok:
                        try:
                            ceiling.record(model=model_or_err, input_tokens=in_tok, output_tokens=out_tok)
                        except CostCeilingExceeded as exc:
                            logger.error("rebuild: cost ceiling tripped (%s) — halting", exc)
                            result.halted = True
                            for f in futures:
                                f.cancel()
                            break
    finally:
        if ckpt_fh:
            ckpt_fh.close()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fetch_abstracts(pmids: list[str]) -> dict[str, str]:
    from utils.sql_queries import get_raw_db_connection

    out: dict[str, str] = {}
    conn = get_raw_db_connection()
    try:
        cur = conn.cursor()
        for i in range(0, len(pmids), 1000):
            chunk = pmids[i : i + 1000]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT pmid, abstractVarchar FROM reporting_abstracts WHERE pmid IN ({fmt})", chunk)
            for row in cur.fetchall():
                out[str(row["pmid"])] = row["abstractVarchar"] or ""
    finally:
        conn.close()
    return out


def republish_sidecar(
    sidecar_path: str,
    live_tools_path: str,
    *,
    live_manifest_path: str | None = None,
    publish: bool = False,
) -> dict:
    """Republish the rebuilt sidecar via the real publish path; tool set frozen.

    The live ``tools.json`` bundle IS the publish payload minus ``tool_context``
    (``publish._split_artifacts``), so the payload is simply that bundle with the
    rebuilt context swapped in. ``publish_artifacts`` then re-emits the full set +
    manifest. SAFETY: the would-write ``tools.json`` / ``families.json`` /
    ``faculty.json`` bytes must be byte-identical to live (only ``tool_context.json``
    changes); we assert their sha256 against the live manifest before any upload.
    Default is a dry run — no S3 write.
    """
    import hashlib
    from pipeline_tools.publish import _build_manifest, _split_artifacts, publish_artifacts, S3_PREFIX

    bundle = json.load(open(live_tools_path))
    bundle.pop("tool_context", None)
    payload = dict(bundle)
    payload["tool_context"] = json.load(open(sidecar_path))

    items = _split_artifacts(payload, prefix=S3_PREFIX)
    shas = {it.key.rsplit("/", 1)[-1]: hashlib.sha256(it.body).hexdigest()
            for it in items if it.key.startswith(f"{S3_PREFIX}latest/")}
    frozen = {"tools.json", "families.json", "faculty.json"}
    if live_manifest_path:
        live = json.load(open(live_manifest_path)).get("objects", {})
        drift = [n for n in frozen if n in live and live[n]["sha256"] != shas.get(n)]
        if drift:
            raise SystemExit(f"ABORT: {drift} would change — republish must touch ONLY tool_context.json")
        logger.info("safety check: tools/families/faculty byte-identical to live ✓ (only tool_context.json changes)")
    manifest = _build_manifest(items, payload, prefix=S3_PREFIX)
    report = publish_artifacts(payload, dry_run=not publish)
    logger.info("%s: %d objects; tool_context.json sha=%s bytes=%s; manifest counts.tool_context=%d",
                "PUBLISHED" if publish else "DRY-RUN (no upload)",
                len(report), shas["tool_context.json"][:12],
                next(it.size for it in items if it.key.endswith("latest/tool_context.json")),
                manifest["counts"]["tool_context"])
    return {"report": report, "manifest": manifest, "published": publish}


def _make_call_llm(max_tokens: int = 1024) -> CallLLM:
    from pipeline_enrichment.llm_call import call_with_fallback
    from utils.bedrock_client import HAIKU_MODEL

    def _call(system: str, user: str):
        return call_with_fallback(
            system_prompt=system, user_prompt=user, model=HAIKU_MODEL, max_tokens=max_tokens,
        )

    return _call


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild tool_context.json with sentence-aligned snippets (#238).")
    ap.add_argument("--registry", default="out/tools/a2/tool_registry.json")
    ap.add_argument("--out", default="out/tools/a2/tool_context.rebuilt.json")
    ap.add_argument("--checkpoint", default="out/tools/a2/_rebuild_context_checkpoint.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="probe: only the newest N pmids")
    ap.add_argument("--max-usd", type=float, default=30.0)
    ap.add_argument("--workers", type=int, default=8)
    # Republish mode (skips regeneration): swap the rebuilt sidecar into the live
    # payload and re-emit via the real publish path. Dry-run unless --publish.
    ap.add_argument("--republish", action="store_true", help="publish the rebuilt sidecar (dry-run unless --publish)")
    ap.add_argument("--live-tools", help="path to the live tools.json bundle (payload base)")
    ap.add_argument("--live-manifest", help="path to the live latest/manifest.json (freeze safety check)")
    ap.add_argument("--publish", action="store_true", help="ACTUALLY upload to S3 (default: dry-run)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.republish:
        if not args.live_tools:
            ap.error("--republish requires --live-tools <live tools.json>")
        republish_sidecar(args.out, args.live_tools,
                          live_manifest_path=args.live_manifest, publish=args.publish)
        return 0

    registry = json.load(open(args.registry))
    records = registry["tools"]
    by_pmid = group_by_pmid(records)
    pmids = sorted(by_pmid, key=lambda p: int(p) if p.isdigit() else 0, reverse=True)
    if args.limit:
        pmids = pmids[: args.limit]
        by_pmid = {p: by_pmid[p] for p in pmids}

    before = fragment_metrics(build_sidecar(records))
    logger.info("BEFORE: %s", before)

    abstracts = _fetch_abstracts(pmids)
    logger.info("fetched %d/%d abstracts", sum(1 for p in pmids if abstracts.get(p, "").strip()), len(pmids))

    ceiling = CostCeiling(cap_usd=Decimal(str(args.max_usd)))
    result = run_rebuild(
        by_pmid, abstracts, call_llm=_make_call_llm(), ceiling=ceiling,
        checkpoint_path=args.checkpoint, workers=args.workers,
    )
    stats = apply_regen(records, result.regen)
    sidecar = build_sidecar(records)
    after = fragment_metrics(sidecar)

    json.dump(sidecar, open(args.out, "w"), ensure_ascii=False, sort_keys=True, indent=0)
    logger.info("pmids=%d done=%d skipped=%d failed=%d halted=%s | %s",
                result.n_pmids, result.n_done, result.n_skipped, result.n_failed, result.halted, stats)
    logger.info("AFTER : %s", after)
    logger.info("observed spend $%s -> %s", ceiling.observed_usd, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
