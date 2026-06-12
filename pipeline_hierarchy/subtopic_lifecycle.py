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
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DEMOTED,
    SUBTOPIC_CANDIDATE_PK_PREFIX,
    build_candidate_record,
    set_quiet_substrate,
    set_status,
    write_subtopic_id,
)

_log = logging.getLogger(__name__)

# Defaults used when fail-soft-disabling (inert while the owning flag is off, but the
# dataclass needs values; mirror the config defaults). PR-2 adds the persistence/retire
# thresholds.
_DEFAULT_MINT_FLOOR_PAPERS = 5
_DEFAULT_MINT_PERSISTENCE_RUNS = 2
_DEFAULT_DEMOTE_QUIET_RUNS = 12
_DEFAULT_ARCHIVE_QUIET_RUNS = 12

# Lifecycle rank for the monotone-forward retire guard: a quiet (unclaimed) row may only
# advance active -> demoted -> archived, never regress; the sole regression (-> active)
# is the claim-driven un-demote path, handled explicitly in sweep_retire.
_STATUS_RANK = {STATUS_ACTIVE: 0, STATUS_DEMOTED: 1, STATUS_ARCHIVED: 2}


def _read_bool_flag(cfg: dict, key: str, thresholds_path) -> bool:
    """Read a JSON-bool enable flag. ABSENT -> False (fail-soft, inert: a config
    predating the flag still loads). Present-but-not-a-JSON-bool -> ValueError (a
    ``"false"`` string must NOT coerce to enabled — mirror the ``llm_arbiter_enabled``
    type guard)."""
    raw = cfg.get(key)
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ValueError(
            f"{thresholds_path}: {key} must be a JSON boolean (true/false), "
            f"got {type(raw).__name__} {raw!r}"
        )
    return raw


def _read_positive_int(cfg, key, thresholds_path, *, required, default, why):
    """Read an int >= 1 threshold. When ``required`` (the owning flag is on) a missing /
    non-int / < 1 value is FATAL (ValueError, mirroring ``ReconcileThresholds``). When
    not required it is read opportunistically with ``default`` and never raises — the
    value is inert while the flag is off and a bad value must not block an unrelated
    publish."""
    raw = cfg.get(key)
    valid = isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1
    if required and not valid:
        raise ValueError(
            f"{thresholds_path}: {key} must be an integer >= 1 when {why}, got {raw!r}"
        )
    return raw if valid else default


