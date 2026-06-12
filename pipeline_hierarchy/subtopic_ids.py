"""Durable opaque subtopic-id minting (#191, brick A).

A subtopic's *identity* must be decoupled from its *label*. Today a subtopic's
id is a slug of its label (e.g. ``cell_cancer_genomics_molecular_oncology``), so
a relabel or a re-cluster rotates the id — orphaning deep-links, spotlight
rotation history, and every consumer keyed on it. The fix (Option C in
``docs/subtopic-lifecycle-and-evolution.md`` §6) is a durable, **opaque**,
**mint-once** id that is *never derived from the label or membership* and is
looked up — not recomputed — on every rebuild.

This module is the minter half of brick A; the durable id<->membership store
(``pipeline_hierarchy/subtopic_id_store.py``) is the persistence half.

Divergence from the tools/families precedent (``pipeline_tools/ids.py``): that
minter uses a monotonic counter seeded from the registry high-water mark, which
is reproducible *because* the tool registry is rebuilt deterministically from
scratch each run. The subtopic store is the opposite — it is the durable source
of truth, looked up across runs — so an id need not be regenerable, and the
settled design (``§6``) mandates a *random* suffix (a counter would leak mint
order). What carries over is the discipline: an opaque, label-independent id and
an **injectable entropy source** (here ``rand``) so tests are deterministic
while production draws from ``secrets``.

Hard constraint — **lowercase only**. SPS validates the deep-link ``subtopicId``
URL param with ``^[a-z0-9_]+$`` (the Phase-1 audit in ``§7``); an uppercase ULID
is silently rejected by the live router. Every minted id is asserted against
that route regex before it is returned.
"""

from __future__ import annotations

import re
import secrets
import string
from typing import Callable

SUBTOPIC_ID_PREFIX = "st_"

# Suffix alphabet: lowercase letters + digits only. Kept deliberately within the
# SPS route charset so a minted id can never trip the live router.
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits  # [a-z0-9], 36 symbols
_SUFFIX_LEN = 26  # ULID-length; 36**26 keyspace makes a collision effectively impossible

# Upper bound on collision redraws. At the default keyspace this is never
# approached (one draw suffices); it converts a pathological exhausted-keyspace
# case (e.g. a tiny suffix_len) from an infinite loop into a fail-loud error.
_MAX_MINT_ATTEMPTS = 64

# Shape of a well-formed subtopic id (prefix + lowercase suffix).
_SUBTOPIC_ID_RE = re.compile(r"^st_[a-z0-9]+$")
# Independent restatement of SPS's deep-link route constraint. Asserted on every
# mint so the lowercase invariant is enforced at the source, not just documented.
_ROUTE_RE = re.compile(r"^[a-z0-9_]+$")


def _default_rand() -> float:
    """A ``secrets``-backed float in [0, 1) (53-bit mantissa precision).

    Non-reproducible by design — production ids must not be derivable. Tests
    inject a seeded ``random.Random(seed).random`` instead.
    """
    return secrets.randbelow(1 << 53) / float(1 << 53)


class SubtopicIdMinter:
    """Opaque, mint-once, lowercase subtopic-id minter with an injectable RNG.

    The minted id is **not** regenerable (random + mint-once) — the store is the
    source of truth. Only the RNG is seedable, so a unit test can pin the minted
    value by passing ``rand=random.Random(seed).random``.
    """

    def __init__(
        self,
        *,
        rand: Callable[[], float] | None = None,
        suffix_len: int = _SUFFIX_LEN,
    ):
        # rand: () -> float in [0, 1). Default: secrets-backed, non-reproducible.
        self._rand = rand or _default_rand
        self._n = suffix_len

    def _draw(self) -> str:
        n = len(_SUFFIX_ALPHABET)
        # Clamp BOTH ends: a correct [0, 1) source never triggers either bound,
        # but an injected non-conforming rand() (>=1.0 or negative) is contained
        # rather than silently picking a wrong index or raising IndexError.
        suffix = "".join(
            _SUFFIX_ALPHABET[max(0, min(int(self._rand() * n), n - 1))]
            for _ in range(self._n)
        )
        sid = f"{SUBTOPIC_ID_PREFIX}{suffix}"
        if not _ROUTE_RE.match(sid):
            # A violation here is a code bug (bad alphabet/prefix), not bad input.
            raise ValueError(f"minted subtopic id violates SPS route regex: {sid!r}")
        return sid

    def mint(self, *, exists: Callable[[str], bool] | None = None) -> str:
        """Mint a fresh id not already present.

        ``exists(id) -> bool`` is the collision check (the store's row lookup in
        production; a set-membership check in tests). Because the suffix is
        random — unlike the tools counter — a collision is *possible* in
        principle, so the redraw loop is correct-by-construction even though at
        the default keyspace (36**26) a single draw effectively always suffices.
        The loop is **bounded** so a pathological tiny ``suffix_len`` whose
        keyspace is exhausted fails loud instead of hanging forever.
        """
        exists = exists or (lambda _sid: False)
        for _ in range(_MAX_MINT_ATTEMPTS):
            sid = self._draw()
            if not exists(sid):
                return sid
        raise RuntimeError(
            f"subtopic id keyspace exhausted after {_MAX_MINT_ATTEMPTS} draws "
            f"(suffix_len={self._n}); widen suffix_len"
        )


def is_subtopic_id(value: str) -> bool:
    """True iff ``value`` is a well-formed durable subtopic id.

    Mirrors ``pipeline_tools.ids.is_tool_id`` — a cheap shape guard for callers
    that must distinguish a durable id from a legacy slug id.
    """
    return bool(_SUBTOPIC_ID_RE.match(value or ""))
