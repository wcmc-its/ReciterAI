"""Merge signal layers into one (publication, core) likelihood + status.

Every signal is EVIDENCE carrying a weight, and the score is one line of log-odds:

    logit(P) = PRIOR_LOGIT + sum(w_i for each piece of evidence present)
    P        = 1 / (1 + exp(-logit))

Naive Bayes on purpose. It is one line, every feature's contribution is separately
inspectable (`explain()` — the claim queue can show a reviewer WHY a paper ranks
where it does), and it spreads by construction. Correlated features are handled by
not double-counting them, not by a fancier model.

WHAT THIS REPLACED, and why. `combine()` used to return the constant 0.98 for any
acknowledgement match and 0.95 for any staff co-authorship, then noisy-OR the LLM
score and affinity for everything else. Both mechanisms destroyed spread:

  * the constants could not tell "Architecture for Research Computing in Health,
    Weill Cornell" (measured 100% precision) from "Flow Cytometry Core ... Stanford"
    (24%). They scored identically.
  * noisy-OR over values already near 1 saturates — all 404 core-14 candidates
    landed in 0.84-0.85, i.e. an unrankable queue.

Log-odds keeps each increment visible instead.

STATUS IS NOW A THRESHOLD ON THE SCORE (approved 2026-09-03), not a deterministic
flag: a 100%-precision alias match simply earns a very large weight and clears
CONFIRM_THRESHOLD on its own. Both thresholds are per-core overridable from
`core_dictionary.yaml`, because a core whose aliases are all generic deserves a
higher bar than one with a proper name nobody else uses.

RESCORING AN EXISTING POOL still needs a reconcile-by-timestamp pass: `run_core`
never emits below_threshold, so a re-run cannot demote anything already written to
DynamoDB. Not built here, and nothing here makes it harder.
"""
from __future__ import annotations

import math

from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    CoreUsageRecord,
    SignalResult,
)

# Prior odds that a random corpus paper used any ONE given core. The 2026-06 random
# WCM sample put a core's base rate at 1-5% of papers (0/267 named the imaging core);
# 2% is the middle of that. logit(0.02) = -3.89. Everything above is evidence moving
# these odds, so this constant is the only place the base rate lives.
PRIOR_LOGIT = math.log(0.02 / 0.98)

