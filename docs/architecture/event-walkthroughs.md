# Key event walkthroughs

Companion to architecture view **⑧ Key event sequences** ([gallery](index.html) · spec in [`scripts/diagrams/definitions/08-key-event-sequences.mjs`](../../scripts/diagrams/definitions/08-key-event-sequences.mjs)). That diagram shows the *shape* of each recurring event; this page walks one **concrete instance** through each, so the steps are tangible.

**Sourcing.** Values are pulled from real, public-facing artifacts in this repo — the hierarchy publish [`out/hierarchy/v2026-06-11/`](../../out/hierarchy/v2026-06-11/) and the spotlight publish `out/spotlight-2026-06-13.json` — and the thresholds from [`config/thresholds.json`](../../config/thresholds.json) / the pinned crons in [`infra/eventbridge.json`](../../infra/eventbridge.json). Values that live only in DynamoDB (per-publication topic scores, `IMPACT#` and `DRIFT#` rows) are not in the local artifacts and are marked **_representative_**. The artifacts are subtopic-centric; the only personal data they carry is paper **author names**, which are elided here.

---

## 1 · New publication scored — *weekly*

**Trigger** EventBridge `reciterai-hot-weekly` · `cron(0 12 ? * MON *)` → Step Functions hot path.

| Step | What happens |
|---|---|
| **①** Haiku screen | one call scores all 66 topics; topics below `score_floor` **0.3** are dropped. `aging_geroscience` clears the screen. |
| **②** Sonnet dense score | the survivors are re-scored on **0–1** with a rationale. _representative:_ ~0.8 for `aging_geroscience`. |
| **③** Assign + rollup | the paper is assigned subtopic **`aging_cellular_senescence_molecular`** and folded into its authors' CWID rollups. |
| **④** Record written | `TOPIC#{pmid}` (per-topic scores + subtopic) and `FACULTY#{cwid}` (ranked rollup). |

**Real** — topic `aging_geroscience` (`display_threshold` 0.5); subtopic `aging_cellular_senescence_molecular` = *"Cellular Senescence and Molecular Aging Mechanisms"* (`activity_count` 31, `total_weight` 13.80); a real member PMID `31913702`. All from `out/hierarchy/v2026-06-11/`. **Representative** — the 0–1 topic score (a DynamoDB `TOPIC#` value).

---

## 2 · Daily enrichment + OpenAI fallback — *daily*

**Trigger** EventBridge `reciterai-enrichment-daily` · `cron(0 11 * * ? *)` → ECS Fargate task.

| Step | What happens |
|---|---|
| **①** Read delta | new/changed PMIDs since the last tick are pulled from ReciterDB. |
| **②** Sonnet synopsis + impact | one ≤95-char plain-language synopsis + an impact score in **[0, 100]**. If Bedrock **content-filters** the call, it retries on OpenAI **`gpt-5.1`**. |
| **③** Record written | `IMPACT#{pmid}` (synopsis + impact). |

**Real** — example PMID `38867038`, *"Endoplasmic reticulum–plasma membrane contact gradients direct cell migration"* (Nature, 2024), from the `2026-06-13` artifact. **Representative** — the synopsis text and impact integer (DynamoDB `IMPACT#`; not in the local artifact).

---

## 3 · Monthly spotlight publish — *monthly*

**Trigger** EventBridge `reciterai-spotlight-monthly` · `cron(0 13 1 * ? *)` → Lambda (spotlight-orchestrator).

| Step | What happens |
|---|---|
| **①** Dirty-gate → pool | a subtopic becomes a candidate at **≥5** new pubs (`spotlight_dirty_pubs_per_subtopic_min`); the run proceeds at **≥3** dirty subtopics (`spotlight_dirty_subtopic_min`). |
| **②** Opus lede + Haiku critic | per card, an editorial lede grounded in the subtopic's top-3 papers, refined by a Haiku critic loop. |
| **③** Clone/coverage gate | theme dedup `spotlight_theme_similarity_max` **0.7**, near-clone `spotlight_clone_overlap_min` **0.4**, scholar-coverage penalty `λ` **0.08**. |
| **④** Publish + rotate | `spotlight.json` + `SPOTLIGHT#` rows. The SPS home page **rotates 10** (`SELECTION_SIZE`) with exponential decay (`τ = 12 weeks`). |

**Worked card (all real, from the `2026-06-13` publish — 19 cards):**

- **Subtopic** `cell_intracellular_signaling_pathways` — *"Intracellular Signaling Pathways and Second Messengers"* (parent topic `cell_molecular_biology`).
- **Lede** opens: *"Cells decide what to do by passing chemical signals through tightly choreographed cascades. WCM researchers are…"*
- **Grounded in** PMIDs `38867038`, `40080571`, `39378884`; **7** supporting papers total (author names elided).

---

## 4 · Hierarchy rebuild — the stability path — *on-demand*

**Trigger** operator runs `--publish`.

| Step | What happens |
|---|---|
| **①** Re-cluster | per-topic subtopic discovery across all 66 topics. |
| **②** Reconcile | each fresh cluster is matched to a prior subtopic by **overlap → centroid → LLM**; the verdict is cached by `input_hash`. |
| **③** Match-or-mint id | a matched cluster **keeps its durable id** (e.g. `aging_cellular_senescence_molecular`); an unmatched one mints a new id. A stage whose `input_hash` is unchanged is skipped via its `STAGE#` row. |
| **④** Publish | `hierarchy.json` + `hierarchy.schema.json` + `manifest.json`. The SPS ETL reads the manifest first and **short-circuits if the sha256 is unchanged**. |

**Real (entirely, from `out/hierarchy/v2026-06-11/manifest.json` + `hierarchy.json`)** — version `v2026-06-11`, `taxonomy_v2`, **66 topics / 1,541 subtopics**, `sha256` `a825d75dbc7b10c44b00835681819d9f570498b571d3629b86ea508d589c1ead`, artifact `1,143,255` bytes, `generated_at` `2026-06-11T23:35:27Z`.

---

## 5 · Drift watchdog fires — *daily*

**Trigger** EventBridge `reciterai-drift-daily` · `cron(0 14 * * ? *)` → Lambda (drift-evaluator).

| Step | What happens |
|---|---|
| **①** Scan window | new PMIDs since the last tick + `STAGE#…#failed` rows in the window. |
| **②** Threshold check | `uncovered_rate` vs `drift_uncovered_rate_alert` **0.05** (plus stage-failure counts). |
| **③** Record written | `DRIFT#{date}` evaluation row. |
| **④** Alert | on a breach → MS Teams webhook. |

**Real** — threshold `0.05` (`config/thresholds.json`). **Representative** — the per-day `uncovered_rate` and counts (DynamoDB `DRIFT#`; not in the local artifacts).

---

### Related views

- **⑧ Key event sequences** — the diagram these examples annotate.
- **③ AWS runtime topology** — the EventBridge crons and compute targets that fire each event.
- **⑦ Stability & drift** — the durable-id / reconcile / skip machinery behind event 4.
- **④ Publish contract** — the `TOPIC#` / `FACULTY#` / `IMPACT#` / `SPOTLIGHT#` record types and the S3 channels.

> Keep these honest: when the schedule, thresholds, or artifact shape change, re-pull the values from the current artifacts and update this page alongside the diagram spec.
