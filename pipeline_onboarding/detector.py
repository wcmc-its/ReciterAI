"""Daily onboarding detector Lambda (#80 Phase 2, PR 5 — spec T1 / R1 / R9).

A scheduled Lambda (EventBridge, daily) that finds CWIDs whose faculty
profile is incomplete and files a GitHub issue for each so an operator can
trigger an onboarding run. It does **not** invoke the onboarding state
machine (D1 — issue-filing only for the first 6-12 months).

Two findings per CWID, mirroring `pipeline_drift/evaluator.py`'s shape — a
pure evaluator (`evaluate_detector`) plus a thin I/O `handler`:

- **Gap scan (R1).** A faculty-scoped SQL query (`scan_faculty_publication_gaps`)
  is the authoritative "what publications should exist"; a DynamoDB
  PROCESSING# `BatchGetItem` is the "what is scored" inner check. A PMID with
  no synopsis, or with a synopsis but no `complete` score, is a gap.
- **ReCiter churn (R9).** A CWID's current accepted PMID set is compared to
  the `input_pmid_set` recorded on its most recent CWID-scoped rollup
  (`STAGE#rollup_by_cwid#cwid:` — written by PR 2 / #90). Additions or
  removals are attribution drift.

  Churn is **baseline-gated**: a CWID with no prior CWID-scoped rollup row has
  no baseline to diff against, so churn is N/A for it — it is evaluated by
  the gap scan alone (which still catches every new researcher, whose
  publications are unscored). Per-CWID churn detection switches on once a
  CWID has been onboarded once. This is deliberate: treating "no rollup" as
  an empty baseline would flag every faculty CWID on the detector's first
  run, before any onboarding has happened.

**Cold-start guard (#106).** Onboarding has not yet run for most faculty, so
the detector's first runs flag hundreds of CWIDs — one GitHub issue each
would flood the repo. When a run flags more CWIDs than the
`config/thresholds.json` cold-start threshold, per-CWID filing is suppressed
and a single digest issue is filed instead; per-CWID filing — the
steady-state behaviour — resumes automatically once the flagged count drops
back below the threshold.

The detector writes one `STAGE#onboarding_detector#GLOBAL` row per run (R8)
and posts a Teams digest when it flags >= 1 CWID (R10 #1). A quiet day —
zero flagged CWIDs — writes the row and sends no alert.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

# Ensure repo root is importable regardless of cwd (Lambda + CLI).
sys.path.insert(0, str(Path(__file__).parent.parent))

# Populate DB_*/AWS creds from Secrets Manager when running in Lambda. Must
# precede the (lazy) `utils.sql_queries` use so the SQLAlchemy engine factory
# finds creds.
import utils.secrets_loader  # noqa: F401

from pipeline_enrichment import alerting
from pipeline_onboarding import github_issues
from utils.stage_records import STATUS_COMPLETE, compute_input_hash, write_failed

logger = logging.getLogger(__name__)

# The detector's STAGE# substrate row (PLAN §3 / spec R8).
DETECTOR_STAGE = "onboarding_detector"
DETECTOR_SCOPE = "GLOBAL"
DETECTOR_PK = f"STAGE#{DETECTOR_STAGE}#{DETECTOR_SCOPE}"
DETECTOR_RECORD_TYPE = "ONBOARDING_DETECTOR_RUN"

# The R9 churn baseline lives on per-CWID rollup rows (PR 2 / #90); the
# GLOBAL CSV rollup path writes STAGE#rollup_by_cwid#GLOBAL, which this
# prefix deliberately excludes.
_ROLLUP_PK_PREFIX = "STAGE#rollup_by_cwid#cwid:"

# A PMID is NOT a score gap if its PROCESSING# status is one of these:
# `complete` is scored; `quarantined` is terminally un-scoreable and already
# audited (a QUARANTINE# row exists) — re-running onboarding cannot help it,
# so the detector must not nag about it forever.
_STATUS_QUARANTINED = "quarantined"
_SCORED_OR_TERMINAL = frozenset({STATUS_COMPLETE, _STATUS_QUARANTINED})

# The onboarding state machine ARN embedded in the issue body's trigger
# command (T2). PR 6 sets the env var; the fallback is the spec T2 literal
# (account 665083158573, us-east-1).
STATE_MACHINE_ARN_ENV = "RECITERAI_ONBOARDING_STATE_MACHINE_ARN"
_DEFAULT_STATE_MACHINE_ARN = (
    "arn:aws:states:us-east-1:665083158573:stateMachine:reciterai-onboarding"
)

# Display caps — the STAGE# row and Teams digest are summaries; the GitHub
# issues carry the full per-CWID detail.
_FLAGGED_CWIDS_CAP = 200      # cwid list on the STAGE# row
_PMID_DISPLAY_CAP = 150       # PMIDs listed per section in an issue body
_DIGEST_CWID_CAP = 15         # CWIDs itemised in the Teams digest
_DIGEST_ISSUE_CWID_CAP = 50   # CWIDs itemised in the cold-start digest issue

_RESOLUTION_BODY = (
    "**Safe to close** — the onboarding detector's most recent pass found no "
    "remaining synopsis/score gaps and no ReCiter attribution drift for this "
    "CWID. The faculty profile is fully onboarded. Close this issue when "
    "convenient; the detector does not auto-close (spec OQ-3)."
)

# Cold-start guard (#106). On the detector's first runs onboarding has not yet
# run for most faculty, so hundreds of CWIDs flag at once. Above this many
# flagged CWIDs in a run, per-CWID issue filing is suppressed and one digest
# issue is filed instead. The threshold lives in config/thresholds.json so an
# operator can tune it without a code change; the default sits well above any
# plausible steady-state day and well below the cold-start backlog.
_DEFAULT_COLD_START_THRESHOLD = 100

# The cold-start digest is a single, fixed-title issue refreshed in place each
# run — the exact title is its idempotency key. It carries no `CWID {cwid}`
# token, so neither the per-CWID matcher nor the OQ-3 resolution pass touches
# it.
_DIGEST_ISSUE_TITLE = "[onboarding] Detector backlog digest"

# Embedded in the digest body once the backlog clears, so a normal-mode run
# marks a lingering digest issue cleared exactly once, not on every run after.
_DIGEST_CLEARED_MARKER = "<!-- onboarding-detector:digest-cleared -->"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration_ms(started_at: str) -> int:
    """Milliseconds between an ISO `started_at` and now; 0 if unparseable."""
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    delta = datetime.now(timezone.utc) - start
    return max(0, int(delta.total_seconds() * 1000))


def _sort_pmids(pmids: Iterable[str]) -> list[str]:
    """Deterministic, numeric-friendly PMID ordering (shorter = smaller)."""
    return sorted({str(p) for p in pmids}, key=lambda p: (len(p), p))


# ---------------------------------------------------------------------------
# Pure evaluation
# ---------------------------------------------------------------------------


@dataclass
class CwidFinding:
    """One flagged CWID's gap + drift detail.

    `missing_synopsis` and `missing_score` partition the gapped PMIDs: a PMID
    with no synopsis can never also be a score gap (synopsis is the scoring
    precondition), so each gapped PMID is in exactly one list.
    """

    cwid: str
    accepted_pmid_count: int
    missing_synopsis: list[str]
    missing_score: list[str]
    churn_added: list[str]
    churn_removed: list[str]
    has_rollup_baseline: bool

    @property
    def gap_count(self) -> int:
        return len(self.missing_synopsis) + len(self.missing_score)

    @property
    def drift_count(self) -> int:
        return len(self.churn_added) + len(self.churn_removed)

    @property
    def flag_weight(self) -> int:
        """Total flagged-PMID count — sort key + the flagged/not-flagged gate."""
        return self.gap_count + self.drift_count


@dataclass
class DetectorEvaluation:
    """The whole detector run: every flagged CWID, plus run-level totals."""

    started_at: str
    completed_at: str
    scanned_cwid_count: int
    findings: list[CwidFinding]  # flagged CWIDs only, flag_weight desc

    @property
    def flagged_cwid_count(self) -> int:
        return len(self.findings)

    @property
    def gap_cwid_count(self) -> int:
        return sum(1 for f in self.findings if f.gap_count)

    @property
    def churn_cwid_count(self) -> int:
        return sum(1 for f in self.findings if f.drift_count)

    def to_stage_record(
        self,
        *,
        run_id: str,
        input_hash: str,
        duration_ms: int,
        cold_start: bool = False,
    ) -> dict[str, Any]:
        """Render the `STAGE#onboarding_detector#GLOBAL` row for this run (R8).

        One row per detector run, SK `RUN#{started_at}`. Counts stay `int`
        (DynamoDB's TypeSerializer types them `N`); `cost_observed_usd` is a
        pinned `Decimal("0")` — the detector spends no model budget.
        `cold_start_mode` records whether the cold-start guard fired this run
        (a digest issue filed instead of per-CWID issues — #106).
        """
        record: dict[str, Any] = {
            "PK": DETECTOR_PK,
            "SK": f"RUN#{self.started_at}",
            "record_type": DETECTOR_RECORD_TYPE,
            "stage": DETECTOR_STAGE,
            "scope": DETECTOR_SCOPE,
            "status": STATUS_COMPLETE,
            "run_id": run_id,
            "input_hash": input_hash,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": int(duration_ms),
            "cost_observed_usd": Decimal("0"),
            "scanned_cwid_count": self.scanned_cwid_count,
            "flagged_cwid_count": self.flagged_cwid_count,
            "gap_cwid_count": self.gap_cwid_count,
            "churn_cwid_count": self.churn_cwid_count,
            "total_missing_synopsis": sum(
                len(f.missing_synopsis) for f in self.findings
            ),
            "total_missing_score": sum(len(f.missing_score) for f in self.findings),
            "total_churn_added": sum(len(f.churn_added) for f in self.findings),
            "total_churn_removed": sum(len(f.churn_removed) for f in self.findings),
            "cold_start_mode": bool(cold_start),
        }
        if self.findings:
            record["flagged_cwids"] = [
                f.cwid for f in self.findings[:_FLAGGED_CWIDS_CAP]
            ]
        return record


def evaluate_detector(
    *,
    gap_rows: Iterable[dict],
    processing_status: dict[str, str],
    rollup_baselines: dict[str, list[str]],
    invalid_pmids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> DetectorEvaluation:
    """Pure detector evaluation — no I/O.

    Args:
        gap_rows: `scan_faculty_publication_gaps()` output — one dict per
            (CWID, PMID) with a `has_synopsis` flag.
        processing_status: pmid -> PROCESSING# status, from a DynamoDB
            `BatchGetItem`. A PMID absent from the map has never been scored.
        rollup_baselines: cwid -> the `input_pmid_set` of that CWID's most
            recent complete CWID-scoped rollup. A CWID absent from the map has
            no churn baseline (churn is N/A for it — see the module docstring).
        invalid_pmids: the INVALID# exclude list (ReciterDB-flagged corrupt
            PMIDs the scorer permanently culls). Dropped from the gap scan
            entirely so the detector never flags a CWID for an un-fixable PMID
            (#106) — otherwise these would re-flag every run forever, the same
            failure mode the `quarantined` guard prevents downstream.
        now: run timestamp; defaults to wall-clock UTC.

    Returns a `DetectorEvaluation` carrying only the flagged CWIDs, ordered by
    descending flag weight (R1 "ordered by descending gap size").
    """
    now = now or datetime.now(timezone.utc)
    ts = _iso(now)
    invalid = {str(p) for p in (invalid_pmids or ())}

    # Group the SQL gap scan by CWID: cwid -> {pmid: has_synopsis}. INVALID#
    # PMIDs are skipped here so they leave the synopsis gap, the score gap, AND
    # the accepted/churn set in one place (#106).
    by_cwid: dict[str, dict[str, bool]] = {}
    for row in gap_rows:
        pmid = str(row["pmid"])
        if pmid in invalid:
            continue
        cwid = str(row["cwid"])
        by_cwid.setdefault(cwid, {})[pmid] = bool(row["has_synopsis"])

    findings: list[CwidFinding] = []
    # Iterate the union so a CWID that has a rollup baseline but zero current
    # accepted publications (ReCiter de-attributed everything) is still
    # churn-evaluated rather than silently dropped.
    for cwid in set(by_cwid) | set(rollup_baselines):
        pmid_map = by_cwid.get(cwid, {})
        accepted = set(pmid_map)

        missing_synopsis = _sort_pmids(p for p, has in pmid_map.items() if not has)
        missing_score = _sort_pmids(
            p
            for p, has in pmid_map.items()
            if has and processing_status.get(p) not in _SCORED_OR_TERMINAL
        )

        baseline = rollup_baselines.get(cwid)
        if baseline is not None:
            baseline_set = {str(p) for p in baseline}
            churn_added = _sort_pmids(accepted - baseline_set)
            churn_removed = _sort_pmids(baseline_set - accepted)
        else:
            churn_added, churn_removed = [], []

        finding = CwidFinding(
            cwid=cwid,
            accepted_pmid_count=len(accepted),
            missing_synopsis=missing_synopsis,
            missing_score=missing_score,
            churn_added=churn_added,
            churn_removed=churn_removed,
            has_rollup_baseline=baseline is not None,
        )
        if finding.flag_weight > 0:
            findings.append(finding)

    findings.sort(key=lambda f: (-f.flag_weight, f.cwid))
    return DetectorEvaluation(
        started_at=ts,
        completed_at=ts,
        scanned_cwid_count=len(by_cwid),
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Issue + digest formatting (pure)
# ---------------------------------------------------------------------------


def _state_machine_arn() -> str:
    return os.environ.get(STATE_MACHINE_ARN_ENV, "").strip() or _DEFAULT_STATE_MACHINE_ARN


def _cost_guard_threshold() -> int:
    """The onboarding cost-guard PMID ceiling, from config/thresholds.json."""
    try:
        from utils.env_check import load_thresholds

        return int(load_thresholds().get("onboarding_cost_guard_max_pmids", 300))
    except Exception:  # noqa: BLE001 — a display detail must never break filing
        return 300


def _cold_start_threshold() -> int:
    """The cold-start guard's flagged-CWID ceiling, from config/thresholds.json.

    Above this many flagged CWIDs in one run the detector files a single
    digest issue rather than one issue per CWID (#106). Guarded like
    `_cost_guard_threshold` — a misconfigured or unreadable threshold degrades
    to the default, it never breaks a run.
    """
    try:
        from utils.env_check import load_thresholds

        return int(
            load_thresholds().get(
                "onboarding_detector_cold_start_threshold",
                _DEFAULT_COLD_START_THRESHOLD,
            )
        )
    except Exception:  # noqa: BLE001 — a tuning knob must never break a run
        return _DEFAULT_COLD_START_THRESHOLD


def _estimate_cost(net_score_pmids: int) -> Decimal | None:
    """Best-effort USD estimate for scoring `net_score_pmids` PMIDs.

    Reuses the orchestrator's estimator so the issue-body preview and the
    orchestrator's own pre-spend Teams preview share one calculation. Imported
    lazily + guarded: a cost figure is observability, never flow control, so
    it must not break issue filing.
    """
    if net_score_pmids <= 0:
        return None
    try:
        from pipeline_onboarding.orchestrator import estimate_onboarding_cost

        return estimate_onboarding_cost(net_score_pmids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("onboarding detector: cost estimate unavailable: %s", exc)
        return None


def _pmid_block(pmids: list[str]) -> str:
    """Render a PMID list for an issue body, capped with a '+N more' tail."""
    shown = pmids[:_PMID_DISPLAY_CAP]
    text = ", ".join(shown)
    if len(pmids) > _PMID_DISPLAY_CAP:
        text += f", ... (+{len(pmids) - _PMID_DISPLAY_CAP} more)"
    return text or "_(none)_"


def _issue_title(finding: CwidFinding) -> str:
    """Issue title. Always carries the literal `CWID {cwid}` — the spec T1
    idempotency key — whether the CWID is gapped, drifting, or both."""
    if finding.gap_count:
        return (
            f"[onboarding] CWID {finding.cwid} needs backfill "
            f"({finding.gap_count} PMIDs)"
        )
    return (
        f"[onboarding] CWID {finding.cwid} attribution drift "
        f"(+{len(finding.churn_added)}, -{len(finding.churn_removed)} PMIDs)"
    )


def _issue_body(finding: CwidFinding, *, now: datetime) -> str:
    """The GitHub issue body: gap audit + churn delta + projected cost + the
    copy-pasteable `aws stepfunctions start-execution` trigger command (T2).

    There is no separate "dry-run" command: PR 3's orchestrator has no
    `dry_run` mode, so a command labelled dry-run would run the workflow for
    real. The preview is this issue itself — the gap audit and projected cost
    below — plus the orchestrator's own Teams cost note before any model call.
    """
    cwid = finding.cwid
    lines: list[str] = [
        f"The onboarding detector flagged **CWID {cwid}** on "
        f"{now.date().isoformat()}.",
        "",
        f"- Accepted publications (Academic Article, year >= 2020): "
        f"**{finding.accepted_pmid_count}**",
    ]
    if finding.missing_synopsis:
        lines.append(f"- Missing a synopsis: **{len(finding.missing_synopsis)}**")
    if finding.missing_score:
        lines.append(
            f"- Have a synopsis but no score: **{len(finding.missing_score)}**"
        )
    if finding.has_rollup_baseline and (finding.churn_added or finding.churn_removed):
        lines.append(
            f"- ReCiter attribution drift since the last rollup: "
            f"**+{len(finding.churn_added)} / -{len(finding.churn_removed)}**"
        )
    lines.append("")

    if finding.missing_synopsis:
        lines += [
            "### PMIDs missing a synopsis",
            _pmid_block(finding.missing_synopsis),
            "",
            "_Onboarding will **defer** until the enrichment job backfills "
            "these synopses (#92) — it does not generate synopses itself. "
            "Re-run onboarding once they land._",
            "",
        ]
    if finding.missing_score:
        lines += [
            "### PMIDs with a synopsis but no score",
            _pmid_block(finding.missing_score),
            "",
        ]
    if finding.churn_added:
        lines += [
            "### Newly attributed by ReCiter (added since the last rollup)",
            _pmid_block(finding.churn_added),
            "",
        ]
    if finding.churn_removed:
        lines += [
            "### De-attributed by ReCiter (removed since the last rollup)",
            _pmid_block(finding.churn_removed),
            "",
            "_Removals need only a rollup recomputation, no rescoring; the "
            "rollup-only path bypasses the cost guard (D8)._",
            "",
        ]

    cost = _estimate_cost(len(finding.missing_score))
    cost_str = f"~${cost}" if cost is not None else "unavailable"
    lines.append("### Projected cost")
    if finding.missing_synopsis:
        lines.append(
            f"This run will **defer** first — {len(finding.missing_synopsis)} "
            f"PMID(s) need synopsis backfill. Once synopses land, scoring the "
            f"{len(finding.missing_score)} synopsis-ready PMID(s) is estimated "
            f"at {cost_str}."
        )
    elif finding.missing_score:
        lines.append(
            f"Scoring {len(finding.missing_score)} PMID(s) is estimated at "
            f"{cost_str}."
        )
    else:
        lines.append(
            "No scoring work — attribution drift only; the run does a rollup "
            "recomputation at no model cost."
        )
    lines += [
        "",
        f"_This estimate is the preview. The orchestrator recomputes it and "
        f"posts a Teams cost note before any model call; a run scoring more "
        f"than {_cost_guard_threshold()} net PMIDs stops at the cost guard "
        f"until re-run with `allow_cost_override`._",
        "",
        "### Trigger the onboarding run",
        "```bash",
        "aws stepfunctions start-execution \\",
        f"  --state-machine-arn {_state_machine_arn()} \\",
        f"  --name onboarding-{cwid}-$(date -u +%Y%m%dT%H%M%SZ) \\",
        f"  --input '{_trigger_input(cwid)}'",
        "```",
        "",
        "Onboarding is idempotent — re-running a CWID re-does only the "
        "outstanding work. Close this issue once the rollup is confirmed; the "
        "detector also posts a \"safe to close\" note when it next sees this "
        "CWID fully onboarded.",
    ]
    return "\n".join(lines)


def _trigger_input(cwid: str) -> str:
    """The `--input` JSON for the start-execution command (spec T2)."""
    import json

    return json.dumps({"cwid": cwid, "allow_cost_override": False})


def _refresh_comment(finding: CwidFinding, *, now: datetime) -> str:
    """The daily-refresh comment added on an existing issue's update (T1)."""
    bits: list[str] = []
    if finding.missing_synopsis:
        bits.append(f"{len(finding.missing_synopsis)} missing synopsis")
    if finding.missing_score:
        bits.append(f"{len(finding.missing_score)} missing score")
    if finding.has_rollup_baseline and (finding.churn_added or finding.churn_removed):
        bits.append(
            f"drift +{len(finding.churn_added)}/-{len(finding.churn_removed)}"
        )
    return (
        f"**Detector refresh {now.date().isoformat()}** — current state: "
        f"{', '.join(bits) or 'no gaps'}. The issue body above carries the "
        "full current PMID lists."
    )


def _build_digest(
    evaluation: DetectorEvaluation,
    issues_by_cwid: dict[str, dict],
    *,
    issues_available: bool,
    cold_start: bool = False,
    threshold: int = 0,
    digest_url: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """Title / message / context for the R10 #1 Teams digest.

    In cold-start mode (#106) per-CWID issue links are absent — one digest
    issue stood in for the flood — so the message flags the suppressed state
    and points at that digest issue instead.
    """
    n = evaluation.flagged_cwid_count
    if cold_start:
        title = f"Onboarding detector — cold-start backlog: {n} CWID(s) flagged"
    else:
        title = f"Onboarding detector flagged {n} CWID(s)"
    lines = [
        f"The daily onboarding detector flagged **{n}** CWID(s) "
        f"(scanned {evaluation.scanned_cwid_count}).",
        "",
    ]
    if cold_start:
        pointer = f" — {digest_url}" if digest_url else ""
        lines += [
            f"**Cold-start guard active** (threshold {threshold}): per-CWID "
            f"issue filing is suppressed this run; a single digest issue "
            f"stands in for the backlog{pointer}.",
            "",
        ]
    for finding in evaluation.findings[:_DIGEST_CWID_CAP]:
        bits: list[str] = []
        if finding.missing_synopsis:
            bits.append(f"{len(finding.missing_synopsis)} need synopsis")
        if finding.missing_score:
            bits.append(f"{len(finding.missing_score)} need score")
        if finding.churn_added or finding.churn_removed:
            bits.append(
                f"drift +{len(finding.churn_added)}/-{len(finding.churn_removed)}"
            )
        result = issues_by_cwid.get(finding.cwid) or {}
        url = (result.get("issue") or {}).get("html_url", "")
        link = f" — {url}" if url else ""
        lines.append(f"- CWID {finding.cwid}: {', '.join(bits)}{link}")
    if n > _DIGEST_CWID_CAP:
        tail = "the digest issue" if cold_start else "the filed issues"
        lines.append(f"- ...and {n - _DIGEST_CWID_CAP} more — see {tail}.")
    if not issues_available:
        lines += [
            "",
            "_GitHub issue filing was unavailable this run — findings are "
            "recorded in the STAGE#onboarding_detector#GLOBAL row only._",
        ]
    context = {
        "flagged_cwid_count": n,
        "gap_cwid_count": evaluation.gap_cwid_count,
        "churn_cwid_count": evaluation.churn_cwid_count,
        "scanned_cwid_count": evaluation.scanned_cwid_count,
        "cold_start_mode": cold_start,
    }
    return title, "\n".join(lines), context


def _digest_issue_body(
    evaluation: DetectorEvaluation, *, now: datetime, threshold: int
) -> str:
    """The cold-start digest issue body — one issue standing in for the whole
    flagged-CWID backlog while per-CWID filing is suppressed (#106).

    Refreshed in place each run. A summary, not a worklist: run totals, the
    worst CWIDs by flagged-PMID count, and the full flagged-CWID list. The
    actionable per-CWID detail (trigger command, projected cost) returns on
    the individual issues once the backlog drops below the threshold.
    """
    n = evaluation.flagged_cwid_count
    total_synopsis = sum(len(f.missing_synopsis) for f in evaluation.findings)
    total_score = sum(len(f.missing_score) for f in evaluation.findings)
    total_added = sum(len(f.churn_added) for f in evaluation.findings)
    total_removed = sum(len(f.churn_removed) for f in evaluation.findings)

    lines: list[str] = [
        f"The onboarding detector flagged **{n} CWID(s)** on "
        f"{now.date().isoformat()} — above the cold-start threshold of "
        f"**{threshold}**. Filing one GitHub issue per CWID would flood the "
        f"repo, so per-CWID filing is suppressed; this single digest issue "
        f"stands in for the backlog.",
        "",
        "### Run totals",
        f"- CWIDs scanned: **{evaluation.scanned_cwid_count}**",
        f"- CWIDs flagged: **{n}** — {evaluation.gap_cwid_count} with "
        f"synopsis/score gaps, {evaluation.churn_cwid_count} with ReCiter "
        f"attribution drift",
        f"- PMIDs missing a synopsis: **{total_synopsis}**",
        f"- PMIDs with a synopsis but no score: **{total_score}**",
        f"- Attribution drift: **+{total_added} / -{total_removed}** PMIDs",
        "",
        "### Why this is a backlog, not steady state",
        "This is a cold-start artifact — onboarding has not yet run for most "
        "faculty, so nearly every researcher has un-onboarded publications. "
        "The backlog is synopsis-generation-bound: a PMID cannot be scored or "
        "rolled up until its synopsis exists. Synopsis backfill is tracked in "
        "#112; the enrichment job that generates synopses, in #92.",
        "",
    ]

    top = evaluation.findings[:_DIGEST_ISSUE_CWID_CAP]
    lines.append(
        f"### Most-affected CWIDs (top {len(top)} by flagged-PMID count)"
    )
    for finding in top:
        bits: list[str] = []
        if finding.missing_synopsis:
            bits.append(f"{len(finding.missing_synopsis)} need synopsis")
        if finding.missing_score:
            bits.append(f"{len(finding.missing_score)} need score")
        if finding.churn_added or finding.churn_removed:
            bits.append(
                f"drift +{len(finding.churn_added)}/-{len(finding.churn_removed)}"
            )
        lines.append(f"- CWID {finding.cwid}: {', '.join(bits)}")
    if n > len(top):
        lines.append(f"- ...and {n - len(top)} more (full list below).")

    lines += [
        "",
        "### All flagged CWIDs",
        ", ".join(f.cwid for f in evaluation.findings) or "_(none)_",
        "",
        "### What happens next",
        f"Per-CWID onboarding issues — each carrying its own "
        f"`start-execution` trigger command and projected cost — resume "
        f"automatically once the flagged-CWID count falls below the "
        f"cold-start threshold ({threshold}); this digest is then marked "
        f"cleared. Re-enabling the daily cron "
        f"(`reciterai-onboarding-detector-daily`) needs both this guard "
        f"shipped **and** the backlog reduced to a steady-state range — see "
        f"#106.",
    ]
    return "\n".join(lines)


def _digest_cleared_body(evaluation: DetectorEvaluation, *, now: datetime) -> str:
    """The digest issue body once the backlog clears — the flagged count is
    back below the cold-start threshold, so per-CWID filing has resumed.

    Carries `_DIGEST_CLEARED_MARKER` so a normal-mode run marks a lingering
    digest issue cleared exactly once, not on every run afterwards.
    """
    return "\n".join(
        [
            _DIGEST_CLEARED_MARKER,
            f"**Backlog cleared — {now.date().isoformat()}.** The onboarding "
            f"detector's flagged-CWID count ({evaluation.flagged_cwid_count}) "
            f"is back below the cold-start threshold, so per-CWID onboarding "
            f"issues have resumed. This digest is no longer the active "
            f"backlog record.",
            "",
            "Safe to close.",
        ]
    )


# ---------------------------------------------------------------------------
# Production DDB query seam
# ---------------------------------------------------------------------------


def scan_rollup_baselines(table: Any) -> dict[str, list[str]]:
    """Each CWID's churn baseline — the `input_pmid_set` of its most recent
    *complete* CWID-scoped rollup row (R9).

    A CWID with no complete `STAGE#rollup_by_cwid#cwid:` row is absent from
    the result, so churn is N/A for it (see the module docstring). A complete
    rollup row missing the `input_pmid_set` field is treated as no baseline —
    PR 2's rollup always writes it, so this guards only against malformed
    rows rather than re-flooding.
    """
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(#pk, :p)",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":p": _ROLLUP_PK_PREFIX},
    }
    latest: dict[str, tuple[str, list[str]]] = {}  # pk -> (best_sk, input_pmid_set)
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            if item.get("status") != STATUS_COMPLETE:
                continue
            if "input_pmid_set" not in item:
                continue
            pk = item.get("PK", "")
            if not pk.startswith(_ROLLUP_PK_PREFIX):
                continue
            sk = item.get("SK", "")
            prior = latest.get(pk)
            if prior is None or sk > prior[0]:
                pmid_set = [str(p) for p in (item.get("input_pmid_set") or [])]
                latest[pk] = (sk, pmid_set)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return {pk[len(_ROLLUP_PK_PREFIX):]: pmids for pk, (_, pmids) in latest.items()}


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def _file_digest_issue(
    evaluation: DetectorEvaluation,
    open_issues: list[dict],
    *,
    now: datetime,
    threshold: int,
) -> dict | None:
    """Create or refresh the single cold-start digest issue (#106).

    Matched by its fixed title among `open_issues`. Best-effort: a GitHub
    error is logged and `None` returned — the STAGE# row and Teams digest
    still land. Returns the issue dict (carrying `number`, `html_url`).
    """
    body = _digest_issue_body(evaluation, now=now, threshold=threshold)
    matches = github_issues.find_issue_by_title(open_issues, _DIGEST_ISSUE_TITLE)
    try:
        if not matches:
            issue = github_issues.create_issue(_DIGEST_ISSUE_TITLE, body)
            logger.info(
                "onboarding detector: cold-start mode — filed digest issue "
                "#%s for %d flagged CWIDs",
                issue.get("number"), evaluation.flagged_cwid_count,
            )
            return issue
        issue = max(matches, key=lambda i: i.get("updated_at") or "")
        updated = github_issues.update_issue(issue["number"], body=body)
        logger.info(
            "onboarding detector: cold-start mode — refreshed digest issue "
            "#%s for %d flagged CWIDs",
            issue["number"], evaluation.flagged_cwid_count,
        )
        return updated or issue
    except github_issues.GithubApiError as exc:
        logger.warning("onboarding detector: digest issue upsert failed: %s", exc)
        return None


def _clear_lingering_digest(
    evaluation: DetectorEvaluation, open_issues: list[dict], *, now: datetime
) -> bool:
    """Normal-mode housekeeping — mark a digest issue left over from a prior
    cold-start period cleared, now that per-CWID filing has resumed (#106).

    Idempotent: an already-cleared digest (one carrying `_DIGEST_CLEARED_MARKER`)
    is skipped, so its body is PATCHed once on the transition, not every run
    afterwards. Returns True if a digest issue was marked cleared this run.
    """
    cleared = False
    for issue in github_issues.find_issue_by_title(open_issues, _DIGEST_ISSUE_TITLE):
        if _DIGEST_CLEARED_MARKER in (issue.get("body") or ""):
            continue
        try:
            github_issues.update_issue(
                issue["number"], body=_digest_cleared_body(evaluation, now=now)
            )
            logger.info(
                "onboarding detector: backlog below threshold — marked digest "
                "issue #%s cleared", issue["number"],
            )
            cleared = True
        except github_issues.GithubApiError as exc:
            logger.warning(
                "onboarding detector: could not clear digest issue #%s: %s",
                issue.get("number"), exc,
            )
    return cleared


def run_detector(
    *,
    table: Any,
    gap_rows: Iterable[dict],
    processing_status: dict[str, str],
    rollup_baselines: dict[str, list[str]],
    run_id: str,
    invalid_pmids: Iterable[str] | None = None,
    now: datetime | None = None,
    file_issues: bool = True,
) -> dict[str, Any]:
    """Evaluate, file/refresh GitHub issues, persist the STAGE# row, alert.

    All inputs are pre-fetched by `handler` so this function is unit-testable
    with an injected `table` double. Issue filing is best-effort — a GitHub
    outage logs and is skipped; the STAGE# row and Teams digest still land.

    Cold-start guard (#106): when a run flags more CWIDs than the
    `config/thresholds.json` cold-start threshold, per-CWID issue filing is
    suppressed and one digest issue is filed instead. Per-CWID filing resumes
    automatically once the flagged count drops back below the threshold, at
    which point a lingering digest issue is marked cleared.
    """
    now = now or datetime.now(timezone.utc)
    evaluation = evaluate_detector(
        gap_rows=gap_rows,
        processing_status=processing_status,
        rollup_baselines=rollup_baselines,
        invalid_pmids=invalid_pmids,
        now=now,
    )

    threshold = _cold_start_threshold()
    cold_start = evaluation.flagged_cwid_count > threshold

    issues_by_cwid: dict[str, dict] = {}
    created = updated = resolved = 0
    issues_available = False
    digest_issue: dict | None = None

    if file_issues:
        open_issues: list[dict] = []
        try:
            open_issues = github_issues.list_open_issues()
            issues_available = True
        except github_issues.GithubApiError as exc:
            logger.warning(
                "onboarding detector: GitHub unavailable, skipping issue "
                "filing: %s", exc,
            )
            open_issues = []

        if issues_available:
            if evaluation.findings:
                try:
                    github_issues.ensure_label()
                except github_issues.GithubApiError as exc:
                    logger.warning(
                        "onboarding detector: ensure_label failed: %s", exc
                    )

            if cold_start:
                # Cold-start backlog: one digest issue, not a per-CWID flood.
                logger.info(
                    "onboarding detector: cold-start mode — %d flagged CWIDs "
                    "exceeds threshold %d; per-CWID filing suppressed",
                    evaluation.flagged_cwid_count, threshold,
                )
                digest_issue = _file_digest_issue(
                    evaluation, open_issues, now=now, threshold=threshold
                )
            else:
                # Steady state: create or refresh one issue per flagged CWID.
                for finding in evaluation.findings:
                    try:
                        result = github_issues.upsert_onboarding_issue(
                            finding.cwid,
                            _issue_title(finding),
                            _issue_body(finding, now=now),
                            open_issues=open_issues,
                            refresh_comment=_refresh_comment(finding, now=now),
                        )
                        issues_by_cwid[finding.cwid] = result
                        if result.get("action") == "created":
                            created += 1
                        else:
                            updated += 1
                    except github_issues.GithubApiError as exc:
                        logger.warning(
                            "onboarding detector: issue upsert failed for CWID "
                            "%s: %s", finding.cwid, exc,
                        )
                # A digest issue left over from a prior cold-start period is
                # marked cleared now that per-CWID filing has resumed.
                _clear_lingering_digest(evaluation, open_issues, now=now)

            # OQ-3: a previously-flagged CWID with an open issue that is clean
            # today gets a one-time "safe to close" comment. Runs in both
            # modes; the digest issue carries no `CWID {cwid}` token, so
            # `cwid_from_title` returns None for it and it is skipped here.
            flagged = {f.cwid for f in evaluation.findings}
            for issue in open_issues:
                cwid = github_issues.cwid_from_title(issue.get("title") or "")
                if not cwid or cwid in flagged:
                    continue
                try:
                    if github_issues.post_resolution_comment(issue, _RESOLUTION_BODY):
                        resolved += 1
                except github_issues.GithubApiError as exc:
                    logger.warning(
                        "onboarding detector: resolution comment failed for "
                        "issue #%s: %s", issue.get("number"), exc,
                    )

    # Persist the run's STAGE# row (R8) — always, including quiet days.
    input_hash = compute_input_hash(
        DETECTOR_STAGE,
        {"flagged": {f.cwid: f.flag_weight for f in evaluation.findings}},
    )
    record = evaluation.to_stage_record(
        run_id=run_id,
        input_hash=input_hash,
        duration_ms=_duration_ms(evaluation.started_at),
        cold_start=cold_start,
    )
    if digest_issue and digest_issue.get("number") is not None:
        record["digest_issue_number"] = int(digest_issue["number"])
    table.put_item(Item=record)

    # Teams digest (R10 #1) — only when something was flagged.
    digest_sent = False
    if evaluation.flagged_cwid_count > 0:
        title, message, ctx = _build_digest(
            evaluation,
            issues_by_cwid,
            issues_available=issues_available,
            cold_start=cold_start,
            threshold=threshold,
            digest_url=(digest_issue or {}).get("html_url", ""),
        )
        digest_sent = bool(alerting.alert("WARN", title, message, ctx))

    summary = {
        "scanned_cwid_count": evaluation.scanned_cwid_count,
        "flagged_cwid_count": evaluation.flagged_cwid_count,
        "gap_cwid_count": evaluation.gap_cwid_count,
        "churn_cwid_count": evaluation.churn_cwid_count,
        "cold_start_mode": cold_start,
        "issues_created": created,
        "issues_updated": updated,
        "resolution_comments": resolved,
        "issues_available": issues_available,
        "digest_issue_number": (digest_issue or {}).get("number"),
        "digest_sent": digest_sent,
    }
    logger.info("onboarding detector run: %s", summary)
    return summary


def _write_failed_detector_row(table: Any, *, started_at: str, error: BaseException) -> None:
    """Write a `failed` STAGE#onboarding_detector#GLOBAL row on a crash.

    Gives the run observability — the drift evaluator's stage-failure scan
    picks up STAGE#…failed rows. Wrapped so a failure here cannot mask the
    original error.
    """
    try:
        write_failed(
            table,
            stage=DETECTOR_STAGE,
            scope=DETECTOR_SCOPE,
            input_hash=compute_input_hash(DETECTOR_STAGE, {"failed_run": started_at}),
            error_code="OnboardingDetectorError",
            error_message=str(error)[:1000],
            started_at=started_at,
            duration_ms=_duration_ms(started_at),
            cost_observed_usd=Decimal("0"),
        )
        logger.error("onboarding detector: wrote failed STAGE# row for the run")
    except Exception as inner:  # noqa: BLE001
        logger.error(
            "onboarding detector: could not write failed STAGE# row: %s", inner
        )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge daily-cron entry point.

    Event fields (all optional):
        now:         ISO-8601 override for the run timestamp (test injection).
        run_id:      run identifier; defaults to the Lambda request id.
        file_issues: set false to scan + record + alert without touching
                     GitHub (a dry inspection mode).

    Flow: SQL gap scan (the authoritative "what should exist") -> DynamoDB
    PROCESSING# BatchGetItem (the "what is scored" inner check) -> the rollup
    churn-baseline scan -> evaluate -> file issues + STAGE# row + Teams digest.
    A catastrophic failure writes a `failed` STAGE# row, then re-raises so
    EventBridge marks the invocation failed.
    """
    event = event or {}
    now = (
        _parse_iso(event["now"])
        if event.get("now")
        else datetime.now(timezone.utc)
    )
    started_at = _iso(now)
    run_id = (
        event.get("run_id")
        or getattr(context, "aws_request_id", None)
        or started_at
    )
    file_issues = bool(event.get("file_issues", True))

    # Local imports: keep cold start lean and let the pure-evaluation tests
    # avoid pulling boto3 / SQLAlchemy.
    from utils.dynamodb_helpers import (
        TABLE_NAME,
        get_dynamo_client,
        get_processing_status,
        get_table,
        scan_invalid_pmids,
    )
    from utils.sql_queries import scan_faculty_publication_gaps

    table = get_table(TABLE_NAME)
    try:
        gap_rows = scan_faculty_publication_gaps()
        # The score-coverage inner check only needs PROCESSING# for PMIDs that
        # already have a synopsis — a synopsis-less PMID is a gap regardless.
        synopsis_pmids = sorted(
            {r["pmid"] for r in gap_rows if r["has_synopsis"]}
        )
        client = get_dynamo_client()
        processing_status = get_processing_status(
            client, TABLE_NAME, synopsis_pmids
        )
        # Drop ReciterDB-flagged corrupt PMIDs so the detector flags only
        # genuine, actionable gaps and never nags about un-fixable PMIDs (#106).
        invalid_pmids = scan_invalid_pmids(client, TABLE_NAME)
        rollup_baselines = scan_rollup_baselines(table)

        logger.info(
            "onboarding detector: scanned %d (CWID, PMID) rows, %d with a "
            "synopsis, %d on the INVALID# exclude list, %d CWIDs with a "
            "rollup baseline",
            len(gap_rows), len(synopsis_pmids), len(invalid_pmids),
            len(rollup_baselines),
        )
        return run_detector(
            table=table,
            gap_rows=gap_rows,
            processing_status=processing_status,
            rollup_baselines=rollup_baselines,
            run_id=run_id,
            invalid_pmids=invalid_pmids,
            now=now,
            file_issues=file_issues,
        )
    except Exception as exc:
        _write_failed_detector_row(table, started_at=started_at, error=exc)
        raise


__all__ = [
    "CwidFinding",
    "DetectorEvaluation",
    "DETECTOR_PK",
    "DETECTOR_STAGE",
    "evaluate_detector",
    "scan_rollup_baselines",
    "run_detector",
    "handler",
]
