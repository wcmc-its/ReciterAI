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
  loader** (`fulltext.py`, cached; signal 3 confirmed end-to-end on real CBIC
  papers); co-authorship SQL (`utils.db`; fires on real pilot PMIDs);
  **two-pass LLM triage** (`signals.llm_triage`, Haiku screen → Sonnet dense,
  calibrated on the pilot — see below); combiner.
- **Wired to real utils, not yet run end-to-end:** DynamoDB persist
  (`utils.dynamodb_helpers`).
- **TODO:** affinity read-back (`run.load_confirmed_pairs`) + full-byline author
  read; one-Haiku-call-screens-all-cores optimization; S3-backed full-text cache
  for the whole corpus.

### LLM-triage calibration (237-paper pilot, Bedrock Haiku 4.5 + Sonnet 4.6)
- Haiku screen at `SCREEN_CUTOFF=2` → **100% recall** (no true positives lost before dense scoring).
- Sonnet dense score AUC vs human labels = **0.933**; at the surface threshold (score ≥ 3) → **94% precision / 83% recall**.
- Repro: `Projects/Inferring Cores and Services/analysis/calibrate_llm_triage.py` (drives the real `llm_triage` path).

## Run
```bash
python3 -m pipeline_cores.run --core 2 --test 200 --dry-run   # no AWS needed
python3 -m pipeline_cores.run --core 2 --with-llm             # Bedrock + DynamoDB
```