@dataclass(frozen=True)
class LifecyclePolicy:
    """Brick F lifecycle config.

    PR-1 fields (capture): ``substrate_enabled``, ``mint_floor_papers``.
    PR-2 fields (act): ``policy_enabled`` + the persistence/retire thresholds. The PR-2
    fields carry DEFAULTS so a two-arg ``LifecyclePolicy(substrate_enabled=...,
    mint_floor_papers=...)`` construction (PR-1 call sites / tests) still works and a
    policy-off run is byte-identical to PR-1.

    TWO flags: ``substrate_enabled`` = capture (PR-1); ``policy_enabled`` = act (PR-2:
    mint-persistence gate + retire transitions). ``policy_enabled`` REQUIRES
    ``substrate_enabled`` (the retire sweep reads ``consecutive_quiet_runs``, which only
    accrues under the substrate flag) — enforced fail-loud in ``from_config``; the
    rollout flips the substrate first, lets it bake, then the policy.

    Quiet is counted in RUNS, cadence-agnostic. All PR-2 thresholds are PROVISIONAL,
    co-decided with #37 (annual=1 / frequent=2 mint persistence; ~12-run demote then a
    further ~12-run archive)."""

    substrate_enabled: bool
    mint_floor_papers: int
    policy_enabled: bool = False
    mint_persistence_runs: int = _DEFAULT_MINT_PERSISTENCE_RUNS
    demote_quiet_runs: int = _DEFAULT_DEMOTE_QUIET_RUNS
    archive_quiet_runs: int = _DEFAULT_ARCHIVE_QUIET_RUNS

    def __post_init__(self):
        # Defense-in-depth: the mint gate + retire sweep read the quiet counter the
        # substrate captures, so policy_enabled without substrate_enabled is incoherent.
        # from_config enforces this, but guard direct construction too (frozen dataclass:
        # __post_init__ may raise even though it cannot assign).
        if self.policy_enabled and not self.substrate_enabled:
            raise ValueError(
                "LifecyclePolicy: policy_enabled requires substrate_enabled "
                "(the retire sweep reads the quiet counter the substrate captures)"
            )

    @classmethod
    def from_config(cls, thresholds_path=DEFAULT_THRESHOLDS_PATH) -> "LifecyclePolicy":
        """Read ``config/thresholds.json``.

        Per flag: FAIL-SOFT to disabled when the enable flag is ABSENT; FAIL-LOUD
        (``ValueError``, mirroring ``ReconcileThresholds.from_config``) when the flag is
        present-and-true but a load-bearing threshold is missing / non-int / < 1. Each
        enable flag must be a JSON bool. ``policy_enabled`` additionally REQUIRES
        ``substrate_enabled`` (fail-loud)."""
        with open(thresholds_path) as f:
            cfg = json.load(f)

        substrate_enabled = _read_bool_flag(
            cfg, "subtopic_lifecycle_substrate_enabled", thresholds_path
        )
        mint_floor_papers = _read_positive_int(
            cfg, "subtopic_lifecycle_mint_floor_papers", thresholds_path,
            required=substrate_enabled, default=_DEFAULT_MINT_FLOOR_PAPERS,
            why="the substrate is enabled",
        )

        policy_enabled = _read_bool_flag(
            cfg, "subtopic_lifecycle_policy_enabled", thresholds_path
        )
        if policy_enabled and not substrate_enabled:
            raise ValueError(
                f"{thresholds_path}: subtopic_lifecycle_policy_enabled requires "
                f"subtopic_lifecycle_substrate_enabled=true (the retire sweep reads the "
                f"quiet counter the substrate captures); flip the substrate on first."
            )
        mint_persistence_runs = _read_positive_int(
            cfg, "subtopic_lifecycle_mint_persistence_runs", thresholds_path,
            required=policy_enabled, default=_DEFAULT_MINT_PERSISTENCE_RUNS,
            why="the policy is enabled",
        )
        demote_quiet_runs = _read_positive_int(
            cfg, "subtopic_lifecycle_demote_quiet_runs", thresholds_path,
            required=policy_enabled, default=_DEFAULT_DEMOTE_QUIET_RUNS,
            why="the policy is enabled",
        )
        archive_quiet_runs = _read_positive_int(
            cfg, "subtopic_lifecycle_archive_quiet_runs", thresholds_path,
            required=policy_enabled, default=_DEFAULT_ARCHIVE_QUIET_RUNS,
            why="the policy is enabled",
        )
        return cls(
            substrate_enabled=substrate_enabled,
            mint_floor_papers=mint_floor_papers,
            policy_enabled=policy_enabled,
            mint_persistence_runs=mint_persistence_runs,
            demote_quiet_runs=demote_quiet_runs,
            archive_quiet_runs=archive_quiet_runs,
        )


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


