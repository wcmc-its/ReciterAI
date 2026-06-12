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

# Default lifecycle status. Brick F (retire policy) owns transitions
# (demote/archive/retire); bricks A–C only ever write/preserve the default. Brick C
# records split/merge *lineage edges* (split_from/merged_into) but never mutates status.
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


@dataclass(frozen=True)
class SubtopicDefer:
    """Brick B's third verdict: the reconcile could not confidently match the new
    cluster to any prior id, so it is held as ambiguous and minted (conservative —
    over-mint is recoverable, a false attach is not). Deliberately NOT a
    ``SubtopicMatch``, so it can never satisfy ``match_or_mint``'s
    ``isinstance(verdict, SubtopicMatch)`` attach branch. ``candidates`` records the
    prior ids it was torn between, for the brick-C lineage pass / audit."""

    reason: str = "ambiguous"
    candidates: tuple = ()


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
        # --- status: brick F owns transitions; A–C leave the default. split_from /
        #     merged_into (brick C lineage edges) are written post-reconcile by
        #     set_lineage and survive an attach via the additive {**existing,**refreshed}
        #     merge — deliberately NOT emitted here, so a refresh can never clobber them. ---
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


# Brick D (#191): the published slug->durable alias/redirect map artifact.
ALIAS_SCHEMA_VERSION = "1.0.0"


def build_alias_map(
    snapshot: dict[str, dict],
    *,
    hierarchy_version: Optional[str],
    taxonomy_version: Optional[str],
) -> dict[str, Any]:
    """Build the published slug->durable alias map (#191 brick D). Pure (no I/O).

    Inverts a durable-ID store ``snapshot`` (``{durable_id: {slug_id, topic_id,
    status, ...}}`` from ``load_id_store_snapshot``) into ``{slug_id: {durable_id,
    parent_topic_id, status}}`` so SPS can migrate its slug-keyed ``Subtopic`` rows +
    deep-link join onto durable ids and 301-redirect old slugs (its new
    ``SubtopicAlias`` table consumes this). Byte-stable under
    ``json.dumps(..., sort_keys=True, ensure_ascii=False)``; aliases are emitted in
    slug order. Rows with no ``slug_id`` are skipped.

    Reflects the durable store as of the START of a publish (the reconcile that
    assigns ids to a run's *new* slugs runs at publish step 10, after upload); a
    subtopic first minted in this run appears in the next publish's alias map — correct
    for a redirect map, since a brand-new subtopic has no prior slug to alias.
    """
    aliases: dict[str, dict] = {}
    for durable_id, row in snapshot.items():
        slug = row.get("slug_id")
        if not slug:
            continue
        aliases[slug] = {
            "durable_id": durable_id,
            "parent_topic_id": row.get("topic_id"),
            "status": row.get("status") or STATUS_ACTIVE,
        }
    return {
        "alias_schema_version": ALIAS_SCHEMA_VERSION,
        "taxonomy_version": taxonomy_version,
        "hierarchy_version": hierarchy_version,
        "subtopic_count": len(aliases),
        "aliases": {slug: aliases[slug] for slug in sorted(aliases)},
    }


# ---------- read API (boto3 Table resource: GetItem) ----------


def resolve_durable_id(table: Any, *, slug_id: str) -> Optional[str]:
    """The slug -> durable lookup (brick A's exact-slug match). None if unminted."""
    resp = table.get_item(
        Key={"PK": f"{SUBTOPIC_SLUG_PK_PREFIX}{slug_id}", "SK": PTR_SK}
    )
    item = (resp or {}).get("Item")
    return item.get("durable_id") if item else None


