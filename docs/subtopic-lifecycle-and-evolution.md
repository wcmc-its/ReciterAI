# Subtopic lifecycle & taxonomy evolution

**Status:** Options analysis → **recommended sequence**, foundation tracked in [#191](https://github.com/wcmc-its/ReciterAI/issues/191) · SPS consumer audit DONE (Phase-1 gate 1) · *no cadence decision yet; the durable-ID foundation is recommended regardless of cadence* (2026-06-10)
**Scope:** How the topic/subtopic taxonomy — and therefore the spotlight content, topic pages, and every subtopic-keyed surface — gains new clusters, updates existing ones, and retires dead ones over the course of a year.

> **TL;DR.** *Updating* existing content is already continuous and automatic (daily + weekly lanes). *Adding* a new cluster or *retiring* a dead one happens only on the manually-run **cold path**, which re-clusters the whole taxonomy at once — a once-a-year big-bang that rotates subtopic IDs, resets spotlight rotation history, and makes a rename indistinguishable from a delete+add.
>
> The recommendation has two parts. **(1) Durable, opaque subtopic IDs + a match-or-mint reconcile stage (Option C) are worth building at _any_ cadence** — they make re-clustering *non-destructive*, which fixes the rename / deep-link / rotation-history / structural-diff problems even if new clusters still mint only once a year. **(2) A _self-sustaining_ taxonomy — one that adds, renames, and retires clusters throughout the year automatically — is then just "put the now-incremental, non-destructive cold path on a schedule,"** not a separate warm lane to build and maintain. The crux risk is **not** the reconcile algorithm (we have an in-repo precedent in tools/families) but the **migration against the live SPS consumer contract**, which must be audited and shadow-run *before* any durable ID is published.

---

## 1. The problem

The spotlight (and the broader Scholars Profile System) presents WCM research organized into ~67 topics and ~1,500 subtopics. Research at the institution is not static: new areas emerge, existing areas grow or shift, and some go quiet. We want the presented taxonomy to track that reality **throughout the year**, not just at a single annual moment.

Two operations are conflated under "keeping it current," and they behave very differently:

1. **Updating** — an *existing* subtopic accrues new papers, its impact scores move, its featured work refreshes. This is well-served today.
2. **Adding / retiring** — a *new* subtopic comes into being (or a dead one is removed). This is **not** well-served: it is coupled to a full re-cluster, which is a heavyweight, disruptive, infrequent operation.

The problem is the second case, and specifically the coupling: **there is no way to add one new cluster, or rename one, without re-minting the entire taxonomy.**

## 2. How it works today

Four lanes drive the data (`docs/hot-cold-paths.md`):

| Lane | Cadence | Trigger | Mints new subtopics? | What it does |
|---|---|---|---|---|
| **Daily enrichment** | Daily 11:00 UTC | EventBridge → Fargate | No | Synopsis + impact for 5–15 new papers/day |
| **Hot path** | Weekly, Mon 12:00 UTC | EventBridge → Step Functions | No | Scores + assigns new papers to **existing** topics/subtopics; CWID rollups |
| **Cold path** | **Manual / on-demand** | `python -m pipeline_cold.run` | **Yes** | score → assign → **discover** → relabel → rollup → spotlight backfill → hierarchy publish; mints a new `taxonomy`/`hierarchy` version |
| **Spotlight publish** | **Weekly, operator-run** | `python -m cli.backfill_spotlight --publish` | No | Regenerates ledes, rotates which cards show |

Two facts about the cold path are the heart of the problem:

- **It is the only lane that creates or removes a subtopic.** `discover_subtopics` re-derives the cluster set from scratch; the hot and daily lanes only ever attach new papers to whatever subtopics already exist. A new research area therefore "exists" in the data (its papers are scored and assigned to the *nearest existing* subtopic) but has no cluster of its own until a cold run.
- **Subtopic IDs are slug-derived and re-minted on every cold run.** A subtopic's ID is a slug of its label (e.g. `cell_cancer_genomics_molecular_oncology`). When the cold path re-clusters, labels shift and IDs rotate. The spotlight contract documents the consequence directly (`docs/spotlight-contract.md` § "Subtopic ID Stability"): *"When a hierarchy recompute lands new subtopic IDs, the existing `SPOTLIGHT_HISTORY#{old_id}` partitions become orphaned"* — and consumers are told to *"treat each publish as a full replacement; re-key on `subtopic_id` and do not carry forward state."*

## 3. Where it falls short

1. **New areas wait up to a year.** Between cold runs, an emerging field is invisible as a distinct cluster — its papers are absorbed into adjacent subtopics. It can only become spotlightable at the next re-cluster.
2. **Re-clustering is a big-bang.** A cold run re-mints the *entire* taxonomy even if only one corner of the field changed. There is no "add one cluster" or "split this one" operation.
3. **Renames read as delete + add.** Because IDs are slug-derived, relabeling a subtopic changes its ID. Downstream, a wording tweak is indistinguishable from retiring one cluster and creating another — `compute_structural_diff` (`pipeline_hierarchy/diff_stats.py`) reports it as removed + added.
4. **Rotation history is reset.** The spotlight rotation selector decays subtopics by `SPOTLIGHT_HISTORY#{id}`. When IDs rotate, that history is orphaned; the operator must `--reset-history`, so the "don't re-show what was just shown" memory is wiped at each re-cluster.
5. **Consumer deep-links break.** Any URL, bookmark, or stored reference keyed on `subtopic_id` (topic pages, saved views) is invalidated by an ID rotation.
6. **Retirement is abrupt, not graceful.** A cluster that goes quiet simply vanishes at the next re-cluster rather than fading out over time.

What is **not** broken: updating existing content. Daily enrichment + the weekly hot path keep every existing subtopic current with new papers and fresh impact scores, and each spotlight publish resurfaces the freshest work and rotates coverage — all with no operator action beyond running `--publish`.

## 4. Goals

What a good lifecycle looks like, roughly in priority order:

1. **Incremental addition.** A genuinely new research area can be minted as its own cluster within **one discovery cycle** of having enough volume — without a full re-cluster. (New-area latency is therefore set by the discovery *cadence* you choose, not by an annual big-bang — see §7.)
2. **Stable identity.** A subtopic keeps its ID across rebuilds. A relabel is a *rename*, not a delete+add. Deep-links and rotation history survive.
3. **Graceful retirement.** A cluster that goes quiet is phased out over time (e.g. demoted, then archived), not deleted in one step.
4. **Continuity of state.** Spotlight rotation history, CTR attribution, and any per-subtopic state carry forward across taxonomy evolution.
5. **Keep updates automatic.** The existing daily/weekly freshness must be preserved (this goal is already met; don't regress it).
6. **Deterministic & auditable.** Whatever mints/renames/retires clusters must be reproducible and leave an audit trail (a diff per change), not a hand-curated edit.
7. **Bounded cost & blast radius.** Evolution steps should be incremental in cost (proportional to what changed), not a full-corpus re-spend each time.

## 5. Constraints / invariants (must not break)

- **Consumer contract.** SPS and any future consumer key on `subtopic_id`. The current contract says treat each publish as a full replacement; a durable-ID model would *strengthen* this (deep-links survive) but must remain backward-compatible during transition.
- **Determinism — redefined deliberately, not weakened.** Today: bit-stable hierarchy generation from inputs alone (`pipeline_hierarchy/generator.py`). A reconcile + skip-logic model changes the function from `f(inputs)` to `f(inputs, prior published snapshot)`. We adopt this *as the new invariant*, stated so it stays testable: **the taxonomy is reproducible from the same inputs _plus_ the same prior published snapshot, and every divergence from a from-scratch rebuild is captured in the audit diff.** Reproducibility remains verifiable (re-running against identical inputs + prior snapshot yields an identical taxonomy); the audit diff is the contract that makes "what carried forward vs. what a clean rebuild would have produced" inspectable rather than silently lost.
- **Cost ceilings.** A full cold-start re-cluster is ~$210 in Bedrock (`docs/cost-model.md`); any higher-cadence scheme must not multiply that naively. Skip-logic (§6) is what keeps higher cadence affordable.
- **The existing automation.** Daily + hot lanes and their watermarks/skip-caches must keep working unchanged.
- **No speculative clusters.** Per `docs/taxonomy-methodology.md`, topics/subtopics must reflect data actually present, not be added speculatively — a mint rule needs a volume floor (and, at frequent cadence, a persistence rule; see §8).

## 6. Options

### Option A — Status quo: annual cold path
Run the full cold path once a year; rely on daily/weekly lanes for updates in between.
- **Pros:** zero new work; simplest operationally; the taxonomy is fully re-derived from current data once a year.
- **Cons:** new areas wait up to a year; every re-cluster rotates IDs, resets rotation history, breaks deep-links; renames look like churn.
- **Effort:** none. **Risk:** none new (it's today). **Mid-year freshness:** none.

### Option B — Naive higher-cadence cold runs (not a real standalone option)
"Just run the cold path more often" (e.g. quarterly) — *before* durable IDs exist.
- This is bad **only because the cold path is destructive**: it gives you 4× the ID rotation, history reset, deep-link breakage, and full re-spend. The big-bang doesn't shrink — it just recurs. **After Option C, higher cadence stops being "Option B" and becomes the cadence dial in §7 — non-destructive and incremental.** So B is not an option to choose; it is the failure mode that C exists to defuse.
- **Effort:** low. **Risk:** moderate→high (multiplies churn). **Mid-year freshness:** partial, at unacceptable cost.

### Option C — Durable opaque subtopic IDs + match-or-mint reconcile  *(the foundation — justified at any cadence)*
Decouple a subtopic's **identity** from its **label**. Assign each subtopic a stable opaque ID at first creation; on every rebuild, **reconcile** the freshly-discovered clusters against existing IDs. This is the **exact pattern already proven in this repo's tools/families taxonomy** (the "D-06 match-or-mint" rule), so it is a known quantity here, not a research bet.

Design picks:

- **ID format.** Opaque, **mint-once**, and **lowercase** — e.g. `st_` + a lowercase ULID / short-random suffix — stored in a durable id↔membership map (DynamoDB fits; we already dual-write there). **Never derived from the label.** Lowercase is a hard constraint, not a preference: SPS validates the deep-link `subtopicId` URL param with `SUBTOPIC_RE = /^[a-z0-9_]+$/` (see the Phase-1 audit in §7), so an uppercase ULID would be silently rejected by the live router.
- **Match key — deterministic-first, two-stage, with an LLM tail.** This deliberately departs from tools/families' embedding+LLM-first approach, because here we want to preserve the bit-stable determinism invariant for almost every decision:
  - *Stage 1 — paper-membership overlap* (Jaccard / containment) against the prior snapshot's memberships. Cheap, fully deterministic, auditable; resolves the large majority of clusters (most don't move much run-to-run).
  - *Stage 2 — embedding-centroid similarity*, only for the ambiguous band Stage 1 can't settle.
  - *Stage 3 — LLM arbiter* (à la tools/families D-06), only for what's *still* ambiguous, with verdicts **cached keyed on an input hash** so reruns reproduce.
- **Split / merge lineage** (the case "match or mint" glosses over): when one old cluster splits into two, the **largest-overlap successor inherits the ID and its rotation history**; the others mint new IDs with a recorded `split_from`. On a merge, the **larger ancestor's ID survives**; the absorbed ID is marked `merged_into` (which doubles as a redirect). These lineage records are what make `compute_structural_diff` genuinely meaningful — adds, renames, splits, merges, and retires become *distinct* events.
- **Why C pays off even at annual cadence.** Renames stay renames; deep-links and rotation history survive; a new cluster slots in without rotating the rest; the structural diff becomes real. It also lets us **delete the `--reset-history` operator step entirely**, which makes CTR attribution *longitudinal* — we can finally ask "how did this subtopic's engagement change after the re-cluster" instead of starting blind each year.

- **Pros:** the enabling layer for *every* freshness improvement, with a working in-repo precedent. **Cons:** a real architectural change — needs the ID store, the reconcile stage, split/merge lineage, and a one-time migration with a compatibility map. **Effort:** medium (well-understood shape: the DDB store and audit-diff machinery exist in adjacent forms; the genuinely novel work is the membership-overlap reconcile stage, the lineage rules, and the migration). **Risk:** the *algorithm* is low-risk; the **migration is the high-risk part** (it touches the live SPS consumer contract — see §7). **Mid-year freshness:** enables it; does not by itself deliver it.

### "Schedule the incremental cold path"  *(formerly Option D — not a separate lane)*
Once C makes reconcile **non-destructive**, "run discovery more often" is safe. Add **skip-logic** — a cluster that matched with high membership overlap keeps its label, synopsis, and scores unless its membership changed beyond a threshold, so **no Bedrock spend is incurred on it** — and the cold run's cost falls to roughly proportional to *drift*, not ~$210 flat. (The savings are in skipped per-cluster Bedrock labeling/synopsis, which is where the cost is; the clustering compute itself still runs over the full corpus each time — see the Phase-1 jitter check in §7.) Then put it on a cron.

This is **one pipeline, one code path, one audit trail** — *not* a parallel warm lane to build and maintain. Only build a genuinely separate discovery-only lane if profiling later shows the incremental cold run is still too heavy (unlikely, given how slowly a ~1,500-subtopic taxonomy drifts quarter to quarter).

## 7. Recommended sequence — toward a self-sustaining taxonomy

A **self-sustaining** taxonomy adds, renames, and retires clusters on a schedule, automatically, with durable IDs, bounded cost, and an audit trail — **no annual operator big-bang and no manual `--reset-history`.** The path has three phases. The discipline that matters: **Phase 1 has entry gates that de-risk the migration _before_ any durable ID is published.**

### Phase 0 — Decide cadence and mint-floor *together* (don't let it gate C)
- Open decision #1 (annual vs. mid-year) is closely related to an already-tracked decision: **#37's "annual rescore rule" residual gate.** They are *distinct jobs* — #37's gate governs the annual **enrichment rescore** (synopsis/impact model-unification, `pipeline_enrichment`), whereas this is the taxonomy **re-cluster** cadence (`pipeline_cold` discovery) — but both are "how often do we run the heavy annual pass" decisions with the same owner. **Co-decide them** (one coordinated annual heavy pass, not two uncoordinated ones) rather than opening a parallel thread.
- **Cadence and the mint floor must be decided in the same breath.** A two-consecutive-runs persistence rule (§8) implies ~6 months to surface a new area at quarterly cadence and ~2 months at monthly — and at annual cadence "two runs" is *two years*, which is incoherent. So: annual cadence ⇒ single-run volume floor; frequent cadence ⇒ two-run persistence. Pick the point on the **responsiveness ↔ stability** dial explicitly. **Default recommendation: monthly discovery + two-run persistence** — keeps the floor meaningful while getting new areas visible inside a quarter.
- **C is justified whichever way #1 lands.** Frame it to stakeholders as "make re-clustering safe," with mid-year freshness as the option it unlocks — not its justification.

### Phase 1 — Durable IDs + reconcile (Option C)
**Entry gates — must pass _before_ building, because the crux risk is the live consumer contract:**
1. **SPS consumer audit — ✅ DONE (2026-06-10, `origin/master` @ `df12ae1`).** Enumerated on SPS's *confirmed* default branch (`master`, not `main` — the wrong-branch trap that sank the tools/families supersede). Findings:
   - **Consumer surface:** the `Subtopic` table (PK `id` **is** the slug-derived subtopic_id; sole writer `etl/hierarchy/index.ts` ← `s3://wcmc-reciterai-hierarchy/latest/manifest.json`); the `Spotlight` table (sole writer `etl/spotlight/index.ts` ← `s3://wcmc-reciterai-artifacts/spotlight/latest/manifest.json`); `PublicationTopic.primarySubtopicId`/`subtopicIds` (embedded, **no FK** by design); the deep-link route `app/api/topics/[slug]/subtopics/[subtopicId]/scholars`; and the `lib/api/*` + `subtopic-*` component layers downstream. `SPOTLIGHT_HISTORY` lives only in SPS *docs*, not code — SPS does not consume rotation history (that coupling is ours).
   - **De-risk:** SPS **already anticipates ID instability** (its D-06 contract says so) and built with no FKs + full-replacement ETL — durable IDs are a *strengthening it would welcome*, not a break. SPS **already has the alias/redirect mechanism** (`SlugHistory` → 301 redirects, `feat/497-slug-registry`) — reuse that pattern for the subtopic alias map, don't invent one.
   - **Constraints surfaced:** (a) **lowercase IDs only** — the route regex `^[a-z0-9_]+$` rejects uppercase ULIDs (folded into §6 ID-format pick); (b) **two S3 integration points** (hierarchy + spotlight ETLs), so the migration is a coordinated two-artifact schema bump, not one; (c) **count reconciliation** — this repo says ~1,500 subtopics, SPS's Hierarchy ETL says "~2,010 rows" (reconcile before migrating — likely legacy DDB Block-2 residue).
   - **Net:** the migration is a *coordinated contract upgrade SPS is already shaped for* — the residual risk is coordination, not breakage. Full detail in #191.
2. **Two-snapshot jitter measurement — ⏸→▶ forward-unblocked (data capture shipped in [#192](https://github.com/wcmc-its/ReciterAI/pull/192); runs once two membership-bearing snapshots exist).** Intended: cluster two corpus snapshots a quarter apart and count **how many clusters fall below the auto-match overlap threshold** — *boundary jitter, not stage separability* — to size whether "incremental" is real and how big the ambiguous-tail LLM budget must be. **Why it couldn't run retrospectively (attempted 2026-06-10, see [#191](https://github.com/wcmc-its/ReciterAI/issues/191) comment):** per-subtopic membership (`seed_pmids`) was **stripped at bundle time**, so no *published* snapshot carried PMIDs; and the published taxonomy is only ~1 month old (`v2026-05-07/12/13`, all `taxonomy_v2` same-corpus republishes — nothing a quarter apart). Overlap needs PMIDs on **both** sides; we had them on neither historical side. **Fix shipped (build-step 1 below):** each cold run now publishes a `membership.json` sidecar (`docs/subtopic-membership-artifact.md`). The measurement runs as soon as two runs (a quarter apart, or scaled) each carry one, via the existing overlap code (`cli/aging_pilot_gate.py::compute_pairwise_overlap`). (Interim structural-churn signal — not jitter — from the existing same-era republishes: `v05-07→v05-12` changed 636/1,526 display_names with 0 ID adds/removes; `v05-12→v05-13` added 15 `implementation_*` subtopics with 0 removes/renames. True ID rotation is correct-by-construction but **not yet observed** — there has been no from-scratch re-cluster in published history.)
3. **Shadow-mode reconcile.** Run match-or-mint against the next cold run's output **without publishing** — diff only. This proves the durable-ID model end-to-end before any consumer sees a single durable ID.

**Then build:** **(step 1) persist per-subtopic membership across runs** — the `seed_pmids` discovery produces but bundling discarded. ✅ **First increment shipped: a per-run `membership.json` sidecar** co-located with each published hierarchy version (`docs/subtopic-membership-artifact.md`), byte-stable and consumer-invisible (SPS's `hierarchy.json` is unchanged). This is the prerequisite for everything below *and* what unblocks gate 2 — once two runs a quarter apart each carry a `membership.json`, the jitter measurement runs directly off them. (It captures discovery *seed* membership, stamped `membership_kind`; promoting to a durable id↔membership store and/or full-assignment membership is the next increment.) Then the reconcile stage (deterministic-first match key); split/merge lineage; and the one-time migration — a 1:1 backfill of current slugs to durable IDs, re-keying `SPOTLIGHT_HISTORY` partitions **in the same migration**, plus a published slug→durable **alias/redirect map** (reuse SPS's existing `SlugHistory` → 301-redirect pattern rather than building a new one) so deep-links resolve through a redirect during a deprecation window. Coordinate **both** S3 integration points the audit found — the hierarchy ETL and the spotlight ETL — in one schema-version bump. Done right, the *first* reconciled cold run is a **non-event for consumers** — that is the proof the model works.

### Phase 2 — Make the cold path incremental, then schedule it
Add skip-logic so matched clusters incur no Bedrock spend; put discovery on the cadence chosen in Phase 0; activate the mint/retire policy (§8). **This is the part that actually produces new groups throughout the year.** Be honest about the lever: *C alone, at annual cadence, still mints only once a year — it just mints safely.* Mid-year new-group creation is delivered by the **schedule**, with C as its prerequisite. If mid-year freshness is a firm requirement, **Phase 2 is not optional** — it is the requirement-meeting piece.

### What makes it self-sustaining (and which manual steps disappear)
- **Delete the `--reset-history` operator step** entirely once IDs are durable.
- **The audit diff is the monitoring surface** — adds / renames / splits / merges / retires per run, reviewable without re-deriving anything.
- **Demotion is auto-reversible** — a single new paper assignment un-demotes a quiet cluster — so retirement needs no manual rescue path.

## 8. Mint & retire policy

- **Mint:** a **volume floor** (N papers, M distinct CWIDs) — *plus persistence at frequent cadence*: a candidate cluster must clear the floor on **two consecutive scheduled discovery runs** before it is minted and spotlightable. The two-run rule is what operationalizes the no-speculative-clusters constraint against clustering noise; it applies **only** once cadence is frequent (see Phase 0 — at annual cadence, use a single-run floor instead).
- **Retire (two stages, reversible):**
  - *Demoted* — no new assignments for ~12 months → excluded from spotlight rotation and browse prominence, but the page and ID **still resolve**.
  - *Archived* — another quiet cycle → page **redirects to its parent topic**, ID retained forever as a redirect.
  - Demotion is trivially reversible (a new paper assignment un-demotes it), which answers the reversibility question for free.

## 9. Comparison

| | A: annual | B: naive quarterly | C: durable IDs | C + scheduled incremental cold path |
|---|---|---|---|---|
| New areas surface in | ≤ 1 year | ≤ 1 quarter (high cost) | (enabler) | one discovery cycle (cadence-set) |
| Stable IDs / deep-links | ✗ | ✗ | ✓ | ✓ |
| Rotation history survives | ✗ | ✗ | ✓ | ✓ |
| Renames ≠ delete+add | ✗ | ✗ | ✓ | ✓ |
| Graceful retirement | ✗ | ✗ | partial | ✓ |
| Incremental cost | n/a | ✗ (full re-spend ×4) | — | ✓ (∝ drift, via skip-logic) |
| Effort | none | low | medium | medium (C + skip-logic + cron) |
| Has in-repo precedent | n/a | n/a | ✓ (tools/families) | ✓ |

## 10. Open decisions

1. **Cadence** (annual vs. mid-year) — **co-decide with the #37 "annual rescore rule" gate** (a distinct job — enrichment rescore vs. taxonomy re-cluster — but the same "annual heavy-pass cadence" owner), and decide *jointly* with the mint floor (Phase 0). Default recommendation: monthly + two-run persistence.
2. **Match key** — *decided:* deterministic-first (membership overlap → embedding centroid → LLM arbiter for the tail, verdicts cached by input hash). *Remaining:* pick the overlap thresholds, sized by the Phase-1 jitter measurement.
3. **Split/merge lineage** — confirm the inherit-by-largest-overlap rule and the `split_from` / `merged_into` record shape (history follows the inheriting ID). *(New — previously unaddressed by "match or mint.")*
4. **Mint floor values** (N papers, M distinct CWIDs) — set jointly with cadence (#1).
5. **Retire timings** — confirm ~12-month quiet for demote, next-cycle for archive; reversibility via new-assignment un-demote.
6. **Migration** — alias/redirect map shape + deprecation-window length; **gated by the Phase-1 SPS consumer audit.**

## 11. References

- `docs/hot-cold-paths.md` — the daily/hot/cold lane definitions and cadences.
- `docs/spotlight-contract.md` § "Subtopic ID Stability" — the authoritative statement that re-cluster rotates IDs + orphans rotation history.
- `docs/taxonomy-methodology.md` — how topics/subtopics are derived; the no-speculative-clusters rule.
- `pipeline_cold/run.py` — the cold-path pipeline (score → assign → discover → relabel → rollup → spotlight → publish; mints versions).
- `pipeline_hierarchy/diff_stats.py::compute_structural_diff` — the existing add/remove/rename tracker (made genuinely meaningful by lineage records).
- Tools/families taxonomy (the in-repo durable-ID precedent): `docs/tools-a2-architecture.md` and the D-06 match-or-mint rule. **Lesson carried into Phase 1:** the A2 DDB supersede was reverted because a live SPS consumer was missed on the wrong branch — hence the consumer-audit gate.
- `docs/cost-model.md` — cold-start (~$210) and per-publish (~$3) cost anchors.
- Issue **#191** — the durable-subtopic-ID foundation (Option C), with the executed SPS consumer audit and Phase-1 scope/gates.
- Issue **#37** — the "annual rescore rule" residual gate (annual *enrichment* rescore); the taxonomy re-cluster cadence (#1 above) is a distinct but adjacent annual-pass decision to co-decide with it.
- SPS (audited @ `origin/master` `df12ae1`): `prisma/schema.prisma` (`Subtopic`, `SlugHistory`), `etl/hierarchy/index.ts`, `etl/spotlight/index.ts`, `app/api/topics/[slug]/subtopics/[subtopicId]/scholars/route.ts`.