def upsert_candidate(table, *, slug: str, n_papers: int, hierarchy_version: str) -> int:
    """Read-modify-put on ``SUBTOPIC_CANDIDATE#{slug}`` / META: create with
    ``seen_runs=1`` (``first_seen`` = ``last_seen`` = this version) or ++``seen_runs`` on
    an existing row, refreshing ``last_seen_hierarchy_version`` + ``n_papers_last`` and
    PRESERVING ``first_seen_hierarchy_version``. Returns the NEW ``seen_runs`` (PR-2's
    mint-persistence gate compares it to ``mint_persistence_runs``; PR-1's
    ``stage_candidates`` ignores the return — the widened ``int`` return is
    backward-compatible).

    IDEMPOTENT per ``hierarchy_version``: ``seen_runs`` counts DISTINCT runs (content
    versions), NOT ``--publish`` invocations. The step-10 reconcile also runs on the
    publish SKIP path with the PRIOR run's version, so a same-content re-publish must not
    advance persistence (else two ``--publish`` calls of byte-identical content would mint
    a held cluster). When ``last_seen_hierarchy_version`` already equals this
    ``hierarchy_version`` the row is left untouched and the unchanged count returned."""
    resp = table.get_item(Key={"PK": f"{SUBTOPIC_CANDIDATE_PK_PREFIX}{slug}", "SK": META_SK})
    existing = (resp or {}).get("Item")
    if existing is None:
        seen_runs = 1
        first_seen = hierarchy_version
    else:
        prev_seen = int(existing.get("seen_runs") or 0)
        if existing.get("last_seen_hierarchy_version") == hierarchy_version:
            # Same version already counted (skip-path / same-content re-publish): do not
            # advance persistence and re-write nothing (idempotent re-run).
            return prev_seen
        seen_runs = prev_seen + 1
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
    return seen_runs


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


# ---------- mint-persistence gate (PR-2: ACTS on the candidate substrate) ----------


def delete_candidate(table, *, slug: str) -> bool:
    """Brick F PR-2 (#191): CONSUME a candidate row — the mint-persistence gate deletes
    it the run the cluster clears persistence and mints a durable id (the candidate has
    served its purpose; the durable id is now its identity). Idempotent at the DDB level
    (deleting an absent key is a no-op). Mirrors ``gc_candidates``' delete shape."""
    table.delete_item(Key={"PK": f"{SUBTOPIC_CANDIDATE_PK_PREFIX}{slug}", "SK": META_SK})
    return True


class MintPersistenceGate:
    """Brick F PR-2 (#191): the injected mint-persistence collaborator that decides
    whether a freshly-discovered (mint-bound) cluster may MINT this run.

    A cluster may mint only when it clears BOTH gates: the volume floor
    (``n_papers >= mint_floor_papers``) AND two-run persistence (``seen_runs >=
    mint_persistence_runs``). Otherwise it is HELD — not minted this run, no durable id,
    no slug pointer; only its ``SUBTOPIC_CANDIDATE#{slug}`` row keeps accruing (the
    already-uploaded slug-keyed hierarchy.json is untouched; this suppresses durable-id
    MINTING only, never the published artifact). The gate OWNS the candidate lifecycle
    when the policy is on (it upserts the accruing rows and consumes the minted one), so
    the post-loop ``stage_candidates`` is skipped; ``gc_candidates`` still runs over
    ``seen_slugs`` to reset abandoned / below-floor candidates.

    Stateful across a run: ``held`` (the single held truth), ``below_floor``,
    ``minted`` (gate-cleared), ``consumed`` (candidate rows deleted), and ``seen_slugs``
    (the still-accruing HELD candidates to protect from this run's GC — minted-consumed
    and below-floor slugs are deliberately excluded so GC reclaims them)."""

    def __init__(self, table, policy: LifecyclePolicy, *, hierarchy_version: str):
        self._table = table
        self._policy = policy
        self._hv = hierarchy_version
        self.seen_slugs: set = set()
        self.held = 0
        self.below_floor = 0
        self.minted = 0
        self.consumed = 0

    def admit(self, *, slug: str, n_papers: int) -> bool:
        """Return True if this mint-bound cluster may MINT now, False to HOLD. Call ONLY
        for mint-bound clusters (no prior match); a re-claimed prior is never gated."""
        p = self._policy
        if n_papers < p.mint_floor_papers:
            # Below the volume floor: HOLD and do NOT upsert — the candidate is left
            # absent, so GC resets any stale streak. A one-run dip below the floor thus
            # resets persistence (same burst-filter posture as PR-1's stage_candidates).
            self.below_floor += 1
            self.held += 1
            return False
        if p.mint_persistence_runs <= 1:
            # Single-run floor (annual cadence): admit on first sighting, no candidate
            # churn (no create-then-immediately-consume).
            self.minted += 1
            return True
        seen_runs = upsert_candidate(
            self._table, slug=slug, n_papers=n_papers, hierarchy_version=self._hv
        )
        if seen_runs >= p.mint_persistence_runs:
            delete_candidate(self._table, slug=slug)  # consume — the durable id replaces it
            self.consumed += 1
            self.minted += 1
            return True  # NOT added to seen_slugs (its row is gone)
        # Still accruing — HOLD and protect the candidate from this run's GC.
        self.seen_slugs.add(slug)
        self.held += 1
        return False


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


