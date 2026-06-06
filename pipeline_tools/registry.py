"""Persistent match-or-mint registries — canonical tools (§8) and families (§7).

These are the load-bearing pieces of the spec: without a *persistent* registry,
``canonical_tool_id`` / ``family_id`` cannot be stable across batches ("MRI
scanner" in batch 1 and "magnetic resonance imaging" in batch 50 must resolve to
one id) and every faculty ``tool_score`` keyed on an id is orphaned the moment a
re-cluster moves members (the D-06 rule). Both registries therefore:

  - mint **durable opaque ids** (``pipeline_tools.ids``), never recomputed from name;
  - **match-or-mint** each incoming item against the current registry, never
    re-cluster from scratch (which forks near-duplicates);
  - **accrete** — aliases, pub_ids (``pub_count = |pub_ids|``), and context evidence
    grow on the existing record; ids do not change.

Matching is name-or-alias (exact, normalized) first, then embedding NN
(``pipeline_tools.embeddings``). The embedding step is injected, so the registry
logic is unit-tested with canned vectors and never touches AWS.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pipeline_tools import vocab
from pipeline_tools.embeddings import EmbeddingCache, Match, nearest_match
from pipeline_tools.ids import IdMinter

logger = logging.getLogger(__name__)

# Default match thresholds (calibratable; embedding model-dependent). A confident
# embedding match attaches; below it, mint. Conservative by design — a missed
# match (over-mint) is recoverable by the periodic dedup sweep (§7); a false
# match (wrong attach) silently corrupts pub_counts and is not.
DEFAULT_TOOL_MATCH_COSINE = 0.86
DEFAULT_FAMILY_MATCH_COSINE = 0.80
# §7 step 2: a strong out-of-supercategory family match flags a possible
# supercategory error to the exceptions queue instead of silently forking.
DEFAULT_FAMILY_CROSS_GUARD_COSINE = 0.88


def norm_name(name: str) -> str:
    """Normalize a raw/alias name for exact matching (case/space/punct-insensitive)."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


# ===========================================================================
# §8 — Canonical-tool registry
# ===========================================================================


