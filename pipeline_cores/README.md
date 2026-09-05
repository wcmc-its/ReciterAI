# pipeline_cores — WCM core-facility usage inference

A third classification axis beside Topics/Subtopics: **which WCM core facility
(Biomedical Imaging, Flow Cytometry, Genomics, …) was used to produce a paper?**
Multi-signal candidate-generation feeding a human claim — not a text-only
auto-labeler. (Full viability analysis + validation: `ReCiter-Publication-Manager`
working tree, `Projects/Inferring Cores and Services/analysis/`.)

## Why this shape (from the validation)
- **Acknowledgement match** is ~100% precision but **near-zero recall in the wild**
  (0/267 random WCM papers named the imaging core, though 31% used imaging) — a
  *confirmer*, not a discoverer.
- **LLM on title+abstract** is conservative (4% FP on clear negatives) and a good
  *triage filter* (flags 8–14% of the corpus) — but at the real <1% base rate its
  precision is too low to auto-label.
- **Resolved co-authorship** of core staff is the best scalable recall lever
  (39% recall / 100% precision on the pilot), capped only by upstream ReCiter
  target-set coverage of some staff.

## Signals

| # | Signal | Module | Role | Fitted weight |
|---|---|---|---|---|
| 1 | author × core affinity (repeat-user prior) | `signals.author_affinity` | recall prior | `aff:trace` +0.79 / `aff:regular` +3.43 / `aff:core` +4.93 |
| 1b | curated known clients (asserted, not inferred) | `run_core`, `persist.get_curated_clients` | recall on cores with no history | `client` **0.00 — unfitted** |
| 2 | core-staff co-authorship (resolved `personIdentifier`) | `signals.coauthorship_index` | deterministic recall | `staff` +4.89 |
| 3 | acknowledgement / alias name-match | `signals.acknowledgement_signal` | strongest single weight | `ack` +6.37, plus conditional terms |
| 4 | LLM triage (two-pass Bedrock) | `signals.llm_triage` | ranking only, never confirms | line: −1.86 + 0.68 × min(score, 9) |
| 5 | human claim (SPS ADR-005 override) | *(in SPS, not here)* | source of truth | n/a — read-time precedence |

## The scoring model

**`combine.combine()` sums the weights of the evidence present, in log-odds:**

```
logit(P) = PRIOR_LOGIT + Σ wᵢ        PRIOR_LOGIT = logit(0.02) = −3.8918
P        = sigmoid(logit)
status   = confirmed  if P >= confirm_threshold (default 0.65)
           candidate  if P >= triage_threshold  (default 0.30)
           below_threshold otherwise
```

Both thresholds are per-core overridable via `confirm_threshold` / `triage_threshold` in
`config/core_dictionary.yaml`. Naive Bayes on purpose: one line, every contribution
separately inspectable via `combine.explain()`, and it spreads by construction.

**Absent evidence contributes no key.** A never-scored `llm_score` is `None`, which is
not the claim "scored 1"; an affinity rate of 0 emits nothing even though the cell
measures −0.99, because pricing that one absence and no other would bias every pair with
no author history.

### What one piece of evidence is worth on its own

Computed from the shipped weights — this is the table to reason from, and to re-run
after any weight change:

| evidence alone | P | status |
|---|---|---|
| distinctive alias beside a home institution | 1.000 | confirmed |
| aff:core (rate ≥ 0.70) | 0.738 | confirmed |
| staff co-author | 0.731 | confirmed |
| LLM 9 or 10 | 0.591 | candidate |
| aff:regular (rate 0.30) | 0.387 | candidate |
| **curated client** | **0.020** | **below_threshold** |
| generic alias beside another institution | 0.002 | below_threshold |

Two things to read off it. **The LLM alone never confirms** — it tops out at 0.591
against a 0.65 bar, deliberately, and that is the one doctrine kept from the old
hard-coded precedence. And **`aff:core` alone confirms**, at 0.738: an author who has
already given ≥70% of their corpus output to this core confirms their next paper on the
strength of that history. That is measured, not chosen (55/137 labelled-yes vs 3/1200
corpus), but it makes the affinity numerator's correctness load-bearing — see #391 below.

### The `client` signal is wired but inert

