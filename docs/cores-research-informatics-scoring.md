# How Research Informatics (core 14) publications are scored

This is the reference for how the cores engine decides whether a WCM paper used the
Research Informatics core (core 14). It is written for two readers:

- the Research Informatics team, who review the queue in SPS and want to know why a paper
  is there and what their decisions do. The first section is for you.
- a future maintainer, who needs every signal, weight, threshold and default, where each
  one is set, and what evidence would justify changing it.

State as of 2026-10-06. `main` is at `4d05f1a` (#418). Several PRs that change core 14's
scoring are open and not merged; where they change a number, this page gives both values
and names the PR. Every number here comes from a command recorded in the cited PR, its
verification, or the code on the named branch.

## In plain language

**What the engine does.** Every night it looks at about 82,000 WCM papers (the scoreable
corpus: 82,203 on 2026-10-06) and asks, for each one, how likely it is that the work used
Research Informatics. Papers above a bar go into your review queue in SPS. A few papers
with very strong evidence are marked "confirmed" without review.

**The evidence it uses.**

1. **The paper names the core.** For example "Architecture for Research Computing in
   Health" (ARCH) in the acknowledgements or methods, or, once #422 merges, "INSIGHT
   Clinical Research Network". This is the strongest evidence by far. A distinctive name
   next to Weill Cornell is effectively proof.
2. **A Research Informatics staff member is an author.** This is strong evidence, but on
   its own it only puts a paper in the queue. It never confirms by itself, because the
   author-matching system sometimes attaches the wrong person to a byline.
3. **An author is a repeat user.** If someone on the byline has had other papers
   confirmed or claimed for Research Informatics, the paper gets a boost. The more of
   their papers have used the core, the bigger the boost.
4. **An AI model read the title and abstract** and scored, from 1 to 10, how likely the
   paper was to need the core. This helps rank papers. It never confirms a paper on its
   own.

The engine adds these up into a single percentage. Evidence it doesn't have adds nothing:
a paper with no repeat user is not marked down for that.

**What the bands in SPS mean.** The queue labels each paper by its score: **Strong**
(85% or more), **Moderate** (65% or more), **Slight** (40% or more) and **Weak** (below
40%). Weak papers are hidden by default (there is a "Show" link) unless the paper uses a
method that is typical of your core's work, such as a clinical data warehouse, clinical
text mining or EHR datasets.

**What you can do in the queue.** Claim or reject one paper at a time, or tick several and
act on all of them at once. "Reject all N…" rejects every row currently shown, after a
confirmation step. Once #3023 merges in SPS, the Filters panel gets a **Score** facet, so
you can tick "Slight" (for example), check the list, and reject all of them in one step.

**What your decisions do.**

- A decision is permanent as far as the engine is concerned. It never overwrites a claim
  or a rejection, and it never puts a rejected paper back in your queue.
- A claim counts straight away. On the next nightly run, every author on that paper counts
  as having used the core once more, which raises their other papers.
- A rejection changes no score automatically. It is still just as useful, because claims
  and rejections together are the only labelled data we have for core 14. Every
  adjustment below was measured on them, and the next ones need more of them (see
  [How reviewer decisions feed back](#how-reviewer-decisions-feed-back)).
- Only papers in the WCM corpus count toward these measurements. 19 of the 26 papers your
  team has claimed so far are outside it (the engine never scores them), so decisions on
  papers in the queue are the ones that help most.

**The repeat-user rule.** The team asked for a rule that an author needs at least 3
confirmed Research Informatics papers before their history counts. We built that and
then replaced it with a sliding scale. Under a hard minimum, 2 confirmed papers count for
nothing and 3 count in full. Under the sliding scale, every confirmed paper counts a
little, and a long record counts much more than a short one. The trade-off: on the 46
papers your team has decided, the hard minimum of 3 separated claimed from rejected papers
better, because it removed the boost from most of the rejected ones. The sliding scale
also lets more papers into the queue: about 601 candidates, against 420 under the minimum
of 3, in a dry run. The details and numbers are in
[Why a sliding scale and not a minimum of 3](#why-a-sliding-scale-and-not-a-minimum-of-3).
Both rules are a single setting, so the choice can be revisited as more decisions come in.

## Where things stand

| PR | Repo | What it does for core 14 | State |
|---|---|---|---|
| #418 | ReciterAI | a core's own staff lend no repeat-user boost to their own core | merged (`main`) |
| #417, #414 | ReciterAI | staff alone and the LLM alone never confirm | merged |
| #422 | ReciterAI | adds aliases "INSIGHT Clinical Research Network" and "INSIGHT CRN" | open |
| #424 | ReciterAI | repeat-user rate: tenure gate, self-exclusion, sliding-scale shrinkage, refit weights | open |
| #429 | ReciterAI | alias matcher treats "and" and "&" as one connector; **no effect on core 14** | open |
| #430 | ReciterAI | per-core calibration hook (identity for every core); stacks on #424 | open |
| #421, #423, #425, #426, #427, #428 | ReciterAI | experiments, measured and not wired (scripts and docs only) | open |
| #3023 | SPS | Score band facet in the review queue Filters panel | open |

CI cannot vouch for any open ReciterAI PR: the `pytest` check fails within 2 to 3 seconds
on every one because of a GitHub Actions billing block. The only test evidence is local
runs of the full suite, recorded per PR below.

## The score

`pipeline_cores/combine.py` sums the weights of the evidence present, in log-odds
(nats):

```
logit(P) = PRIOR_LOGIT + sum of the weights of the evidence present
P        = 1 / (1 + exp(-logit))
status   = confirmed        if P >= confirm_threshold   (default 0.65)
           candidate        if P >= triage_threshold    (default 0.30)
           below_threshold  otherwise
```

- `PRIOR_LOGIT = logit(0.02) = -3.89`: every paper starts at 2%. The 2% came from a
  random WCM sample that put a core's base rate at 1 to 5%. Core 14's measured base rate
  is much lower, 79 / 82,203 = 0.096%, which is about 3.05 nats below the prior. That gap
  is the main thing the calibration measurement found (see [Calibration](#calibration)).
- Naive Bayes on purpose: each weight is fitted on its own and the contributions are
  summed, so `combine.explain()` can show a reviewer exactly why a paper ranks where it
  does.
- Absent evidence adds no key. A paper the LLM never scored gets no LLM term, and an
  affinity rate of 0 gets no affinity term.
- **Two signals are never the deciding vote for confirmation** (#414, #417). If a paper
  clears 0.65 only because of its LLM term, or only because of a staff co-author with no
  alias match beside it, it is held at `candidate` and its likelihood is kept, so it still
  ranks at the top of the queue.
- Weights are not hand-picked. Every cell in `combine.WEIGHTS` comes from
  `scripts/fit_evidence_weights.py` and carries the counts behind it. A cell too thin to
  fit is 0.00 and says so.

### Where the weights come from

The fit uses three panels, none of them core 14:

- **Panel A**: 278 staff-co-author confirms against 8,397 random corpus pairs. Prices `ack`.
- **Panel C**: the 87 alias matches among those confirms against 115 alias matches in
  papers with no WCM author. Prices alias specificity and institution.
- **Panel B**: 137 human-labelled "yes" papers for the imaging core (core 2) against
  1,200 random corpus papers. Prices `staff`, the three `aff:*` buckets and the LLM line.

Core 14's own labelled data is its 46 human-decided rows (26 claimed, 20 rejected). That
is too few to fit weights on, so it is used to check the weights and to choose per-core
settings, never to price a cell.

## Signals for core 14

### Signal 3: the paper names the core (aliases)

The matcher (`signals.acknowledgement_signal`) scans the paper's PMC full text, including
the reference list, for each alias. An alias of 2 to 6 capitals or digits
(`^[A-Z0-9]{2,6}$`) is treated as an acronym and matched case-sensitively on word
boundaries; anything else matches case-insensitively.

A match is worth `ack` +6.37 (87/278 confirms vs 4/8,397 random pairs), plus two
conditional terms:

| Alias specificity (global PMC hits) | Weight | Fitted on |
|---|---|---|
| distinctive (100 or fewer) | +4.35 | 29/87 vs 0/115 (bound) |
| moderate (101 to 799) | +1.90 | 58/87 vs 11/115 |
| generic (800 or more) | -5.07 | 0/87 vs 104/115 (bound) |
| unknown (count not cached; acronyms) | 0.00 | never observed |

| Institution named beside the match | Weight | Fitted on |
|---|---|---|
| home (WCM or a Tri-Institutional partner) | +3.47 | 85/87 vs 3/115 |
| another institution | -3.62 | 1/87 vs 73/115 |
| none, distinctive alias | 0.00 | never observed |
| none, moderate alias | -0.57 | 1/87 vs 3/115 |
| none, generic alias | -4.01 | 0/87 vs 36/115 (bound) |

The section the match sits in (`sec:ack`, `sec:methods`, `sec:body`) is collected but
held at 0.00 until a labelling pass can price it.

**Core 14's aliases.** On `main`:

| Alias | Global PMC hits (cached) | Bucket |
|---|---|---|
| Architecture for Research Computing | 25 | distinctive |
| Architecture for Research Computing in Health | 20 | distinctive |
| WCM Research Informatics | 2 | distinctive |

#422 adds two:

| Alias | Global PMC hits | Bucket | In the WCM corpus | Already confirmed or claimed |
|---|---|---|---|---|
| INSIGHT Clinical Research Network | 76 | distinctive | 45 | 16 |
| INSIGHT CRN | 33 | distinctive | 18 | 10 |

A distinctive alias match is worth 6.37 + 4.35 = 10.72 nats before the institution term,
so a match beside a home institution scores 1.000 and confirms, and a match with no
institution named scores 0.999. INSIGHT is the NYC PCORnet clinical research network WCM
leads, and WCM's EHR data reaches it through ARCH. The core 14 owner proposed it, and the
owner decision recorded in #422 is that a paper that used INSIGHT CRN data counts as
Research Informatics usage. Of the 43 corpus papers whose full text names either phrase,
16 are already confirmed or claimed; the other 27 (18 candidate, 4 below threshold, 5 with
no core-14 row) should move toward confirmed on the first run after #422 merges.

**Variants considered and left out**, with the reason for each:

| Variant | Why it is out |
|---|---|
| "Research Informatics" (bare) | 5 of its 16 hits in the claimed papers are the REDCap citation in the reference list ("…translational research informatics support", Harris 2009). It would confirm every paper that cites REDCap. The rest are generic uses of the discipline name. |
| "Research Informatics Core" | 0 hits in the 67 claimed papers with full text. |
| "Weill Cornell Medicine Research Informatics" | 0 hits in the 67 claimed papers with full text. |
| "ARCH" (bare acronym) | Over the 67 claimed papers with full text it finds exactly one paper the spelled-out phrase misses, and `\bARCH\b` also matches ARCH econometric models. An alias match can confirm on its own, so that trade is wrong. |
| "INSIGHT" (bare) | 7 letters, so not an acronym to the matcher: it would match the English word "insight". 1,628,883 PMC hits (1,628,826 on a later live count), the generic bucket, so every paper saying "new insight into" would get +1.30 nats. It is also the name of the NIAID HIV trials network. |
| "INSIGHT Network" | 124 hits, the moderate bucket (8.27 nats from the alias terms alone). It is shared with the NIAID / Leidos INSIGHT trials network: of its 17 corpus papers, 12 name it in text and 4 of those are the trials network. |

**Known issues with the aliases.**

- The cached hit counts for the three ARCH-era aliases are stale: 25 / 20 / 2 cached
  against 26 / 21 / 3 from a read-only `refresh_alias_hits --core 14` on 2026-10-06. All
  stay distinctive, so no score changes. The `--write` step was not run.
- The matcher scans the reference list, so any alias that appears in a commonly cited
  title would match citing papers. This is generic to signal 3 and not fixed.
- The institution check reads "Cornell Univ" as Weill Cornell (PMID 40312501, Cornell's
  Ithaca facility, surfaced by #429 on core 13). Not fixed; listed in #429 as a follow-up.
- #429's "and"/"&" fix compiles every core-14 alias to exactly the old regex, so core 14
  has the same 12 matches before and after.

### Signal 2: Research Informatics staff on the byline

`staff` +4.89, fitted on panel B (53/137 labelled yes vs 3/1,200 corpus papers). One
feature, not a count: two staff on one byline are worth the same as one. Staff are
matched by resolved CWID in `reciterdb.analysis_summary_author`, looked up live every run.

| CWID | Name | Notes |
|---|---|---|
| evs2008 | Evan Sholle | Population Health Sciences; large own publication record |
| thc2015 | Thomas Campion | Population Health Sciences; large own publication record |
| saa3011 | Sajjad Abedian | not in `identity` but has resolved-author rows |
| nik2004 | Nivedita Chang | not a ReCiter target person, so cannot fire |

- Staff alone scores 0.731, which would clear 0.65, but staff is never the deciding vote
  unless an alias also matches (#417). Staff-only papers sit at the top of the candidate
  queue instead.
- Staff are excluded from their own core's repeat-user rate (#418). Publishing through
  their own core is their job, and before #418 they lent the strongest affinity bucket to
  every byline they were on.

### Signal 1b: curated clients

The core owner can list known clients in SPS (`CoreClient`); the engine reads that list as
`CORE#14/CLIENTS`. A read-only DynamoDB get on 2026-10-06 found 1 client CWID for core 14.
The `client` weight is **0.00, unfitted**: the feature is extracted and shown by
`explain()`, but moves no score. There is no labelled data to fit it on yet, and before
any non-zero value the overlap with the `aff:*` buckets must be measured, because a
curated client and a repeat user will often be the same person. The owner is welcome to
supply a fuller list. It costs nothing, and it gives the next fit something to price.

### Signal 3b: method families

Core 14 is the only core with curated A2 method families (`method_families:` in
`config/core_dictionary.yaml`). Lifts were measured 2026-09-05 on core 14's 94 surfaced
rows that are in the A2 corpus, against the 5,354 A2-corpus papers with no core-14 row:

| Tier | Family | Lift | n | Positive vs background |
|---|---|---|---|---|
| strong | Clinical data warehouse/cohort platforms | 399x | 7 | 7.4% vs 0.02% |
| strong | Clinical text mining | 34x | 10 | 10.6% vs 0.3% |
| strong | Electronic health record datasets | 32x | 36 | 38.3% vs 1.2% |
| moderate | Machine learning classification | 6.2x | 15 | 16.0% vs 2.6% |
| moderate | Predictive model validation | 4.9x | 15 | 16.0% vs 3.2% |
| weak | Regression modeling | 1.7x | 26 | 27.7% vs 16.6% |
| weak | Observational study design | 1.6x | 20 | 21.3% vs 13.4% |

- All three `method:*` weights are **0.00**. The lifts are an upper bound: part of them
  is the LLM and the A2 extractor agreeing about the same abstract. Holding out both the
  LLM and affinity leaves 5 `method:strong` events, below the fit's `MIN_PANEL` of 30.
- The A2 artifact covers only 8.7% of the corpus (faculty first/last-author, 2020+), so a
  weight large enough to surface a paper on its own would favour that slice.
- Where it does matter today is SPS: a strong or moderate tier exempts a row from the
  display floor (see [Thresholds and the review queue](#thresholds-and-the-review-queue)).

### Signal 3c: MeSH tree

Not active for core 14. `mesh:tree` is 0.00 for every core, and core 14 has no key in
`prefilter.CORE_MESH_TREE_PREFIXES`.

### Signal 4: the LLM

Two passes on Bedrock: a cheap screen (cutoff 2 of 10) and then a dense 1 to 10 score on
title and abstract against the core's `llm_description`. On the 237-paper imaging pilot
the dense score had AUC 0.933 against human labels.

- Weight is a line, not buckets: `w = -1.86 + 0.68 * min(score, 9)` (weighted R² 0.78;
  no panel paper scored 10, so the line is not extended past 9).
- Alone, a score of 7 gives 0.271 (below triage), 8 gives 0.423 (candidate) and 9 or 10
  gives 0.591 (candidate). The LLM alone never reaches 0.65, and #414 also stops it being
  the deciding vote in combination.
- **The nightly makes no Bedrock calls.** It runs with `--llm-carry-forward`, which keeps
  each row's stored LLM score; papers that were never LLM-scored have no LLM term. The
  nightly Bedrock budget is open in #388.
- On core 14's decided rows, 19 of the 20 rejected papers had LLM scores of 7 or 8. That
  is a selection effect (reviewers decided what the engine surfaced), which is why a fit on
  these rows gives the LLM a negative weight and why that fit is not used.

### Signal 1: repeat users (author affinity)

For each author on the byline the engine computes a **rate**: how much of that author's
corpus output is already confirmed or claimed for core 14. The paper takes the **maximum**
over its byline, not a sum: one author whose work is largely this core's is the evidence,
and four people who each used it once are not four times that (noisy-OR across the byline
measured AUC 0.6505 on panel B and turned over past 2 authors). The rate is then bucketed:

| Bucket | Rate | Weight on `main` | Weight after #424 | Fitted on (after #424) |
|---|---|---|---|---|
| `aff:trace` | above 0, below 0.05 | +0.79 | +1.18 | 9/137 vs 25/1,200 |
| `aff:regular` | 0.05 to below 0.70 | +3.43 | +3.49 | 43/137 vs 11/1,200 |
| `aff:core` | 0.70 and above | +4.93 | +3.49 | 0/0, empty, pooled with `aff:regular` |

A rate of 0 adds no key (it measures -0.45 after #424, but absent evidence is not priced).

**On `main`** the rate is `n / total`: n = the author's confirmed or claimed core-14
papers, total = their papers in the corpus. Both sides are gated to the corpus (19 of
core 14's 43 prior rows were outside it when this was fixed). There is no tenure gate, no
self-exclusion and no shrinkage, and `aff:core` alone scores 0.738, enough to confirm.

**After #424** (head `0924002`, branch `feat/affinity-tenure-decay-shrinkage`) four
things change:

1. **Tenure gate.** An author's history only counts for papers published within
   `[start - 3, end + 2]` years of their WCM appointment (from `identity`), and only
   confirmations and corpus papers inside that window build the rate.
   - `TENURE_LAG_BEFORE = 3`: identity's start date is the faculty appointment, not
     arrival, so postdocs and fellows who later joined the faculty would lose real
     history. Panel B bucket AUC is 0.6722 / 0.6715 / 0.6753 at 0 / 1 / 2 years and
     returns to 0.6786 at 3, flat beyond. Smallest lag that costs nothing.
   - `TENURE_LAG_AFTER = 2`: not measurable on panel B (flat from 0 to 5). Chosen for the
     publishing tail after someone leaves. On core 14 the whole gate moves 1 of 486 open
     candidates.
   - An author with no tenure row is not gated.
2. **Self-exclusion, numerator only.** The paper being scored is removed from each
   author's n if it is one of their confirmed or claimed papers, but stays in their total.
   Without this a confirmed paper kept itself confirmed with its own label on every
   re-run: all 47 of core 14's affinity-carried confirmations on 2026-10-06 counted
   themselves, and 1 had no other confirmed paper behind it. Removing it from the
   numerator only measured AUC 0.6212 on core 14's decided rows, against 0.6115 when it
   was also removed from the denominator (the paper is honestly part of the author's
   output; only its label was the leak).
3. **Sliding-scale shrinkage** (empirical Bayes):

   ```
   rate = (n + s * p0) / (total + s)
   ```

   - p0 is the core's base rate, its confirmed and claimed papers as a share of the
     corpus, computed every run. For core 14 that is 79 / 82,203 = 0.00096.
   - s is the prior strength: "s papers' worth of belief that this author is an average
     WCM author for this core". Default `signals.AFFINITY_PRIOR_STRENGTH = 5`; a core can
     override it with `affinity_prior_strength` in `config/core_dictionary.yaml`. Core 14
     has no override.
   - An author with n = 0 after self-exclusion still contributes 0. The prior shrinks
     evidence; it never gives an author with none the base rate for free.
   - Because p0 is tiny for every core, `s * p0` barely moves a rate. In practice this is
     `n / (total + s)`, and s does the work of pulling small denominators down. It
     replaces the intermediate `n / (total + K)` with K = 1, which is the same formula
     with p0 = 0 and s = 1.

   Worked examples at s = 5 and core 14's p0:

   | Author's record (n of total) | Rate | Bucket |
   |---|---|---|
   | 1 of 1 | 0.17 | regular |
   | 1 of 2 | 0.14 | regular |
   | 2 of 4 | 0.22 | regular |
   | 3 of 3 | 0.38 | regular |
   | 10 of 10 | 0.67 | regular |
   | 1 of 80 | 0.012 | trace |

4. **Refit weights** at s = 5 (see the table above). No panel-B rate reaches 0.70 at
   s = 5, so the `aff:core` cell is empty. A new rule in the fit script,
   `pool_empty_affinity`, gives an empty bucket the weight of the one below rather than
   0.00. As a result **`aff:core` alone now scores 0.401, a candidate, not confirmed**:
   a repeat-user history needs a second piece of evidence to confirm. The 0.70 edge
   currently prices nothing differently; it is kept so a larger panel can repopulate it.

**Time decay is off** (`AFFINITY_HALF_LIFE_YEARS = None`). On panel B decay never helps
and short half-lives hurt: bucket AUC 0.6770 / 0.6777 / 0.6785 / 0.6786 / 0.6786 at 1 / 2
/ 3 / 5 / 8 years against 0.6786 off. Core 14 is too small to say otherwise and its
results are non-monotone (2 years 0.6038, 3 years 0.6212, 5 years 0.6192, off 0.6144). The
corpus starts in 2020, so decay has only about 6 years to act on.

#### Why a sliding scale and not a minimum of 3

The core 14 owner asked that an author need at least 3 confirmed papers (not counting the
paper being scored) before their history counts. That was built in #424 (commit
`542937f`) as a per-core `affinity_min_confirms` and measured on core 14's 26 claimed and
20 rejected rows (`scripts/measure_affinity_min_confirms.py`, since removed):

| Minimum | AUC (rate / bucket) | Rejected rows with affinity | Claimed rows with affinity | Open candidates that drop below (of 486) |
|---|---|---|---|---|
| 1 | 0.6212 / 0.5558 | 17/20 | 17/26 | 12 |
| 2 | 0.6317 / 0.6000 | 14/20 | 16/26 | 131 |
| 3 | 0.7279 / 0.6962 | 4/20 | 16/26 | 174 |

The same minimum applied to panel B **lowered** bucket AUC: 0.6786 / 0.6027 / 0.5637 at
1 / 2 / 3. So it could only ever have been a core-14 setting.

It was replaced (commit `0924002`) because a hard minimum is a cliff: 2 confirmations
count for exactly nothing and 3 count in full, and nothing about an author changes between
the two. The sliding scale treats each confirmation as partial evidence and a long record
as strong evidence, with one knob (s) instead of a threshold.

The prior strength was swept on both panels (`fit_evidence_weights.py --affinity-only
--prior-strength S` and `measure_affinity_prior_strength.py --core 14`):

| s | Panel B AUC (rate / bucket) | Core 14 AUC (rate / bucket) | Core 14 open candidates that drop below (of 486) |
|---|---|---|---|
| 0 | 0.6788 / 0.6780 | 0.6144 / 0.5558 | 8 |
| 1 | 0.6787 / 0.6776 | 0.6212 / 0.5558 | 14 |
| 2 | 0.6787 / 0.6776 | 0.6250 / 0.5558 | 25 |
| **5** | **0.6785 / 0.6777** | **0.6250 / 0.5404** | **32** |
| 10 | 0.6786 / 0.6781 | 0.6144 / 0.5654 | 54 |
| 20 | 0.6782 / 0.6771 | 0.5894 / 0.5981 | 106 |

What this shows, plainly:

- **Panel B cannot choose s.** Bucket AUC is flat within 0.0005 from s = 0 to 10 and drops
  at 20. s = 5 is the middle of that flat range, the value least exposed to either end
  moving as labels accrue. It is not a measured optimum.
- **On core 14 the sliding scale gives back most of the minimum-of-3 gain.** Rate AUC is
  0.6250 at s = 5 against 0.7279 under the minimum of 3. Bucket AUC, which is what the
  weights price and what ships, is 0.5404 at s = 5, the lowest of the six values tested.
  The minimum of 3 worked on core 14 because it removed the boost from 13 of the 17
  rejected papers that had one. Shrinkage never removes a boost entirely, so 17 of 26
  claimed and 17 of 20 rejected rows keep some prior at every s.
- **46 rows is too few to set a core-specific s.** Core 14's rate and bucket AUCs point in
  opposite directions across the sweep, and the 46-row panel has 520 claimed-rejected
  pairs. Core 14 therefore has no override.
- **The queue grows.** A dry run of the core-14 pipeline reusing stored LLM scores (no
  LLM calls, nothing written) gives 601 candidates at s = 5, against 420 under the
  minimum of 3.

Against the weights live on `main` (0.79 / 3.43 / 4.93), #424 at s = 5 moves core 14's
stored rows as follows: 59 engine-confirmed rows become candidates, 1 drops below
threshold and 12 stay confirmed; 32 of 486 open candidates drop below threshold; 59
below-threshold rows become candidates (4 of them at 0.40 or more).

If reviewers find too many repeat-user-only papers in the queue, the next step is to
re-measure with more decided rows, not to guess: once core 14 has 100 or more decided
rows, re-run the sweep and set `affinity_prior_strength` only if a value clearly beats the
global default. A per-core minimum could be reintroduced the same way if the evidence
favours it again on a larger panel.

## What one piece of evidence is worth on its own

Computed from the weights, for a paper with only this evidence. Re-run after any weight
change.

| Evidence alone | `main` | After #424 | Status |
|---|---|---|---|
| distinctive alias beside a home institution | 1.000 | 1.000 | confirmed |
| distinctive alias, no institution named | 0.999 | 0.999 | confirmed |
| distinctive alias beside another institution | 0.961 | 0.961 | confirmed |
| generic alias beside a home institution | 0.706 | 0.706 | confirmed |
| staff co-author | 0.731 | 0.731 | candidate (held; never the deciding vote) |
| `aff:core` | 0.738 | 0.401 | confirmed on `main`; candidate after #424 |
| `aff:regular` | 0.387 | 0.401 | candidate |
| `aff:trace` | 0.043 | 0.062 | below threshold |
| LLM 9 or 10 | 0.591 | 0.591 | candidate |
| LLM 8 | 0.423 | 0.423 | candidate |
| LLM 7 | 0.271 | 0.271 | below threshold |
| curated client, any method family, MeSH | 0.020 | 0.020 | below threshold (weight 0.00) |
| no evidence | 0.020 | 0.020 | below threshold |

Two combinations reviewers will see often, both held at candidate: `aff:regular` + LLM 8
scores 0.960, but without the LLM it is 0.401; staff + `aff:regular` scores 0.989, but
without the staff term (no alias matched) it is 0.401.

## Thresholds and the review queue

| Threshold | Value | Set in | Effect |
|---|---|---|---|
| confirm | 0.65 | `combine.DEFAULT_CONFIRM_THRESHOLD`; per core `confirm_threshold` (core 14: none) | confirmed without review |
| triage | 0.30 | `combine.DEFAULT_TRIAGE_THRESHOLD`; per core `triage_threshold` (core 14: none); `run.py --threshold` | enters the queue as a candidate |
| SPS display floor | 0.40 | SPS `lib/cores/review-thresholds.ts`, `CANDIDATE_DISPLAY_FLOOR` | candidates below it hidden by default |
| floor exemption | method tier strong or moderate | SPS `FLOOR_EXEMPT_METHOD_TIERS` | such rows are shown even below 0.40 |
| high confidence | 0.80 | SPS `HIGH_CONFIDENCE_LIKELIHOOD` | "high confidence" counts on the index and editor |
| bands | Strong ≥ 0.85, Moderate ≥ 0.65, Slight ≥ 0.40, else Weak | SPS `likelihoodBand` in `components/edit/core-claim-queue.tsx` | the word and colour on each row |

**Why 0.65.** The confirm bar is bracketed by the weights, not chosen. Above it sit a lone
staff co-author (0.731) and the weakest alias evidence worth confirming, a generic alias
beside a home institution (0.706). Below it sits the LLM alone (0.591). Nothing sits within
0.05 of the bar.

**Why the display floor is 0.40.** A prod probe of core 14 recorded in the SPS code found
all 2,276 rows below the floor at 0.35 to 0.40 carried only the repeat-user signal plus at
most a method tier: 1,845 repeat-user only, 295 method weak, 78 method moderate, 58
method strong. None had an alias, a staff co-author or an LLM score. Human claims started
at 0.40. Strong and moderate method tiers are in-text evidence about the paper, so those
rows are exempt; the weak tier is near background, so it is not.

**Interaction to check when #424 merges.** On `main` an `aff:regular`-only paper scores
0.387, under the 0.40 floor, which is why the floor hides them. After #424 the same paper
scores 0.401 (stored as 0.4009), a hair **above** the floor, so the floor would stop
hiding repeat-user-only rows. The #430 measurement, which uses #424's weights, counts 595
candidates and 544 rows above the 0.40 floor. Decide before or with the #424 merge whether to move
the floor (for example just above 0.401) or accept the larger visible queue.

**Review queue features** (SPS `components/edit/core-claim-queue.tsx` on `master`):

- Claim or reject one paper; reject with a reason chip.
- Tick rows and confirm or reject them in one bulk request, capped at 500 PMIDs
  (`MAX_BULK_PMIDS`).
- "Reject all N…" rejects every row shown, behind an inline guard.
- Filters: Signals fired, LLM score, Method match, Method family, Repeat user, Year.
- **#3023 (open)** adds a **Score** facet listing Strong / Moderate / Slight / Weak in
  band order. Facet counts run over rows after the display floor, so ticking a band never
  brings a hidden row back. Queued and manually added rows carry no band and drop out
  while a Score is ticked. Checks run: typecheck, the 8 core-queue test files (375
  passed), eslint on touched files. Still owed: a staging eyeball on desktop and at 390px.

## Calibration

#430 (head `e1bfa2f`, stacked on #424) adds a per-core calibration map applied to the
summed log-odds before banding:

```
logit' = intercept + slope * logit
```

Set per core as `calibration: {intercept, slope}` in `config/core_dictionary.yaml`;
default identity (0, 1); the loader rejects slope ≤ 0. **Core 14's current value is
identity**, so no score moves. Both production `combine()` call sites pass the core, and
the held-score check uses the same map.

Why it exists: summing marginally fitted weights double-counts correlated evidence, so
the probability is overconfident even where the ranking is fine. On panel B with the
alias evidence set aside (staff + `aff:*` + LLM, 137 yes vs 421 random papers) the
held-out Platt slope is 0.82 [0.70, 1.06], and a joint refit beats a Platt rescale by
0.0055 [0.0004, 0.0114] nats of log-loss. Fitted jointly, the affinity weights drop to
about +0.4 nats, against +1.18 / +3.49 fitted one at a time: once staff and the LLM score
are known, affinity mostly counts the same evidence twice.

Core 14 held-out log-loss (leave-one-out; 2,000-sample paired bootstrap 95% CIs):

| Rows | n (claimed/rejected) | Current score | Platt map | Difference |
|---|---|---|---|---|
| All decided | 46 (26/20) | 0.918 | 0.586 | -0.33 [-0.60, -0.10] |
| In the WCM corpus | 27 (7/20) | 1.526 | 0.678 | -0.85 [-1.32, -0.38] |
| Engine-scored | 26 (6/20) | 1.389 | 0.845 | -0.54 [-1.30, +0.44] |

The verification of #430 also found that an **intercept-only offset** passes the
"CI excludes zero" test on the rows it would act on: engine-scored 0.591 against 1.389,
-0.80 [-1.37, -0.15], and in-corpus -0.62 [-1.21, -0.02]. It is still not shipped:

- **It breaks the anchors the thresholds were bracketed on.** The fitted offset of about
  -3.9 would drop a generic alias beside a home institution from 0.706 to 0.047 and staff
  alone from 0.731 to 0.053. The all-46 Platt map would drop the generic-alias anchor to
  0.343 and raise a no-evidence paper from 0.020 to 0.075.
- **The panel is selected on the score.** Reviewers decided rows the engine surfaced, and
  19 of the 20 rejected papers had LLM scores of 7 or 8.
- **Most claims are invisible to it.** 19 of the 26 claimed papers are outside the corpus.
- **The intervals are optimistic.** The bootstrap does not refit the model per resample,
  and the in-corpus offset passes by a narrow margin (upper bound -0.02).
- **It would shrink the queue sharply.** Under the all-46 Platt map, candidates over all
  5,365 stored rows fall from 595 to 345, and rows at or above the 0.40 floor from 544 to
  307. That 595 baseline is synthetic and already reflects #424's weights; the stored rows
  hold 486 candidates.

Every core-14 version wants a downward shift, which fits the 3.05-nat gap between core
14's base rate and the 2% prior. An intercept-only shift is the most likely first
calibration to turn on, once there are 100 or more decided in-corpus rows and the anchors
question is settled.

On panel B, adding first/last authorship, the INSIGHT alias and method tier to the joint
model gave nothing measurable (-0.0007 [-0.0057, +0.0070]). On core 14 it did
(-0.15 [-0.32, -0.02]), but that gain comes from INSIGHT firing on 2 claimed rows and 0
rejected, and it vanishes on the in-corpus and engine-scored subsets.

## Tried and rejected this week

Every one of these was measured read-only and left unwired: no `WEIGHTS` key, no pipeline
change. The PRs carry the scripts and result docs.

| Idea | PR | What was measured | Verdict |
|---|---|---|---|
| TF-IDF text similarity | #423 | Core 2 hard panel (n = 558): core-wide similarity +0.0157 [+0.0066, +0.0261] overall, but -0.0053 [-0.0118, -0.0008] on repeat users; within-author -0.0030 on repeat users. Spearman 0.58 to 0.70 with the LLM score. | Measures topicality the LLM already reads; no within-author lift where measurable. |
| Science-Metrix journal subfield | #421 | Covers 98.8% of the corpus. With the LLM in the score: +0.0097 [-0.0069, +0.0275]. Repeat users: -0.0343 [-0.0616, -0.0099]. | Inside the noise once the LLM is in; hurts the repeat-user ranking. |
| NIH S10 instrument grants / CTSC UL1 | #426 | 36 WCM S10s, 20 mapped to 6 cores, fire on 56 of 82,173 papers. For core 14, UL1 fires on 1/7 claimed and 1/20 rejected engine-surfaced rows; lift +0.0000 [-0.043, +0.045]. | Dead for core 14; for core 2 it marks one PI's own lab. |
| Lab-level usage (last non-staff author's other core papers) | #425 | Fires on 77 rows, affinity silent on 0 of them. Core 14 precision 0.43 vs 0.50 for affinity (production core set), 0.29 vs 0.55 (strict set). Core 2: +0.0040 [+0.0008, +0.0079] with a **negative** weight (-3.20). | Nested inside affinity by construction. The one untested variant is a lab head not on the byline (a trainee's mentor). |
| Instrument model named in full text | #427 | Not applicable to core 14 (no instruments). Core 2 over staff + affinity + LLM: +0.0031 [-0.0050, +0.0135]. Verification found 3 flow-core models significant under matched sampling (BD Influx, Sony MA900, FACSymphony A5). | Not general; a few strings worth a labelling pass for other cores. |
| GEO deposit text | #428 | Precise (12/14 core 5, 13/13 core 3) but fires on about 2.5% of GEO-linked papers; +0.014 to +0.023 AUC. | Dead. |
| WCM first/last authorship (PubMed affiliation) | #428 | +0.200 core 5, +0.126 core 3, +0.036 panel B. Verification: the labels are acknowledgement labels, and placebos show a generic "WCM-led paper" prior (AUC 0.84 and 0.90 for "body mentions WCM"). | Promising but unproven; needs labels that are not acknowledgement-based, a fit, and an overlap check. Not measured on core 14. |
| Core billing / usage records | none | No billing, booking or usage data anywhere reachable (repos, Projects, reciterdb and reciter schemas, SPS Prisma). The likely join key is `infoed_all.Account_Number` (67,050 of 67,050 rows filled, 28,799 distinct, all 10 characters). As a stand-in, "a funded PI is on the byline" fires on 21/26 claimed, 10/20 rejected, 0.838 of below-threshold and 0.870 of candidate rows. About 56% of InfoEd PI award rows have no project period. | Infeasible without an external extract. The funded-PI stand-in only identifies funded labs. Billing would be the cleanest signal outside the affinity loop if an extract (core, service date, requester and PI CWIDs, fund number) could be obtained. |
| Minimum of 3 confirmations | #424 (`542937f`) | See [above](#why-a-sliding-scale-and-not-a-minimum-of-3). | Replaced by the sliding scale. |
| Time decay | #424 | See [Signal 1](#signal-1-repeat-users-author-affinity). | Off. |
| Calibration map for core 14 | #430 | See [Calibration](#calibration). | Hook shipped at identity; no map set. |

## Defaults and when to change them

| Knob | Current value | Where set | Evidence that would change it | How to measure |
|---|---|---|---|---|
| Base-rate prior | logit(0.02) = -3.89 | `combine.PRIOR_LOGIT` (global) | Don't change globally; core 14's 0.096% base rate is handled through calibration | `measure_calibration.py` |
| Calibration | identity (0, 1) | `calibration:` per core in `core_dictionary.yaml` (#430) | ≥ 100 decided in-corpus rows; held-out log-loss CI excluding zero on engine-scored rows; a map that keeps a distinctive alias confirming and a lone LLM score not | `python3 scripts/measure_calibration.py collect ...` then `analyze --inputs inputs.json` |
| Confirm threshold | 0.65 | `combine.DEFAULT_CONFIRM_THRESHOLD`; `confirm_threshold` per core | Reviewers revoke a meaningful share of engine-confirmed rows; or a weight change moves an anchor across 0.65 | Re-run the "evidence alone" table; count revoked confirmations by evidence type |
| Triage threshold | 0.30 | `combine.DEFAULT_TRIAGE_THRESHOLD`; `triage_threshold` per core; `run.py --threshold` | Claims appearing just above it at a rate that justifies the queue size, or none at all | Claim rate per likelihood band from SPS decisions |
| SPS display floor | 0.40 | SPS `CANDIDATE_DISPLAY_FLOOR` (global; per-core override not built) | #424 lifts affinity-only rows to 0.401; claim rate on Slight rows near the floor | Count candidates and claims at 0.40 to 0.41 after #424 merges |
| Floor-exempt method tiers | strong, moderate | SPS `FLOOR_EXEMPT_METHOD_TIERS` | Claim rate on exempt rows vs other rows near the floor | SPS decisions by `methodTier` |
| Score bands | 0.85 / 0.65 / 0.40 | SPS `likelihoodBand` | Display only; keep Moderate aligned with the confirm bar | n/a |
| Repeat-user prior strength s | 5 (global; core 14 none) | `signals.AFFINITY_PRIOR_STRENGTH`; `affinity_prior_strength` per core (#424) | Global: another s beats 5 on panel B bucket AUC by more than one pair's worth with monotone refit cells. Core 14: ≥ 100 decided rows that clearly prefer another s | `python3 scripts/measure_affinity_prior_strength.py --core 14 --strengths 0 1 2 5 10 20`; `python3 scripts/fit_evidence_weights.py --affinity-only --prior-strength S` |
| Tenure lags | 3 years before, 2 after | `signals.TENURE_LAG_BEFORE` / `TENURE_LAG_AFTER` (#424) | A labelled paper that post-dates its author's departure (AFTER is unmeasured) | `fit_evidence_weights.py --affinity-only` with the lag changed |
| Decay half-life | off | `signals.AFFINITY_HALF_LIFE_YEARS` (#424) | A half-life beating off on panel B and on ≥ 100 core-14 decided rows by more than one pair's worth, with monotone buckets | `fit_evidence_weights.py --affinity-only --half-life H` for H in 2, 3, 5, 8 |
| Affinity bucket edges | 0.05, 0.70 | `combine._AFF_REGULAR_MIN`, `_AFF_CORE_MIN` | A larger panel that repopulates the top bucket | `fit_evidence_weights.py --affinity-only` |
| Affinity weights | 1.18 / 3.49 / 3.49 after #424 (0.79 / 3.43 / 4.93 on `main`) | `combine.WEIGHTS`, fitted only | Any change to the rate formula or panel | `fit_evidence_weights.py --affinity-only`, paste its block |
| `client` weight | 0.00 | `combine.WEIGHTS` | A real client list, plus the overlap with `aff:*` measured | `fit_evidence_weights.py` (prints and refuses the cell today) |
| `method:*` weights | 0.00 | `combine.WEIGHTS` | ≥ 30 positives with affinity 0 and no LLM yes, from a sampling frame that produces them | the overlap measurement in `combine.py`'s `method:*` comment |
| Alias list | 3 on `main`, 5 after #422 | `aliases:` for core 14 | Reviewers rejecting rows whose only alias is an INSIGHT one (0 of 43 today); a new name in claimed papers' full text | `python3 -m pipeline_cores.suggest_aliases`; DynamoDB rows by `ack_alias` and status |
| Alias hit counts | 25 / 20 / 2 cached (26 / 21 / 3 live) | `alias_hits:` | Any alias crossing 100 or 800 hits | `python3 -m pipeline_cores.refresh_alias_hits --core 14` (add `--write` to save) |
| Specificity edges | 100, 800 | `combine._DISTINCTIVE_MAX`, `_GENERIC_MIN` | A new alias-precision survey | `refresh_alias_hits` plus a precision read |
| LLM line | -1.86 + 0.68 × min(score, 9) | `combine.LLM_INTERCEPT`, `LLM_PER_POINT`, `LLM_MAX_FITTED` | A refit on a panel with LLM scores; not core 14's decided rows (selected on the score) | `fit_evidence_weights.py` |

## How reviewer decisions feed back

**Decisions are sticky.** `claimed` and `rejected` are written by SPS and never by the
engine. Every engine write is conditional on the row not being `claimed` or `rejected`
(`persist.py`), and the nightly `--reconcile` sweep only demotes `candidate` and
`confirmed` rows. SPS's `core_claim` table is the display authority regardless of what
DynamoDB holds.

**What a claim does immediately.** `claimed` and `confirmed` are the statuses that feed
the repeat-user numerator (`persist._USER_STATUSES`). A claim on an in-corpus paper raises
every byline author's rate on the next run (after #424, never for the claimed paper itself,
and only within each author's tenure window). A claim on a paper outside the corpus does
not count, because the numerator is gated to the corpus.

**What a rejection does immediately.** Nothing to any score. It removes the paper from the
queue for good, and the engine has no way to learn from it automatically.

**What both do later: they are the labels.** Every core-14-specific setting on this page
was measured on the 46 decided rows, and the next refit needs more. Today:

| | Now | Target | What it unlocks |
|---|---|---|---|
| Decided rows (claimed + rejected) | 46 (26 / 20) | 100 or more | re-measuring s, decay and the INSIGHT revisit rule on core 14 |
| Decided rows the engine scored (in the corpus) | 27 (7 / 20) | 100 or more | a calibration map (likely an intercept-only shift first) |
| Positives with no affinity and no LLM yes | 5 `method:strong` events | 30 (`MIN_PANEL`) | pricing the method-family tiers |
| Rejected rows whose only alias is INSIGHT | 0 of 43 | any material share | revisiting the INSIGHT aliases |

Three things make decisions more useful:

1. **Decide papers in the queue.** Claims on papers the engine never scored (19 of the 26
   so far) do not help any of the measurements above.
2. **Reject as well as claim.** Separation is measured on claimed against rejected, so a
   panel of only claims tells us nothing about precision.
3. **Decide across bands, not only the top.** The panel is currently selected on the
   score (19 of 20 rejections had LLM 7 or 8). Decisions on Slight and Weak rows, which
   the Score facet in #3023 makes easy to pull up, are what would let a calibration fit be
   trusted.

## Open items

- **Pending merges (ReciterAI):** #422 (INSIGHT aliases), #424 (affinity), #429 ("and"/"&";
  no core-14 effect), #430 (calibration hook; merge after #424, its diff includes #424
  until then). The experiment PRs #421, #423, #425, #426, #427 and #428 carry scripts and
  result docs only; merge them as records or close them.
- **Pending merge (SPS):** #3023, after a staging eyeball on desktop and at 390px.
- **CI billing block:** every ReciterAI PR's `pytest` check fails in 2 to 3 seconds.
  Local full-suite results: #422 2,925 passed; #424 2,949 passed; #429 2,953 passed; #430
  2,965 passed (each with 21 skipped and 14 deselected). Re-run CI once billing is fixed,
  before or after merge.
- **Display floor after #424:** affinity-only rows move from 0.387 to 0.401, just above
  the 0.40 floor. Decide whether to move the floor.
- **Stale ARCH alias hits:** run `python3 -m pipeline_cores.refresh_alias_hits --core 14
  --write` on the #422 branch. No score changes.
- **Small doc and test gaps found in verification:** `combine.py` on #424 says the rate-0
  cell is 85/137 vs 1166/1200, and a fresh fit prints 1164; three mutants survive in #424
  (the `affinity_base_rate` clamp and self-exclusion in the two measurement scripts, which
  have no tests); #422's test would still pass if `strict=True` were removed from
  `suggest_aliases`; `test_score_is_monotone_in_the_evidence` was loosened to `<=`
  because `aff:regular` and `aff:core` are now equal.
- **Institution check** reads "Cornell Univ" as Weill Cornell (PMID 40312501).
- **Signal 3 scans reference lists**, so an alias in a commonly cited title would match
  citing papers.
- **The nightly makes no LLM calls**, so new papers carry no LLM term (#388).
- **Client list:** the core owner can add known clients in SPS; the weight stays 0.00 until
  fitted.
- **Billing extract:** the one signal that escapes the affinity loop. Needs someone to
  obtain an extract keyed by fund number (`infoed_all.Account_Number`).

## Re-measuring

All read-only (DynamoDB Scan, reciterdb SELECTs, PMC/NCBI, S3 reads). None writes.

```bash
# Repeat-user prior strength on core 14's decided rows (#424)
python3 scripts/measure_affinity_prior_strength.py --core 14 --strengths 0 1 2 5 10 20

# Affinity weights and AUC on panel B (#424); add --half-life H to test decay
python3 scripts/fit_evidence_weights.py --affinity-only --prior-strength 5

# Calibration on core 14 and panel B (#430)
python3 scripts/measure_calibration.py collect --out inputs.json \
    --llm-cache <panel-B llm json> --c14-llm-cache c14_llm.json --live-llm --cache-dir <dir>
python3 scripts/measure_calibration.py analyze --inputs inputs.json

# Alias hit counts (add --write to save to the yaml)
python3 -m pipeline_cores.refresh_alias_hits --core 14
```
