"""Brick F PR-1 (#191): the forward-only lifecycle SUBSTRATE — data capture only.

Brick F is the #191 capstone: a self-sustaining taxonomy that auto-MINTS new
clusters (volume floor + two-run persistence) and auto-RETIRES quiet ones
(active -> demoted -> archived, reversible). It ships as TWO PRs. THIS module is
PR-1: the per-run data capture PR-2's policy will read. It writes COUNTERS; it
NEVER acts on them.

What PR-1 captures (additive, flag-off by default):
  - a per-run QUIET-SINCE sweep that stamps, on each durable-id store row,
    ``consecutive_quiet_runs`` (++ when the id is UNCLAIMED this run, reset to 0 when
    CLAIMED) and ``last_active_hierarchy_version`` (the version it was last claimed);
  - CANDIDATE-STAGING rows (``SUBTOPIC_CANDIDATE#{slug}`` / META) accruing
    ``seen_runs`` for each freshly-discovered, mint-bound cluster that clears the
    paper floor, plus a GC pass that deletes candidate rows not seen this run (the
    burst filter).

Hard invariants a reviewer will check:
  1. FORWARD-ONLY honesty. The counters START AT 0 and accrue; there is NO
     retroactive backfill. A row's quiet count means "runs since this module first
     observed it unclaimed", not "runs since it was last truly active".
  2. NO status transitions. Every row stays ``status='active'``. ``set_quiet_substrate``
     can only ever write ``consecutive_quiet_runs`` / ``last_active_hierarchy_version``.
     Demote/archive/un-demote — the status writes — are PR-2.
  3. NO mint-gating. ``stage_candidates`` / ``gc_candidates`` run AFTER the reconcile
     has ALREADY persisted every mint; they only READ which slugs minted and WRITE
     counter rows. Minting proceeds EXACTLY as today — nothing here holds, defers, or
     blocks a mint. PR-2 is what will later READ ``seen_runs`` to enforce two-run
     persistence.
  4. Candidate identity key = the cluster SLUG for v1. A new area's slug is stable
     across two adjacent runs; if it churns, the candidate count resets and PR-2's
     mint just waits a cycle (fails SAFE). Membership-signature keying is the
     documented fallback, NOT built here.
  5. Quiet is counted in RUNS, cadence-AGNOSTIC. ``last_active_hierarchy_version`` is
     stamped only for audit / a future wall-clock reinterpretation; it is never used
     for arithmetic (the run id is a random uuid4, non-orderable).

FLAG-OFF byte-identity: when ``substrate_enabled`` is false (the default), every
pass here is a no-op — ZERO new fields on any SUBTOPIC_ID# row, ZERO
SUBTOPIC_CANDIDATE# rows, ZERO deletes — so the store rows AND ``aliases.json`` stay
byte-identical to today (the substrate fields are deliberately NOT emitted by
``build_subtopic_id_record``). Best-effort placement: these passes run in publish's
post-upload step-10 helper, inside the broad ``except`` that already swallows reconcile
failures, so any substrate error is logged and never fails a publish.

Mirrors the shape of ``subtopic_reconcile.py`` / ``relabel_skip.py``: a frozen config
dataclass (``LifecyclePolicy``) + pure passes + thin DDB callers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from boto3.dynamodb.conditions import Attr

from pipeline_hierarchy.bundler import DEFAULT_THRESHOLDS_PATH
from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    RECORD_TYPE_SUBTOPIC_CANDIDATE,
    SUBTOPIC_CANDIDATE_PK_PREFIX,
    build_candidate_record,
    set_quiet_substrate,
    write_subtopic_id,
)

_log = logging.getLogger(__name__)

# Floor used when fail-soft-disabling (the value is inert while disabled, but the
# dataclass needs an int; mirror the config default).
_DEFAULT_MINT_FLOOR_PAPERS = 5


@dataclass(frozen=True)
class LifecyclePolicy:
    """Brick F PR-1 config. Holds ONLY the two fields PR-1 consumes; PR-2 extends it
    with the retire/persistence thresholds the gates need. Keeping it to two fields is
    the structural signal that PR-1 cannot act on the substrate it captures."""

    substrate_enabled: bool
    mint_floor_papers: int

    @classmethod
    def from_config(cls, thresholds_path=DEFAULT_THRESHOLDS_PATH) -> "LifecyclePolicy":
        """Read ``config/thresholds.json``.

        FAIL-SOFT to disabled when ``subtopic_lifecycle_substrate_enabled`` is ABSENT
        (a config predating this PR still loads, inert). FAIL-LOUD (``ValueError``,
        mirroring ``ReconcileThresholds.from_config``) only when the flag is
        present-and-true but ``subtopic_lifecycle_mint_floor_papers`` is missing /
        non-int / < 1 (enabled-but-misconfigured). The flag must be a JSON bool — a
        coerced ``bool('false')`` would silently ENABLE the substrate (mirror the
        ``llm_arbiter_enabled`` type guard)."""
        with open(thresholds_path) as f:
            cfg = json.load(f)

        raw = cfg.get("subtopic_lifecycle_substrate_enabled")
        if raw is None:
            # Absent -> inert. No raise; the substrate simply stays off.
            return cls(substrate_enabled=False, mint_floor_papers=_DEFAULT_MINT_FLOOR_PAPERS)
        if not isinstance(raw, bool):
            raise ValueError(
                f"{thresholds_path}: subtopic_lifecycle_substrate_enabled must be a JSON "
                f"boolean (true/false), got {type(raw).__name__} {raw!r}"
            )
        if not raw:
            # Present-false -> read the floor opportunistically but stay inert; a bad
            # floor while disabled is not fatal (the substrate never reads it).
            floor_raw = cfg.get("subtopic_lifecycle_mint_floor_papers")
            floor = floor_raw if isinstance(floor_raw, int) and not isinstance(floor_raw, bool) else _DEFAULT_MINT_FLOOR_PAPERS
            return cls(substrate_enabled=False, mint_floor_papers=floor)
        # Enabled -> the floor is load-bearing; fail loud on a bad value.
        floor_raw = cfg.get("subtopic_lifecycle_mint_floor_papers")
        if not isinstance(floor_raw, int) or isinstance(floor_raw, bool) or floor_raw < 1:
            raise ValueError(
                f"{thresholds_path}: subtopic_lifecycle_mint_floor_papers must be an integer "
                f">= 1 when the substrate is enabled, got {floor_raw!r}"
            )
        return cls(substrate_enabled=True, mint_floor_papers=floor_raw)


# ---------- quiet-since sweep (++ on unclaimed, reset+stamp on claimed) ----------


def sweep_quiet_substrate(
    table,
    *,
    claimed_ids: set,
    all_prior_ids: set,
    hierarchy_version: str,
    policy: LifecyclePolicy,
) -> dict:
    """Stamp the forward-only quiet counter on every prior durable-id row. CAPTURE
    ONLY — no status change, no policy action.

    For each prior id: if CLAIMED this run, reset ``consecutive_quiet_runs`` to 0 and
    stamp ``last_active_hierarchy_version``; if UNCLAIMED (in ``all_prior_ids`` but not
    ``claimed_ids``), ``++consecutive_quiet_runs``. No-op (returns ``{'enabled': False}``)
    when the substrate is disabled. Iterates ``sorted(all_prior_ids)`` for determinism.
    Returns an audit summary."""
    if not policy.substrate_enabled:
        return {"enabled": False}
    quiet_incremented = 0
    rows_written = 0
    for durable_id in sorted(all_prior_ids):
        if durable_id in claimed_ids:
            wrote = set_quiet_substrate(
                table,
                durable_id=durable_id,
                reset=True,
                last_active_hierarchy_version=hierarchy_version,
            )
        else:
            wrote = set_quiet_substrate(table, durable_id=durable_id, reset=False)
            quiet_incremented += 1
        if wrote:
            rows_written += 1
    return {
        "enabled": True,
        "claimed": len(claimed_ids),
        "quiet_incremented": quiet_incremented,
        "rows_written": rows_written,
    }


# ---------- candidate staging (++seen_runs for mint-bound clusters >= floor) ----------


def upsert_candidate(table, *, slug: str, n_papers: int, hierarchy_version: str) -> bool:
    """Read-modify-put on ``SUBTOPIC_CANDIDATE#{slug}`` / META: create with
    ``seen_runs=1`` (``first_seen`` = ``last_seen`` = this version) or ++``seen_runs`` on
    an existing row, refreshing ``last_seen_hierarchy_version`` + ``n_papers_last`` and
    PRESERVING ``first_seen_hierarchy_version``. Returns True (it always writes)."""
    resp = table.get_item(Key={"PK": f"{SUBTOPIC_CANDIDATE_PK_PREFIX}{slug}", "SK": META_SK})
    existing = (resp or {}).get("Item")
    if existing is None:
        seen_runs = 1
        first_seen = hierarchy_version
    else:
        seen_runs = int(existing.get("seen_runs") or 0) + 1
        first_seen = existing.get("first_seen_hierarchy_version") or hierarchy_version
    write_subtopic_id(
        table,
        build_candidate_record(
            slug=slug,
            seen_runs=seen_runs,
            first_seen_hierarchy_version=first_seen,
            last_seen_hierarchy_version=hierarchy_version,
            n_papers_last=n_papers,
        ),
    )
    return True


def stage_candidates(
    table,
    *,
    mint_bound: list,
    hierarchy_version: str,
    policy: LifecyclePolicy,
) -> dict:
    """Accrue ``seen_runs`` for the mint-bound clusters that clear the paper floor.

    ``mint_bound`` is a list of ``{'slug': str, 'n_papers': int}`` for the clusters the
    reconcile WILL mint this run (no prior match). For each whose ``n_papers >=
    policy.mint_floor_papers``, upsert its candidate row. No-op (returns
    ``{'enabled': False, 'seen_slugs': set()}``) when disabled. Does NOT gate, hold, or
    block any mint — purely records. Returns an audit summary plus ``seen_slugs`` (the
    slugs that cleared the floor and were upserted, for the GC burst filter)."""
    if not policy.substrate_enabled:
        return {"enabled": False, "seen_slugs": set()}
    seen_slugs: set = set()
    below_floor = 0
    for entry in mint_bound:
        slug = entry.get("slug")
        n_papers = int(entry.get("n_papers") or 0)
        if not slug:
            continue
        if n_papers < policy.mint_floor_papers:
            below_floor += 1
            continue
        upsert_candidate(table, slug=slug, n_papers=n_papers, hierarchy_version=hierarchy_version)
        seen_slugs.add(slug)
    return {
        "enabled": True,
        "staged": len(seen_slugs),
        "below_floor": below_floor,
        "seen_slugs": seen_slugs,
    }


# ---------- GC (the burst filter: delete candidates not seen this run) ----------


def _scan_candidate_slugs(table) -> dict:
    """Paginated Scan over PK begins_with ``SUBTOPIC_CANDIDATE#`` / SK == META into
    ``{slug: row}``. Guards on ``record_type == RECORD_TYPE_SUBTOPIC_CANDIDATE`` so an
    over-returning scan cannot leak a foreign row (mirrors ``load_id_store_snapshot``'s
    record_type guard + the ``not in`` LastEvaluatedKey pagination idiom)."""
    rows: dict = {}
    scan_kwargs = {
        "FilterExpression": Attr("PK").begins_with(SUBTOPIC_CANDIDATE_PK_PREFIX)
        & Attr("SK").eq(META_SK)
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            if item.get("record_type") != RECORD_TYPE_SUBTOPIC_CANDIDATE:
                continue
            slug = item.get("slug")
            if slug:
                rows[slug] = item
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


def gc_candidates(table, *, seen_slugs: set, policy: LifecyclePolicy) -> int:
    """Delete every ``SUBTOPIC_CANDIDATE#`` row whose slug is NOT in ``seen_slugs``
    (the burst filter — a candidate that did not reappear this run is removed, so its
    count resets next time and a one-run blip can never accrue ``seen_runs >= 2``).
    No-op (returns 0) when disabled. Returns the count deleted."""
    if not policy.substrate_enabled:
        return 0
    deleted = 0
    for slug, row in sorted(_scan_candidate_slugs(table).items()):
        if slug in seen_slugs:
            continue
        table.delete_item(Key={"PK": row["PK"], "SK": META_SK})
        deleted += 1
    return deleted