# ---------------------------------------------------------------------------
# The weights. w = log( P(e | used the core) / P(e | did not) ), FITTED by
# scripts/fit_evidence_weights.py — that script is the provenance and re-running it
# is how these refresh. Every entry carries the n behind it; a cell nobody can
# support is 0.00 and says so instead of being invented.
#
# THREE PANELS, because one ground truth cannot fit all of it without circularity:
#   A  278 per-core SIGNAL-2 confirms (core staff on the byline) vs 8,397 random
#      corpus pairs. Prices `ack` — signal 3 is independent of signal 2.
#   C  the same confirms' 87 alias matches vs 115 alias matches in papers with NO
#      WCM author, drawn in proportion to each alias's global PMC hits. Prices the
#      CONDITIONAL terms (`ack.spec:*`, `inst:*`) — panel A's random negatives
#      produced only 4 alias matches, far too few to price an institution.
#   B  the 137 human "yes" imaging-core labels vs random corpus papers (1,200, of
#      which 400 also went through live Bedrock triage). Prices `staff`, the three
#      `aff:*` buckets and the LLM slope. Panel A's positives ARE signal-2 confirms,
#      so they cannot price signal 2 itself — the circularity this split exists to
#      avoid.
#
# `ack` is MARGINAL and everything under it is CONDITIONAL on a match, so summing
# them is the chain rule, not double counting.
#
# Fitted 2026-09-04. Every weight is in nats; +3.9 exactly cancels the prior.
# ---------------------------------------------------------------------------
WEIGHTS = {
    # An alias matched at all. The single largest piece of evidence there is.
    "ack": 6.37,                       # n=87/278 confirms vs 4/8397 random pairs
    # ...and how specific the alias that matched was (conditional on the match).
    # This is measured fact 1 entering the score: a generic alias is a NET LOSS of
    # 5 nats against the +6.37 above, because 104 of the 115 alias matches in papers
    # with no WCM author came from one, and none of the 87 true ones did.
    "ack.spec:distinctive": 4.35,      # <=100 global PMC hits; n=29/87 vs 0/115 (bound)
    "ack.spec:moderate": 1.90,         # 100-800 hits;          n=58/87 vs 11/115
    "ack.spec:generic": -5.07,         # >=800 hits;            n=0/87 vs 104/115 (bound)
    "ack.spec:unknown": 0.00,          # REFUSED: never observed on either side. Only an
                                       # ACRONYM alias lands here — esearch has no
                                       # case-sensitive mode, so those are deliberately
                                       # never counted. Neutral, not penalised.
    # Which institution the match sits next to. NEVER a gate: these cores are genuinely
    # shared across Tri-Institutional partners, so a partner counts as home, and
    # requiring a Weill Cornell affiliation would discard ~47% of core 4's real hits.
    "inst:home": 3.47,                 # WCM or a partner;      n=85/87 vs 3/115
    "inst:other": -3.62,               # a DIFFERENT institution; n=1/87 vs 73/115
    # "no institution named" is conditioned on specificity, because a specific enough
    # name needs no qualifier (ARCH is 43% "none" with 0% "other") while a generic one
    # is genuinely ambiguous. The measurement bears that out: -0.57 vs -4.01.
    "inst:none@distinctive": 0.00,     # REFUSED: never observed on either side
    "inst:none@moderate": -0.57,       # n=1/87 vs 3/115
    "inst:none@generic": -4.01,        # n=0/87 vs 36/115 (bound)
    "inst:none@unknown": 0.00,         # REFUSED: never observed on either side
    # Where the match sits. HELD AT ZERO BY DECISION 3: unmeasured, and guessing it is
    # how a confident wrong prior gets in. The counts are collected (12 ack / 3 methods
    # / 72 body among the positives, 85 / 1 / 29 among the negatives) but the two panels
    # differ in JATS coverage, not in what a section MEANS, so they cannot price it.
    # Needs a labelling pass. Delete these three lines when one happens.
    "sec:ack": 0.00,
    "sec:methods": 0.00,
    "sec:body": 0.00,
    # A tracked core-staff member on the byline. One feature, not a count.
    "staff": 4.89,                     # n=53/137 labelled yes vs 3/1200 corpus papers
    # A curated client of this core on the byline (dictionary `clients:`). One feature,
    # not a count, exactly like `staff`.
    #
    # UNFITTED, not refused. There is no curated list in the dictionary yet, so there is
    # no n on either side and nothing to fit. It gets priced on panel B the way `staff`
    # was (n=53/137 labelled yes vs 3/1200 corpus papers) by
    # scripts/fit_evidence_weights.py — that script is the provenance for every other
    # cell here and it is the only thing that may move this one. `client` is wired into
    # its panel-B key list TODAY, so a refresh run prints the cell and REFUSES it (0
    # hits on either side) rather than omitting the key from its paste-ready block.
    # Until a list exists the feature is INERT: extracted, visible in explain(), and
    # worth exactly nothing to the score.
    #
    # "A curated list is high-precision BY CONSTRUCTION" is not a fit. That argument is
    # what produced the hand-picked 0.45/0.15/0.85 constants #382 deleted.
    #
    # PREREQUISITE for any non-zero value, not a nice-to-have: measure the overlap
    # between `client` and the three `aff:*` buckets first. Signal 1 INFERS a core's
    # users from prior confirmations while this key ASSERTS them, so wherever curation
    # and confirmation history agree the two fire on the same rows and summing both
    # double-counts — the same correlation the `ack` / `ack.spec:*` / `inst:*` family
    # handles with the chain rule. A weight fitted on `client` marginally, without that
    # overlap in hand, is a second copy of the affinity prior.
    "client": 0.00,
    # An A2 METHOD FAMILY this core curated, bucketed by measured lift (dictionary
    # `method_families:`). One key — the STRONGEST tier that fired — never one per tier
    # and never one per family.
    #
    # Measured on core 14 (2026-09-05) with its 94 surfaced rows that are in the A2
    # corpus as positives, against the 5,354 A2-corpus PMIDs with no core-14 row:
    #
    #   strong   (>=30x)
    #     Clinical data warehouse/cohort platforms   399x  n=7    7.4% vs 0.02%
    #     Clinical text mining                        34x  n=10  10.6% vs 0.3%
    #     Electronic health record datasets           32x  n=36  38.3% vs 1.2%
    #   moderate (~4-7x)
    #     Machine learning classification            6.2x  n=15  16.0% vs 2.6%
    #     Predictive model validation                4.9x  n=15  16.0% vs 3.2%
    #   weak     (<2x, i.e. background)
    #     Regression modeling                        1.7x  n=26  27.7% vs 16.6%
    #     Observational study design                 1.6x  n=20  21.3% vs 13.4%
    #
    # For scale: `aff:regular` was fitted at 26/137 vs 7/1200 — ~32x — and carries
    # +3.43. So `method:strong` is in the range where a fitted weight would MATTER, and
    # that is exactly why it ships at 0.00 rather than at a plausible-looking number.
    #
    # WHY THE 399x IS NOT THE WEIGHT. Those positives are core-14 ENGINE rows, and a
    # large share of them were produced by an LLM reading the SAME title+abstract the
    # A2 extractor read (`signals.llm_triage` and `pipeline_tools/extract.py` differ in
    # what they ask for, not in what they see). Part of that lift is therefore two
    # systems agreeing about one piece of text, which is not two pieces of evidence.
    # The fitted number will be lower than the raw lift, possibly much lower, and
    # nobody can say how much lower without the measurement below.
    #
    # PREREQUISITE for any non-zero value, in the same shape as `client`'s: measure the
    # overlap between `method:strong` and BOTH `llm_score` and the `aff:*` buckets
    # first. If `method:strong` fires on exactly the papers the LLM already scored 8+,
    # it is a second copy of signal 4 and summing them double-counts the same read of
    # the same abstract. Only scripts/fit_evidence_weights.py may move these cells —
    # and see the README: panel B is the IMAGING core (LABEL_CORE = "2") while this
    # curation is on core 14, so the fitter cannot price these keys at all until a
    # core-14 panel exists. That is one obstacle more than `client` has.
    #
    # AND IT MUST NOT BECOME A DISCOVERY SIGNAL. The A2 artifact covers 8.7% of the
    # corpus (6,981 of 80,203 PMIDs) and that 8.7% is not a random slice — it is the
    # faculty FIRST/LAST-author, 2020+, Academic Article slice the tools pipeline
    # sweeps. A weight big enough to surface a paper on a family alone would silently
    # rank that population up and everyone else down, on a queue whose whole job is to
    # find a core's users. Keep it below the triage bar on its own, the way the LLM is
    # deliberately kept below the confirm bar (0.591 against 0.65).
    "method:strong": 0.00,
    "method:moderate": 0.00,
    "method:weak": 0.00,
    # Author x core affinity, bucketed on the RATE (signals.author_affinity — the
    # largest share of their own corpus output that any author on this byline has
    # already given to this core). A rate, not a count: the count could not tell a
    # regular from a passer-by, so its curve put 83.9% of a live queue on one value.
    "aff:trace": 0.79,                 # 0 < rate < 0.05;    n=7/137 labelled yes vs 29/1200
    "aff:regular": 3.43,               # 0.05 <= rate < 0.70; n=26/137 vs 7/1200
    "aff:core": 4.93,                  # rate >= 0.70;       n=55/137 vs 3/1200
    # NOT a key, on purpose: rate == 0 measures -0.99 (n=49/137 vs 1161/1200), and it is
    # held at 0 rather than shipped as a fourth bucket because evidence_features()'s
    # convention is that ABSENT evidence contributes no key — the same reason a
    # never-scored llm_score adds nothing. Pricing the absence here and nowhere else
    # would move every affinity-less pair down against features that stay silent.
}

