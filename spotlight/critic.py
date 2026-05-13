"""Hybrid critic — deterministic regex bundle + Bedrock Haiku LLM-judge slice
+ retry-3 loop. Implements SPOT-06 (voice contract enforcement) and SPOT-07
(retry-3 with review queue routing on persistent failure).

Two-stage critic per RESEARCH §"Pattern 3: Hybrid critic":

  1. ``run_deterministic_checks`` — regex bundle covering em-dash, time-bound
     language, marketing words, dead words, the "WCM scholars are X-ing"
     tic, and 22-38 word length bounds. Microseconds; runs first.

  2. ``run_llm_critic`` — Bedrock Haiku judges the four constraints that
     resist regex enforcement (active verb, anchored in synopses, no
     specific faculty named, institutional voice). Temperature=0.0 for
     reproducibility; max_tokens=200.

The ``run_critic_loop`` outer loop generates a lede, runs the critic
stack, and on failure feeds the failure reason back into the next
``generate_lede`` call as ``prior_failure`` for self-correction.
After ``MAX_RETRIES + 1`` attempts (4 total: initial + 3 retries), the
loop exits and writes a SPOTLIGHT_REVIEW# entry with ``flag_reason='critic'``
and ``status='needs_review'``. CONTEXT decision Q3.2 locks
``MAX_RETRIES = 3``.

Bedrock model ID is imported (``HAIKU_MODEL`` from ``utils.bedrock_client``);
typing the literal anywhere is forbidden (RESEARCH Pitfall 5 / T-06-05-03).

Logging policy (T-06-05-04): only operational metadata is logged. The
lede text and rendered prompts are NEVER logged.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from spotlight.lede_generator import generate_lede
from spotlight.review_queue import write_review_entry
from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Paper
from utils.bedrock_client import BedrockClient, HAIKU_MODEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Voice-contract regex bundle (RESEARCH §Pattern 3, lines 707-750)
# ---------------------------------------------------------------------------
#
# EM_DASH_RE covers U+2014 (em-dash) AND U+2013 (en-dash) per RESEARCH
# Pitfall 3 — both look identical at editorial scale and the prompt bans
# both.

EM_DASH_RE = re.compile(r"[—–]")

TIME_BOUND_RE = re.compile(
    r"\b(this (quarter|year)|currently|right now|recently|of late|in recent (months|years|weeks))\b",
    re.IGNORECASE,
)

MARKETING_RE = re.compile(
    r"\b(cutting[- ]edge|world[- ]class|pioneering|revolutionary|groundbreaking|leading|innovative)\b",
    re.IGNORECASE,
)

DEAD_WORDS_RE = re.compile(
    r"\b(important|complex|vital|novel)\b",
    re.IGNORECASE,
)

# Allowed institutional-voice openers. The lede must contain exactly one of
# these followed by an active -ing verb. Per wcmc-its/ReciterAI#2 §3,
# rotation across openers is enforced at the artifact level (a single
# publish must not contain duplicates) so the surface doesn't read
# mechanical when multiple ledes are visible together.
ALLOWED_OPENERS = (
    "WCM scholars are",
    "Weill Cornell scholars are",
    "Scholars at Weill Cornell Medicine are",
    "Researchers at WCM are",
    "Investigators at Weill Cornell are",
    "WCM researchers are",
    "Weill Cornell Medicine scholars are",
    # Three more variants so a 10-spotlight publish has headroom to keep
    # each opener unique. Same pattern, same active-verb-ing continuation.
    "WCM faculty are",
    "Faculty at Weill Cornell are",
    "Weill Cornell investigators are",
)

# OPENER_RE matches any of the allowed openers WITHOUT the verb suffix —
# used by the artifact-level duplicate check to extract just the opener
# phrase for comparison. Case-sensitive (proper-noun phrases).
OPENER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(o) for o in ALLOWED_OPENERS) + r")\b"
)

# TIC_RE matches any allowed opener followed by an active -ing verb. The
# original locked-to-"WCM scholars" form is preserved as one of the
# alternatives. Case-sensitive: lowercase variants count as missing-tic
# violations (Test 10).
TIC_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(o) for o in ALLOWED_OPENERS) + r") [a-zA-Z]+ing\b"
)

# Meta-language about the model's own input — leaks of "the papers
# provided" / "the input papers" / "based on the synopses" etc. The
# lede should describe WCM research, not the prompt's input artifacts.
# Per wcmc-its/ReciterAI#2 §2.
META_LANGUAGE_RE = re.compile(
    r"\b(?:"
    r"the papers provided|"
    r"the input papers|"
    r"based on the (?:papers|synopses|inputs?)|"
    r"from the synopses|"
    r"the available data|"
    r"the provided data|"
    r"the source material|"
    r"the representative papers"
    r")\b",
    re.IGNORECASE,
)

# Chain-of-thought preamble cues — the lede generator sometimes prepends
# meta-commentary about its own reasoning (especially on paper-subtopic
# mismatch). Per wcmc-its/ReciterAI#1.
PREAMBLE_RE = re.compile(
    r"^\s*(?:Looking at|I notice|I['']ll|Let me|First,|Note that|"
    r"The papers provided|The representative papers)\b",
    re.IGNORECASE,
)

# Cherry-picking specific mechanism-disease pairs in one sentence.
# Catches "from {mechanism} in {disease A} to/and {mechanism} in {disease B}"
# style constructions that exclude faculty in adjacent areas. Per
# wcmc-its/ReciterAI#2 §1.
CHERRY_PICK_RE = re.compile(
    r"\bfrom\b[^.]{1,60}\bin\b[^.]{1,60}\b(?:to|and)\b[^.]{1,60}\bin\b",
    re.IGNORECASE,
)

# Length bounds — lenient by design (spec is 25-35; we accept 22-38 to
# give the model regeneration room without forcing a retry on a single
# borderline word count).
LENGTH_MIN = 22
LENGTH_MAX = 38


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRITIC_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "spotlight_critic_v0.md"
)

# CONTEXT decision Q3.2 LOCKED — never relax without an explicit CONTEXT
# revision. The retry budget bounds Bedrock cost (T-06-05-06).
MAX_RETRIES = 3

CRITIC_TEMPERATURE = 0.0
CRITIC_MAX_TOKENS = 200

_CRITIC_SYSTEM_PROMPT = (
    "You evaluate editorial ledes against voice constraints."
)


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeterministicVerdict:
    """Outcome of the regex-bundle pass.

    ``failed_constraints`` is a tuple of stable constraint identifiers
    (``em_dash_present``, ``time_bound_language``, ``marketing_language``,
    ``dead_word:<word>``, ``missing_wcm_scholars_tic``,
    ``length_out_of_band:<word_count>``). The tuple is empty when
    ``passed`` is True.
    """

    passed: bool
    failed_constraints: tuple[str, ...]
    word_count: int


@dataclass(frozen=True)
class LLMVerdict:
    """Outcome of the Haiku LLM-judge slice.

    ``failed_constraint`` and ``reason`` are empty strings on a passing
    verdict. On failure, ``failed_constraint`` is one of the four
    closed-vocabulary identifiers from the critic prompt (``active_verb``,
    ``anchored_in_synopses``, ``no_faculty_named``, ``institutional_voice``).
    """

    passed: bool
    failed_constraint: str
    reason: str


class CritReasonCode(StrEnum):
    """Closed vocabulary for CRITIC_REJECT#.reason_code (Phase 12 D-31).

    Values match the LLM critic prompt's ``failed_constraint`` output verbatim
    so SPOTLIGHT_REVIEW# rows (carrying critic_verdict.failed_constraint)
    and CRITIC_REJECT# rows (carrying reason_code) speak the same vocabulary.

    PRE_LLM_GATE is reserved for deterministic-gate failures that never
    reached the LLM; the specific deterministic code is preserved separately
    in the row's ``pre_llm_constraint`` field.
    """

    ACTIVE_VERB = "active_verb"
    ANCHORED_IN_SYNOPSES = "anchored_in_synopses"
    NO_FACULTY_NAMED = "no_faculty_named"
    INSTITUTIONAL_VOICE = "institutional_voice"
    PRE_LLM_GATE = "pre_llm_gate"


@dataclass(frozen=True)
class ValidatedLede:
    """Final output of ``run_critic_loop``.

    ``status`` is ``"pass"`` when both the deterministic and LLM critics
    pass within ``MAX_RETRIES + 1`` attempts; ``"needs_review"`` when the
    loop exhausts and a SPOTLIGHT_REVIEW# entry has been written.

    ``attempts`` is the full attempt log (each entry: ``{lede, verdict,
    stage}``). ``papers_used`` is the tuple of PMIDs for the papers that
    grounded the FINAL attempt's call.
    """

    subtopic_id: str
    parent_topic: str
    lede: str
    status: str
    attempts: tuple[dict, ...]
    papers_used: tuple[str, ...]


# ---------------------------------------------------------------------------
# Lazy critic-prompt body load (matches lede_generator pattern)
# ---------------------------------------------------------------------------

_CRITIC_PROMPT_BODY: str | None = None


def _load_critic_prompt() -> str:
    """Read the v0 critic prompt and extract the fenced markdown body.

    The file has an operator-facing markdown header before the fenced
    block; we extract only the fenced body so the runtime call doesn't
    leak operator notes into the Bedrock prompt.

    Raises ``RuntimeError`` if the fenced markdown body is missing — the
    file format is operator-controlled (Plan 06-05 owns it) and a missing
    fence is a contract violation, not a silent fail-open.
    """
    global _CRITIC_PROMPT_BODY
    if _CRITIC_PROMPT_BODY is None:
        full = CRITIC_PROMPT_PATH.read_text(encoding="utf-8")
        # Extract the body between the first ```markdown fence and the
        # next closing ``` fence.
        match = re.search(r"```markdown\n(.*?)\n```", full, re.DOTALL)
        if not match:
            raise RuntimeError(
                f"critic prompt {CRITIC_PROMPT_PATH} missing fenced markdown body"
            )
        _CRITIC_PROMPT_BODY = match.group(1)
    return _CRITIC_PROMPT_BODY


# ---------------------------------------------------------------------------
# Deterministic critic
# ---------------------------------------------------------------------------


def run_deterministic_checks(lede: str) -> DeterministicVerdict:
    """Apply the regex bundle to ``lede`` and return a frozen verdict.

    Order of checks does not matter for correctness — all violations
    accumulate. The caller (``run_critic_loop``) joins the
    ``failed_constraints`` tuple into the ``prior_failure`` string fed
    back to the next ``generate_lede`` call.
    """
    failed: list[str] = []

    if EM_DASH_RE.search(lede):
        failed.append("em_dash_present")
    if TIME_BOUND_RE.search(lede):
        failed.append("time_bound_language")
    if MARKETING_RE.search(lede):
        failed.append("marketing_language")

    dead_match = DEAD_WORDS_RE.search(lede)
    if dead_match:
        failed.append(f"dead_word:{dead_match.group(0).lower()}")

    if PREAMBLE_RE.search(lede):
        failed.append("preamble_chain_of_thought")
    meta_match = META_LANGUAGE_RE.search(lede)
    if meta_match:
        failed.append(f"meta_language:{meta_match.group(0).lower()}")
    if CHERRY_PICK_RE.search(lede):
        failed.append("cherry_picked_mechanism_disease_pairs")

    if not TIC_RE.search(lede):
        failed.append("missing_wcm_scholars_tic")

    word_count = len(lede.split())
    if word_count < LENGTH_MIN or word_count > LENGTH_MAX:
        failed.append(f"length_out_of_band:{word_count}")

    return DeterministicVerdict(
        passed=(len(failed) == 0),
        failed_constraints=tuple(failed),
        word_count=word_count,
    )


def find_duplicate_openers(ledes: list[str]) -> dict[int, str]:
    """Return ``{lede_index: opener}`` for every lede whose institutional-voice
    opener also appears in an EARLIER-indexed lede in the same artifact.

    Per wcmc-its/ReciterAI#2 §3: when multiple ledes are visible together
    (SPS home page rotation), repeating the same opener reads mechanical.
    The artifact-level critic enforces uniqueness across a single publish.

    The first occurrence of each opener is NOT returned (it's allowed to
    stay). Only the second-and-later duplicates are flagged. Caller routes
    those to the review queue and excludes them from the artifact.

    Ledes that contain no recognized opener are silently skipped here —
    that's a per-spotlight ``missing_wcm_scholars_tic`` violation that the
    deterministic checks already catch upstream.
    """
    seen: dict[str, int] = {}
    duplicates: dict[int, str] = {}
    for i, lede in enumerate(ledes):
        m = OPENER_RE.search(lede)
        if not m:
            continue
        opener = m.group(0)
        if opener in seen:
            duplicates[i] = opener
        else:
            seen[opener] = i
    return duplicates


# ---------------------------------------------------------------------------
# LLM critic
# ---------------------------------------------------------------------------


def _strip_json_fences(text: str) -> str:
    r"""Strip ``\`\`\`json`` / ``\`\`\`\`` markdown fences from a Bedrock
    response (Phase 2 stripJsonFences pattern, also used in
    ``utils/bedrock_client.call_json``).
    """
    return re.sub(r"```json\n?|\n?```", "", text).strip()


def run_llm_critic(
    lede: str,
    meta: SubtopicMeta,
    papers: list[Paper],
    client: BedrockClient | None = None,
) -> LLMVerdict:
    """Bedrock Haiku LLM-judge slice.

    Renders the v0 critic prompt with ``lede``, ``subtopic_name``, and
    ``papers_brief``; calls Haiku at temperature=0.0; parses the JSON
    verdict (tolerating markdown fences).

    On parse failure, returns a failing verdict with
    ``failed_constraint='parse_error'`` rather than raising — the caller's
    retry loop will treat it as a regular failure and attempt another
    generation. This keeps the loop resilient to transient JSON drift
    without crashing the publish run.
    """
    client = client or BedrockClient()

    papers_brief = "\n".join(
        f"- {p.synopsis} ({p.impact_justification})" for p in papers
    )

    body = _load_critic_prompt()
    rendered = (
        body
        .replace("{lede}", lede)
        .replace("{subtopic_name}", meta.label)
        .replace("{papers_brief}", papers_brief)
    )

    response_text = client.call(
        model=HAIKU_MODEL,
        messages=[{"role": "user", "content": rendered}],
        system=_CRITIC_SYSTEM_PROMPT,
        max_tokens=CRITIC_MAX_TOKENS,
        temperature=CRITIC_TEMPERATURE,
    )

    cleaned = _strip_json_fences(response_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "Critic JSON parse failed for subtopic_id=%s; treating as fail",
            meta.subtopic_id,
        )
        return LLMVerdict(
            passed=False,
            failed_constraint="parse_error",
            reason="critic returned non-JSON response",
        )

    verdict = parsed.get("verdict", "fail")
    failed_constraint = parsed.get("failed_constraint", "") or ""
    reason = parsed.get("reason", "") or ""

    return LLMVerdict(
        passed=(verdict == "pass"),
        failed_constraint=failed_constraint,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Generate-and-critic loop
# ---------------------------------------------------------------------------


def _verdict_to_dict(v: Any) -> dict:
    """Serialize a frozen verdict to a plain dict for the attempt log."""
    if isinstance(v, DeterministicVerdict):
        return {
            "kind": "deterministic",
            "passed": v.passed,
            "failed_constraints": list(v.failed_constraints),
            "word_count": v.word_count,
        }
    if isinstance(v, LLMVerdict):
        return {
            "kind": "llm",
            "passed": v.passed,
            "failed_constraint": v.failed_constraint,
            "reason": v.reason,
        }
    raise TypeError(f"unknown verdict type: {type(v)}")


def run_critic_loop(
    meta: SubtopicMeta,
    papers: list[Paper],
    publish_id: str,
    parent_topic: str,
    lede_client: BedrockClient | None = None,
    critic_client: BedrockClient | None = None,
    dynamo_client=None,
    excluded_openers: tuple[str, ...] = (),
    stage_table=None,
) -> ValidatedLede:
    """Generate-and-critic outer loop.

    Up to ``MAX_RETRIES + 1`` attempts (4 total: initial + 3 retries).
    On each attempt:

      1. Call ``generate_lede`` (passes ``prior_failure`` from the previous
         attempt's failure summary so the model can self-correct).
      2. Run ``run_deterministic_checks``. If it fails, log the attempt
         and continue.
      3. Otherwise run ``run_llm_critic``. If it fails, log and continue.
      4. Both pass → return a ValidatedLede with status="pass".

    After ``MAX_RETRIES + 1`` attempts without success, write a
    SPOTLIGHT_REVIEW# entry via ``review_queue.write_review_entry`` with
    ``flag_reason='critic'`` and ``regen_count=MAX_RETRIES``. Return a
    ValidatedLede with status="needs_review".

    ``stage_table`` (Phase 12 D-08): optional DynamoDB resource table for the
    additive CRITIC_REJECT# write. When None the CRITIC_REJECT# write is
    skipped (dry-run / no AWS creds). Follows the ``stage_table`` pattern
    established by ``score_publications.py`` and ``assign_subtopics.py``.
    """
    attempts: list[dict] = []
    last_failure_reason: str | None = None
    last_lede: str = ""
    last_papers: list[Paper] = []
    last_llm_verdict: LLMVerdict | None = None
    last_deterministic_verdict: DeterministicVerdict | None = None

    for attempt_idx in range(MAX_RETRIES + 1):
        lede_text, selected_papers = generate_lede(
            meta,
            papers,
            prior_failure=last_failure_reason,
            client=lede_client,
            excluded_openers=excluded_openers,
        )
        last_lede = lede_text
        last_papers = selected_papers

        det = run_deterministic_checks(lede_text)
        # Fail-closed when the lede uses an opener that's already been used
        # by an earlier spotlight in this publish. Treated as a deterministic
        # violation so the retry-3 budget gets spent on regen with the same
        # excluded list — model picks a different variant on the next try.
        if excluded_openers and det.passed:
            opener_match = OPENER_RE.search(lede_text)
            if opener_match and opener_match.group(0) in excluded_openers:
                det = DeterministicVerdict(
                    passed=False,
                    failed_constraints=(f"opener_already_used:{opener_match.group(0)}",),
                    word_count=det.word_count,
                )
        if not det.passed:
            last_deterministic_verdict = det
            attempts.append({
                "attempt": attempt_idx,
                "lede": lede_text,
                "verdict": _verdict_to_dict(det),
                "stage": "deterministic",
            })
            last_failure_reason = ", ".join(det.failed_constraints)
            logger.info(
                "Critic deterministic-fail: subtopic_id=%s attempt=%d failed=%d",
                meta.subtopic_id,
                attempt_idx,
                len(det.failed_constraints),
            )
            continue

        llm = run_llm_critic(lede_text, meta, selected_papers, client=critic_client)
        if not llm.passed:
            last_llm_verdict = llm
            attempts.append({
                "attempt": attempt_idx,
                "lede": lede_text,
                "verdict": _verdict_to_dict(llm),
                "stage": "llm",
            })
            last_failure_reason = f"{llm.failed_constraint}: {llm.reason}"
            logger.info(
                "Critic llm-fail: subtopic_id=%s attempt=%d constraint=%s",
                meta.subtopic_id,
                attempt_idx,
                llm.failed_constraint,
            )
            continue

        # Both passed.
        attempts.append({
            "attempt": attempt_idx,
            "lede": lede_text,
            "verdict": _verdict_to_dict(llm),
            "stage": "llm",
        })
        logger.info(
            "Critic pass: subtopic_id=%s attempts=%d",
            meta.subtopic_id,
            attempt_idx + 1,
        )
        return ValidatedLede(
            subtopic_id=meta.subtopic_id,
            parent_topic=parent_topic,
            lede=lede_text,
            status="pass",
            attempts=tuple(attempts),
            papers_used=tuple(p.pmid for p in selected_papers),
        )

    # Loop exhausted without a passing attempt — route to the review queue.
    review_entry: dict = {
        "publish_id": publish_id,
        "subtopic_id": meta.subtopic_id,
        "parent_topic": parent_topic,
        "lede_text": last_lede,
        "flag_reason": "critic",
        "papers_used": [p.pmid for p in last_papers],
        "regen_count": MAX_RETRIES,
        "attempts": list(attempts),
    }
    if last_llm_verdict is not None:
        review_entry["critic_verdict"] = {
            "failed_constraint": last_llm_verdict.failed_constraint,
            "reason": last_llm_verdict.reason,
        }

    write_review_entry(dynamo_client, review_entry)   # EXISTING — UNCHANGED

    # Phase 12 D-08: additive CRITIC_REJECT# write for per-(publish,subtopic,pmid_set) aggregation.
    # Keying is per-(publish_id, subtopic_id, pmid_set_hash) — NOT per-cwid (D-08 re-framing).
    # publish_id and meta.subtopic_id are already in scope; no cwid threading is required.
    if stage_table is not None:
        from pipeline_common.alert import dispatch as _alert_dispatch
        from utils.event_records import write_critic_reject

        if last_llm_verdict is not None:
            raw = last_llm_verdict.failed_constraint
            try:
                reason_code = CritReasonCode(raw).value
                extra: dict[str, str] = {}
            except ValueError:
                reason_code = "unknown"
                extra = {"raw_failed_constraint": raw}
                _alert_dispatch(
                    "WARN",
                    (
                        f"CRITIC_REJECT# vocabulary drift: LLM returned '{raw}' "
                        f"which is not in CritReasonCode. "
                        f"Row written with reason_code='unknown'."
                    ),
                    {"subtopic_id": meta.subtopic_id, "publish_id": publish_id},
                )
        else:
            # Deterministic-only failure — never reached the LLM (PRE_LLM_GATE)
            pre_llm = "unknown"
            if (
                last_deterministic_verdict is not None
                and last_deterministic_verdict.failed_constraints
            ):
                pre_llm = last_deterministic_verdict.failed_constraints[0]
            reason_code = CritReasonCode.PRE_LLM_GATE.value
            extra = {"pre_llm_constraint": pre_llm}

        # author_cwids derivation (D-08 + D-32): distinct first/last-author
        # person_identifiers across last_papers (the rejected pmid_set's source).
        # See spotlight/types.py:39-57 for Paper/Author shape.
        author_cwids = sorted(
            {
                p.first_author.person_identifier
                for p in last_papers
                if p.first_author and p.first_author.person_identifier
            }
            | {
                p.last_author.person_identifier
                for p in last_papers
                if p.last_author and p.last_author.person_identifier
            }
        )

        write_critic_reject(
            stage_table,
            publish_id=publish_id,
            subtopic_id=meta.subtopic_id,
            pmids=[p.pmid for p in last_papers],
            author_cwids=author_cwids,
            reason_code=reason_code,
            regen_count=MAX_RETRIES,
            reason=(last_llm_verdict.reason if last_llm_verdict is not None else ""),
            **extra,
        )

    logger.info(
        "Critic exhausted: subtopic_id=%s regen_count=%d -> review queue",
        meta.subtopic_id,
        MAX_RETRIES,
    )

    return ValidatedLede(
        subtopic_id=meta.subtopic_id,
        parent_topic=parent_topic,
        lede=last_lede,
        status="needs_review",
        attempts=tuple(attempts),
        papers_used=tuple(p.pmid for p in last_papers),
    )
