"""The signal layers. Deterministic signals are fully implemented; the LLM and
author-affinity layers are structured against the existing utils but left as
scaffolds (prompt/calibration TODOs flagged inline).

Heavy deps (sqlalchemy, boto3, bedrock) are imported INSIDE the functions that
need them, so `acknowledgement_signal` and the combiner stay importable with
only the stdlib + PyYAML (keeps unit tests dependency-light).
"""
from __future__ import annotations

import logging
import re

from pipeline_cores.fulltext import to_plain_text
from pipeline_cores.models import METHOD_FAMILY_TIERS, CoreDefinition, SignalResult

logger = logging.getLogger(__name__)

# Acronym-style aliases (<=5 uppercase chars) must match on a word boundary to
# avoid spurious substring hits; longer names match case-insensitively.
_ACRONYM = re.compile(r"^[A-Z0-9]{2,6}$")

# The window each side of a match that the institution read looks at — 140 chars,
# the window the alias-power measurement used.
_WINDOW = 140

# Weill Cornell itself: always home, for every core.
_WCM = re.compile(r"Weill Cornell|Cornell Univ|Weill Medical|\bWCMC?\b", re.I)

# Funders are not host institutions. An acknowledgements section names the NIH far
# more often than it names anybody's university, so leaving these in would read
# almost every ack window as "a DIFFERENT institution".
_FUNDER = re.compile(
    r"National Institutes? of Health|National Cancer Institute|National Science Foundation"
    r"|Howard Hughes Medical Institute|Department of (?:Defense|Veterans Affairs)"
    r"|Rockefeller Foundation"   # the FUNDER, not the Tri-I university
    r"|\bNIH\b|\bNCI\b|\bNSF\b|\bHHMI\b", re.I)

# Some OTHER institution is named in the window (21-53% of today's matches).
_OTHER_INSTITUTION = re.compile(
    r"\bUniversit(?:y|ies|at|\u00e4t|e|\u00e9)\b|\bCollege\b|\bInstitut(?:e|es|o|ut)\b"
    r"|\bHospital\b|\bMedical Cent(?:er|re)\b|\bCancer Cent(?:er|re)\b"
    r"|\bSchool of Medicine\b|\bHealth System\b|\bClinic\b|\bAcademy of Sciences\b", re.I)

# JATS blocks for the match-location read. Same block idiom (and the same nesting
# caveat) as suggest_aliases.ack_text.
_ACK_BLOCK = re.compile(r"<ack\b.*?</ack\s*>", re.I | re.S)
_TITLED_SEC = re.compile(
    r"<sec\b[^>]*>\s*(?:<label>.*?</label>\s*)?<title>([^<]*)</title>(.*?)</sec>", re.I | re.S)
_ACK_TITLE = re.compile(r"acknowledg|funding|financial support", re.I)
_METHODS_TITLE = re.compile(r"method|material|experimental|procedure", re.I)


def _alias_pattern(name: str):
    """Acronym -> case-sensitive word boundary; anything longer -> case-insensitive."""
    if _ACRONYM.match(name):
        return re.compile(rf"\b{re.escape(name)}\b")
    return re.compile(re.escape(name), re.IGNORECASE)


def classify_institution(text: str, match, core: CoreDefinition) -> str:
    """"home" | "other" | "none" for the institution named around an alias match.

    Three states, and the third is the subtle one (2026-09-03 audit): naming WCM or
    a Tri-I partner is strong POSITIVE evidence, naming a different institution is
    strong NEGATIVE, and naming NOBODY is only ambiguous for a GENERIC alias — a
    specific name needs no qualifier (ARCH is 43% "none" with 0% "other"). So this
    reports the state and lets the weighting condition it on alias specificity;
    it is deliberately NOT a gate.
    """
    lo, hi = max(0, match.start() - _WINDOW), min(len(text), match.end() + _WINDOW)
    before, after = text[lo:match.start()], text[match.end():hi]
    window = _FUNDER.sub(" ", before + match.group(0) + after)
    if _WCM.search(window) or any(_alias_pattern(p).search(window)
                                  for p in core.partner_institutions):
        return "home"
    # The NEGATIVE read drops the matched alias: a core whose own name carries an
    # institution word ("Institute for Precision Medicine Biobank") is not evidence
    # of somebody else's institution. The positive read above keeps it, because an
    # alias that spells out "… Weill Cornell Medicine" IS the home affiliation.
    outside = _FUNDER.sub(" ", before + " " + after)
    return "other" if _OTHER_INSTITUTION.search(outside) else "none"


def match_section(xml: str, alias: str) -> str:
    """Which JATS block names `alias`: "ack" | "methods" | "body" ("" without XML).

    EXTRACTED, NOT WEIGHTED. <ack> vs methods vs a passing body mention are plainly
    not equal evidence, but nobody has measured by how much, so combine() must not
    price this until a labelling pass does (SPEC feature 3).

    Caveat inherited from the block idiom: a <sec> containing SUBSECTIONS is cut at
    the first </sec>, so an alias deep inside a subsection of Methods reads as
    "body". Harmless while the weight is zero; measure before trusting it.
    """
    if not xml:
        return ""                                   # plain text only: no structure to read
    pattern = _alias_pattern(alias)
    blocks = [("ack", b) for b in _ACK_BLOCK.findall(xml)]
    for title, body in _TITLED_SEC.findall(xml):
        if _ACK_TITLE.search(title):
            blocks.append(("ack", body))
        elif _METHODS_TITLE.search(title):
            blocks.append(("methods", body))
    for label, block in blocks:
        if pattern.search(to_plain_text(block)):
            return label
    return "body"


# ---------------------------------------------------------------------------
# Signal 3 — acknowledgement / alias name-match (deterministic confirmer)
# ---------------------------------------------------------------------------
def acknowledgement_signal(full_text: str, core: CoreDefinition, xml: str = "") -> SignalResult:
    """High-precision confirmer: did the full text name this core?

    ~100% precision but near-zero recall in the wild (most real users never name
    the core) — a confirmer, not a discoverer. Coverage is the PMC full-text
    subset only. `full_text` is the plain-text body (empty when unavailable).

    Also records the evidence a flat confirmer would throw away — the alias's
    global PMC hit count, the institution named around the match, and which JATS
    block it sits in. `xml` is the RAW PMC XML for the same paper (fulltext.get_xml;
    get() is just to_plain_text of it, so passing both costs no extra fetch);
    without it the section degrades to "" and everything else still works.
    """
    res = SignalResult()
    if not full_text:
        return res
    for alias in core.aliases:
        m = _alias_pattern(alias).search(full_text)
        if m:
            s, e = max(0, m.start() - 70), min(len(full_text), m.end() + 70)
            res.ack_matched = True
            res.ack_alias = alias
            res.ack_snippet = full_text[s:e].strip()
            res.ack_alias_hits = core.alias_hits.get(alias)
            res.ack_institution = classify_institution(full_text, m, core)
            res.ack_section = match_section(xml, alias)
            break
    return res


