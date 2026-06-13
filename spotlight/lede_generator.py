"""Bedrock Sonnet lede generator.

Renders ``prompts/spotlight_synopsis_v0.md`` with 2-3 papers per subtopic
(synopsis + impactJustification grounding). Implements SPOT-05.

D-19 LOCKED: never passes ``display_name`` or ``short_description`` to the
LLM. The lede prompt operates on subtopic ``label`` + ``description``
(machine-canonical) only; UI fields are SPS-side only and would contaminate
the editorial voice if propagated through the LLM boundary.

Voice / length / banned-word constraints live in the prompt and are
enforced code-side by the critic regex bundle in ``spotlight/critic.py``.

Bedrock model ID is imported (``OPUS_MODEL`` from ``utils.bedrock_client``);
typing the literal anywhere is forbidden (RESEARCH Pitfall 5 / T-06-05-03).

Logging policy (T-06-05-04): only operational metadata is logged. The
rendered prompt and the lede response text are NEVER logged to avoid
operationally sensitive content leakage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Paper
from utils.bedrock_client import BedrockClient, OPUS_MODEL
from utils.scoring import article_score

logger = logging.getLogger(__name__)


# Repository-relative path to the v0 prompt body (Plan 06-05 owns the
# critic prompt; the synopsis prompt is owned by an earlier phase artifact).
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "spotlight_synopsis_v0.md"

# Locked v1 hyperparameters (CONTEXT decision; A/B confirmation deferred per
# RESEARCH A1 — temperature 0.5 chosen for voice variation across subtopics).
LEDE_TEMPERATURE = 0.5
LEDE_MAX_TOKENS = 300

# 2-3 papers grounding per CONTEXT — fewer than 2 raises (insufficient signal),
# more than 3 clamps to top-3 by impact_score.
MIN_PAPERS = 2
MAX_PAPERS = 3

# System prompt for the Sonnet call. Kept short and stable so the prompt
# template carries the voice contract; this is the prompt-injection trust
# boundary (T-06-05-01 mitigation: defense-in-depth via critic regex).
_SYSTEM_PROMPT = "You write editorial ledes for the Scholars @ WCM home page."

# Lazy-load the prompt body on first use to avoid import-time file IO.
_PROMPT_BODY: str | None = None


def _load_prompt_body() -> str:
    """Read the v0 prompt body from disk on first use, then cache.

    No-op on subsequent calls. Tests that swap the prompt file should
    clear ``_PROMPT_BODY`` between runs (not exercised by the v0 suite).
    """
    global _PROMPT_BODY
    if _PROMPT_BODY is None:
        _PROMPT_BODY = PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_BODY


def _filter_and_clamp_papers(papers: list[Paper]) -> list[Paper]:
    """Drop papers with no author identity and clamp to top-MAX_PAPERS.

    A paper contributes zero grounding signal if BOTH first_author and
    last_author have empty ``person_identifier`` strings — pool_ranker
    may emit such Papers when the source TOPIC# row predates the author
    fanout enrichment (see ``spotlight/types.Paper`` docstring).

    The remaining valid papers are sorted by the blended ``article_score``
    (impact x topic-relevance, ``utils.scoring.article_score``) DESC and
    clamped to the top MAX_PAPERS, so the lede is grounded in the papers
    that best fit the subtopic rather than the most prominent ones. Caller
    is responsible for raising when the result is shorter than MIN_PAPERS.
    """
    valid = [
        p for p in papers
        if p.first_author.person_identifier or p.last_author.person_identifier
    ]
    valid.sort(
        key=lambda p: article_score(p.impact_score, p.relevance_score),
        reverse=True,
    )
    return valid[:MAX_PAPERS]


def _render_prompt(
    prompt_body: str,
    meta: SubtopicMeta,
    papers: list[Paper],
    prior_failure: str | None = None,
    excluded_openers: tuple[str, ...] = (),
) -> str:
    """Build the Bedrock user message for the lede call.

    D-19 enforcement: only ``meta.label`` + ``meta.description`` +
    ``meta.parent_topic_label`` flow into the rendered string. Any future
    expansion of ``SubtopicMeta`` MUST NOT propagate ``display_name`` or
    ``short_description`` through this function — those fields are
    SPS/UI-side only and would corrupt the editorial voice.

    Per-paper rendering uses ``synopsis`` + ``impact_justification`` as
    the canonical signal (CONTEXT). Title and journal may be carried
    along as low-priority context but are NOT the LLM-canonical fields.
    """
    paper_lines = []
    for p in papers:
        # synopsis + impact_justification are the primary grounding signal.
        # Title/journal/year ride along as lower-priority context.
        paper_lines.append(
            f"- {p.synopsis} (impact: {p.impact_justification}) "
            f"[ctx: {p.title}, {p.journal}, {p.year}]"
        )
    papers_block = "\n".join(paper_lines)

    # Description is included alongside label so the LLM has the
    # machine-canonical scope of the subtopic without seeing the UI copy.
    user_msg = (
        prompt_body
        .replace("{parent_topic}", meta.parent_topic_label)
        .replace("{subtopic_name}", meta.label)
        .replace("{papers}", papers_block)
    )
    # Append description (machine-canonical) so the LLM has scope context.
    user_msg += f"\n\nSubtopic description: {meta.description}"

    if prior_failure:
        user_msg += (
            f"\n\nPrior attempt failed: {prior_failure}\n"
            "Revise to address this failure while keeping the voice contract."
        )

    if excluded_openers:
        # #219 defense in depth: never emit an unsatisfiable constraint. If every
        # allowed opener is already excluded, forbidding the entire voice list
        # forces the model to refuse -- and that refusal gets persisted as the
        # lede. Opener rotation is a soft anti-repetition goal, so when variety is
        # exhausted, ask the model to REUSE an opener rather than forbidding all.
        from spotlight.critic import ALLOWED_OPENERS

        remaining = [o for o in ALLOWED_OPENERS if o not in set(excluded_openers)]
        if remaining:
            opener_list = ", ".join(repr(o) for o in excluded_openers)
            user_msg += (
                f"\n\nOPENER CONSTRAINT: the following institutional-voice openers "
                f"have already been used by other spotlights in this publish run "
                f"and MUST NOT be used here: {opener_list}. Pick a DIFFERENT allowed "
                f"opener from the <voice> list."
            )
        else:
            user_msg += (
                "\n\nOPENER CONSTRAINT: every allowed institutional-voice opener has "
                "already been used in this publish run, so opener variety is "
                "exhausted. REUSE an allowed opener from the <voice> list (do NOT "
                "invent a new opener, and do NOT refuse) -- pick whichever fits the "
                "lede best."
            )

    return user_msg


def generate_lede(
    meta: SubtopicMeta,
    papers: list[Paper],
    prior_failure: str | None = None,
    client: BedrockClient | None = None,
    excluded_openers: tuple[str, ...] = (),
) -> tuple[str, list[Paper]]:
    """Generate one lede via Bedrock Sonnet.

    Returns ``(lede_text, selected_papers)`` so the caller can record the
    PMIDs that grounded the actually-issued LLM call (after filter/clamp).

    Raises ``ValueError`` if fewer than MIN_PAPERS valid papers remain after
    filtering out papers without author identity.

    Tests inject a MagicMock client; production callers either pass an
    explicit ``BedrockClient`` (preferred for shared-instance reuse across
    a publish run) or accept the lazily-constructed default.

    NOTE: function signature deliberately excludes ``display_name`` and
    ``short_description`` (D-19 LOCKED, T-06-05-05). Adding those would
    pierce the LLM trust boundary and is rejected at acceptance.
    """
    client = client or BedrockClient()

    selected_papers = _filter_and_clamp_papers(papers)
    if len(selected_papers) < MIN_PAPERS:
        raise ValueError(
            f"requires at least {MIN_PAPERS} papers with valid author "
            f"payload; got {len(selected_papers)}"
        )

    user_msg = _render_prompt(
        _load_prompt_body(),
        meta,
        selected_papers,
        prior_failure,
        excluded_openers=excluded_openers,
    )

    # BedrockClient.call returns the response text directly (Converse API
    # text content). The plan refers to ``client.complete`` in pseudocode,
    # but the actual method on utils.bedrock_client.BedrockClient is
    # ``call`` — see utils/bedrock_client.py line 97.
    # Opus 4.7 deprecates `temperature` — pass None so the client omits it
    # from inferenceConfig (the model uses its built-in default). The
    # LEDE_TEMPERATURE constant is preserved as documentation of the
    # earlier Sonnet-era setting.
    response = client.call(
        model=OPUS_MODEL,
        messages=[{"role": "user", "content": user_msg}],
        system=_SYSTEM_PROMPT,
        max_tokens=LEDE_MAX_TOKENS,
        temperature=None,
    )

    lede_text = response.strip()

    # Operational metadata only — never log the prompt or the lede text.
    logger.info(
        "Lede generated: subtopic_id=%s, papers=%d, len_words≈%d",
        meta.subtopic_id,
        len(selected_papers),
        len(lede_text.split()),
    )

    return (lede_text, selected_papers)
