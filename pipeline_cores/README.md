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

## Signals (priority order)
| # | Signal | Module | Role |
|---|---|---|---|
| 1 | author × core affinity (repeat-user prior) | `signals.author_affinity` | recall prior |
| 2 | core-staff co-authorship (resolved `personIdentifier`) | `signals.coauthorship_index` | deterministic recall |
| 3 | acknowledgement / alias name-match | `signals.acknowledgement_signal` | deterministic confirmer |
| 4 | LLM triage (two-pass Bedrock) | `signals.llm_triage` | ranking only |
| 5 | human claim (SPS ADR-005 override) | *(in SPS, not here)* | source of truth |

`combine.combine()` auto-**confirms** on signals 2 or 3; otherwise noisy-ORs the
LLM score and affinity into a **candidate** likelihood for the claim queue.

**Repeat-user prior (signal 1):** core users are overwhelmingly repeat users, so
`run_core` runs two phases — deterministic+LLM first, then it attributes every
confirmed/claimed paper (this run + prior runs via DynamoDB) to its byline
authors and re-scores the rest. One confirmation thus lifts all of that author's
other papers. `affinity_strength(n)` scales 0.45→0.85 with the author's confirmed
count for the core.

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
- **Wired to real utils, not yet run end-to-end:** DynamoDB persist
  (`utils.dynamodb_helpers`).
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
  - **noisy-OR author affinity** — `signals.author_affinity` combines a paper's
    repeat-user co-authors with 1−Π(1−sᵢ) (was: max), clamped below the
    deterministic-confirmer ceiling.

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

# full corpus, all cores, on the warm S3 cache. Triage is threaded (--llm-workers,
# default 8) so the run finishes in reasonable wall-clock; lower it if Bedrock throttles.
python3 -m pipeline_cores.run --with-llm --with-fulltext --fulltext-s3 --with-affinity
```
Run deterministic-only (no `--with-llm`) for a fast, AWS-cheap pass: co-authorship
confirms staff papers and the repeat-user affinity prior surfaces their siblings as
candidates — no Bedrock involved.
