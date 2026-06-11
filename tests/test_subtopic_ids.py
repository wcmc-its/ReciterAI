"""Tests for durable opaque subtopic-id minting (#191, brick A).

Covers the load-bearing invariants:
- minted ids are opaque, lowercase, and satisfy the SPS deep-link route regex;
- the RNG is injectable, so a seed makes the suffix a deterministic function of
  the seed (production draws from ``secrets``, non-reproducible by design);
- the collision guard redraws on a clash (random suffix => possible in principle);
- ``is_subtopic_id`` distinguishes a durable id from a legacy slug id.
"""

from __future__ import annotations

import random
import re

from pipeline_hierarchy.subtopic_ids import (
    SUBTOPIC_ID_PREFIX,
    SubtopicIdMinter,
    is_subtopic_id,
)

# The exact regex SPS uses to validate the deep-link `subtopicId` URL param.
_SPS_ROUTE_RE = re.compile(r"^[a-z0-9_]+$")


def test_minted_id_is_prefixed_lowercase_and_route_safe():
    sid = SubtopicIdMinter(rand=random.Random(0).random).mint()
    assert sid.startswith(SUBTOPIC_ID_PREFIX)
    assert _SPS_ROUTE_RE.match(sid), f"{sid} would be rejected by the SPS router"
    assert sid == sid.lower()
    assert is_subtopic_id(sid)


def test_seeded_rng_makes_mint_deterministic():
    # Same seed -> identical id (the property tests rely on; production differs).
    a = SubtopicIdMinter(rand=random.Random(1234).random).mint()
    b = SubtopicIdMinter(rand=random.Random(1234).random).mint()
    assert a == b
    # Different seed -> (overwhelmingly likely) different id.
    c = SubtopicIdMinter(rand=random.Random(9999).random).mint()
    assert c != a


def test_distinct_draws_within_one_minter_differ():
    m = SubtopicIdMinter(rand=random.Random(42).random)
    ids = {m.mint() for _ in range(50)}
    assert len(ids) == 50  # no repeats across draws


def test_collision_guard_redraws_until_fresh():
    # `exists` reports the first draw as taken, the second as free.
    answers = iter([True, False])
    m = SubtopicIdMinter(rand=random.Random(1234).random)
    first_draw = SubtopicIdMinter(rand=random.Random(1234).random).mint()
    got = m.mint(exists=lambda _sid: next(answers))
    assert got != first_draw  # the collision was skipped
    assert is_subtopic_id(got)


def test_suffix_length_is_configurable():
    sid = SubtopicIdMinter(rand=random.Random(1).random, suffix_len=8).mint()
    assert len(sid) == len(SUBTOPIC_ID_PREFIX) + 8
    assert is_subtopic_id(sid)


def test_mint_fails_loud_when_keyspace_exhausted():
    # exists() always True -> bounded loop must raise, not hang forever.
    import pytest

    m = SubtopicIdMinter(rand=random.Random(1).random, suffix_len=2)
    with pytest.raises(RuntimeError, match="keyspace exhausted"):
        m.mint(exists=lambda _sid: True)


def test_draw_clamps_a_misbehaving_rand_both_ends():
    # A non-conforming source (>=1.0 or negative) must be contained, not crash or
    # mis-index. Cycle through out-of-range values; every id stays route-safe.
    bad_values = iter([1.0, -0.001, -2.0, 1.5] * 100)
    sid = SubtopicIdMinter(rand=lambda: next(bad_values)).mint()
    assert is_subtopic_id(sid)
    assert _SPS_ROUTE_RE.match(sid)


def test_is_subtopic_id_rejects_uppercase_and_slugs():
    assert is_subtopic_id("st_01jw9kabc")
    assert not is_subtopic_id("ST_01JW9K")  # uppercase ULID -> rejected
    assert not is_subtopic_id("cell_cancer_genomics")  # a legacy slug id
    assert not is_subtopic_id("st_")  # prefix only, no suffix
    assert not is_subtopic_id("")
    assert not is_subtopic_id("st_has-a-dash")  # dash outside the alphabet