# ---------------------------------------------------------------------------
# Signal 3b — A2 method families (deterministic join, no I/O)
# ---------------------------------------------------------------------------
def method_family_signal(family_index: dict, pmid: str, core: CoreDefinition):
    """(evidence, tier) for one (publication, core) pair.

    `evidence` is [(family_label, tool_display_name, sentence), ...] — ONE entry per
    curated family that fired, ranked strongest tier first and, inside a tier, in the
    order the dictionary lists them, which is measured-lift order.

    Pure. `family_index` is `method_families.load_family_index()` — the whole join is
    already done, so this is a dict lookup and a tier walk; nothing here reads S3, a DB
    or Bedrock, which is what lets the index be loaded once per run.

    EVERY family keeps its own tool and sentence, not just the top one. Papers carry
    several families at once (median 2 on core 14's live queue, max 4; the artifact's
    ceiling is 9), and the reviewer-facing question is what the paper DID — a bare
    "Regression modeling" with the quote belonging to a different family is a label the
    reviewer cannot check. The join already holds all three fields for every family, so
    carrying them costs a list comprehension; dropping them would cost a full re-scoring
    run to recover, on an engine with no schedule.

    ONE entry per family, not per (family, tool): several tools can sit in the same
    family (up to 6 tool rows behind 4 families on the live queue) and a second tool in a
    family the paper already claims is not a second claim. The tool kept is the first in
    the index's stable sort.

    The TIER returned is the strongest that fired, and only that one. combine() emits a
    single `method:*` key from it: the tiers co-occur (a paper with a 399x family usually
    also does regression), so reporting every tier would invite summing correlated
    evidence.
    """
    rows = family_index.get(str(pmid)) or []
    if not rows or not core.method_families:
        return [], ""
    ranked, seen = [], set()
    for tier in METHOD_FAMILY_TIERS:                      # strongest first
        for label in core.method_families.get(tier, []):  # curator order = lift order
            # Casefolded on both sides: the dictionary side by load_cores, the artifact
            # side here. The label KEPT is the artifact's, so the canonical display form
            # is what reaches DynamoDB.
            hit = next((r for r in rows if r[0].casefold() == label), None)
            # `seen` guards the one duplicate the curator can create: the same label
            # listed under two tiers. It keeps the STRONGER placement, since tiers are
            # walked strongest-first.
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                ranked.append(((hit[0], hit[1], hit[2]), tier))
    if not ranked:
        return [], ""
    return [ev for ev, _tier in ranked], ranked[0][1]


# ---------------------------------------------------------------------------
# Signal 2 — core-staff co-authorship (deterministic, resolved identity)
# ---------------------------------------------------------------------------
def resolved_staff(engine, cwids) -> set:
    """The subset of `cwids` ReCiter has resolved onto at least one author row — i.e.
    the staff coauthorship_index can match TODAY. A live lookup, not a dictionary
    flag: staff come and go, and ReCiter starts (or stops) resolving people over time."""
    from sqlalchemy import bindparam, text  # lazy

    cwids = sorted(set(cwids))
    if not cwids:
        return set()
    stmt = text("SELECT DISTINCT personIdentifier FROM analysis_summary_author "
                "WHERE personIdentifier IN :cwids").bindparams(bindparam("cwids", expanding=True))
    with engine.connect() as conn:
        return {row.personIdentifier for row in conn.execute(stmt, {"cwids": cwids})}


def coauthorship_index(engine, core: CoreDefinition, pmids: list = None,
                       extra_cwids=()) -> dict:
    """Map pmid -> [core-staff CWIDs on its byline], from RESOLVED authorship.

    Source: reciterdb.analysis_summary_author.personIdentifier (resolved CWID per
    author row; mirrored into the SPS DB). Matched on personIdentifier, NEVER on
    name. Every listed staff member is asked for (the dictionary's `staff:` plus
    `extra_cwids`, SPS's curated CORE#{id}/STAFF); one ReCiter has not resolved simply
    matches nothing until it does, so coverage is live rather than a stored flag.
    A staff match is one signal: it raises the likelihood but never confirms on its
    own (combine's hold). Validated: 39% recall / 100% precision on the 237-paper
    pilot, before the large-lab PIs were included.
    """
    from sqlalchemy import bindparam, text  # lazy

    cwids = sorted(set(core.staff_cwids) | set(extra_cwids))
    if not cwids:
        return {}
    sql = (
        "SELECT DISTINCT pmid, personIdentifier FROM analysis_summary_author "
        "WHERE personIdentifier IN :cwids"
    )
    binds = [bindparam("cwids", expanding=True)]
    params = {"cwids": list(cwids)}
    if pmids:
        sql += " AND pmid IN :pmids"
        binds.append(bindparam("pmids", expanding=True))
        params["pmids"] = [int(p) for p in pmids]
    stmt = text(sql).bindparams(*binds)
    out: dict = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, params):
            out.setdefault(str(row.pmid), []).append(row.personIdentifier)
    return out


# ---------------------------------------------------------------------------
# Signal 1 — author x core affinity (repeat-user RATE)
# ---------------------------------------------------------------------------
# How much of an author's OWN output already belongs to this core, not how many
# papers of theirs do. Core users are overwhelmingly repeat users, but a raw
# confirmed-paper count cannot tell a core's regular from someone who passed
# through it four times: the hand-picked curve this replaced (0.45 + 0.15 per
# extra paper, capped at 0.85) put a single author with 4 confirms at the ceiling,
# left 83.9% of core 14's live 347-row queue on that one value, and took 6 distinct
# values in total. The rate separates where the count could not — panel B
# conditional AUC 0.8770 vs 0.8091, 39 distinct values on those same 347 rows with
# a largest tie of 19.3% (vs 83.3%) — which is what lets combine.WEIGHTS price it
# as three fitted buckets instead of a slope through a made-up curve.


# Every number below is from `python3 scripts/fit_evidence_weights.py --affinity-only`
# (panel B: 137 labelled-yes imaging papers vs 1,200 random corpus papers, core staff
# excluded as run_core excludes them) unless it says core 14, which is the 46
# human-decided rows (26 claimed / 20 rejected) of core 14, leave-one-out. Measured
# 2026-10-06. Bucket AUC is what ships (WEIGHTS prices buckets, not the rate).

