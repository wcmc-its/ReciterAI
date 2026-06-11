"""Durable id<->membership store + match-or-mint (#191, brick A).

The persistence half of brick A. It promotes the per-run ``membership.json``
sidecar (#192, ``pipeline_hierarchy/bundler.py::build_membership``) into a
**durable** store on the shared ``reciterai`` DynamoDB table, keyed by an opaque
subtopic id (minted by ``pipeline_hierarchy/subtopic_ids.py``). The store is what
makes a re-cluster non-destructive: a subtopic keeps its id across rebuilds, so
deep-links, spotlight rotation history, and the structural diff all survive a
relabel. Full design: ``docs/subtopic-lifecycle-and-evolution.md`` §6-§7;
contract: ``docs/subtopic-durable-id-store.md``.

Scope discipline (brick A only):
- ``match()`` here is **exact-slug equality and nothing more** — a deliberate
  placeholder. Brick B replaces its *body* with the deterministic-first reconcile
  (membership-overlap -> embedding-centroid -> LLM arbiter, verdicts cached by
  input hash). ``membership`` and ``topic_id`` are already parameters, so the
  narrow swap — return a hit or ``None`` — needs no signature change and no edit
  to ``mint``/``attach``/``match_or_mint``. This mirrors the proven
  ``pipeline_tools.registry.ToolRegistry.match_or_mint`` seam, where all match
  policy lives in ``match()``. Two honest caveats for brick B (so they are not
  discovered mid-implementation — cf. the "scope via code comment" rule):
    * A *third* "flagged/defer" verdict (hold for arbitration rather than attach)
      is NOT free: ``match_or_mint`` branches on ``hit is not None`` and would
      route any non-None hit into ``attach``. Brick B must add a branch (e.g. on
      ``hit.reason``) for a defer state — that is a control-flow change here.
    * ``match_or_mint`` persists each row *immediately* inside the reconcile pass.
      A store-scanning matcher (brick B's overlap reconcile) would therefore see
      rows written for *earlier* subtopics of the *same* run and could self-match
      a later sibling. Brick B should read a **pre-run store snapshot** (or
      exclude ids written during the current pass) rather than scanning live.
  Brick A's exact-slug match is immune to both (slug is unique per pass).
- Split/merge lineage (``status`` transitions) is brick C; this module writes a
  default ``status="active"`` and never mutates it.
- Migration + the slug->durable alias map that repoints SPS consumers is brick D.
  These ``SUBTOPIC_SLUG#`` pointer rows are the natural seam for it, but brick A
  flips no consumer — the published ``hierarchy.json`` keeps slug ids.

**Internal-only.** Nothing here is read by SPS today; it is pipeline-internal
recognition memory, like the membership sidecar.

Conservative-substrate rule (carried from the tools precedent): a missed match
(over-mint) is recoverable by a later brick; a *false* match silently corrupts
membership and is not. So the match stays strict — exact string equality, no
normalization, no fuzzing. Over-minting is the designed-safe failure mode.

Table layout (single shared ``reciterai`` table, composite PK/SK; no GSI — the
pointer row makes both lookups an O(1) ``GetItem``):

    Primary row     PK = SUBTOPIC_ID#{durable_id}   SK = META
    Slug pointer    PK = SUBTOPIC_SLUG#{slug_id}     SK = PTR
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from pipeline_hierarchy.bundler import MEMBERSHIP_KIND
from pipeline_hierarchy.subtopic_ids import SubtopicIdMinter

_log = logging.getLogger(__name__)

# ---- key conventions (mirror the repo's {ENTITY}#{key} + SK-discriminator) ----
SUBTOPIC_ID_PK_PREFIX = "SUBTOPIC_ID#"
SUBTOPIC_SLUG_PK_PREFIX = "SUBTOPIC_SLUG#"
META_SK = "META"
PTR_SK = "PTR"
RECORD_TYPE_SUBTOPIC_ID = "SUBTOPIC_ID"
RECORD_TYPE_SUBTOPIC_SLUG = "SUBTOPIC_SLUG"

# Default lifecycle status. Brick C (split/merge lineage) owns transitions; brick
# A only ever writes the default.
STATUS_ACTIVE = "active"


@dataclass(frozen=True)
class MintContext:
    """Per-reconcile provenance threaded from the publish flow into the store."""

    run_id: Optional[str]
    created_at: str  # ISO8601; the publish's started_at — mint/reconcile time
    taxonomy_version: str
    hierarchy_version: str


@dataclass(frozen=True)
class SubtopicMatch:
    """Result of ``SubtopicIdStore.match``. ``score``/``reason`` are trivial in
    brick A (always 1.0 / "exact_slug"); brick B populates them from the
    reconcile stage so the audit diff can record *how* a cluster was matched."""

    durable_id: str
    score: float
    reason: str


# ---------- pure record builders (no I/O — byte-stable, unit-testable) ----------


def _sorted_pmids(seed_pmids: Optional[Iterable]) -> list[int]:
    """Int-coerce, de-dup, and sort — the membership sidecar's posture
    (``bundler.py::_index_seed_pmids``), so the stored list is order-stable."""
    return sorted({int(p) for p in (seed_pmids or [])})


def build_subtopic_id_record(
    *,
    durable_id: str,
    slug_id: str,
    topic_id: Optional[str],
    seed_pmids: Optional[Iterable],
    label_at_mint: str,
    taxonomy_version: str,
    hierarchy_version: str,
    created_at: str,
    first_run_id: Optional[str],
    last_seen_run_id: Optional[str],
    status: str = STATUS_ACTIVE,
    membership_kind: str = MEMBERSHIP_KIND,
) -> dict[str, Any]:
    """Build the primary ``SUBTOPIC_ID#`` row dict. No I/O.

    ``seed_pmids`` are stored as ``int`` (the boto3 Table resource serializes
    int -> Number natively; only ``float`` must become ``Decimal``). The dict is
    byte-stable under ``json.dumps(..., sort_keys=True)`` for fixed inputs.

    ``membership_kind`` is stamped from ``bundler.MEMBERSHIP_KIND`` so downstream
    never mistakes discovery-seed overlap for full-assignment overlap.

    Inline ``seed_pmids`` assumes the **bounded discovery-seed** kind (largest
    observed ~86 PMIDs -> ~1-2KB items, far under DynamoDB's 400KB limit). If a
    later increment promotes this to *full-assignment* membership (potentially
    thousands of PMIDs), move membership to overflow rows
    (``SUBTOPIC_ID#{id} / MEMBER#{chunk}``) rather than inlining.
    """
    return {
        "PK": f"{SUBTOPIC_ID_PK_PREFIX}{durable_id}",
        "SK": META_SK,
        "record_type": RECORD_TYPE_SUBTOPIC_ID,
        "durable_id": durable_id,
        # --- mutable: refreshed on every reconcile ---
        "slug_id": slug_id,
        "topic_id": topic_id,
        "seed_pmids": _sorted_pmids(seed_pmids),
        "membership_kind": membership_kind,
        "taxonomy_version": taxonomy_version,
        "hierarchy_version": hierarchy_version,
        "last_seen_run_id": last_seen_run_id,
        # --- owned by brick C; brick A leaves the default ---
        "status": status,
        # --- mint-once: never rewritten after creation ---
        "label_at_mint": label_at_mint,
        "created_at": created_at,
        "first_run_id": first_run_id,
    }


def build_slug_pointer_record(
    *, slug_id: str, durable_id: str, created_at: str
) -> dict[str, Any]:
    """Build the ``SUBTOPIC_SLUG#`` pointer row dict (slug -> durable). No I/O."""
    return {
        "PK": f"{SUBTOPIC_SLUG_PK_PREFIX}{slug_id}",
        "SK": PTR_SK,
        "record_type": RECORD_TYPE_SUBTOPIC_SLUG,
        "slug_id": slug_id,
        "durable_id": durable_id,
        "created_at": created_at,
    }


# ---------- read API (boto3 Table resource: GetItem) ----------


def resolve_durable_id(table: Any, *, slug_id: str) -> Optional[str]:
    """The slug -> durable lookup (brick A's exact-slug match). None if unminted."""
    resp = table.get_item(
        Key={"PK": f"{SUBTOPIC_SLUG_PK_PREFIX}{slug_id}", "SK": PTR_SK}
    )
    item = (resp or {}).get("Item")
    return item.get("durable_id") if item else None


def get_subtopic_row(table: Any, *, durable_id: str) -> Optional[dict]:
    """The durable -> primary-row lookup (collision check + provenance read).

    Returns the raw item as DynamoDB stores it. NOTE: boto3's Table resource
    reads numbers back as ``Decimal``, so ``seed_pmids`` here are ``Decimal``,
    not ``int``. Brick A never compares/serializes them on a read path, so this
    is safe today; consumers that need ``int`` (brick B's membership-overlap
    reconcile) should use ``read_subtopic_row`` below.
    """
    resp = table.get_item(
        Key={"PK": f"{SUBTOPIC_ID_PK_PREFIX}{durable_id}", "SK": META_SK}
    )
    return (resp or {}).get("Item") or None


def read_subtopic_row(table: Any, *, durable_id: str) -> Optional[dict]:
    """``get_subtopic_row`` with ``seed_pmids`` int-coerced on the way out.

    The read-side mirror of ``_sorted_pmids``: closes the ``Decimal`` foot-gun
    before brick B (which reads membership back to compute overlap) is written.
    ``Decimal(30) == 30`` holds, but ``isinstance(p, int)``, ``json.dumps``
    without a Decimal encoder, and float arithmetic do not — so coerce once here.
    """
    row = get_subtopic_row(table, durable_id=durable_id)
    if row is not None and "seed_pmids" in row:
        row = {**row, "seed_pmids": _sorted_pmids(row.get("seed_pmids"))}
    return row


# ---------- thin writers (boto3 Table resource: PutItem) ----------


def write_subtopic_id(table: Any, record: dict) -> None:
    table.put_item(Item=record)


def write_slug_pointer(table: Any, record: dict) -> None:
    table.put_item(Item=record)


# ---------- the store ----------


class SubtopicIdStore:
    """Match-or-mint over the durable id<->membership store.

    ``match_or_mint`` is matcher-agnostic: it branches solely on whether
    ``match()`` returned a hit. Brick B swaps the body of ``match()`` and nothing
    else in this class changes.
    """

    def __init__(self, table: Any, minter: Optional[SubtopicIdMinter] = None):
        self._table = table
        self._minter = minter or SubtopicIdMinter()

    def match(
        self, *, slug: str, membership: Iterable, topic_id: Optional[str]
    ) -> Optional[SubtopicMatch]:
        """BRICK A: exact-slug equality ONLY — the seam brick B will replace.

        ``membership``/``topic_id`` are accepted now (and ignored here) so brick
        B's deterministic-first reconcile slots in without a signature change.
        """
        durable = resolve_durable_id(self._table, slug_id=slug)
        if durable is None:
            return None
        return SubtopicMatch(durable_id=durable, score=1.0, reason="exact_slug")

    def match_or_mint(
        self,
        *,
        slug: str,
        membership: Iterable,
        topic_id: Optional[str],
        label: str,
        ctx: MintContext,
    ) -> tuple[str, str]:
        """Resolve a published subtopic to a durable id. Returns ``(durable_id, action)``.

        ``action`` is ``"attached"`` (matched an existing id; mutable fields
        refreshed, mint-once provenance preserved) or ``"minted"`` (no match; a
        fresh opaque id created with a slug pointer).
        """
        hit = self.match(slug=slug, membership=membership, topic_id=topic_id)  # <- the ONLY line brick B changes
        if hit is not None:
            existing = get_subtopic_row(self._table, durable_id=hit.durable_id)
            if existing is not None:
                self._attach(
                    hit.durable_id,
                    existing=existing,
                    slug=slug,
                    membership=membership,
                    topic_id=topic_id,
                    ctx=ctx,
                )
                return hit.durable_id, "attached"
            # Dangling pointer: a slug pointer resolved to a durable id that has
            # no primary row. Brick A's own writes never produce this (mint writes
            # the META row BEFORE the pointer), so it implies external corruption
            # or a future brick's row deletion. Re-mint an honest, fully-
            # provenanced row instead of fabricating provenance from empty
            # defaults; the re-mint overwrites the stale pointer, self-healing it.
            _log.warning(
                "dangling slug pointer %r -> %r (no primary row); re-minting",
                slug,
                hit.durable_id,
            )
        return self._mint(
            slug=slug, membership=membership, topic_id=topic_id, label=label, ctx=ctx
        )

    def _mint(
        self,
        *,
        slug: str,
        membership: Iterable,
        topic_id: Optional[str],
        label: str,
        ctx: MintContext,
    ) -> tuple[str, str]:
        durable = self._minter.mint(
            exists=lambda s: get_subtopic_row(self._table, durable_id=s) is not None
        )
        # META row FIRST, pointer SECOND. If the process dies between the two
        # writes, the orphan META is still visible to the collision guard above,
        # so the id is never reused for a different slug — the partial-write
        # failure mode is an over-mint (safe, recoverable by a later brick),
        # never a false match. (A single-transaction two-row write is deferred;
        # unnecessary for brick A's serial, best-effort, ~1,500-row path.)
        write_subtopic_id(
            self._table,
            build_subtopic_id_record(
                durable_id=durable,
                slug_id=slug,
                topic_id=topic_id,
                seed_pmids=membership,
                label_at_mint=label,
                taxonomy_version=ctx.taxonomy_version,
                hierarchy_version=ctx.hierarchy_version,
                created_at=ctx.created_at,
                first_run_id=ctx.run_id,
                last_seen_run_id=ctx.run_id,
            ),
        )
        write_slug_pointer(
            self._table,
            build_slug_pointer_record(
                slug_id=slug, durable_id=durable, created_at=ctx.created_at
            ),
        )
        return durable, "minted"

    def _attach(
        self,
        durable_id: str,
        *,
        existing: dict,
        slug: str,
        membership: Iterable,
        topic_id: Optional[str],
        ctx: MintContext,
    ) -> None:
        """Refresh the mutable fields (membership/topic/version/last-seen),
        preserving the mint-once provenance (durable_id, created_at, first_run_id,
        label_at_mint, status).

        Additive overwrite — ``{**existing, **refreshed}`` — so any column a later
        brick (C lineage, an ops backfill) persists onto the row that is NOT in
        ``build_subtopic_id_record``'s output survives an attach; only the keys
        that builder emits are refreshed. Simpler to fake/assert than an
        UpdateItem expression and negligible at ~1,500 rows."""
        refreshed = build_subtopic_id_record(
            durable_id=durable_id,
            slug_id=slug,
            topic_id=topic_id,
            seed_pmids=membership,
            label_at_mint=existing.get("label_at_mint", ""),
            taxonomy_version=ctx.taxonomy_version,
            hierarchy_version=ctx.hierarchy_version,
            created_at=existing.get("created_at", ctx.created_at),
            first_run_id=existing.get("first_run_id", ctx.run_id),
            last_seen_run_id=ctx.run_id,
            status=existing.get("status", STATUS_ACTIVE),
        )
        write_subtopic_id(self._table, {**existing, **refreshed})


def reconcile_durable_ids(
    store: SubtopicIdStore,
    *,
    membership: dict,
    hierarchy: dict,
    ctx: MintContext,
) -> dict[str, int]:
    """Promote one run's membership into the durable store. Returns a summary.

    Iterates ``membership["subtopics"]`` (guaranteed 1:1 with the published
    hierarchy, ``bundler.build_membership``) and runs exact-slug match-or-mint per
    subtopic. ``hierarchy`` supplies ``label`` for the mint-time provenance
    snapshot (``label`` is in ``bundler._SUBTOPIC_REQUIRED``). A subtopic with no
    seeds still gets a durable id (membership ``[]``).
    """
    labels = {
        s.get("id"): s.get("label", "")
        for t in (hierarchy.get("topics") or {}).values()
        for s in t.get("subtopics", [])
    }
    minted = attached = 0
    subtopics = membership.get("subtopics") or {}
    for slug, entry in subtopics.items():
        _durable, action = store.match_or_mint(
            slug=slug,
            membership=entry.get("seed_pmids") or [],
            topic_id=entry.get("topic_id"),
            label=labels.get(slug, ""),
            ctx=ctx,
        )
        if action == "minted":
            minted += 1
        elif action == "attached":
            attached += 1
    return {
        "subtopic_count": len(subtopics),
        "minted": minted,
        "attached": attached,
    }