# The LLM's 1-10 does not bucket without throwing resolution away — it is real
# information (AUC 0.933 vs human labels) and four coarse buckets would collapse a
# whole candidate pool onto four scores. Its weight is a straight line through the
# same Laplace-smoothed bin log-LRs, inverse-variance weighted; monotone by
# construction, and the per-bin residuals are in the fitting script's output
# (weighted R2 0.78). The affinity rate went the other way — it buckets cleanly and
# the buckets are fitted cells in WEIGHTS above, which is why there is no slope here.
LLM_INTERCEPT = -1.86               # w(llm) = -1.86 + 0.68 * score
LLM_PER_POINT = 0.68
LLM_MAX_FITTED = 9                  # no paper in the panel scored 10 — do not
                                    # extrapolate the line past its own support

# Alias-specificity buckets, on the alias's global PMC hit count. Specificity is the
# cheapest precision signal there is: over 10 aliases spanning 1 -> 23,544 hits,
# Pearson r = -0.852 between log10(hits) and the share of matches that mean OUR core.
# The measured inflection sits between `Epigenomics Core` (666 hits, 89% home) and
# `NMR Core` (926 hits, 50% home), so the generic line is drawn at 800.
_DISTINCTIVE_MAX = 100
_GENERIC_MIN = 800