# Sliding-scale shrinkage toward the core's base rate (empirical Bayes):
#
#     rate = (n + s * p0) / (total + s)
#
# n = the author's confirmed/claimed papers for the core (the scored paper left out —
# self-exclusion, see AffinityIndex.rate), total = their papers in the scoreable corpus,
# p0 = the core's BASE RATE (affinity_base_rate: the corpus share of the core's
# confirmed + claimed papers, computed each run), s = the PRIOR STRENGTH, read as "s
# papers' worth of belief that this author is an average WCM author for this core".
# One confirmation is weak evidence and many are strong, continuously — there is no
# cut-off count below which an author stops counting. At s = 5 with core 14's p0
# (79 / 82,203 = 0.00096 on 2026-10-06): 1-of-1 -> 0.17, 1-of-2 -> 0.14, 3-of-3 -> 0.38,
# 10-of-10 -> 0.67, 1-of-80 -> 0.012. p0 is small for every core (a core's work is a
# sliver of the corpus), so in practice s*p0 barely lifts a rate and s does the work of
# pulling small denominators down. It replaces both the old n / (total + K) (K=1, which
# is this with p0 = 0 and s = K) as the global rate. A per-core hard minimum sits on top
# of it for core 14 only (AFFINITY_MIN_CONFIRMS, below, with the soft-threshold
# measurement that decided it). An
# author with NO other confirmed paper (n = 0 after self-exclusion) still lends 0 — the
# prior shrinks evidence, it never invents it for an author with none (absent evidence
# contributes nothing, as everywhere else in this model).
#
# Global default s = AFFINITY_PRIOR_STRENGTH; a core sets its own with
# `affinity_prior_strength:` in config/core_dictionary.yaml. MEASURED 2026-10-06:
#     s                     0       1       2       5       10      20
#     panel B AUC, rate     0.6788  0.6787  0.6787  0.6785  0.6786  0.6782
#     panel B AUC, bucket   0.6780  0.6776  0.6776  0.6777  0.6781  0.6771
#     core 14 AUC, rate     0.6144  0.6212  0.6250  0.6250  0.6144  0.5894
#     core 14 AUC, bucket   0.5558  0.5558  0.5558  0.5404  0.5654  0.5981
# (panel B = `fit_evidence_weights.py --affinity-only --prior-strength S`, p0 56/82,203;
# core 14 = its 26 claimed vs 20 rejected rows, `measure_affinity_gates.py --strengths`.)
# Panel B is FLAT from 0 to 10 (bucket AUC within 0.0005) and drops at 20: it cannot pick
# s. 5 is the middle of that flat range — least exposed to either end moving as labels
# accrue — and ties core 14's best rate AUC (with 2). s = 0 is no shrinkage (1-of-1 =
# 1.0). Core 14 (46 rows; rate and bucket AUC disagree on direction) does not clearly
# want a different s, so it has no override. At s >= 2 no panel-B rate reaches the 0.70
# `aff:core` edge, so that cell is empty and fitted as `aff:regular`'s (combine.WEIGHTS).
# WHAT WOULD JUSTIFY CHANGING IT: the default, a sweep of
# `python3 scripts/fit_evidence_weights.py --affinity-only --prior-strength S` on panel B
# where another s beats this one on bucket AUC by more than one pair's worth and the
# refit aff:* cells stay monotone (then refit WEIGHTS at it). A per-core override, the
# same sweep on that core's human-decided rows (`python3
# scripts/measure_affinity_gates.py --core <id> --strengths ...`, read-only) once there are
# >= 100 of them and they clearly prefer a different s.
AFFINITY_PRIOR_STRENGTH = 5.0

# p0 when the core's base rate cannot be computed this run (no engine to count the
# corpus — unit tests — or an empty corpus). 0.0 = shrink toward "no affinity", i.e.
# rate = n / (total + s): the conservative choice, since it can only LOWER a rate
# relative to any real p0 (every real p0 is >= 0). Production always computes p0.
AFFINITY_BASE_RATE_FALLBACK = 0.0


def affinity_prior_strength(core) -> float:
    """The core's `affinity_prior_strength` (core_dictionary.yaml), else the global default."""
    value = getattr(core, "affinity_prior_strength", None) if core is not None else None
    return AFFINITY_PRIOR_STRENGTH if value is None else float(value)


def affinity_base_rate(n_core_papers: int, corpus_size) -> float:
    """p0: the share of the scoreable corpus that is this core's confirmed/claimed work.

    Same universe as each author's rate (both sides corpus-gated), so p0 is what an
    author's rate would be if they used the core exactly as often as WCM does on
    average. AFFINITY_BASE_RATE_FALLBACK when the corpus size is unknown or 0."""
    if not corpus_size:
        return AFFINITY_BASE_RATE_FALLBACK
    return min(max(n_core_papers / corpus_size, 0.0), 1.0)

# SOFT THRESHOLD on the repeat-user count (per core; global default OFF, no core sets it):
#
#     affinity = rate * g(n),   g(n) = n^h / (n^h + c^h)
#
# n = the author's confirmed/claimed papers for this core counted the way a minimum
# would count them: inside their tenure window, undecayed, the scored paper itself left
# out (self-exclusion). c is the count at which g = 0.5, h the steepness. At c = 3,
# h = 2: 1 confirmation -> 0.10, 2 -> 0.31, 3 -> 0.50, 5 -> 0.74, 10 -> 0.92. Large h
# approaches a hard minimum (h = 8: 2 -> 0.04, 3 -> 0.5, 4 -> 0.91); h = 1 is gentle.
# g(0) = 0 and g rises strictly with n, so it never makes a zero rate positive or a
# positive one zero: "does the prior fire" (batch_screen) cannot change. It is a
# MULTIPLIER on the (already shrunk) rate, so it moves rates across the 0.05 / 0.70
# bucket edges, and a core running it globally would need the `aff:*` weights refitted
# with the same g.
#
# MEASURED 2026-10-06 (read-only), pre-registered primary c = 3, h = 2 at s = 5, against
# the bar "bucket AUC >= 0.66 on core 14 (claimed 26 vs rejected 20) AND on panel B":
#                                  core 14 AUC         panel B AUC        panel B refit
#                                  rate    bucket      rate    bucket     trace/reg/core
#   sliding s=5, g=1 (global)      0.6250  0.5404      0.6785  0.6777     1.18/3.49/3.49
#   s=5, c=3 h=2 (primary)         0.5913  0.5981      0.6780  0.6755     2.38/3.19/3.19
#   s=5, minimum 3 (core 14)       0.7279  0.6962      0.5632  0.5632     3.01/3.20/3.20
# Grid c in {2,3,4} x h in {1,2,4,8}, at s = 5 and at s = 0: core 14 bucket AUC 0.5712 to
# 0.6144 everywhere (never >= 0.66); panel B bucket 0.6754 to 0.6783. The soft threshold
# keeps the prior on 17 of the 20 rejected core-14 rows at every setting — it shrinks
# them, it never removes them — and the minimum of 3 helped there by REMOVING 13 of them
# (4/20 left). So the primary FAILED the core-14 bar and is not shipped anywhere; the
# decision rule restored the minimum of 3 as a core-14 setting (AFFINITY_MIN_CONFIRMS).
# Paired bootstrap (2000 resamples, n = 46): c3/h2 minus minimum-3 bucket AUC -0.098
# [95% CI -0.181, -0.021]; c3/h2 minus sliding +0.058 [+0.010, +0.120].
# Kept as a per-core key (`affinity_soft_threshold: {c: 3, h: 2}`, or `false`) so it can
# be re-measured at >= 100 decided rows:
#   python3 scripts/measure_affinity_gates.py --core <id> --min-confirms 1 3 \
#       --soft-threshold off --soft-threshold 3 2 --bootstrap 2000
#   python3 scripts/fit_evidence_weights.py --affinity-only --soft-threshold 3 2
AFFINITY_SOFT_THRESHOLD = None


