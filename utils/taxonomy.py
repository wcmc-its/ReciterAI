"""Canonical taxonomy load + content hash.

Nine ad-hoc `_load_taxonomy` helpers across four path idioms existed before this
module (see ADR `docs/adr-taxonomy-change-propagation.md`, trap 6). New code
should import from here rather than adding a tenth.

The content hash is the single reference value the ADR's D1 step 5, D3 handshake,
and D5 layer 1 all compare against. It covers every field of every topic — not
just `id` — because two artifacts can agree on ids and still score differently
(different `display_threshold`, different `description` reaching the LLM).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy_v2.json"


def load_taxonomy(path: str | Path | None = None) -> dict[str, Any]:
    """Return the parsed taxonomy document."""
    return json.loads(Path(path or TAXONOMY_PATH).read_text())


def topic_ids(taxonomy: dict[str, Any] | None = None) -> set[str]:
    """Return the set of topic ids in the taxonomy."""
    return {t["id"] for t in (taxonomy or load_taxonomy())["topics"]}


def content_hash(taxonomy: dict[str, Any] | None = None) -> str:
    """Return the sha256 of the taxonomy's semantic content.

    Hashes the canonicalised `topics` list — every field, order-independent — so
    the value is stable against reformatting and key reordering but moves on any
    change that can affect scoring. `taxonomy_version` is deliberately excluded:
    it is a static family label (`"taxonomy_v2"`), not a version that moves, so
    including it would add nothing and imply it did.
    """
    topics = sorted((taxonomy or load_taxonomy())["topics"], key=lambda t: t["id"])
    canonical = json.dumps(topics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def current_content_hash() -> str:
    """Content hash of the taxonomy bundled with this artifact."""
    return content_hash()
