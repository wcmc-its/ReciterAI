#!/usr/bin/env python3
"""Verify config/spin_target_funders.json against the live SPIN SponsorList directory.

The funder list is the biomed-relevance pre-filter for the SPIN source; its
``spin_name`` values are what ``ingest_spin`` pulls by (exact phrase), so a name
that drifts from the directory silently pulls the wrong sponsor — or nothing.
This is the repeatable "human pass" tool: run it whenever funders are added/edited.

    python -m cli.verify_spin_funders                  # audit every mapping vs the directory
    python -m cli.verify_spin_funders --search "leukemia lymphoma"   # find canonical candidates

Audit flags: spin_name not an exact directory entry (typo/drift); spon_code that
disagrees with the directory's code for that name. Needs SPIN_* creds (read-only).
"""
import argparse
import json
import os

from pipeline_grants import spin

_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "config", "spin_target_funders.json")


def search(sponsors: list, tokens: list, limit: int = 12) -> list:
    """Directory entries whose name contains ALL tokens (case-insensitive)."""
    toks = [t.lower() for t in tokens]
    hits = [s for s in sponsors if all(t in s["spon_name"].lower() for t in toks)]
    return hits[:limit]


def audit(sponsors: list, funders: list) -> list:
    """Returns a list of (funder, problem) for mappings that don't cleanly resolve."""
    by_name = {s["spon_name"].strip(): s for s in sponsors}
    problems = []
    for f in funders:
        name, code = f.get("spin_name", "").strip(), f.get("spon_code")
        rec = by_name.get(name)
        if rec is None:
            problems.append((f["funder"], f"spin_name not in directory: {name!r}"))
        elif code and rec["spon_code"] != code:
            problems.append((f["funder"], f"spon_code {code} != directory {rec['spon_code']} for {name!r}"))
    return problems


def demo():
    """Self-check: matching + audit logic, no network."""
    dir_ = [{"spon_code": "1", "spon_name": "Leukemia and Lymphoma Society of Canada"},
            {"spon_code": "2", "spon_name": "American Cancer Society, Inc."}]
    assert len(search(dir_, ["leukemia", "lymphoma"])) == 1
    assert search(dir_, ["nonexistent"]) == []
    ok = [{"funder": "ACS", "spin_name": "American Cancer Society, Inc.", "spon_code": "2"}]
    bad = [{"funder": "X", "spin_name": "Not In Directory", "spon_code": "9"}]
    badcode = [{"funder": "ACS", "spin_name": "American Cancer Society, Inc.", "spon_code": "99"}]
    assert audit(dir_, ok) == []
    assert len(audit(dir_, bad)) == 1 and len(audit(dir_, badcode)) == 1
    print("verify_spin_funders.demo OK")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Verify SPIN target funders vs the live directory")
    p.add_argument("--search", nargs="+", metavar="TOKEN",
                   help="find directory candidates whose name contains ALL tokens")
    p.add_argument("--demo", action="store_true", help="run offline self-check and exit")
    args = p.parse_args(argv)
    if args.demo:
        demo()
        return 0

    sponsors = spin.fetch_sponsor_list()
    print(f"directory: {len(sponsors)} sponsors")
    if args.search:
        for s in search(sponsors, args.search):
            print(f"  {s['spon_code']}  {s['spon_name'].strip()!r}  state={s.get('spon_state')!r}")
        return 0

    funders = json.load(open(_CONFIG)).get("target_funders", [])
    problems = audit(sponsors, funders)
    if not problems:
        print(f"OK — all {len(funders)} funder mappings resolve exactly.")
        return 0
    print(f"{len(problems)} problem(s):")
    for funder, msg in problems:
        print(f"  [{funder}] {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