def soft_threshold_gate(n: float, threshold) -> float:
    """g(n) = n^h / (n^h + c^h) for threshold (c, h); 1.0 when threshold is None.

    n <= 0 gives 0 (an author with no other confirmed paper lends nothing either way)."""
    if threshold is None:
        return 1.0
    if n <= 0:
        return 0.0
    c, h = threshold
    # n^h / (n^h + c^h) == 1 / (1 + (c/n)^h): no overflow at large h.
    return 1.0 / (1.0 + (c / n) ** h)


def affinity_soft_threshold(core):
    """The core's `affinity_soft_threshold` as (c, h), else the global default (None = off)."""
    value = getattr(core, "affinity_soft_threshold", None) if core is not None else None
    if value is None:
        return AFFINITY_SOFT_THRESHOLD
    return None if value is False else tuple(value)   # False = this core explicitly off


# HARD MINIMUM on the same count n (per core; global default 1 = no minimum). An author
# with n < the core's minimum lends 0 — not a smaller rate — and above it the shrunk rate
# is unchanged. Core 14 sets 3 (`affinity_min_confirms: 3`, core_dictionary.yaml) because
# the soft threshold failed its pre-registered bar there (table above): on core 14's 46
# human-decided rows the minimum of 3 at s = 5 gives AUC 0.7279 rate / 0.6962 bucket vs
# 0.6250 / 0.5404 for the sliding scale alone (paired bootstrap, 2000 resamples: +0.103
# [+0.034, +0.185] rate, +0.156 [+0.075, +0.240] bucket). n = 46, so those CIs are wide.
# NOT global: on core 2's panel B the same minimum drops bucket AUC 0.6777 -> 0.5632.
# The fit (panel B, core 2) runs at the global default, so a core's minimum removes
# authors from the feature and never reprices a surviving rate.
# WHAT WOULD JUSTIFY CHANGING IT: the claimed-vs-rejected sweep on that core's rows at
# >= 100 decided rows (`python3 scripts/measure_affinity_gates.py --core <id>
# --min-confirms 1 2 3 --bootstrap 2000`); a global default other than 1 only if it
# holds on two or more cores AND panel B does not drop.
AFFINITY_MIN_CONFIRMS = 1


def affinity_min_confirms(core) -> int:
    """The core's `affinity_min_confirms` (core_dictionary.yaml), else the global default."""
    value = getattr(core, "affinity_min_confirms", None) if core is not None else None
    return AFFINITY_MIN_CONFIRMS if value is None else int(value)


# Per-confirmation time decay, as a half-life in years on |scored paper year - confirmed
# paper year|, applied to numerator AND denominator alike (so a decayed rate is still a
# share of the author's own output, just recency-weighted). OFF (None): on panel B decay
# never helps and short half-lives hurt — bucket AUC 0.6770 / 0.6777 / 0.6785 / 0.6786 /
# 0.6786 at 1 / 2 / 3 / 5 / 8 years vs 0.6786 off — and the core-14 rows are too few to
# say otherwise (non-monotone: 2y 0.6038, 3y 0.6212, 5y 0.6192, off 0.6144, at K=0).
# The corpus starts in 2020, so the spread decay can act on is ~6 years.
# MEASUREMENT PLAN before turning it on: `fit_evidence_weights.py --affinity-only
# --half-life H` for H in 2/3/5/8, plus the same sweep on a core's human-decided
# (claimed vs rejected) rows once there are >=100 of them — SPS claim decisions on core
# 14 are the panel that grows. Ship H only if it beats OFF on both by more than one
# pair's worth of AUC and the refit buckets stay monotone; then refit WEIGHTS with it.
AFFINITY_HALF_LIFE_YEARS = None

# Tenure window lags, in years, around identity's WCM appointment span (see
# ingest.fetch_author_tenure). An author's history only lights up a paper published
# inside [start - BEFORE, end + AFTER], and only confirmations / corpus papers inside
# that window build the rate (numerator and denominator gated alike).
#   BEFORE = 3: identity's start is the FACULTY (or student) appointment, not arrival.
#   Postdocs, fellows and research staff who later join the faculty carry no earlier
#   date, so a tight start gate drops real users — panel B bucket AUC 0.6722 / 0.6715
#   / 0.6753 at BEFORE = 0 / 1 / 2 (human-labelled "yes" papers lose their author),
#   back to 0.6786 at 3 and flat beyond. Smallest lag that costs nothing.
#   AFTER = 2: NOT measurable here (panel B is flat for AFTER 0..5: no labelled paper
#   post-dates its author's departure). Chosen on the publishing tail — work done at WCM
#   keeps appearing for a year or two after someone leaves — and it is the side identity
#   records reliably. On core 14 the whole gate moves 1 of 486 open candidates.
TENURE_LAG_BEFORE = 3
TENURE_LAG_AFTER = 2


def tenure_window(span):
    """(earliest, latest) publication year an author's affinity may speak for, or None.

    `span` is (start_year, end_year) from ingest.fetch_author_tenure; end None means
    still here (open-ended). None, or no start, means unknown, which is NOT gated —
    absent evidence takes nothing away, as everywhere else in this model."""
    if not span or not span[0]:
        return None
    start, end = span
    return (start - TENURE_LAG_BEFORE, end + TENURE_LAG_AFTER if end else None)


def _in_window(year, window) -> bool:
    if year is None or window is None:
        return True
    lo, hi = window
    return year >= lo and (hi is None or year <= hi)


