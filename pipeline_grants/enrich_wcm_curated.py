"""Enrich the curated awards CSV with a research synopsis per row.

The curated export carries only a short award name + a coarse field, but the
topic scorer needs descriptive text. This one-time (resumable) step fetches each
award's ``Website``, summarizes it with Bedrock into a 2-4 sentence
research-focused synopsis, and writes ``*_enriched.csv`` (original columns +
``synopsis`` + ``enrich_status``). Failures fall back to a field-based synopsis
so a row is never dropped.

Run (outward-facing: ~N web fetches + Bedrock calls):
    python -m pipeline_grants.enrich_wcm_curated \
        --in pipeline_grants/data/wcm_curated_opportunities_2026-04-22.csv \
        --out pipeline_grants/data/wcm_curated_opportunities_enriched.csv

Resumable: re-running skips rows whose synopsis is already filled (keyed on the
name+sponsor slug), so an interrupted run resumes cheaply.
"""
import argparse
import csv
import html
import logging
import re
import time
import urllib.request

from pipeline_grants import wcm_curated as wc
from utils.bedrock_client import BedrockClient, HAIKU_MODEL

log = logging.getLogger("pipeline_grants.enrich_wcm_curated")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DROP_RE = re.compile(r"<(script|style|head|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_UA = "Mozilla/5.0 (compatible; ReciterAI-GrantRecs/1.0; +https://reciter.weill.cornell.edu)"

_SUMMARY_SYSTEM = (
    "You write a concise research-focused synopsis of a funding award/prize so it "
    "can be matched to the right researchers by topic. In 2-4 sentences, describe "
    "what research areas, methods, diseases, or disciplines the award targets. "
    "Focus on the science; omit deadlines, amounts, and application logistics. "
    'Respond ONLY with JSON: {"synopsis": str}.'
)

_OUT_FIELDS_EXTRA = ["synopsis", "enrich_status"]


def strip_html(raw: str) -> str:
    """Crude HTML → visible text (no extra deps; mirrors normalize._strip_html)."""
    without = _DROP_RE.sub(" ", raw or "")
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", without))).strip()


def fetch_page_text(url: str, *, timeout: int = 20, max_chars: int = 6000) -> str:
    """Fetch a URL and return stripped visible text (truncated). '' on any failure."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read(2_000_000).decode(charset, errors="replace")
    except Exception as exc:  # noqa: BLE001 - network is best-effort
        log.info("fetch failed %s: %s", url, exc)
        return ""
    return strip_html(body)[:max_chars]


def summarize(bedrock, *, name: str, field: str, sponsor: str, page_text: str) -> str:
    """Bedrock synopsis from page text + award metadata. '' if the model returns nothing."""
    user = (
        f"Award name: {name}\n"
        f"Field (curated): {field}\n"
        f"Sponsor: {sponsor}\n"
        f"Website text:\n{page_text}"
    )
    raw = bedrock.call_json(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": user}],
        system=_SUMMARY_SYSTEM,
    )
    return (raw.get("synopsis") or "").strip()


def _load_existing(out_path: str) -> dict:
    """Map source_id → enriched row from a prior run (for resume). {} if absent."""
    try:
        rows = wc.read_curated_csv(out_path)
    except (FileNotFoundError, OSError):
        return {}
    done = {}
    for r in rows:
        sid = wc.make_source_id(r.get(wc.H_NAME, ""), r.get(wc.H_SPONSOR, ""))
        if (r.get("synopsis") or "").strip():
            done[sid] = r
    return done


def enrich_rows(rows: list, bedrock, *, fetcher=fetch_page_text, existing=None,
                sleep_s: float = 0.5, progress=lambda *_: None) -> list:
    """Return rows with ``synopsis`` + ``enrich_status`` filled. Pure-ish (DI for tests)."""
    existing = existing or {}
    out = []
    for i, row in enumerate(rows):
        sid = wc.make_source_id(row.get(wc.H_NAME, ""), row.get(wc.H_SPONSOR, ""))
        if sid in existing:
            merged = dict(row)
            merged["synopsis"] = existing[sid]["synopsis"]
            merged["enrich_status"] = existing[sid].get("enrich_status", "cached")
            out.append(merged)
            continue
        name = (row.get(wc.H_NAME) or "").strip()
        field = (row.get(wc.H_FIELD) or "").strip()
        sponsor = (row.get(wc.H_SPONSOR) or "").strip()
        text = fetcher(row.get(wc.H_WEBSITE, ""))
        synopsis, status = "", ""
        if text:
            try:
                synopsis = summarize(bedrock, name=name, field=field, sponsor=sponsor, page_text=text)
                status = "summarized" if synopsis else "fallback_empty_summary"
            except Exception as exc:  # noqa: BLE001 - model errors fall back, never abort the batch
                log.info("summarize failed %s: %s", name, exc)
                status = "fallback_summary_error"
        else:
            status = "fallback_no_page"
        if not synopsis:
            synopsis = wc.fallback_synopsis(row)
        merged = dict(row)
        merged["synopsis"] = synopsis
        merged["enrich_status"] = status
        out.append(merged)
        progress(i + 1, len(rows), name, status)
        if sleep_s:
            time.sleep(sleep_s)
    return out


def _write_csv(out_path: str, rows: list) -> None:
    if not rows:
        return
    base = [k for k in rows[0].keys() if k not in _OUT_FIELDS_EXTRA]
    fields = base + _OUT_FIELDS_EXTRA
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run(in_path: str, out_path: str, *, sleep_s: float = 0.5) -> dict:
    rows = wc.read_curated_csv(in_path)
    existing = _load_existing(out_path)
    log.info("enriching %d rows (%d already cached) -> %s", len(rows), len(existing), out_path)

    def _progress(done, total, name, status):
        log.info("[%d/%d] %s -> %s", done, total, name[:60], status)

    enriched = enrich_rows(rows, BedrockClient(), existing=existing, sleep_s=sleep_s, progress=_progress)
    _write_csv(out_path, enriched)
    counts = {}
    for r in enriched:
        counts[r["enrich_status"]] = counts.get(r["enrich_status"], 0) + 1
    summary = {"rows": len(enriched), "out": out_path, "status_counts": counts}
    log.info("enrich summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Enrich curated WCM awards with research synopses")
    p.add_argument("--in", dest="in_path",
                   default="pipeline_grants/data/wcm_curated_opportunities_2026-04-22.csv")
    p.add_argument("--out", dest="out_path",
                   default="pipeline_grants/data/wcm_curated_opportunities_enriched.csv")
    p.add_argument("--sleep", type=float, default=0.5, help="delay between fetches (seconds)")
    args = p.parse_args(argv)
    run(args.in_path, args.out_path, sleep_s=args.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
