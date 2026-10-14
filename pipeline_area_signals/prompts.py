"""Synopsis prompts for grant and trial area signals.

Ported verbatim from the 2026-10-14 area-signals pilot, which validated them:

- ``GRANT_SYNOPSIS_SYSTEM`` is the POC ``synopsis-grant`` prompt ("ReCiterAI -
  Active Prompts"), character for character.
- ``TRIAL_SYNOPSIS_SYSTEM`` is the one new prompt the pilot wrote. The POC's
  ``synopsis-clinical-trial`` was a copy of the publication prompt, so the pilot
  modeled this one on the grant prompt instead.

The user-content builders are the pilot's too. Each is passed to
``pipeline_enrichment.synopsis.generate_synopsis`` as ``user_content_builder``,
which calls it with ``title``, ``journal``, ``year`` and ``abstract``; the
``journal`` slot carries the grant's funder and mechanism or the trial's study
type and phase.

Changing any text here changes what the pilot measured. Bump
``PROMPT_VERSION`` with it: every ``scored`` AREAINPUT item records the
``prompt_version`` it was scored under, ``drain.due_reason`` makes an item whose
stored version differs due again (``prompt_changed``), and because the version
also feeds the input hash, the stored AREASIGNAL row no longer matches and the
item is re-scored rather than reused. Expect a full re-score's cost (the whole
queue, subject to the per-run ceiling).
"""
from __future__ import annotations

PROMPT_VERSION = "2026-10-14"

GRANT_SYNOPSIS_SYSTEM = """You are an expert research writer creating concise grant synopses.
Given grant information, create a 40-95 character summary that captures:

- Primary research objective or hypothesis
- Key methodology or approach being funded
- Target condition, population, or scientific domain
- Significance of potential outcomes

Focus on the most scientifically significant aspects. Use professional terminology.
Be precise and informative within the character limit.

Return a JSON object with "synopsis" field containing the summary."""

TRIAL_SYNOPSIS_SYSTEM = """You are an expert research writer creating concise clinical trial synopses.
Given clinical trial information, create a 40-95 character summary that captures:

- Intervention or exposure being studied
- Target condition or population
- Study design or phase, when informative
- Primary aim of the study

Focus on the most clinically significant aspects. Use professional terminology.
Be precise and informative within the character limit.

Return a JSON object with "synopsis" field containing the summary."""


def build_grant_user_content(*, title, journal, year, abstract):
    """Grant synopsis user content; ``journal`` carries funder and mechanism."""
    return (f"Title: {title}\nFunder and mechanism: {journal or 'N/A'}\n"
            f"Start year: {year if year is not None else 'N/A'}\nAbstract: {abstract}\n\nReturn JSON only.")


def build_trial_user_content(*, title, journal, year, abstract):
    """Trial synopsis user content; ``journal`` carries study type and phase."""
    return f"Title: {title}\n{journal or ''}\nStart year: {year}\nBrief summary: {abstract}\n\nReturn JSON only."