def _by_year(value) -> dict:
    """{year|None: n}. A bare count is a year-UNKNOWN count, which neither the tenure
    gate nor decay can act on; callers without years (batch_screen's selection, the
    spread script) keep the plain-count contract they had."""
    if isinstance(value, dict):
        return {y: n for y, n in value.items() if n}
    return {None: value} if value else {}


class AffinityIndex:
    """cwid x core -> repeat-user RATE, resolved per scored paper's year.

    Not a plain dict any more because the rate is no longer one number per author: the
    tenure gate depends on WHEN the scored paper was published, and so would decay."""

    def __init__(self, papers: dict, totals: dict, windows: dict, *,
                 prior_strength, half_life, members: dict | None = None,
                 base_rate=None, soft_threshold=None, min_confirms=None):
        self.papers, self.totals, self.windows = papers, totals, windows
        self.half_life = half_life
        # Each a number (every core) or {core_id: number}; a core missing from a dict
        # falls back to AFFINITY_PRIOR_STRENGTH / AFFINITY_BASE_RATE_FALLBACK.
        self.prior_strength = prior_strength
        self.base_rate = AFFINITY_BASE_RATE_FALLBACK if base_rate is None else base_rate
        # None / (c, h) for every core, or {core_id: None | (c, h)}; a core missing from
        # the dict falls back to AFFINITY_SOFT_THRESHOLD.
        self.soft_threshold = soft_threshold
        # int (every core) or {core_id: int}; a core missing from the dict falls back to
        # AFFINITY_MIN_CONFIRMS.
        self.min_confirms = AFFINITY_MIN_CONFIRMS if min_confirms is None else min_confirms
        # cwid -> {core_id: {pmid, ...}}: WHICH papers make up each numerator, kept so
        # rate() can leave the scored paper out of its own prior (self-exclusion).
        self.members = members or {}

    def _w(self, year, ref) -> float:
        if self.half_life is None or year is None or ref is None:
            return 1.0
        return 0.5 ** (abs(ref - year) / self.half_life)

    def strength_for(self, core_id: str) -> float:
        if isinstance(self.prior_strength, dict):
            return float(self.prior_strength.get(core_id, AFFINITY_PRIOR_STRENGTH))
        return float(self.prior_strength)

    def soft_threshold_for(self, core_id: str):
        if isinstance(self.soft_threshold, dict):
            return self.soft_threshold.get(core_id, AFFINITY_SOFT_THRESHOLD)
        return self.soft_threshold

    def min_for(self, core_id: str) -> int:
        if isinstance(self.min_confirms, dict):
            return int(self.min_confirms.get(core_id, AFFINITY_MIN_CONFIRMS))
        return int(self.min_confirms)

    def gate(self, core_id: str, n_excl: float) -> float:
        """The multiplier on the rate for an author with n_excl confirmations: 0 below the
        core's minimum, else g(n_excl) (soft_threshold_gate; 1.0 when it has none)."""
        if n_excl < self.min_for(core_id):
            return 0.0
        return soft_threshold_gate(n_excl, self.soft_threshold_for(core_id))

    def base_rate_for(self, core_id: str) -> float:
        if isinstance(self.base_rate, dict):
            return float(self.base_rate.get(core_id, AFFINITY_BASE_RATE_FALLBACK))
        return float(self.base_rate)

    def rate(self, cwid: str, core_id: str, year=None, *, pmid=None) -> float:
        """The author's rate for a paper published in `year`, leaving that paper out.

        `pmid` is the paper being scored. It is subtracted from the author's NUMERATOR
        when it is one of their confirmed/claimed papers for this core (weight 1: the
        paper is its own decay reference and, if the gate let the author speak for it
        at all, inside their tenure window). It is NOT subtracted from the denominator.
        Measured 2026-10-06 on core 14's 46 human-decided rows (26 claimed / 20
        rejected): affinity AUC 0.6212 numerator-only vs 0.6115 with the denominator
        side too (gate + K=1). The denominator is "the author's corpus output", of
        which the scored paper is honestly a part; only its LABEL was the leak. None =
        nothing excluded (callers that never score a paper from their own numerator,
        e.g. batch_screen's pool, which drops confirmed pubs).

        rate = (n + s * p0) / (total + s), s and p0 this core's (AFFINITY_PRIOR_STRENGTH
        and affinity_base_rate). n = 0 after the exclusion still reads 0: the prior
        shrinks an author's evidence toward the core's base rate, it does not hand an
        author with no other confirmed paper the base rate for free.

        MINIMUM / SOFT THRESHOLD (gate): n_excl = the author's in-tenure confirmations
        for this core counted UNDECAYED with this paper left out. Below the core's
        minimum (AFFINITY_MIN_CONFIRMS) the author lends 0; otherwise the rate is
        multiplied by g(n_excl) (AFFINITY_SOFT_THRESHOLD), 1 for a core without one.
        """
        by_year = self.papers.get(cwid, {}).get(core_id)
        if not by_year or not _in_window(year, self.windows.get(cwid)):
            return 0.0
        own = pmid is not None and pmid in self.members.get(cwid, {}).get(core_id, ())
        num = sum(n * self._w(y, year) for y, n in by_year.items()) - (1.0 if own else 0.0)
        den = sum(n * self._w(y, year) for y, n in self.totals[cwid].items())
        if num <= 1e-9 or den <= 1e-9:
            return 0.0
        s = self.strength_for(core_id)
        rate = min((num + s * self.base_rate_for(core_id)) / (den + s), 1.0)
        return rate * self.gate(core_id, sum(by_year.values()) - (1 if own else 0))


_DEFAULT = object()


