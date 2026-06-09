#!/usr/bin/env python3
"""Dump the human-reviewable A2 method-family list from the corpus hierarchy artifact.

Reads ``tool_hierarchy_corpus.json`` (supercategory → families with ``n_members``) and
writes ``family_list.txt``: a header line + one ``=== supercategory (F families, T tools)``
block per supercategory (ordered by family count, descending), each listing its families
``  <label>  (<n_members>)`` ordered by member count, descending.

This is the D-07 review artifact; ``write_outputs`` does not emit it. Usage:

    python -m cli.dump_family_list --out-dir out/tools/a2 --version "v7 (D-07 fix batch applied)"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(hierarchy: dict, version: str) -> str:
    blocks = []
    for sc, fams in hierarchy.items():
        n_fams = len(fams)
        n_tools = sum(int(f.get("n_members") or 0) for f in fams)
        blocks.append((sc, n_fams, n_tools, fams))
    blocks.sort(key=lambda b: (-b[1], b[0]))  # supercats by family count desc

    total_fams = sum(b[1] for b in blocks)
    total_tools = sum(b[2] for b in blocks)
    lines = [f"A2 METHOD FAMILIES ({version}) — {total_fams} families / {total_tools} familied tools", ""]
    for sc, n_fams, n_tools, fams in blocks:
        lines.append(f"=== {sc}  ({n_fams} families, {n_tools} tools) ===")
        for f in sorted(fams, key=lambda f: (-int(f.get("n_members") or 0), f.get("label") or "")):
            lines.append(f"  {f.get('label')}  ({int(f.get('n_members') or 0)})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dump family_list.txt from tool_hierarchy_corpus.json")
    ap.add_argument("--out-dir", type=Path, default=Path("out/tools/a2"))
    ap.add_argument("--version", default="v7 (D-07 fix batch applied)")
    args = ap.parse_args(argv)
    hierarchy = json.loads((args.out_dir / "tool_hierarchy_corpus.json").read_text(encoding="utf-8"))
    text = render(hierarchy, args.version)
    (args.out_dir / "family_list.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