class ToolRegistry:
    """Persistent canonical-tool registry (§8): identity, accretion, denylist.

    Holds one record per canonical tool. ``match_or_mint`` resolves an extracted
    mention to an existing record (attach) or a new opaque id (mint). Excluded
    mentions are never minted; their normalized aliases go on a persistent
    denylist so the same extraction error is not re-triaged every batch.
    Infrastructure records are canonicalized here too (same match-or-mint), but
    carry no supercategory/kind/family/salience.
    """

    def __init__(
        self,
        records: list[dict] | None = None,
        denylist: list[str] | None = None,
        *,
        cache: EmbeddingCache | None = None,
        match_cosine: float = DEFAULT_TOOL_MATCH_COSINE,
    ):
        self._records: dict[str, dict] = {}
        self._name_index: dict[str, str] = {}  # norm_name(form) -> canonical_tool_id
        for rec in records or []:
            self._records[rec["canonical_tool_id"]] = _normalize_tool_record(rec)
            self._reindex(rec["canonical_tool_id"])
        self._denylist: list[str] = sorted({norm_name(a) for a in (denylist or []) if norm_name(a)})
        self._denyset: set[str] = set(self._denylist)
        self._minter = IdMinter.for_tools(list(self._records))
        self._cache = cache or EmbeddingCache()
        self._match_cosine = match_cosine

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(
        cls,
        registry_path: Path,
        denylist_path: Path | None = None,
        *,
        cache: EmbeddingCache | None = None,
        match_cosine: float = DEFAULT_TOOL_MATCH_COSINE,
    ) -> "ToolRegistry":
        records: list[dict] = []
        if registry_path.exists():
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            records = data.get("tools", data) if isinstance(data, dict) else data
        denylist: list[str] = []
        if denylist_path and denylist_path.exists():
            d = json.loads(denylist_path.read_text(encoding="utf-8"))
            denylist = d.get("aliases", d) if isinstance(d, dict) else d
        return cls(records, denylist, cache=cache, match_cosine=match_cosine)

    def save(self, registry_path: Path, denylist_path: Path | None = None) -> None:
        payload = {"tools": [self._serialize(r) for r in self.records()]}
        registry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        if denylist_path is not None:
            denylist_path.write_text(
                json.dumps({"aliases": self._denylist}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    # --- read --------------------------------------------------------------

    def records(self) -> list[dict]:
        """All records, sorted by id for a stable, reviewable artifact."""
        return [self._records[cid] for cid in sorted(self._records)]

    def get(self, canonical_tool_id: str) -> dict | None:
        return self._records.get(canonical_tool_id)

    def __len__(self) -> int:
        return len(self._records)

    @property
    def denylist(self) -> list[str]:
        return list(self._denylist)

    # --- denylist (§8 — excluded) ------------------------------------------

    def is_denied(self, raw_name: str) -> bool:
        return norm_name(raw_name) in self._denyset

    def deny(self, raw_name: str) -> None:
        """Append a normalized alias to the persistent denylist (idempotent)."""
        key = norm_name(raw_name)
        if key and key not in self._denyset:
            self._denyset.add(key)
            self._denylist = sorted(self._denyset)

    # --- match -------------------------------------------------------------

    def match(self, raw_name: str) -> Match | None:
        """Resolve ``raw_name`` to an existing canonical tool, or None to mint.

        Exact normalized name/alias match first (score 1.0); else embedding NN
        over every record's display_name + aliases at/above ``match_cosine``.
        """
        key = norm_name(raw_name)
        if key in self._name_index:
            cid = self._name_index[key]
            return Match(key=cid, score=1.0, matched_text=raw_name)
        candidates = {
            cid: [rec["display_name"], *rec.get("aliases", [])]
            for cid, rec in self._records.items()
        }
        if not candidates:
            return None
        return nearest_match(raw_name, candidates, threshold=self._match_cosine, cache=self._cache)

    # --- mint / attach -----------------------------------------------------

    def mint(
        self,
        *,
        display_name: str,
        raw_name: str | None = None,
        disposition: str = vocab.DEFAULT_DISPOSITION,
        pub_ids: list | None = None,
        pub_count: int | None = None,
        context: str | None = None,
    ) -> dict:
        """Create a new canonical tool with a durable opaque id (identity only).

        Classification fields (kind/supercategory/attributes/salience/family) are
        set later by the classify + salience + family stages via
        ``update_classification`` — match-or-mint resolves *identity* first.

        ``pub_count`` is the SEED path: the seed carries an aggregate count, not
        a PMID list (real per-pub dedup arrives at A2). When given, it is stored
        as the record's count and summed on merge; ``pub_ids`` stays the A2 path.
        ``pub_count`` reported by ``_serialize`` is ``|pub_ids|`` when ids exist,
        else this stored seed count.
        """
        cid = self._minter.mint()
        aliases = sorted({a for a in {display_name, raw_name} if a and a.strip()})
        ids = sorted({p for p in (pub_ids or [])})
        rec = {
            "canonical_tool_id": cid,
            "display_name": display_name.strip(),
            "aliases": aliases,
            "disposition": disposition,
            "kind": None,
            "supercategory": None,
            "attributes": vocab.default_attributes(),
            "salience_tier": None,
            "salience_tier_basis": None,
            "member_of_family": None,
            "pub_ids": ids,
            "pub_count": int(pub_count) if pub_count is not None else len(ids),
            "context_evidence": [context] if context else [],
        }
        self._records[cid] = rec
        self._reindex(cid)
        return rec

    def attach(
        self,
        canonical_tool_id: str,
        *,
        raw_name: str | None = None,
        pub_ids: list | None = None,
        pub_count: int | None = None,
        context: str | None = None,
    ) -> dict:
        """Accrete a mention onto an existing record: alias + pub_ids + context.

        pub_ids accumulate as a set, so ``pub_count = |pub_ids|`` stays correct
        across batches (dedup by (tool, pub)); re-seeing the same pub does not
        double-count. The SEED ``pub_count`` (aggregate, no ids) instead SUMS on
        merge — approximate by design, since two raw seed names lack the PMID
        overlap needed to dedup (the A2 path with real ids does dedup correctly).
        """
        rec = self._records[canonical_tool_id]
        if raw_name and raw_name.strip():
            merged = set(rec["aliases"]) | {raw_name.strip()}
            rec["aliases"] = sorted(merged)
        if pub_ids:
            rec["pub_ids"] = sorted(set(rec["pub_ids"]) | {p for p in pub_ids})
        if pub_count is not None:
            rec["pub_count"] = int(rec.get("pub_count", 0)) + int(pub_count)
        if context and context not in rec["context_evidence"]:
            rec["context_evidence"].append(context)
        self._reindex(canonical_tool_id)
        return rec

    def update_classification(
        self,
        canonical_tool_id: str,
        *,
        disposition: str | None = None,
        kind: str | None = None,
        supercategory: str | None = None,
        attributes: dict | None = None,
        salience_tier: str | None = None,
        salience_tier_basis: str | None = None,
        member_of_family: str | None = None,
    ) -> dict:
        """Set classification fields produced by the §0.5/§1-§6/§5/§7 stages."""
        rec = self._records[canonical_tool_id]
        if disposition is not None:
            rec["disposition"] = disposition
        if kind is not None:
            rec["kind"] = kind
        if supercategory is not None:
            rec["supercategory"] = supercategory
        if attributes is not None:
            rec["attributes"] = {**vocab.default_attributes(), **attributes}
        if salience_tier is not None:
            rec["salience_tier"] = salience_tier
        if salience_tier_basis is not None:
            rec["salience_tier_basis"] = salience_tier_basis
        if member_of_family is not None:
            rec["member_of_family"] = member_of_family
        return rec

    def match_or_mint(
        self,
        *,
        raw_name: str,
        display_name: str | None = None,
        disposition: str = vocab.DEFAULT_DISPOSITION,
        pub_ids: list | None = None,
        pub_count: int | None = None,
        context: str | None = None,
    ) -> tuple[dict | None, str]:
        """Resolve a mention to a record. Returns ``(record, action)``.

        action is one of:
          - ``"denied"``  — mention is on the denylist (excluded); record is None.
          - ``"attached"`` — matched an existing canonical tool; mention accreted.
          - ``"minted"``  — no confident match; a new opaque id was created.
        """
        if self.is_denied(raw_name):
            return None, "denied"
        hit = self.match(raw_name)
        if hit is not None:
            rec = self.attach(hit.key, raw_name=raw_name, pub_ids=pub_ids, pub_count=pub_count, context=context)
            return rec, "attached"
        rec = self.mint(
            display_name=display_name or raw_name,
            raw_name=raw_name,
            disposition=disposition,
            pub_ids=pub_ids,
            pub_count=pub_count,
            context=context,
        )
        return rec, "minted"

    # --- internals ---------------------------------------------------------

    def _reindex(self, canonical_tool_id: str) -> None:
        rec = self._records[canonical_tool_id]
        for form in [rec["display_name"], *rec.get("aliases", [])]:
            key = norm_name(form)
            if key:
                self._name_index[key] = canonical_tool_id

    @staticmethod
    def _serialize(rec: dict) -> dict:
        out = dict(rec)
        out["pub_ids"] = sorted(rec.get("pub_ids", []))
        # |pub_ids| when real ids exist (A2 path); else the stored seed count.
        out["pub_count"] = len(out["pub_ids"]) if out["pub_ids"] else int(rec.get("pub_count", 0))
        return out


def _normalize_tool_record(rec: dict) -> dict:
    """Coerce a loaded record into the in-memory shape (pub_ids as a list, attrs present)."""
    rec = dict(rec)
    rec.setdefault("aliases", [])
    rec.setdefault("disposition", vocab.DEFAULT_DISPOSITION)
    rec.setdefault("attributes", vocab.default_attributes())
    rec.setdefault("context_evidence", [])
    rec["pub_ids"] = sorted(set(rec.get("pub_ids", [])))
    rec.setdefault("pub_count", len(rec["pub_ids"]))
    for field in ("kind", "supercategory", "salience_tier", "salience_tier_basis", "member_of_family"):
        rec.setdefault(field, None)
    return rec


# ===========================================================================
# §7 — Method-family registry
# ===========================================================================


class FamilyRegistry:
    """Persistent method-family registry (§7): match-or-mint, durable family_id.

    A method_tool (already resolved to a canonical tool via §8) matches an
    existing family by embedding NN on the tool + the family's members, gated to
    the **same supercategory** for precision. A strong nearest match in a
    *different* supercategory is NOT silently minted — it is returned as a
    cross-supercategory flag for the exceptions queue (§7 step 2). Otherwise a
    provisional family is minted. ``family_id`` is opaque and stable across
    re-clusters (D-06).
    """

    def __init__(
        self,
        records: list[dict] | None = None,
        *,
        cache: EmbeddingCache | None = None,
        match_cosine: float = DEFAULT_FAMILY_MATCH_COSINE,
        cross_guard_cosine: float = DEFAULT_FAMILY_CROSS_GUARD_COSINE,
    ):
        self._records: dict[str, dict] = {
            r["family_id"]: _normalize_family_record(r) for r in (records or [])
        }
        self._minter = IdMinter.for_families(list(self._records))
        self._cache = cache or EmbeddingCache()
        self._match_cosine = match_cosine
        self._cross_guard_cosine = cross_guard_cosine

    @classmethod
    def load(
        cls,
        registry_path: Path,
        *,
        cache: EmbeddingCache | None = None,
        match_cosine: float = DEFAULT_FAMILY_MATCH_COSINE,
    ) -> "FamilyRegistry":
        records: list[dict] = []
        if registry_path.exists():
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            records = data.get("families", data) if isinstance(data, dict) else data
        return cls(records, cache=cache, match_cosine=match_cosine)

    def save(self, registry_path: Path) -> None:
        payload = {"families": [self._records[fid] for fid in sorted(self._records)]}
        registry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def records(self) -> list[dict]:
        return [self._records[fid] for fid in sorted(self._records)]

    def get(self, family_id: str) -> dict | None:
        return self._records.get(family_id)

    def __len__(self) -> int:
        return len(self._records)

    def _candidates(self, supercategory: str | None, *, same: bool) -> dict[str, list[str]]:
        """family_id -> matchable texts (label + member display names), filtered by supercategory.

        ``same=True`` keeps only families in ``supercategory`` (the precision pass);
        ``same=False`` keeps only families in a *different* supercategory (the
        cross-supercategory guard pass).
        """
        out: dict[str, list[str]] = {}
        for fid, fam in self._records.items():
            in_super = fam.get("supercategory") == supercategory
            if same and not in_super:
                continue
            if not same and in_super:
                continue
            out[fid] = [fam["label"], *fam.get("member_display_names", [])]
        return out

    def match_or_mint(
        self,
        *,
        tool_id: str,
        tool_text: str,
        supercategory: str,
        dominant_kind: str | None = None,
        label: str | None = None,
    ) -> tuple[dict | None, str, Match | None]:
        """Resolve a tool into a family. Returns ``(family, action, cross_flag)``.

        action is one of:
          - ``"attached"`` — matched a same-supercategory family; tool added as a member.
          - ``"minted"``   — no same-supercategory match and no cross flag; a new
                             provisional family was created.
          - ``"flagged"``  — a strong match exists in a DIFFERENT supercategory; the
                             tool is still minted into its own supercategory, but
                             ``cross_flag`` carries the suspected-error match for the
                             exceptions queue (§7 step 2). Never a silent cross-fork.
        """
        same = self._candidates(supercategory, same=True)
        hit = nearest_match(tool_text, same, threshold=self._match_cosine, cache=self._cache) if same else None
        if hit is not None:
            fam = self._attach_member(hit.key, tool_id, tool_text)
            return fam, "attached", None

        # No same-supercategory home — check for a strong out-of-bucket match.
        other = self._candidates(supercategory, same=False)
        cross = nearest_match(tool_text, other, threshold=self._cross_guard_cosine, cache=self._cache) if other else None
        fam = self._mint(supercategory, dominant_kind, label, tool_id, tool_text)
        return fam, ("flagged" if cross is not None else "minted"), cross

    def _mint(
        self,
        supercategory: str,
        dominant_kind: str | None,
        label: str | None,
        tool_id: str,
        tool_text: str,
    ) -> dict:
        fid = self._minter.mint()
        fam = {
            "family_id": fid,
            "label": (label or tool_text).strip(),
            "supercategory": supercategory,
            "dominant_kind": dominant_kind,
            "member_tool_ids": [tool_id],
            "exemplar_tool_ids": [tool_id],
            "status": "provisional",
            "member_display_names": [tool_text],
        }
        self._records[fid] = fam
        return fam

    def _attach_member(self, family_id: str, tool_id: str, tool_text: str) -> dict:
        fam = self._records[family_id]
        if tool_id not in fam["member_tool_ids"]:
            fam["member_tool_ids"].append(tool_id)
            fam.setdefault("member_display_names", []).append(tool_text)
        return fam

    def relabel(self, family_id: str, label: str, *, status: str | None = None) -> dict:
        """Set the controlled display label (§7.2); optionally promote provisional→active."""
        fam = self._records[family_id]
        fam["label"] = label.strip()
        if status is not None:
            fam["status"] = status
        return fam


def _normalize_family_record(rec: dict) -> dict:
    rec = dict(rec)
    rec.setdefault("member_tool_ids", [])
    rec.setdefault("exemplar_tool_ids", [])
    rec.setdefault("status", "provisional")
    rec.setdefault("dominant_kind", None)
    rec.setdefault("member_display_names", [])
    return rec