def build_affinity_index(user_paper_counts: dict, author_totals: dict, *, tenure: dict = None,
                         prior_strength=_DEFAULT, base_rate=None, half_life=_DEFAULT,
                         members: dict | None = None,
                         soft_threshold=_DEFAULT, min_confirms=None) -> AffinityIndex:
    """cwid x core -> each author's SHARE of their own corpus output that already
    belongs to the core, as an AffinityIndex.

    `user_paper_counts` maps cwid -> {core_id: n} or cwid -> {core_id: {year: n}},
    aggregated from this run's confirmations plus prior confirmed/claimed records.
    `author_totals` maps cwid -> that author's TOTAL papers in the scoreable WCM
    corpus (ingest.fetch_author_totals), likewise a count or {year: n}. The
    denominator is the CORPUS (~82k pubs), NOT all of analysis_summary_author
    (392,769 pmids): the unrestricted form deflates every rate by ~4x, which walks
    real core regulars down out of the top bucket.

    `tenure` maps cwid -> (start_year, end_year) (ingest.fetch_author_tenure). Per
    author, confirmations and corpus papers outside tenure_window() are dropped from
    BOTH sides of the ratio before it is taken — an out-of-tenure confirmation should
    not build the rate, and an out-of-tenure paper should not dilute it. An author with
    no tenure row, or a year-unknown count, is not gated.

    rate = (n + s * p0) / (total + s) — see AFFINITY_PRIOR_STRENGTH — and recency-weighted
    by `half_life` when set (AFFINITY_HALF_LIFE_YEARS; None = off). `prior_strength` (s)
    and `base_rate` (p0) are each a number (every core) or {core_id: number}; omitted =
    AFFINITY_PRIOR_STRENGTH and AFFINITY_BASE_RATE_FALLBACK. Callers that score one core
    pass {core.core_id: affinity_prior_strength(core)} so the core_dictionary.yaml key
    applies, and {core.core_id: affinity_base_rate(...)} computed this run.

    `soft_threshold` is None / (c, h) or {core_id: None | (c, h)}: the multiplier g(n)
    on each rate (AFFINITY_SOFT_THRESHOLD; omitted = that global default). Callers that
    score one core pass {core.core_id: affinity_soft_threshold(core)}. `min_confirms` is
    an int or {core_id: int} (AFFINITY_MIN_CONFIRMS; omitted = 1): below it an author
    lends 0. Callers pass {core.core_id: affinity_min_confirms(core)}.

    `members` is the cwid -> {core_id: {pmid, ...}} the counts were made from. Pass it
    whenever a scored paper can be one of those pmids (run_core: a paper an earlier run
    confirmed, or a human claimed, is re-scored every run). Without it a paper's OWN
    prior confirmation sits in its byline's numerator and keeps it confirmed with its
    own label — a self-confirmation loop (see author_affinity).

    An author with NO corpus total is DROPPED, not floored. "We could not measure this
    author's output" and "this author's entire output is this core's work" are opposite
    claims, and flooring conflated them into rate 1.0 = `aff:core`, which (at the then
    +4.93 nats) alone cleared DEFAULT_CONFIRM_THRESHOLD — so an unmeasurable author
    auto-confirmed every paper they touched, with no acknowledgement, no staff
    co-author and no LLM score behind it. Absent evidence contributes nothing here, as everywhere else.

    n > total should be impossible once the caller gates its numerator through
    ingest.filter_corpus_pmids (both sides of the ratio on the corpus), so it is clamped
    AND logged rather than silently floored: it means a caller skipped the gate.
    """
    prior_strength = AFFINITY_PRIOR_STRENGTH if prior_strength is _DEFAULT else prior_strength
    half_life = AFFINITY_HALF_LIFE_YEARS if half_life is _DEFAULT else half_life
    soft_threshold = AFFINITY_SOFT_THRESHOLD if soft_threshold is _DEFAULT else soft_threshold
    tenure = tenure or {}
    papers, totals, windows = {}, {}, {}
    missing, over = [], []
    for cwid, by_core in user_paper_counts.items():
        window = tenure_window(tenure.get(cwid))
        total = {y: n for y, n in _by_year(author_totals.get(cwid, 0)).items()
                 if _in_window(y, window)}
        if not sum(total.values()):
            missing.append(cwid)
            continue
        gated = {}
        for core_id, value in by_core.items():
            kept = {y: n for y, n in _by_year(value).items() if _in_window(y, window)}
            if kept:
                gated[core_id] = kept
        if not gated:
            continue
        if any(sum(v.values()) > sum(total.values()) for v in gated.values()):
            over.append(cwid)
        papers[cwid], totals[cwid], windows[cwid] = gated, total, window
    if missing:
        logger.warning("build_affinity_index: %d authors have no corpus paper total and "
                       "were DROPPED from the affinity index (rate unknown, not maximal)",
                       len(missing))
    if over:
        logger.warning("build_affinity_index: %d authors have more core confirms than "
                       "corpus papers — the numerator was not gated through "
                       "ingest.filter_corpus_pmids; rates clamped to 1.0", len(over))
    return AffinityIndex(papers, totals, windows, prior_strength=prior_strength,
                         half_life=half_life, members=members, base_rate=base_rate,
                         soft_threshold=soft_threshold, min_confirms=min_confirms)


def author_affinity(affinity_index: AffinityIndex, byline_cwids: list, core_id: str,
                    pub_year=None, *, pmid=None) -> float:
    """Prior that THIS paper used the core: the MAX rate across its byline.

    MAX, not noisy-OR. Noisy-OR over the byline is a monotone function of HOW MANY
    authors fire, and that count is the one thing measured NOT to separate here
    (panel B AUC 0.6505, and its curve turns over past k=2). One author whose work
    is largely this core's work is the evidence; four people who each used the core
    once are not four times that, and under noisy-OR they outscored the core's
    heaviest single user. Returns 0.0 when no author on the byline has any history
    with the core.

    `pub_year` is the scored paper's year. Each author is gated on it individually
    (tenure_window): an author who had left WCM years before this paper lends it
    nothing, while a co-author still here keeps theirs — the PAPER is not dropped, and
    the acknowledgement / staff signals never pass through here at all. None = not
    gated (and no decay reference).

    SELF-EXCLUSION (numerator only). `pmid` is the paper being scored, and each
    author's numerator leaves it out: n - [pmid is one of their confirmed/claimed papers
    for this core]. The denominator keeps it (see AffinityIndex.rate for the measurement
    that decided that). Without it the prior was circular. A
    paper confirmed (or claimed) once is re-scored on every run, and its own row sat in
    each byline author's numerator, so it kept itself confirmed with its own label: all
    47 of core 14's affinity-only confirmations counted themselves, and 1 had no other
    confirmed paper behind it at all. The fit already scores papers this way (labelled
    papers are never in its numerator), so leaving the paper out is also what makes the
    production feature the one the weights were fitted on. A 3-of-3 author scoring one
    of their own three reads as 2-of-3 ((2 + s*p0) / (3 + s)); an author whose only
    confirmation is this paper reads 0.
    """
    return max((affinity_index.rate(cwid, core_id, pub_year, pmid=pmid)
                for cwid in byline_cwids), default=0.0)


# ---------------------------------------------------------------------------
# Signal 4 — LLM triage (two-pass Bedrock; ranking only, never auto-label)
# ---------------------------------------------------------------------------
# Recall-first screen cutoff: a pub whose Haiku screen score is >= this advances
# to Sonnet dense scoring. Calibrated on the 237-paper pilot (Bedrock Haiku 4.5):
# cutoff 2 retains 100% of true positives at the screen, deliberately below the
# combiner's surface threshold (score>=3) so dense scoring, not the cheap screen,
# decides what is surfaced. Dense (Sonnet 4.6) AUC vs human labels = 0.933;
# at score>=3 -> 94% precision / 83% recall.
SCREEN_CUTOFF = 2