def load_slug_pointer_map(table: Any) -> dict[str, str]:
    """Scan every ``SUBTOPIC_SLUG#`` pointer row ONCE into a ``{slug_id: durable_id}`` map.

    The bulk companion to ``resolve_durable_id`` (a single-slug GetItem): one
    paginated Scan yields the whole slug->durable mapping for an O(1)-lookup
    migration. Keyed by *slug*, so — unlike ``build_alias_map``, which projects the
    store by each durable's *current* ``slug_id`` — it still resolves a slug a later
    reconcile relabeled (the original mint always wrote that slug's pointer, and
    ``_attach`` never rewrites it). That makes it the right source for re-keying
    historical, slug-keyed rows (#191 brick D,
    ``scripts/migrate_spotlight_history_pk.py``).

    Mirrors ``load_id_store_snapshot``'s pagination idiom: filter on the
    ``SUBTOPIC_SLUG#`` PK prefix + ``PTR`` SK, and terminate on an absent
    ``LastEvaluatedKey`` via a membership test (not truthiness), so a mock table
    whose ``.get`` returns a truthy sentinel cannot spin the loop forever.
    """
    from boto3.dynamodb.conditions import Attr

    pointers: dict[str, str] = {}
    scan_kwargs = {
        "FilterExpression": Attr("PK").begins_with(SUBTOPIC_SLUG_PK_PREFIX)
        & Attr("SK").eq(PTR_SK)
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            # Guard on record_type, not just the server-side filter: ``SUBTOPIC_ID#``
            # META rows also carry slug_id + durable_id, so a scan that ever
            # over-returns must not leak a META row in as a (wrong-direction) pointer.
            if item.get("record_type") != RECORD_TYPE_SUBTOPIC_SLUG:
                continue
            slug = item.get("slug_id")
            durable = item.get("durable_id")
            if slug and durable:
                pointers[slug] = durable
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return pointers


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


def load_lineage_overlay(table: Any) -> dict[str, list]:
    """Resolve durable-id split/merge edges into the slug-space lineage overlay
    ``compute_structural_diff`` consumes (#191 brick D). One Scan of ``SUBTOPIC_ID#``/META.

    Brick C writes lineage as *durable-id* edges (``split_from`` on a split child,
    ``merged_into`` on an absorbed prior — both durable ids). The diff overlay is
    *slug-space*, so each edge endpoint is resolved back to its slug via the same scan:
        {"split":  [{"id": <child slug>,  "split_from":  <parent slug>}, ...],
         "merged": [{"id": <prior slug>,  "merged_into": <successor slug>}, ...]}
    An edge whose endpoint durable has no live slug (dangling) is skipped — a durable id
    must never leak into a slug-space field. Both lists are id-sorted so the published
    ``diff.json`` bytes stay stable.

    Reflects lineage SETTLED in the store as of the scan — i.e. PRIOR runs' reconcile.
    This run's brand-new edges are written post-upload (publish step 10), so they appear
    in the NEXT publish's diff: the accepted one-run lag that keeps the reconcile
    best-effort/post-upload (no Bedrock in the publish critical path).
    """
    from boto3.dynamodb.conditions import Attr

    rows: list[dict] = []
    scan_kwargs = {
        "FilterExpression": Attr("PK").begins_with(SUBTOPIC_ID_PK_PREFIX)
        & Attr("SK").eq(META_SK)
    }
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # durable -> slug, over the same scan, so both edge endpoints resolve to slug space.
    durable_to_slug = {
        r.get("durable_id"): r.get("slug_id")
        for r in rows
        if r.get("record_type") == RECORD_TYPE_SUBTOPIC_ID
        and r.get("durable_id")
        and r.get("slug_id")
    }
    split: list[dict] = []
    merged: list[dict] = []
    for r in rows:
        if r.get("record_type") != RECORD_TYPE_SUBTOPIC_ID:
            continue
        slug = r.get("slug_id")
        if not slug:
            continue
        parent = durable_to_slug.get(r.get("split_from")) if r.get("split_from") else None
        if parent:
            split.append({"id": slug, "split_from": parent})
        succ = durable_to_slug.get(r.get("merged_into")) if r.get("merged_into") else None
        if succ:
            merged.append({"id": slug, "merged_into": succ})
    split.sort(key=lambda e: (e["id"], e["split_from"]))
    merged.sort(key=lambda e: (e["id"], e["merged_into"]))
    return {"split": split, "merged": merged}


# ---------- thin writers (boto3 Table resource: PutItem) ----------


def write_subtopic_id(table: Any, record: dict) -> None:
    table.put_item(Item=record)


def write_slug_pointer(table: Any, record: dict) -> None:
    table.put_item(Item=record)


def set_lineage(
    table: Any,
    *,
    durable_id: str,
    split_from: Optional[str] = None,
    merged_into: Optional[str] = None,
) -> bool:
    """Brick C: write a split/merge lineage edge onto an EXISTING primary row.

    Read-modify-PutItem that sets only the provided, *changed* edge(s) and preserves
    every other field — the additive seam ``_attach`` already relies on (a column not
    emitted by ``build_subtopic_id_record`` survives an attach). NEVER touches
    ``status``: lifecycle transitions are brick F. Idempotent — returns ``False`` (no
    write) when the row is absent or the edge already matches, so a content-identical
    republish on the skip path re-writes nothing. Edges are ``durable_id`` strings, so
    the only Decimal concern (``seed_pmids``) is closed by ``read_subtopic_row``.
    """
    row = read_subtopic_row(table, durable_id=durable_id)
    if row is None:
        return False
    patch: dict[str, Any] = {}
    if split_from is not None and row.get("split_from") != split_from:
        patch["split_from"] = split_from
    if merged_into is not None and row.get("merged_into") != merged_into:
        patch["merged_into"] = merged_into
    if not patch:
        return False
    write_subtopic_id(table, {**row, **patch})
    return True


# ---------- the store ----------


class SubtopicIdStore:
    """Match-or-mint over the durable id<->membership store.

    ``match_or_mint`` is matcher-agnostic: it branches on the verdict TYPE
    (``SubtopicMatch`` -> attach, ``SubtopicDefer`` / ``None`` -> mint). The
    matching policy lives entirely in ``match()``:
      - no ``reconciler`` (brick A) -> exact-slug equality;
      - a ``reconciler`` (brick B) -> the 3-stage deterministic-first reconcile,
        delegated to ``SubtopicReconciler.match`` (which reads a pre-run snapshot).
    """

    def __init__(
        self,
        table: Any,
        minter: Optional[SubtopicIdMinter] = None,
        reconciler: Optional[Any] = None,
    ):
        self._table = table
        self._minter = minter or SubtopicIdMinter()
        self._reconciler = reconciler

    def match(self, *, slug: str, membership: Iterable, topic_id: Optional[str]):
        """Resolve a slug to a verdict: ``SubtopicMatch`` (attach), ``SubtopicDefer``
        (held -> mint), or ``None`` (no candidate -> mint).

        Brick B injects a ``reconciler`` and ``match()`` delegates to it. Brick A's
        exact-slug behavior is preserved when no reconciler is present, so brick-A
        callers/tests are unaffected.
        """
        if self._reconciler is not None:
            return self._reconciler.match(slug=slug, membership=membership, topic_id=topic_id)
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
        verdict = self.match(slug=slug, membership=membership, topic_id=topic_id)  # exact-slug (A) or reconcile (B)
        if isinstance(verdict, SubtopicMatch):
            existing = get_subtopic_row(self._table, durable_id=verdict.durable_id)
            if existing is not None:
                self._attach(
                    verdict.durable_id,
                    existing=existing,
                    slug=slug,
                    membership=membership,
                    topic_id=topic_id,
                    ctx=ctx,
                )
                return verdict.durable_id, "attached"
            # Matched a durable id with no primary row. For brick A this is a
            # dangling slug pointer; for brick B a snapshot id deleted mid-run.
            # Brick A's mint writes the META row BEFORE the pointer, so its own
            # writes never produce this — it implies external corruption or a
            # future brick's deletion. Re-mint an honest, fully-provenanced row
            # rather than fabricating provenance from empty defaults.
            _log.warning(
                "matched durable id %r for slug %r has no primary row; re-minting",
                verdict.durable_id,
                slug,
            )
        # SubtopicDefer (brick B held-as-ambiguous) or None (no candidate) -> mint.
        # Conservative substrate: over-mint is recoverable; a false attach is not.
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
    reconciler: Optional[Any] = None,
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
    subtopics = membership.get("subtopics") or {}
    # Brick B: resolve the whole conflict-free assignment ONCE, before the mint
    # loop, off a pre-run snapshot — so a later sibling can't match an id minted
    # earlier in the same pass (brick A's documented intra-run self-match guard).
    if reconciler is not None:
        reconciler.precompute(membership=membership, labels=labels)
    minted = attached = 0
    # Capture slug -> minted/attached durable id so the brick-C lineage pass can turn
    # the reconciler's slug-keyed split decisions into durable-id edges (the durable id
    # for a minted-from-split child only exists after its match_or_mint runs).
    slug_to_durable: dict[str, str] = {}
    for slug, entry in subtopics.items():
        durable, action = store.match_or_mint(
            slug=slug,
            membership=entry.get("seed_pmids") or [],
            topic_id=entry.get("topic_id"),
            label=labels.get(slug, ""),
            ctx=ctx,
        )
        slug_to_durable[slug] = durable
        if action == "minted":
            minted += 1
        elif action == "attached":
            attached += 1
    summary = {
        "subtopic_count": len(subtopics),
        "minted": minted,
        "attached": attached,
    }
    # Per-decision reason breakdown (overlap/centroid/llm/deferred) for the audit
    # diff. Authoritative minted/attached come from the actions above; "reasons" is
    # the reconciler's view of HOW each match was made.
    if reconciler is not None and getattr(reconciler, "tally", None):
        summary["reasons"] = dict(reconciler.tally)
    # Brick-C seam (primitive splits + merge-absorbed priors), surfaced as counts;
    # the full maps live on the reconciler (split_parents / unclaimed_priors).
    if reconciler is not None:
        summary["split_losers"] = len(getattr(reconciler, "split_parents", {}) or {})
        summary["unclaimed_priors"] = len(getattr(reconciler, "unclaimed_priors", ()) or ())
        # Brick C: persist the split/merge edges B's reconcile already decided.
        summary.update(_write_lineage(store._table, reconciler, slug_to_durable))
    return summary


def _write_lineage(
    table: Any,
    reconciler: Any,
    slug_to_durable: dict[str, str],
) -> dict[str, int]:
    """Brick C: persist the split/merge lineage edges brick B already decided.

    Reads B's ACTUAL decisions off the reconciler (never replays the greedy claim
    order):
      - ``split_parents`` {minted slug -> contested prior id}: the minting loser
        inherits ``split_from`` pointing at the prior a stronger sibling claimed.
      - ``unclaimed_priors`` absorbed into a successor (>= the merge bar) get a
        ``merged_into`` redirect; the rest are left untouched — brick F owns
        demote/retire and the ``status`` transition.

    Conservative substrate, two guards:
      - mutual exclusion: ``split_parents`` values are by construction *claimed*
        priors (``_note_split_loser`` only records a parent that is already in
        ``claimed``), and ``unclaimed_priors = snapshot - claimed``, so the two sets
        are disjoint today. Subtracting the split parents from the merge set is cheap
        insurance that stays correct if that invariant ever changes;
      - dangling-parent skip: a ``split_from`` whose parent has no live row (the
        re-mint path) is skipped rather than written as a broken edge.

    Best-effort by placement: the caller runs after the artifact is live and swallows
    failures, so a partial lineage write never fails a publish."""
    split_parents = getattr(reconciler, "split_parents", {}) or {}
    unclaimed = getattr(reconciler, "unclaimed_priors", set()) or set()
    split_written = merged_written = 0
    # Splits: each minting loser -> split_from(the contested prior it lost).
    for minted_slug, parent_id in sorted(split_parents.items()):
        child = slug_to_durable.get(minted_slug)
        if not child:
            continue
        if get_subtopic_row(table, durable_id=parent_id) is None:
            continue  # dangling parent (re-mint path) — skip, don't write a broken edge
        if set_lineage(table, durable_id=child, split_from=parent_id):
            split_written += 1
    # Merges: an unclaimed prior absorbed into a successor -> merged_into(successor).
    merge_candidates = unclaimed - set(split_parents.values())
    for prior_id in sorted(merge_candidates):
        best = (
            reconciler.best_successor_overlap(prior_id)
            if hasattr(reconciler, "best_successor_overlap")
            else None
        )
        if not best:
            continue  # quiet/demote candidate -> brick F (no status mutation here)
        succ_slug, _frac = best
        succ_durable = slug_to_durable.get(succ_slug)
        if not succ_durable:
            continue
        if set_lineage(table, durable_id=prior_id, merged_into=succ_durable):
            merged_written += 1
    return {"split_from_written": split_written, "merged_into_written": merged_written}
