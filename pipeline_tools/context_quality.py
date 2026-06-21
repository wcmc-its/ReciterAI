"""Shared quality guards for tool-usage `context` snippets (#238).

ONE source of truth for what makes a per-(tool, pmid) usage snippet acceptable,
imported by BOTH the live backfill (``cli.rebuild_tool_context``) and the
extraction path (``pipeline_tools.extract`` / ``prompts.tool_extract``), so the
"now" fix and the "going forward" fix enforce the same contract:

1. VERBATIM  — the snippet is a contiguous span of the abstract (extracted, not
   generated: the SPS grounding contract, #879 D-19).
2. NAMES THE TOOL — it references the tool, so the snippet is actually about *X*.
   A complete sentence about something else is worse grounding/display than the
   relevant fragment it would replace (the #10 failure mode).
3. ONE SENTENCE — it is a single sentence, not two merged into a run-on (the #3
   failure mode), whether the merge has a stray internal period or none at all.

None of these ever TRUNCATE: a snippet that fails is rejected wholesale so the
caller can keep the prior value, never clamped mid-clause (clamping just relocates
the fragment to the tail, the very thing #238 set out to remove).
"""

from __future__ import annotations

import re

# A returned span longer than this is a run-on / merged-sentences / whole-abstract
# miss, not one sentence — reject (keep prior), never clamp.
MAX_SENTENCE_CHARS = 600
MIN_SNIPPET_CHARS = 12

# Generic tool-TYPE nouns that don't, on their own, prove a snippet is about the
# specific tool. Distinctive domain words (e.g. "sequencing", "photography") are
# intentionally NOT here, so the relevance guard stays lenient (it only rejects a
# snippet that shares NO salient token with the tool name at all).
_GENERIC_TYPE = frozenset(
    "platform system tool tools device kit instrument software service facility "
    "method methods assay assays technique techniques model models scanner "
    "sequencer reagent approach apparatus".split()
)

# Function words that are not, on their own, evidence a snippet is about the tool.
_STOPWORDS = frozenset(
    "the and for with from that this these those was were are its via per all any "
    "based using used onto into over under not new non".split()
)

# Tokens that, capitalized MID-sentence after a lowercase word, almost always
# start a new sentence — the signature of a merge whose period was lost in the
# source abstract (e.g. "...clinical notes Our study highlights..."). Deliberately
# EXCLUDES IMRAD section nouns (Results/Conclusions/Methods/Background/Objective):
# those are common noun phrases mid-sentence ("the Results and Conclusions of …")
# and flagging them is a false positive.
_SENTENCE_STARTERS = (
    "Our We This These Here However Moreover Furthermore Therefore Thus Importantly"
).split()

# Abbreviations whose period is NOT a sentence end.
_ABBREV = frozenset(
    "e.g. i.e. vs. etc. al. fig. figs. no. cf. approx. inc. ltd. dr. st. mr. ms. "
    "mrs. ca. ref. refs. eq. eqs. spp. sp. vol. expt.".split()
)

_INTERNAL_TERMINATOR = re.compile(r"([.!?])[\"')\]]?\s+([A-Z])")
_STARTER_MERGE = re.compile(
    r"\b[a-z]{3,}\s+(?:" + "|".join(_SENTENCE_STARTERS) + r")\s+[a-z]"
)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def is_verbatim(span: str | None, abstract: str) -> bool:
    """The span appears (whitespace/case-insensitively) contiguously in the abstract."""
    return bool(span) and norm(span) in norm(abstract)


def _salient_tokens(tool_name: str) -> set[str]:
    toks = set(re.findall(r"[a-z0-9]{3,}", (tool_name or "").lower()))
    return {t for t in toks if t not in _GENERIC_TYPE and t not in _STOPWORDS}


def _acronyms(tool_name: str) -> set[str]:
    """Short acronyms of the tool: parenthetical ``(PET)`` AND bare all-caps ``VR``."""
    paren = re.findall(r"\(([A-Za-z0-9][A-Za-z0-9-]{1,})\)", tool_name or "")
    bare = re.findall(r"\b[A-Z][A-Z0-9]{1,5}\b", tool_name or "")  # VR, CT, MS, NGS
    return {a.lower() for a in (*paren, *bare)}


def _token_in(token: str, low_span: str) -> bool:
    """Token appears in the snippet, with a prefix fallback for inflections.

    ``cryosectioning`` matches ``cryosectioned`` via the shared 6-char stem; short
    tokens must match exactly (no over-eager prefixing).
    """
    if token in low_span:
        return True
    return len(token) >= 6 and token[:6] in low_span


def names_tool(span: str, tool_name: str) -> bool:
    """The snippet references the tool (shares a salient name token or its acronym).

    Lenient by design: if the tool name has no salient token at all (pure generic),
    we cannot judge and accept. Otherwise a single shared token/acronym suffices.
    """
    salient = _salient_tokens(tool_name)
    acronyms = _acronyms(tool_name)
    if not salient and not acronyms:
        return True
    low = (span or "").lower()
    if any(_token_in(t, low) for t in salient):
        return True
    return any(re.search(rf"\b{re.escape(a)}\b", low) for a in acronyms)