def _screen_prompt(core: CoreDefinition, title: str, abstract: str) -> str:
    return (
        "Quickly screen whether a WCM institutional core facility was plausibly "
        f"USED to produce this research.\n\nCore: \"{core.name}: {core.llm_description}\"\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "From title+abstract only, reply with a single integer 1 (clearly unrelated) "
        "to 10 (clearly used the core). When unsure, lean higher. Integer only."
    )


def _screen_all_prompt(cores: list, title: str, abstract: str) -> str:
    catalog = "\n".join(f'  "{c.core_id}": {c.name} — {c.llm_description}' for c in cores)
    return (
        "Quickly screen which WCM institutional core facilities were plausibly "
        "USED to produce this research, judging from title+abstract only.\n\n"
        f"Cores (id: name — what it does):\n{catalog}\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "For EACH core id give an integer 1-10 for how plausibly that core was USED to "
        "produce this work. Be recall-first: give at least 2 to any core whose involvement "
        "is even slightly plausible from the methods the title/abstract imply, and reserve 1 "
        "only for cores clearly unrelated to the work. When unsure, lean higher. "
        'Return ONLY a JSON object mapping every core id to its integer, e.g. {"2": 7, "5": 1}. '
        "Include every core id."
    )


def _parse_core_scores(raw, cores: list) -> dict:
    """Coerce a model JSON reply into {core_id: int 1-10}; missing id -> 1."""
    raw = raw if isinstance(raw, dict) else {}    # a valid-but-non-dict reply (array/int) -> screen out
    return {c.core_id: _parse_int(raw.get(c.core_id, raw.get(str(c.core_id), 1))) for c in cores}


def screen_all_cores(bedrock, cores: list, pubs: list) -> dict:
    """One Haiku call per pub screens it against ALL cores at once.

    Returns {pmid: {core_id: screen_int}}. This is the production cost lever:
    ~13x fewer Haiku screen calls than screening each core separately (one call
    per pub instead of one per pub*core). A pub the safety filter drops, or whose
    reply omits a core, defaults that core to 1 (screened out) — dense scoring is
    where anything at/above SCREEN_CUTOFF is re-judged per core.

    CALIBRATION (237-paper pilot, 2026-06-21, analysis/calibrate_all_cores_screen.py):
    this lost screen recall vs the per-core screen — 89.1% (vs 100%), 15 TP
    regressions that collapse to score 1 (judging 13 cores at once makes Haiku too
    decisive, erasing the recall-first "unsure -> 2" band). A recall-first prompt
    floor recovered it only to 93.4% (9 regressions) while ~doubling dense fan-out
    (2.93 -> 4.85 cores/pub). So this path is OPT-IN (run.py --all-cores-screen) and
    NOT the default until it reaches per-core parity; the per-core screen carries the
    full run. Re-run the calibration before flipping it on.
    """
    from utils.bedrock_client import HAIKU_MODEL, BedrockEmptyContentError  # lazy

    out: dict = {}
    for pub in pubs:
        pmid, title, abstract = str(pub["pmid"]), pub.get("title", ""), pub.get("abstract", "")
        try:
            raw = bedrock.call_json(
                model=HAIKU_MODEL,
                messages=[{"role": "user", "content": _screen_all_prompt(cores, title, abstract)}],
            )
            out[pmid] = _parse_core_scores(raw, cores)
        except BedrockEmptyContentError:
            out[pmid] = {c.core_id: 1 for c in cores}  # safety filter -> screen all out
    return out


# ---------------------------------------------------------------------------
# Batched one-core screen (the batch_screen run-mode) — Sonnet, title-only
# ---------------------------------------------------------------------------
# Validated mechanism (analysis/prototype_one_core_synopsis.py + the cores-subheadings
# FINDINGS): ONE core per call, ~40 papers/batch, TITLE input recovers 95-100% recall
# where the all-cores batch capped at 88% — focus is the lever (Sonnet ~= Opus). Returns
# a graded confidence 1-10 per pmid that feeds the candidate/curator/drop bands. Title
# keeps it affordable (the ~$30-280 frontier); abstract buys precision, not recall.

def _batch_screen_prompt(core: CoreDefinition, papers: list) -> str:
    body = "\n\n".join(f"PMID {p['pmid']}\nTitle: {p.get('title', '')}" for p in papers)
    return (
        "You screen WCM publications for use of ONE specific institutional core "
        f"facility.\n\nCORE: {core.name} — {core.llm_description}\n\n"
        f"PAPERS:\n{body}\n\n"
        "For EACH paper, judge from its title whether it plausibly USED this core's "
        "instruments/services. Be recall-first (include if plausibly used). Reply ONLY "
        "with JSON mapping each PMID (string) to a confidence integer 1-10 "
        '(10=clearly used, 1=clearly not), e.g. {"12345678": 8, "23456789": 1}. '
        "Include every PMID."
    )


def batched_one_core_screen(bedrock, core: CoreDefinition, pubs: list, *,
                            batch_size: int = 40, max_workers: int = 4) -> dict:
    """Map pmid -> confidence 1-10 for ONE core, batching ~`batch_size` pubs per Sonnet call.

    Title-only. Threaded across batches (Bedrock is I/O-bound); a batch that errors or a
    pmid the reply omits defaults to 1 (screened low) rather than killing the run. Returns
    {} for empty input. Pair with a short-read_timeout BedrockClient so a hung call fails
    fast. Use max_workers<=1 for deterministic serial scoring (tests).
    """
    from utils.bedrock_client import SONNET_MODEL  # lazy

    if not pubs:
        return {}
    if bedrock is None:                            # dry-run / no --with-llm: screen everything low
        return {str(p["pmid"]): 1 for p in pubs}
    batches = [pubs[i:i + batch_size] for i in range(0, len(pubs), batch_size)]

    def _run(batch):
        try:
            raw = bedrock.call_json(
                model=SONNET_MODEL,
                messages=[{"role": "user", "content": _batch_screen_prompt(core, batch)}],
                max_tokens=4096,
            )
        except Exception as exc:  # noqa: BLE001 — one bad batch must not kill the run
            logger.warning("batch screen failed for core %s (%s) -> %d pmids screened low",
                           core.core_id, type(exc).__name__, len(batch))
            raw = {}
        if not isinstance(raw, dict):              # a valid-but-non-dict reply (array/int) must not crash the run
            logger.warning("batch screen for core %s returned non-dict %s -> %d pmids screened low",
                           core.core_id, type(raw).__name__, len(batch))
            raw = {}
        result = {str(p["pmid"]): _parse_int(raw.get(str(p["pmid"]), 1)) for p in batch}
        omitted = sum(1 for p in batch if str(p["pmid"]) not in raw)
        if raw and omitted:                        # genuine omission (e.g. max_tokens truncation), not a failed batch
            logger.warning("batch screen for core %s omitted %d/%d pmids -> screened low",
                           core.core_id, omitted, len(batch))
        return result

    scores: dict = {}
    if max_workers and max_workers > 1 and len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed  # lazy

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed([ex.submit(_run, b) for b in batches]):
                scores.update(fut.result())
    else:
        for b in batches:
            scores.update(_run(b))
    return scores