# Affinity-rate buckets. Both edges are where the fitted cells stop being one cell:
# the panel separates "has touched this core at all" (0.79 nats) from "a working
# relationship" (3.43) at 0.05, and that from "this is largely what they do" (4.93)
# at 0.70. Every bucket clears MIN_PANEL=30 on the positive side and the three are
# monotone, so the edges are supported rather than drawn to taste.
_AFF_REGULAR_MIN = 0.05
_AFF_CORE_MIN = 0.70

# Status thresholds on the resulting probability, per SPEC decision 1: a
# 100%-precision alias match no longer asserts a status, it earns a weight and
# clears a bar. Both are per-core overridable from `confirm_threshold` /
# `triage_threshold` in core_dictionary.yaml — a core whose aliases are all generic,
# or whose affinity index has saturated, deserves a different bar without a code
# change.
#
# 0.65 is bracketed by the weights, not chosen. Above it sit the things that should
# confirm: a lone staff co-author (0.731, one of the two 100%-precision confirmers in
# validation) and even the weakest alias evidence worth confirming, a GENERIC alias
# beside a home institution (0.707 — the branch the institution audit said should
# confirm). Below it sits the thing that must not: the LLM alone, which tops out at
# 0.591, keeping the one doctrine from the old hard-coded precedence worth keeping.
# Nothing sits within 0.05 of the bar, so it is not balanced on a rounding decision.
#
# `aff:core` alone now clears it too, at 0.738 — its fitted 4.93 came out just above
# staff co-authorship's 4.89 (55/137 vs 3/1200), so an author who has already given
# 70%+ of their corpus output to this core confirms their next paper on the strength
# of that history. Measured, not chosen; a core that does not want it can raise its
# own `confirm_threshold`. The old constant could not do this (4.60 * 0.85 = 3.91,
# a candidate at best), so it IS a behaviour change and the first thing to eyeball
# in a live queue.
DEFAULT_CONFIRM_THRESHOLD = 0.65
# Unchanged from the old combiner (and still what run.py --threshold overrides), so
# "what reaches the claim queue" moves for measured reasons rather than by a
# constant quietly changing underneath it.
DEFAULT_TRIAGE_THRESHOLD = 0.30


def noisy_or(*probabilities: float) -> float:
    """1 - product(1 - p): the probability that at least one independent signal fires.

    No longer used by combine() — log-odds replaced it here precisely because it
    saturates — nor by the author-affinity prior, which now takes the MAX over the
    byline (noisy-OR there was monotone in how many authors fire, the one thing
    measured not to separate). It remains the shared primitive behind the prefilter
    prior and the batch_screen likelihood, so it stays in one place. noisy_or() with
    no args returns 0.0.
    """
    complement = 1.0
    for p in probabilities:
        complement *= (1.0 - p)
    return 1.0 - complement


def _alias_bucket(hits) -> str:
    """How specific was the matched alias?

    `None` means the count was never cached — in practice an ACRONYM alias, which
    `refresh_alias_hits` skips on purpose because esearch has no case-sensitive mode.
    That lands in "unknown", which carries weight 0.00: unmeasured, so neutral, and
    NOT a penalty (the acronym matcher's own word-boundary rule is what keeps those
    matches honest).
    """
    if hits is None:
        return "unknown"
    if hits <= _DISTINCTIVE_MAX:
        return "distinctive"
    return "moderate" if hits < _GENERIC_MIN else "generic"


def _affinity_bucket(rate: float) -> str:
    """Which fitted cell an author x core affinity RATE falls in. Callers only ask
    about a rate > 0; a rate of 0 is absent evidence and gets no key at all."""
    if rate >= _AFF_CORE_MIN:
        return "core"
    return "regular" if rate >= _AFF_REGULAR_MIN else "trace"


