"""Durable opaque id minting for the tool/method registries (§7, §8, D-06).

The single most load-bearing invariant in the spec: ``canonical_tool_id`` (§8)
and ``family_id`` (§7) are **durable, opaque, and NEVER recomputed from name or
membership**. A re-cluster that moves members or a rename that changes a display
form maps onto the *existing* id; minting a fresh id orphans every faculty
``tool_score`` keyed on the old one (the D-06 orphan rule).

This is the explicit correction of the A1 proof (#169), which derived
``canonical_tool_id`` from ``slugify(name)`` — making the id a function of the
name and therefore unstable across renames/merges. Ids here are opaque,
monotonic counters seeded from the registry's current high-water mark, so a
mint is deterministic given the registry state and an insertion order.
"""

from __future__ import annotations

import re

TOOL_ID_PREFIX = "tool_"
FAMILY_ID_PREFIX = "fam_"

_TOOL_ID_RE = re.compile(r"^tool_(\d+)$")
_FAMILY_ID_RE = re.compile(r"^fam_(\d+)$")

# Zero-pad widths — cosmetic only (ids sort lexicographically at expected scale).
# The numeric part is authoritative; a width overflow simply produces a longer id.
_TOOL_ID_WIDTH = 6      # tool_000001 .. tool_999999, then tool_1000000
_FAMILY_ID_WIDTH = 4    # fam_0001 .. fam_9999, then fam_10000


def _max_seq(ids: list[str], pattern: re.Pattern[str]) -> int:
    """Highest numeric suffix among ids matching ``pattern``; 0 if none match."""
    hi = 0
    for value in ids:
        m = pattern.match(value or "")
        if m:
            hi = max(hi, int(m.group(1)))
    return hi


class IdMinter:
    """Monotonic opaque-id minter seeded from existing ids (durable across batches).

    Construct from the ids already in a registry; ``mint()`` returns the next
    id and advances the counter. Two minters seeded from the same high-water
    mark and called the same number of times produce identical ids, so a seed
    run is reproducible without a clock or RNG (neither is available in the
    deterministic-replay environment, and ids must not depend on either).
    """

    def __init__(self, prefix: str, width: int, pattern: re.Pattern[str], start_from: int = 0):
        self._prefix = prefix
        self._width = width
        self._pattern = pattern
        self._counter = start_from

    @classmethod
    def for_tools(cls, existing_ids: list[str]) -> "IdMinter":
        return cls(TOOL_ID_PREFIX, _TOOL_ID_WIDTH, _TOOL_ID_RE, _max_seq(existing_ids, _TOOL_ID_RE))

    @classmethod
    def for_families(cls, existing_ids: list[str]) -> "IdMinter":
        return cls(FAMILY_ID_PREFIX, _FAMILY_ID_WIDTH, _FAMILY_ID_RE, _max_seq(existing_ids, _FAMILY_ID_RE))

    @property
    def high_water(self) -> int:
        """The current counter value (highest seq minted so far)."""
        return self._counter

    def mint(self) -> str:
        """Mint and return the next opaque id, advancing the counter."""
        self._counter += 1
        return f"{self._prefix}{self._counter:0{self._width}d}"


def is_tool_id(value: str) -> bool:
    return bool(_TOOL_ID_RE.match(value or ""))


def is_family_id(value: str) -> bool:
    return bool(_FAMILY_ID_RE.match(value or ""))