def _dense_prompt(core: CoreDefinition, title: str, abstract: str) -> str:
    return (
        "Score whether a specific WCM core facility was USED to produce this "
        f"research.\n\nCore facility: \"{core.name}: {core.llm_description}\"\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "Judge ONLY from title+abstract. A paper whose methods plainly required "
        "the core's instrumentation scores high; incidental/clinical mentions of "
        "the modality, or use of a different facility, score low.\n"
        'Return ONLY JSON: {"score": <int 1-10>, "rationale": "<=80 chars"}'
    )


_RATIONALE_MAX = 80


def _fit(text: str, width: int = _RATIONALE_MAX) -> str:
    """`text` capped at `width`, cut on a word boundary rather than mid-word.

    The prompt already asks for <=80 chars (line ~421), so this only fires when
    the model overruns its own budget. Under the cap the string is returned
    untouched — including its internal whitespace, which is why this is a plain
    slice and not textwrap.shorten (shorten also drops every word after an
    oversized token, which is the failure this guards against).
    """
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    head = cut.rpartition(" ")[0]
    # Prefer the word boundary, but not at any price. "Uses core: <90-char URL>"
    # has its only space at index 10, so backing up to it yields a 3-character
    # evidence chip — strictly worse than the mid-word slice this replaced.
    # ponytail: half the budget is the give-up line; it only has to separate
    # "trimmed a partial word" from "threw the rationale away".
    return (head if len(head) >= width // 2 else cut) + "…"


def _triage_one(bedrock, core: CoreDefinition, pub: dict, screen_map: dict):
    """Score a single pub: Haiku screen (or shared screen) then Sonnet dense pass."""
    from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL, BedrockEmptyContentError  # lazy

    pmid, title, abstract = str(pub["pmid"]), pub.get("title", ""), pub.get("abstract", "")
    if screen_map is not None:
        screen = screen_map.get(pmid, {}).get(core.core_id, 1)  # shared all-cores screen
    else:
        try:
            screen = _parse_int(bedrock.call(model=HAIKU_MODEL,
                                             messages=[{"role": "user", "content": _screen_prompt(core, title, abstract)}]))
        except BedrockEmptyContentError:
            screen = 1  # safety-filter on a biomedical abstract -> treat as screened out
    rec = {"screen": screen, "score": screen, "rationale": ""}
    if screen >= SCREEN_CUTOFF:
        try:
            dense = bedrock.call_json(model=SONNET_MODEL,
                                      messages=[{"role": "user", "content": _dense_prompt(core, title, abstract)}])
            rec["score"] = _parse_int(dense.get("score"))
            # Cut on a word boundary: the bare [:80] slice this replaces left
            # rationales ending "retrospective coho" in the live claim queue.
            # NOT textwrap.shorten — it drops every word after an oversized
            # token, so "Uses core: <100-char URL>" comes back as "Uses core:…",
            # a 3-character evidence chip strictly worse than the slice it
            # replaced. Trimming back to the last space in the slice has no such
            # case: with no space to find it degrades to exactly the old 80 chars.
            rec["rationale"] = _fit(str(dense.get("rationale", "")))
        except BedrockEmptyContentError:
            pass  # keep the screen score; no rationale
    return pmid, rec


def llm_triage(bedrock, core: CoreDefinition, pubs: list, screen_map: dict = None,
               max_workers: int = 8) -> dict:
    """Map pmid -> {"screen", "score", "rationale"} from title+abstract.

    Two-pass, mirroring score_publications.py: Pass 1 Haiku screen (recall-first,
    cheap); Pass 2 Sonnet dense score + rationale only for pubs at/above
    SCREEN_CUTOFF. `score` is the dense score when computed, else the screen score.
    Conservative in validation (4% FP on clear negatives) — used to RANK the claim
    queue, never to auto-label.

    `screen_map` is the precomputed all-cores screen ({pmid: {core_id: int}} from
    screen_all_cores): when supplied, Pass 1 reads the shared screen for THIS core
    instead of a per-core Haiku call. Omit it for the per-core Haiku screen (the
    calibration-validated default).

    THREADED: pubs are scored concurrently with `max_workers` threads (Bedrock
    calls are I/O-bound). A serial loop over a full corpus is both slow and
    fragile — one slow/hung Converse call would block every subsequent pub up to
    the client read_timeout — so per-pub scoring is fanned out and any pub that
    errors (timeout/throttle/etc.) is logged and screened out (score 1) rather
    than killing the batch. Pair with a short-read_timeout BedrockClient (run.py
    builds one) so a hung call fails fast instead of stalling a worker. Use
    max_workers<=1 for deterministic serial scoring.
    """
    if not pubs:
        return {}

    out: dict = {}
    failures = 0

    def _safe(pub):
        return _triage_one(bedrock, core, pub, screen_map)

    if max_workers and max_workers > 1 and len(pubs) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed  # lazy

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_safe, p): str(p["pmid"]) for p in pubs}
            for fut in as_completed(futs):
                pmid = futs[fut]
                try:
                    pmid, rec = fut.result()
                except Exception as exc:  # noqa: BLE001 — resilience: never let one pub kill the batch
                    failures += 1
                    logger.warning("triage failed for pmid %s (%s) -> screened out", pmid, type(exc).__name__)
                    rec = {"screen": 1, "score": 1, "rationale": ""}
                out[pmid] = rec
    else:
        for pub in pubs:
            pmid = str(pub["pmid"])
            try:
                pmid, rec = _safe(pub)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning("triage failed for pmid %s (%s) -> screened out", pmid, type(exc).__name__)
                rec = {"screen": 1, "score": 1, "rationale": ""}
            out[pmid] = rec

    if failures:
        logger.warning("llm_triage: %d/%d pubs failed scoring and were screened out", failures, len(pubs))
    return out


def _parse_int(raw) -> int:
    if isinstance(raw, (int, float)):
        return max(1, min(10, int(round(raw))))
    m = re.search(r"\b(10|[1-9])\b", str(raw or ""))
    return int(m.group(1)) if m else 1
