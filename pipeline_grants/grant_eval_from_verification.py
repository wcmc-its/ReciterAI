"""Bootstrap: parse the human-verification markdown into eval ranking-dump JSON.

One-time bridge so the harness has real benchmark dumps NOW. The durable path is the
SPS ECS ranker emitting this JSON directly; once that exists, this file is retired.

Usage:
  python -m pipeline_grants.grant_eval_from_verification \\
      docs/grant-matching-verification-2026-06-28.md OUTDIR \\
      --solicitations <grant3>/scratch-matching/grant_solicitations.json \\
      --extra-grants <grant3>/scratch-matching/extra_grants.json \\
      --funding-db wcm_funding_db_2026-06-28.json
"""
from __future__ import annotations

import argparse
import json
import os
import re

META_RE = re.compile(r"`(\w+)` · gate \*\*(\w+)\*\* · pediatric-restricted \*\*(\w+)\*\*")
RES_RE = re.compile(r"^\*\*(\d+)\. (.+?)\*\* · `([^`]+)` · .*?\*\*Fit ([\d.]+)\*\*", re.M)
PUB_RE = re.compile(
    r'^\s*-\s*"(?P<title>.*?)"\s*(?P<journal>.*?)\s*·\s*(?P<year>\d{4})\s*—\s*'
    r"impact\s*(?P<impact>[\d.]+),\s*rel\s*(?P<rel>[\d.]+)\s*—\s*\[PMID\s*(?P<pmid>\d+)\]"
    r".*?`(?P<subtopic>[^`]+)`\s*$",
    re.M,
)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _funding_db_solicitation_and_awardees(db_path):
    """{title_key -> (focus_description, {cwid})} from the funding DB."""
    out = {}
    if not db_path or not os.path.exists(db_path):
        return out
    for rec in json.load(open(db_path)):
        cwids = {
            e["email"].split("@")[0].strip().lower()
            for e in (rec.get("past_recipients") or [])
            if e.get("email") and "@" in e["email"]
        }
        out[_norm(rec.get("title", ""))] = (rec.get("focus_description", ""), cwids)
    return out


def parse(md: str) -> list[dict]:
    """Split the report into per-grant sections and parse each ranked researcher."""
    metas = list(META_RE.finditer(md))
    grants = []
    for i, m in enumerate(metas):
        start = m.end()
        end = metas[i + 1].start() if i + 1 < len(metas) else len(md)
        section = md[start:end]
        key, gate = m.group(1), m.group(2)
        ranked = []
        res = list(RES_RE.finditer(section))
        for j, r in enumerate(res):
            r_start = r.end()
            r_end = res[j + 1].start() if j + 1 < len(res) else len(section)
            block = section[r_start:r_end]
            evidence = [
                {
                    "pmid": p.group("pmid"),
                    "title": p.group("title"),
                    "year": int(p.group("year")),
                    "subtopic": p.group("subtopic"),
                    "relevance": float(p.group("rel")),
                }
                for p in PUB_RE.finditer(block)
            ]
            ranked.append(
                {
                    "cwid": r.group(3).strip().lower(),
                    "name": r.group(2).strip(),
                    "rank": int(r.group(1)),
                    "fit": float(r.group(4)),
                    "evidence": evidence,
                }
            )
        grants.append({"grant": key, "gate": gate, "ranked": ranked})
    return grants


def enrich_dump(g: dict, solic: dict, extra: dict, db: dict) -> dict:
    """Add solicitation + solicitation_title + awardees to a {grant,gate,ranked} dump.

    Shared by the markdown converter and the ECS full-pool dump path so both resolve a
    grant's text and past winners the same way. solicitation text priority: dedicated
    solicitations file > extra_grants desc > funding-db focus.
    """
    key = g["grant"]
    text = ""
    title = key
    if key in solic:
        v = solic[key]
        text = v if isinstance(v, str) else v.get("desc") or v.get("text", "")
        title = (v.get("title") if isinstance(v, dict) else "") or key
    elif key in extra:
        text, title = extra[key].get("desc", ""), extra[key].get("title", key)
    awardees: set[str] = set()
    for t, (focus, cwids) in db.items():
        nk = _norm(title)
        if nk and (nk in t or t in nk):
            awardees |= cwids
            if not text:
                text = focus
            break
    g["solicitation"] = text or f"(grant: {title})"
    g["solicitation_title"] = title
    if awardees:
        g["awardees"] = sorted(awardees)
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("outdir")
    ap.add_argument("--solicitations", default=None)
    ap.add_argument("--extra-grants", default=None)
    ap.add_argument("--funding-db", default=None)
    args = ap.parse_args()

    solic = json.load(open(args.solicitations)) if args.solicitations else {}
    extra = json.load(open(args.extra_grants)) if args.extra_grants else {}
    db = _funding_db_solicitation_and_awardees(args.funding_db)

    os.makedirs(args.outdir, exist_ok=True)
    for g in parse(open(args.report).read()):
        enrich_dump(g, solic, extra, db)
        json.dump(g, open(os.path.join(args.outdir, f"{g['grant']}.json"), "w"), indent=2)
        print(f"{g['grant']:<16} ranked={len(g['ranked']):>2} awardees={len(g.get('awardees', [])):>2} solic_chars={len(g['solicitation'])}")


if __name__ == "__main__":
    main()
