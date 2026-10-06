"""Refresh the cached `alias_hits` (global PMC hit count per alias) in the dictionary.

Alias SPECIFICITY is the cheapest precision signal we have: over 10 aliases spanning
1 -> 23,544 global PMC hits, Pearson r = -0.852 between log10(hits) and the share of
matches that mean OUR core (100% home at 1 hit, 24% at 23,544). The count is free —
esearch already reports it — so it is fetched once here and cached in the dictionary
rather than during a scoring run.

    python3 -m pipeline_cores.refresh_alias_hits                # print the YAML block
    python3 -m pipeline_cores.refresh_alias_hits --core 4
    python3 -m pipeline_cores.refresh_alias_hits --write        # edit the dictionary

`--write` is a SURGICAL line edit, not a yaml round-trip: safe_dump would strip every
comment out of the dictionary, and the comments are the project's IP. The new text is
parsed before it replaces the file, so a botched edit fails instead of landing.

AN ALIAS WITH AN "and"/"&" CONNECTOR is counted over BOTH spellings (the union, via
pmc_search.esearch_count), because the matcher accepts both: "Proteomics & Metabolomics
Core" counted alone was 192 (moderate) but the phrase the matcher
actually fires on appears in 1,122 PMC papers (generic). Re-run with --write after
any change to the connector rule in signals._alias_pattern, or the cached counts
describe a different alias from the one that matches.

ACRONYM ALIASES ARE SKIPPED, for pmc_search's reason: esearch has no case-sensitive
mode, so "CBIC" would be counted with the noise the matcher's word-boundary rule
exists to exclude. Their `ack_alias_hits` stays None — weight them at the floor.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import yaml

from pipeline_cores.dictionary import _DEFAULT_PATH, load_cores
from pipeline_cores.signals import _ACRONYM

logger = logging.getLogger(__name__)

_CORE_START = re.compile(r"^  - ")        # a core entry in the `cores:` list
_FIELD = re.compile(r"^    \S")           # a field of that entry (aliases:, staff:, ...)


def fetch_hits(core, *, counter=None) -> dict:
    """alias -> global PMC hit count, acronyms skipped, fail-soft per alias."""
    if counter is None:
        from pipeline_cores.pmc_search import esearch_count as counter  # lazy: import stays network-free
    out = {}
    for alias in core.aliases:
        if _ACRONYM.match(alias):
            logger.info("alias %r is an acronym — skipped (esearch is case-insensitive)", alias)
            continue
        try:
            n = counter(alias)
        except Exception as err:  # noqa: BLE001 — one bad alias keeps its cached count
            logger.warning("hit-count failed for %r: %s", alias, err)
            continue
        # None = PMC could not run it as a phrase and answered a DIFFERENT query.
        # Leave it uncached rather than cache a number that measures nothing; an
        # absent count reads as ack.spec:unknown (weight 0.00).
        if n is None:
            logger.warning("no phrase count for %r — left uncached", alias)
            continue
        out[alias] = n
    return out


def render(hits: dict) -> list:
    """The `alias_hits:` YAML lines for one core (4-space field indent)."""
    lines = ["    alias_hits:                 # global PMC hits per alias (refresh_alias_hits)\n"]
    lines += [f"      {json.dumps(a)}: {n}\n" for a, n in hits.items()]
    return lines


def write_hits(text: str, hits_by_core: dict) -> str:
    """Return `text` with each core's `alias_hits:` block replaced, comments intact."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if _CORE_START.match(ln)] + [len(lines)]
    out = lines[: starts[0]]
    for lo, hi in zip(starts, starts[1:]):
        block = lines[lo:hi]
        hits = hits_by_core.get(_core_id(block))
        if hits:                                  # no counts -> leave the core's block alone
            block = _strip_existing(block)
            at = _insert_at(block)
            block = block[:at] + render(hits) + block[at:]
        out += block
    return "".join(out)


def _core_id(block: list) -> str:
    for ln in block:
        m = re.match(r"^  - core_id:\s*(.+?)\s*$", ln)
        if m:
            return m.group(1).strip("\"'")
    return ""


def _strip_existing(block: list) -> list:
    out, skipping = [], False
    for ln in block:
        if _FIELD.match(ln):
            skipping = ln.strip().startswith("alias_hits:")
        if not skipping:
            out.append(ln)
    return out


def _insert_at(block: list) -> int:
    """Just after the core's `aliases:` list — where the counts read naturally."""
    start = next((i for i, ln in enumerate(block)
                  if _FIELD.match(ln) and ln.strip().startswith("aliases:")), None)
    if start is None:
        return len(block)
    for j in range(start + 1, len(block)):
        if _FIELD.match(block[j]):
            return j
    return len(block)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--core", help="core_id, e.g. 4 (default: every core)")
    ap.add_argument("--write", action="store_true", help="edit config/core_dictionary.yaml in place")
    ap.add_argument("--path", default=None, help="dictionary path (default: config/core_dictionary.yaml)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    path = Path(args.path) if args.path else _DEFAULT_PATH
    cores = [c for c in load_cores(path) if not args.core or c.core_id == str(args.core)]
    hits_by_core = {}
    for core in cores:
        hits = fetch_hits(core)
        hits_by_core[core.core_id] = hits
        print(f"\n=== core {core.core_id}  {core.name} ===")
        for alias, n in sorted(hits.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>8}  {alias}")

    if not args.write:
        print("\nNothing written. Re-run with --write to cache these in the dictionary.")
        return 0

    new_text = write_hits(path.read_text(), hits_by_core)
    yaml.safe_load(new_text)              # a botched edit fails HERE, not in the next run
    path.write_text(new_text)
    print(f"\nwrote alias_hits for {len(hits_by_core)} core(s) to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