def is_single_sentence(span: str) -> bool:
    """Reject a span that merges two sentences (with a stray period, or with none)."""
    s = (span or "").strip()
    if not s:
        return False
    # (a) a real internal terminator followed by a capitalized word, unless the
    #     token before it is a known abbreviation or a single-letter initial.
    for m in _INTERNAL_TERMINATOR.finditer(s[:-1]):  # ignore the final terminator
        prev_raw = s[: m.start() + 1].split()[-1]
        # Strip a leading "(" / quote / bracket so "(no." and "(W.L." normalize.
        prev = re.sub(r"^[^A-Za-z]+", "", prev_raw).lower()
        # Skip known abbreviations and initial sequences ("a.", "w.l.", "u.s.").
        # fullmatch (not search): "assay." must NOT be read as a trailing "y." initial.
        if prev in _ABBREV or re.fullmatch(r"(?:[a-z]\.)+", prev):
            continue
        return False
    # (b) a missing-period merge: a sentence-starter word capitalized mid-clause.
    return _STARTER_MERGE.search(s) is None


def accept_snippet(span: str | None, abstract: str, tool_name: str) -> bool:
    """True iff the span is verbatim, in-length, names the tool, and is one sentence."""
    if not span or not isinstance(span, str):
        return False
    s = span.strip()
    if not (MIN_SNIPPET_CHARS <= len(s) <= MAX_SENTENCE_CHARS):
        return False
    return is_verbatim(s, abstract) and names_tool(s, tool_name) and is_single_sentence(s)


# ---------------------------------------------------------------------------
# Entity-grain helpers (#1166, the specific-cell-line Surface B data stage).
#
# These power the per-(publication x entity) provenance the Methods Surface-B
# strip/directory render. They are the producer-side source of truth for two
# fields the SPS rail/snippet were built to consume but nothing populated yet
# (see SPS components/method/{provenance-rail,highlight-snippet}.tsx):
#
#   - ``matched_span`` — exact char offsets of the entity term within the
#     usage sentence, so the SPS ``<mark>`` is reliable instead of a fragile
#     client-side substring re-match (spec §7 "highlight needs offsets").
#   - ``centrality_score`` — how central the entity is in the sentence; this is
#     the port of the SPS-side ``nameFirstFraction`` heuristic
#     (``etl/tools/tool-context.ts``) UP to the producer (spec Q-5), so SPS reads
#     it instead of re-deriving, and the rail eyebrow ("How it was used" vs
#     "Where it appears", D5) is driven from one place.
#
# Naming forms are derived from the entity's display name + registry aliases, so
# a casing/synonym variant in the sentence still resolves a span (the
# normalization already done by the tool registry's alias set).
# ---------------------------------------------------------------------------

_PAREN_INNER = re.compile(r"\(([^)]{2,})\)")


def salient_name_forms(display_name: str, aliases: list[str] | None = None) -> list[str]:
    """Lowercase forms of an entity name used to locate it in a sentence.

    The full name, any parenthetical short form (``"(scRNA-seq)" -> "scrna-seq"``),
    the name with parentheticals stripped, and every registry alias. Longest first,
    so :func:`compute_matched_span` prefers the most specific occurrence (e.g.
    ``"3T3-L1 adipocytes"`` over the bare ``"3T3-L1"``). Faithful port of the SPS
    ``salientNameForms`` plus alias coverage. De-duplicated, order-stable.
    """
    forms: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        v = (value or "").strip().lower()
        if len(v) >= 2 and v not in seen:
            seen.add(v)
            forms.append(v)

    name = (display_name or "").strip()
    lower = name.lower()
    if len(lower) >= 3:
        _add(lower)
    for m in _PAREN_INNER.finditer(name):
        _add(m.group(1))
    no_parens = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", lower)).strip()
    if len(no_parens) >= 3 and no_parens != lower:
        _add(no_parens)
    for alias in aliases or []:
        _add(alias)
    # Longest first so a span match prefers the most specific (longest) form.
    forms.sort(key=len, reverse=True)
    return forms


def name_first_fraction(snippet: str, forms: list[str]) -> float:
    """Earliest position (fraction of length) at which the snippet names the entity.

    Returns 1.0 when no form occurs. Lower => the entity is the subject (named
    early); high => a late/incidental mention. Faithful port of the SPS
    ``nameFirstFraction``.
    """
    if not forms:
        return 1.0
    s = (snippet or "").lower()
    first = -1
    for f in forms:
        i = s.find(f)
        if i >= 0 and (first < 0 or i < first):
            first = i
    return 1.0 if first < 0 else first / max(1, len(snippet or ""))


def centrality_score(snippet: str, forms: list[str]) -> float:
    """1 - name_first_fraction, rounded to 4dp; high => entity named early (central)."""
    return round(1.0 - name_first_fraction(snippet, forms), 4)


def compute_matched_span(snippet: str, forms: list[str]) -> tuple[int, int] | None:
    """Char offsets ``(start, end)`` of the entity term in ``snippet``, or None.

    Prefers the EARLIEST occurrence of the LONGEST matching form (``forms`` is
    longest-first), so the most specific span is marked. Offsets index the
    ORIGINAL (not lowercased) snippet; the match is case-insensitive. None when
    no form occurs verbatim — SPS then falls back to its term-match highlighter.
    """
    if not snippet or not forms:
        return None
    low = snippet.lower()
    best: tuple[int, int] | None = None
    for f in forms:
        i = low.find(f)
        if i < 0:
            continue
        cand = (i, i + len(f))
        # Earliest start wins; longer span breaks an exact-start tie.
        if best is None or cand[0] < best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    return best