def evidence_features(signals: SignalResult) -> list:
    """The CATEGORICAL evidence keys on one (publication, core) pair, for WEIGHTS.

    Pure, and the single definition of what counts as evidence — the fitting script
    imports THIS, so the weights can never be fitted against a different feature set
    than the one that scores production.

    `ack` is the marginal "an alias matched at all"; the keys under it are all
    conditional on that match, so the sum is the chain rule rather than three
    correlated features each claiming the same credit.

    Absent evidence contributes nothing (no key). Deliberate for llm_score: `None`
    means "never scored", which is not the claim "scored 1".
    """
    out = []
    if signals.ack_matched:
        bucket = _alias_bucket(signals.ack_alias_hits)
        out += ["ack", f"ack.spec:{bucket}"]
        # The institution named around the match. "none" is conditioned on alias
        # specificity because a specific enough name needs no qualifier (ARCH is
        # 43% "none" with 0% "other") while a generic one is genuinely ambiguous —
        # a flat rule on this bucket is wrong in one direction or the other.
        if signals.ack_institution == "none":
            out.append(f"inst:none@{bucket}")
        elif signals.ack_institution:
            out.append(f"inst:{signals.ack_institution}")
        if signals.ack_section:
            out.append(f"sec:{signals.ack_section}")
    if signals.coauthor_cwids:
        # One feature, not a count. The 1-vs-2+ split is not fittable from what
        # exists (14 positives, and its marginal fit comes out NON-monotone), so
        # scaling with how many staff appear waits for a labelling pass.
        out.append("staff")
    if signals.client_cwids:
        # One feature, not a count, exactly like `staff` — the dictionary asserts these
        # people use the core, and a second asserted client on one byline is not a
        # second independent claim. Weight 0.00 until it is fitted, so this key is
        # carried and shown by explain() while moving no score.
        out.append("client")
    if signals.method_tier:
        # EXACTLY ONE key, the strongest tier that fired — not one per tier, not one
        # per family. The tiers are correlated (a paper carrying a 399x family usually
        # also does regression), so emitting both would sum two views of the same
        # evidence; and 816 families would make WEIGHTS an 816-row table nobody can
        # fit, most of whose cells no panel would ever observe. One feature, not a
        # count — the same rule as `staff` and `client`.
        out.append(f"method:{signals.method_tier}")
    if signals.author_affinity > 0:
        # The MAX rate over the byline (signals.author_affinity), bucketed. A rate of
        # 0 emits nothing, per the absent-evidence rule above — even though the cell
        # measures -0.99, because pricing THIS absence and no other would bias every
        # pair that simply has no author history.
        out.append(f"aff:{_affinity_bucket(signals.author_affinity)}")
    return out


def explain(signals: SignalResult) -> list:
    """[(evidence, weight)] for one pair, largest contribution first.

    The point of naive Bayes over anything cleverer: a 3,000-row claim queue is only
    reviewable if it can say why a paper sits where it does. The LLM score contributes
    a line rather than a table entry, and is labelled with its value.

    The affinity line is a WEIGHTS cell now, but it is still shown with the rate that
    chose the cell ("aff:core=0.833"): the bucket is what moves the score, the rate is
    what tells a reviewer whether this is the core's heaviest user or someone who just
    crossed the 0.70 edge, and flattening 39 distinct values to 3 labels in the one
    place built to answer "why" gives that resolution up for nothing. The weight still
    comes from the bare key, so only the DISPLAY carries the value — at 3 dp, because
    2 dp rounds 0.699 to the very edge it is on the other side of.
    """
    pairs = [(f"{f}={signals.author_affinity:.3f}" if f.startswith("aff:") else f,
              WEIGHTS.get(f, 0.0))
             for f in evidence_features(signals)]
    if signals.llm_score:
        pairs.append((f"llm:{signals.llm_score}",
                      LLM_INTERCEPT + LLM_PER_POINT * min(signals.llm_score, LLM_MAX_FITTED)))
    return sorted(pairs, key=lambda kv: -abs(kv[1]))


def score(signals: SignalResult) -> float:
    """P(this publication used this core) = sigmoid(prior + sum of the weights)."""
    logit = PRIOR_LOGIT + sum(w for _, w in explain(signals))
    return 1.0 / (1.0 + math.exp(-logit))


def combine(
    pmid: str,
    core_id: str,
    signals: SignalResult,
    *,
    scored_at: str = "",
    core=None,
    triage_threshold: float = None,
    confirm_threshold: float = None,
) -> CoreUsageRecord:
    """Score one (publication, core) pair and band it into a status.

    Thresholds resolve explicit argument -> `core`'s dictionary override -> module
    default, so a caller that knows better still wins and a core with only generic
    aliases can demand a higher bar without a code change.
    """
    triage = _threshold(triage_threshold, core, "triage_threshold", DEFAULT_TRIAGE_THRESHOLD)
    confirm = _threshold(confirm_threshold, core, "confirm_threshold", DEFAULT_CONFIRM_THRESHOLD)
    likelihood = score(signals)
    status = (STATUS_CONFIRMED if likelihood >= confirm
              else STATUS_CANDIDATE if likelihood >= triage else STATUS_BELOW)
    return CoreUsageRecord(pmid, core_id, round(likelihood, 4), status, signals, scored_at)


def _threshold(explicit, core, attr: str, default: float) -> float:
    if explicit is not None:
        return explicit
    return getattr(core, attr, None) if getattr(core, attr, None) is not None else default