`WEIGHTS["client"]` is **0.00** and `client_cwids` is **not in `persist.build_core_item`**,
so a curated known client on a byline is extracted, unioned from YAML + DynamoDB, visible
in `explain()`, persisted nowhere, and worth exactly zero nats. A paper whose only
evidence is a curated client scores 0.020 and is never written.

This is refusal, not oversight (#383). There is no curated list large enough to fit a
weight against, and "a curated list is high-precision by construction" is not a fit — that
argument is what produced the hand-picked constants #382 deleted. The prerequisite is
measuring the overlap between `client` and the `aff:*` buckets first: signal 1 *infers* a
core's users from prior confirmations while this key *asserts* them, so wherever curation
and history agree they fire on the same rows and summing both double-counts.

**Ack evidence:** an alias match records `ack_alias_hits` (the alias's global PMC hit
count, cached by `python3 -m pipeline_cores.refresh_alias_hits`; specificity predicts
precision, r=−0.852 vs home%), `ack_institution` (`home`/`other`/`none`, where a Tri-I
partner is home and "none" is only ambiguous for a generic alias), and `ack_section`
(`ack`/`methods`/`body`). `ack` is the marginal "an alias matched at all" and everything
under it is conditional on that match, so summing them is the chain rule, not double
counting — which is why a *generic* alias is a net loss of ~5 nats against the +6.37.
Section is extracted but **weighted at zero until measured**; `run.py` does not pass the
XML through, so it is `""` in production.

**Repeat-user prior (signal 1):** core users are overwhelmingly repeat users, so
`run_core` runs two phases — deterministic + LLM first, then it attributes every
confirmed/claimed paper (this run + prior runs via DynamoDB) to its byline authors and
re-scores the rest. One confirmation lifts all of that author's other papers. The strength
of that lift is a **rate**, not a count: `build_affinity_index` divides each author's
confirmed papers for the core by their total papers in the scoreable corpus, and
`author_affinity` takes the **max** over the byline.

Both sides of that ratio have to mean the same thing, and there are two gates that keep
them honest:

- **The numerator is corpus-gated** (`ingest.filter_corpus_pmids`). DynamoDB holds
  confirmed/claimed rows for papers this pipeline does not score — 19 of core 14's 43
  prior rows — and counting those against a corpus-restricted denominator produced rates
  above 1 that floor into the strongest bucket.
- **A paper counts once, not once per source** (#391). Prior confirmations and this run's
  confirmations are largely the *same papers* on a corpus-wide run, so phase 2 carries
  pmid **sets** and unions them, taking `len()` only at the `build_affinity_index`
  boundary. A counter double-credited every re-confirmed paper to every byline author,
  which moved authors a whole bucket — and since `aff:core` alone confirms, an inflated
  author's next paper auto-confirmed with no ack, no staff co-author and no LLM score.
  `build_affinity_index` only *warns* when the doubled value exceeds the author's own
  corpus total, so most of the inflation was silent. Fixing it moved a live full-corpus
  run from 216→186 confirmed and 1632→1434 candidates.

### A worked example

One pair, every signal firing, computed from the shipped weights
(`combine.explain()` returns the seven evidence rows below, in that order and with those
labels — it is what the claim queue renders; the prior, logit and P rows are `score()`'s
arithmetic around them):

| evidence | weight |
|---|---|
| *(prior — 2% base rate)* | −3.89 |
| `ack` — an alias matched at all | +6.37 |
| `staff` — a tracked core-staff member on the byline | +4.89 |
| `inst:home` — the match sits beside WCM or a Tri-I partner | +3.47 |
| `aff:regular=0.420` — best byline author has given 42% of their output to this core | +3.43 |
| `llm:7` — Sonnet dense score 7 → −1.86 + 0.68×7 | +2.90 |
| `ack.spec:moderate` — "Epigenomics Core", 666 global PMC hits | +1.90 |
| `client` — a curated known client on the byline | +0.00 |
| **= logit** | **+19.07** |
| **= P** | **1.000 → confirmed** |

Note `ack` + `ack.spec:moderate` + `inst:home` is the chain rule on one match, not three
independent claims — and had the alias been generic beside another institution those three
would sum to −2.32 instead of +11.74, i.e. the same "an alias matched" turned into
evidence *against*. That is the whole point of the conditional terms.

### Status lifecycle

```
                              ┌──── run.py re-score, either direction ────┐
                              ▼                                            │
   (absent) ──────────────► candidate ───────────────────────────────► confirmed
                              │  ▲                P >= confirm_threshold
                              │  └──── below_threshold  (operator reconcile
                              │            scripts ONLY — nothing in this package)
                              ├──► claimed   (human, in SPS) ─┐
                              └──► rejected  (human, in SPS) ─┴─► engine never overwrites (#386)
```

| status | written by | meaning |
|---|---|---|
| `below_threshold` | **nothing in this package** — see below | scored, too low to surface |
| `candidate` | `run.py` and `batch_screen` | in the review queue |
| `confirmed` | `run.py` | cleared `confirm_threshold` on evidence alone |
| `claimed` | SPS (human) | a reviewer said yes — engine reads it, never writes it |
| `rejected` | SPS (human) | a reviewer said no |

**`below_threshold` is never written by `pipeline_cores`.** `combine.combine()` can return
it, but `run.py` persists only `confirmed`/`candidate` and `put_candidate` always writes
`candidate` (drop-band pairs are skipped entirely). It has been that way since the first
commit. The status exists in the live table anyway — 4,407 rows, **all on core 14 and none
on any other core** — because out-of-band operator scripts wrote them during the core-14
work (`demote_core14_stale.py` and friends, the reconcile pass that exists precisely
because a re-score cannot demote).

That matters for reading queue growth: those rows are the residue of a *manual* demotion,
and a corpus-wide `run.py` pass re-scores them and promotes a large share back. The first
nightly's jump from 63 open candidates to ~1,434 is therefore partly the engine undoing a
hand cleanup, not purely new discovery. Check `scored_at` vintage before concluding the
model changed its mind.

`_USER_STATUSES` is `{"confirmed", "claimed"}` — a human **`claimed`** feeds back in as
affinity-prior input, lifting that author's other papers on the next run; a `rejected`
does **not** feed back as negative evidence anywhere. The model has no way to learn from a
rejection. SPS's `core_claim` table is the display authority regardless of what DynamoDB
`status` says.

## Candidate generation — `prefilter` and the `topicalPrior` trap

`batch_screen` attaches a free prior to each pair *before* the Sonnet screen. It is a
**soft prioritizer, not a drop gate** — `--drop-threshold` defaults to 0.0, so nothing is
dropped until calibration justifies it. Two free signals, noisy-OR'd, author outranking
topic:

| author-affinity | MeSH E-tree | `prefilter_prior` |
|---|---|---|
| no | no | 0.00 |
| no | yes | 0.40 |
| yes | no | 0.60 |
| yes | yes | 0.76 |

**This value is not a MeSH match, and SPS's chip label has said otherwise.** A
`prefilter_prior` of 0.6 means *author affinity fired and MeSH did not*. Since author
affinity is by far the more common of the two, a chip reading "Topical MeSH match" is
false on the majority of live rows — it was wrong on 78% of them. Read the value, not the
label: only 0.4 and 0.76 imply any MeSH involvement at all, and only 0.4 implies MeSH
*alone*.

MeSH qualifiers/subheadings were measured and **rejected** (they discriminate imaging
only, are redundant there, and as a gate MEDLINE indexing lag would drop ~half the
corpus). Bare descriptors joined to `mesh_tree_numbers` are reciterdb-native and free.
Five cores (1 Bioinformatics, 6 Biorepository, 7 Metabolic Phenotyping, 8 Microbiome,
10 Immune Monitoring) have no clean MeSH technique branch and rely on author-affinity plus
the screen alone.

## What a run WRITES, and what it destroys

The least obvious part of this pipeline, and the part that has caused every data-loss
incident in it. `persist.put_core_usage` is an **UpdateItem that SETs what this run
produced and REMOVEs every `_OWNED_ATTRS` attribute it did not**. `_OWNED_ATTRS` is all 12
attributes `build_core_item` can emit; the five *optional* ones are the only members that
can ever land in REMOVE: `ack_alias`, `ack_snippet`, `llm_score`, `llm_rationale`,
`author_affinity`. The other seven (`pmid`, `core_id`, `likelihood`, `status`,
`scored_at`, `signal_coauthors`, `signal_ack`) are written on every record, so they are
always in SET.

That is deliberate (#384) — a previous run's `llm_rationale` surviving on a pair scored
without the LLM this time is stale evidence reading as fresh. Three consequences:

1. **A run without `--with-llm` strips the LLM evidence** off every row it re-surfaces,
   and exits green doing it. `--llm-carry-forward` exists for exactly this: it reads the
   stored `llm_score`/`llm_rationale` back and re-emits them, so they land in SET rather
   than REMOVE. The scheduled nightly depends on it.
2. **A degraded read becomes a wipe.** Any upstream read that fails soft to empty gets its
   emptiness *written*. So the two reads on the write path raise instead:
   `scan_core_llm_scores` unconditionally, and `scan_prior_core_usage` under `strict=True`
   (it still degrades for `batch_screen` and `suggest_aliases`, which only report what
   they read). The alias search is the one remaining fail-soft — "no PMC hits" is a
   legitimate result that cannot be told from an NCBI outage — so a log metric filter
   covers it instead.
3. **Attributes outside `_OWNED_ATTRS` are never touched**, which is what protects
   `batch_screen`'s six (`prefilter_prior`, `screen_confidence`, `screen_band`,
   `screen_version`, `prefilter_version`, `run_mode`). SPS reads `prefilter_prior` as the
   queue's `topicalPrior` chip.

**Human decisions survive** (#386). The write carries
`attribute_not_exists(#st) OR NOT #st IN (:claimed, :rejected)` — `#st` being the
placeholder the SET clause already minted for `status`, not a second name bound to the
same attribute — and on
`ConditionalCheckFailedException` re-issues the same update with `status` dropped — so
likelihood, `scored_at` and the evidence still refresh under a reviewer's decision while
the decision itself stands. Engine statuses stay fully mutable: a `confirmed` row can
still be re-scored down to `candidate`.

**`run.py` never writes `below_threshold`.** Only `confirmed`/`candidate` are persisted,
so a re-score cannot demote a row out of the queue, and there is no record of "scored and
rejected" anywhere. That second fact is why no "only score what is new" flag can bound a
corpus-wide LLM pass: 79,860 of the 80,203 corpus publications carry no stored `llm_score`
on any given night (#388).

## Known: two writers, one `likelihood`

`batch_screen` writes `likelihood` from a noisy-OR of its screen confidence and prefilter
prior; `run.py` writes it from the log-odds score above. They are different scales on the
same attribute, and `run.py`'s crosses the 0.30 triage bar more easily. Observed directly
on the first live write: rows moved `below_threshold → candidate` while their `likelihood`
*fell* from 0.75 to 0.3866 — the status rose because the scale changed under it, not
because the evidence did.

Those `below_threshold` rows came from the operator reconcile scripts (see the lifecycle
section), **not** from `batch_screen`, which has never written to core 14 at all — its
`prefilter_prior` count there is 0. So a corpus-wide `run.py` pass promoting a large share
of them back is the engine re-litigating a hand demotion. Measured full-corpus: 1,434
candidates against ~63 open before it. Not reconciled; read queue growth with this in mind
and check `scored_at` vintage before concluding the model changed its mind.

## Data flow
```
ReciterDB (read-only)            config/core_dictionary.yaml
  analysis_summary_article   ─┐    aliases, staff CWIDs, owner
  reporting_abstracts         ├─►  pipeline_cores.run
  analysis_summary_author    ─┘      ├─ ingest → signals → combine
                                     └─ persist → DynamoDB  PUB#{pmid} / CORE#{core_id}
                                                   │
                                                   ▼
        SPS  etl/dynamodb/publication-core-mapper (parallel to publication-topic-mapper)
              → MySQL → profile/methods surface;  claims via ADR-005 override layer
```
No new ReciterDB MySQL table: input read-only, output DynamoDB, claims in SPS.

## Status
- **Live + tested:** dictionary load; acknowledgement matcher + **PMC full-text
  loader** (`fulltext.py`, two-tier cache **disk → S3 → NCBI**; signal 3 confirmed
  end-to-end on real CBIC papers); co-authorship SQL (`utils.db`; fires on real pilot PMIDs);
  **two-pass LLM triage** (`signals.llm_triage`, Haiku screen → Sonnet dense,
  calibrated on the pilot — see below; **threaded** `--llm-workers` and resilient —
  a slow/hung Bedrock call is bounded by a short read-timeout and screened out
  rather than stalling the corpus); **author-affinity repeat-user prior**
  (`run.run_core` two-phase + `persist.scan_prior_core_usage` cross-run
  read-back; verified live: a staff-confirmed paper lifts its non-staff
  co-authors' sibling papers to candidate); combiner.
- **Running in production.** `reciterai-cores-daily` (EventBridge, `cron(0 5 * * ? *)`)
  runs core 14 corpus-wide every night on Fargate — see `infra/README.md`, "Cores daily
  run launch path". Measured on Fargate 2026-09-05: 80,245 pubs -> 186 confirmed / 1,434
  candidates, ~42s wall clock, **zero Bedrock calls**. (Other figures in this repo say
  80,203 pubs and 62 open candidates — earlier measurements of numbers that move. Prefer
  the dated one and re-measure rather than reconciling them on paper.)
- **TODO:** per-author **time decay** in affinity — weight each confirmation by
  recency. Deferred: needs the publication year carried into
  `persist.scan_prior_core_usage` and a half-life calibrated on
  `analysis/labeled_set.csv` (don't guess the decay blind).
- **Done (was TODO):**
  - **S3-backed full-text cache** — `fulltext.py` reads/writes through
    `s3://wcmc-reciterai-artifacts/cores/fulltext/{pmid}.xml` (best-effort,
    negatives cached durably). Warm it out of band before a full run with
    `python3 -m pipeline_cores.prefetch_fulltext` so the corpus is fetched from
    NCBI once, not on every cold host.
  - **one-Haiku-screens-all-cores** — `signals.screen_all_cores` does ONE Haiku
    screen per pub covering every core (≈13x fewer screen calls); wired into
    `run.py` behind `--all-cores-screen`. **OPT-IN, not default:** calibration on the
    237-pilot (`analysis/calibrate_all_cores_screen.py`) showed it loses screen recall
    — 89.1% vs the per-core screen's 100% (a recall-first prompt floor only reached
    93.4%, while ~doubling dense fan-out). The screen must be recall-first, so the
    **per-core screen carries the full run** until the all-cores path re-calibrates to
    parity. Re-run that script before flipping it on.
  - **author-affinity RATE** — `signals.author_affinity` is the max over the byline
    of each author's *share* of their own corpus output already confirmed for the
    core. Replaced the hand-picked count curve (0.45→0.85, capped) **and** its
    noisy-OR aggregation: the count put 83.9% of core 14's live 347-row queue on one
    value, and noisy-OR is a monotone function of how many authors fire — the one
    feature measured *not* to separate (AUC 0.6505 vs 0.8770 for the rate).

### LLM-triage calibration (237-paper pilot, Bedrock Haiku 4.5 + Sonnet 4.6)
- Haiku screen at `SCREEN_CUTOFF=2` → **100% recall** (no true positives lost before dense scoring).
- Sonnet dense score AUC vs human labels = **0.933**; at the surface threshold (score ≥ 3) → **94% precision / 83% recall**.
- Repro: `Projects/Inferring Cores and Services/analysis/calibrate_llm_triage.py` (drives the real `llm_triage` path).

## Candidate generation — `batch_screen` run-mode
The deterministic run (`pipeline_cores.run`) lands the **confirmed** substrate; the
`batch_screen` run-mode generates the **candidate** queue for everything not
deterministically confirmed, via the validated batched one-core LLM screen:

```
pool = corpus pubs (minus this core's already-confirmed pubs)
 → prefilter SOFT PRIOR (author-affinity OR bare-descriptor MeSH E-tree; recorded, NOT
    dropped in v1 — --drop-threshold defaults to 0.0)
 → Sonnet ONE-CORE TITLE screen (~40 papers/call) → confidence 1-10
 → bands: high=candidate, mid=curator (both written status=candidate; band distinguishes),
    low=drop (skipped)
 → idempotent, never-downgrade conditional writes to the `reciterai` table.
```
- **Validated mechanism** (`analysis/prototype_one_core_synopsis.py`): one core per call +
  a title input recovers 95–100% recall where the all-cores batch capped at 88% (focus is
  the lever; Sonnet ≈ Opus). See `analysis/SPEC-cores-probabilistic-cascade.md`.
- **Pre-filter = soft prioritizer, not a gate (v1).** The prior is recorded so the
  Option-3 calibration can later measure where a drop threshold could sit without losing
  recall; until then nothing is dropped. **MeSH subheadings (qualifiers) were measured and
  rejected** (`analysis/FINDINGS-cores-mesh-subheadings-2026-06-21.md`): discriminative for
  imaging only, redundant there, and a gate would lose ~half the corpus to MEDLINE indexing
  lag. The bare-descriptor E-tree signal is reciterdb-native (no out-of-band fetch) and
  fires on ~21% of confirmed imaging pubs (concentrated in the imaging/equipment families;
  genomics relies on author-affinity + the screen).
- **Calibrated bands.** The Option-3 Sonnet pass (`analysis/calibrate_batch_screen.py`) set
  `curator-min=2` (recall-safe drop floor — held-out recall 91–100% at ≥2 across the
  well-powered cores) and `candidate-min=5` (auto-surface at ~91% pilot precision). Writes are
  opt-in (`--write`); the full-corpus run is the next gated step. Candidate writes
  (`put_candidate`) **never downgrade** a `confirmed`/`claimed`/`rejected`
  row and are idempotent for their decision attributes. (Caveat: a pair that bands `candidate`
  -> `drop` on a later run keeps its stale candidate row — a demote/reconcile pass is a
  follow-up before the calibration flip.)

```bash
python3 -m pipeline_cores.batch_screen --core 2 --test 200            # dry-run (no AWS write)
python3 -m pipeline_cores.batch_screen --core 2 --with-llm            # screen, dry-run
python3 -m pipeline_cores.batch_screen --core 2 --with-llm --write    # screen + write candidates
```

## Run
```bash
# (optional, recommended before a full run) warm the shared full-text cache once:
python3 -m pipeline_cores.prefetch_fulltext                    # whole corpus -> disk + S3
python3 -m pipeline_cores.prefetch_fulltext --test 200         # first N (smoke)
python3 -m pipeline_cores.prefetch_fulltext --no-s3            # disk only (no AWS)

python3 -m pipeline_cores.run --core 2 --test 200 --dry-run   # no AWS needed
python3 -m pipeline_cores.run --core 2 --with-llm             # Bedrock + DynamoDB

# THE SCHEDULED NIGHTLY (reciterai-cores-daily, 05:00 UTC). Core 14 only, corpus-wide,
# and ZERO Bedrock calls: --llm-carry-forward re-emits the stored LLM evidence that
# put_core_usage would otherwise REMOVE from every row it re-surfaces.
python3 -m pipeline_cores.run --core 14 --with-affinity --alias-search --llm-carry-forward

# full corpus, all cores, on the warm S3 cache. Triage is threaded (--llm-workers,
# default 8) so the run finishes in reasonable wall-clock; lower it if Bedrock throttles.
python3 -m pipeline_cores.run --with-llm --with-fulltext --fulltext-s3 --with-affinity
```

### Choosing the LLM posture

| flags | Bedrock cost | stored LLM evidence |
|---|---|---|
| neither | none | **DESTROYED** on every re-surfaced row (#384) — never run this against live data |
| `--llm-carry-forward` | none | preserved. Everything else IS re-scored and every surfaced row rewritten — only LLM *triage* is skipped |
| `--with-llm --llm-carry-forward` | only pubs with no stored score | preserved; new pubs triaged |
| `--with-llm` | every pub in scope (~80k unscoped) | fully re-scored |

`--llm-carry-forward` without `--with-llm` is the nightly. The combination with
`--with-llm` sounds like it should be cheap on a schedule and is not: `run.py` persists no
`below_threshold` rows, so ~79,860 of 80,203 corpus pubs have no stored score on any given
night. The nightly Bedrock budget is open in #388.

Run deterministic-only for a fast, AWS-cheap pass: co-authorship confirms staff papers and
the repeat-user affinity prior surfaces their siblings as candidates — but pair it with
`--llm-carry-forward` against the live table, or it strips the queue's LLM chips.

### Operational reads

- `scan_prior_core_usage(core_id, strict=True)` — the affinity prior. `strict` is for
  callers that persist what they read; it raises rather than degrading to `[]`.
- `scan_core_llm_scores(core_id)` — the carry-forward read. Raises on a Scan error **and**
  on any row whose `llm_score` is in an unreadable shape, because a partial result would
  mass-REMOVE the rest.
- `get_curated_clients(core_id)` — `PK=CORE#{id}`, `SK=CLIENTS`, written by SPS's "Known
  clients" panel. Fail-soft by design. **The SK must never begin with `CORE#`**: both
  `scan_prior_core_usage` and the SPS ETL select usage rows with
  `begins_with(SK, "CORE#")`, so the config item would surface as a phantom publication in
  both.

## Changing a weight

Do not hand-edit `combine.WEIGHTS`. Every cell is fitted and carries the n behind it, and
a cell too thin to support a weight is `0.00` **and says so** rather than being invented.

1. `scripts/fit_evidence_weights.py` is the provenance. It imports
   `combine.evidence_features`, so the weights can never be fitted against a different
   feature set than the one that scores production.
2. `scripts/measure_evidence_spread.py` re-measures spread and AUC against
   `origin/main`'s combiner after any change.
3. Re-run the "what one piece of evidence is worth" table above. The thresholds are
   *bracketed* by the weights, not chosen: 0.65 sits **below** a lone staff co-author
   (0.731), which must confirm, and **above** the LLM's ceiling (0.591), which must not,
   with nothing within 0.05 of the bar (nearest: the LLM at Δ0.059, and a generic alias
   beside a home institution at 0.706, Δ0.056). A weight change can quietly move a piece
   of evidence across it.

Three refusals worth preserving, because each is a place someone will be tempted to guess:
`sec:ack`/`sec:methods`/`sec:body` are 0.00 (collected but the two panels differ in JATS
coverage, not in what a section *means*); `ack.spec:unknown` and `inst:none@*unknown*` are
0.00 (never observed on either side — neutral, not penalised); `client` is 0.00 (#383).

## File map

| file | what it owns |
|---|---|
| `models.py` | `CoreDefinition`, `SignalResult`, `CoreUsageRecord`, the status constants |
| `dictionary.py` | loads `config/core_dictionary.yaml` (aliases, staff, clients, per-core thresholds) |
| `ingest.py` | ReciterDB reads: corpus, bylines, author totals, corpus gating |
| `signals.py` | ack matcher, co-authorship, affinity index, LLM triage, screens |
| `combine.py` | `WEIGHTS`, `evidence_features`, `explain`, `score`, `combine` — **the model** |
| `run.py` | the entry point: two-phase `run_core`, CLI flags, orchestration |
| `persist.py` | DynamoDB writes and the three reads; `_OWNED_ATTRS`; the #386 guard |
| `prefilter.py` | the free `prefilter_prior` (author-affinity OR MeSH E-tree) |
| `batch_screen.py` | the candidate-generation run-mode |
| `pmc_search.py`, `fulltext.py` | alias search + PMC full-text, disk→S3→NCBI cache |
| `refresh_alias_hits.py` | caches each alias's global PMC hit count (drives specificity) |
| `suggest_aliases.py`, `suggest_staff.py` | curation helpers, not part of scoring |

Infra and the nightly: `infra/README.md`, "Cores daily run launch path".

## Open threads

| # | what |
|---|---|
| #383 | fit the `client` weight — it is 0.00 and `client_cwids` is not persisted |
| #388 | the nightly Bedrock budget; a corpus-wide `--with-llm` is ~80k Haiku screens |
| — | `batch_screen` and `run.py` write `likelihood` on different scales (above) |
| — | per-author **time decay** in affinity: needs publication year carried through `scan_prior_core_usage` and a half-life calibrated on `analysis/labeled_set.csv`. Do not guess the decay |
| — | `ack_section` is extracted but unpriced; `run.py` does not pass the XML through |
| — | a scoped run (`--pmids-file`) can neither surface a pair outside its set nor demote one — never use it to generate a pool |