# ---------- retire transitions (PR-2: READS the quiet counter, WRITES status) ----------


def sweep_retire(
    table,
    *,
    snapshot: dict,
    claimed_ids: set,
    policy: LifecyclePolicy,
) -> dict:
    """Apply forward-only retire transitions over the prior durable-id rows. The status-
    WRITE companion to ``sweep_quiet_substrate`` (which wrote the counter); gated on
    ``policy_enabled`` (which requires ``substrate_enabled``). No-op (returns
    ``{'enabled': False}``) when the policy is off — so a policy-off run writes ZERO
    ``status`` fields and every row stays ``active`` (the aliases.json byte-identity
    guard).

    Computes each prior's POST-sweep quiet count from the pre-sweep ``snapshot`` + the
    ``claimed_ids`` set, mirroring ``sweep_quiet_substrate``'s write contract exactly
    (claimed -> 0; unclaimed -> ``snapshot.consecutive_quiet_runs + 1``) — the snapshot
    is the table state at step-10 start and nothing writes the counter between load and
    sweep, so this needs no re-read of ~1,500 rows. ``snapshot`` must carry
    ``consecutive_quiet_runs`` (``load_id_store_snapshot`` projects it) and ``status``.

    Transitions (thresholds in RUNS):
      - CLAIMED + status in {demoted, archived} -> **active** (un-demote; counter already
        reset to 0 by ``sweep_quiet_substrate``'s claimed path). Fires for ARCHIVED too
        (§8 reversibility: a single new assignment revives even an archived subtopic).
      - UNCLAIMED, quiet >= demote_quiet_runs + archive_quiet_runs -> **archived**.
      - UNCLAIMED, quiet >= demote_quiet_runs -> **demoted**.
    Monotone-forward for the unclaimed path (``_STATUS_RANK``): never regress
    active<-demoted<-archived; the only regression (-> active) is the claim path above.
    ``set_status`` is idempotent, so a no-change row writes nothing. Counts returned for
    the audit event."""
    if not policy.policy_enabled:
        return {"enabled": False}
    demote_at = policy.demote_quiet_runs
    archive_at = policy.demote_quiet_runs + policy.archive_quiet_runs
    demoted = archived = undemoted = 0
    for durable_id in sorted(snapshot):
        row = snapshot[durable_id]
        status = row.get("status") or STATUS_ACTIVE
        if durable_id in claimed_ids:
            # Re-claimed: revive a retired subtopic. The counter reset to 0 is owned by
            # sweep_quiet_substrate; here we only flip the status back to active.
            if status in (STATUS_DEMOTED, STATUS_ARCHIVED):
                if set_status(table, durable_id=durable_id, status=STATUS_ACTIVE):
                    undemoted += 1
            continue
        quiet = int(row.get("consecutive_quiet_runs") or 0) + 1
        if quiet >= archive_at:
            target = STATUS_ARCHIVED
        elif quiet >= demote_at:
            target = STATUS_DEMOTED
        else:
            continue
        # Monotone-forward only: a quiet row may advance but never regress (e.g. an
        # already-archived row must not be re-demoted if a threshold is lowered mid-life).
        if _STATUS_RANK.get(target, 0) <= _STATUS_RANK.get(status, 0):
            continue
        if set_status(table, durable_id=durable_id, status=target):
            if target == STATUS_ARCHIVED:
                archived += 1
            else:
                demoted += 1
    return {"enabled": True, "demoted": demoted, "archived": archived, "undemoted": undemoted}
