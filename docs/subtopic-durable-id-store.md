# Durable subtopic-id store (`SUBTOPIC_ID#` / `SUBTOPIC_SLUG#`)

**Status:** shipped (#191, brick A) · **Consumers:** none required today — internal tooling only

## What it is

A durable, opaque, **mint-once** identity for every subtopic, decoupled from its
label, persisted on the shared `reciterai` DynamoDB table. It is the load-bearing
foundation of the durable-ID plan (Option C in
`docs/subtopic-lifecycle-and-evolution.md` §6–§7): once a subtopic's id is durable,
a re-cluster stops being destructive — a relabel is a *rename* (not delete+add),
deep-links and spotlight rotation history survive, and the structural diff becomes
meaningful.

Today a subtopic's id is a slug of its label (e.g.
`cell_cancer_genomics_molecular_oncology`), re-minted on every cold run. This store
promotes the per-run `membership.json` sidecar (#192) into durable records keyed by
an opaque id, so the slug can change while the identity does not.

## Why a separate store (not a field on `hierarchy.json`)

`hierarchy.json` is the SPS consumer contract. Brick A is **internal-only**: the
published hierarchy keeps slug ids, and durable ids live solely in this store until
the brick-D migration repoints consumers (reusing SPS's existing `SlugHistory`
redirect, per the §7 audit). Nothing here is read by SPS today.

## Where it is written

`pipeline_hierarchy.publish.main` **step 10**, on a real (non-`--dry-run`) publish,
**after** `hierarchy.json` / `manifest.json` are serialized and uploaded and the
`STAGE#` complete row is written. It is therefore structurally incapable of altering
any consumer-facing byte, and it is **best-effort**: by the time it runs the artifact
is already live, so a store-write failure is logged and the publish still returns OK.
Under `--dry-run` it is unreachable (no AWS, no store I/O).

## Shape

Two item types on the single `reciterai` table (composite `PK`/`SK`; **no GSI** —
the pointer row makes both lookups an O(1) `GetItem`):

**Primary row — one per durable subtopic**

```
PK: SUBTOPIC_ID#st_8pa67uyd1ib2mwwfgear8ctqvd     SK: META
{
  "record_type": "SUBTOPIC_ID",
  "durable_id": "st_8pa67uyd1ib2mwwfgear8ctqvd",
  "slug_id": "aging_cellular_senescence",          // mutable: current slug
  "topic_id": "aging_geroscience",                  // mutable
  "seed_pmids": [32545261, 33243840],               // mutable: ints, sorted/de-duped
  "membership_kind": "discovery_seed_pmids",        // seed set, NOT full assignment
  "taxonomy_version": "taxonomy_v2",                // mutable
  "hierarchy_version": "v2026-06-11",               // mutable
  "last_seen_run_id": "<uuid>",                     // mutable: refreshed each reconcile
  "status": "active",                               // brick F owns transitions (untouched by C)
  "split_from": "st_<parent>",                      // brick C: present only on a minted split child
  "merged_into": "st_<successor>",                  // brick C: present only on a merge-absorbed prior (a redirect)
  "label_at_mint": "Cellular Senescence",           // mint-once: never rewritten
  "created_at": "2026-06-11T12:22:19Z",             // mint-once
  "first_run_id": "<uuid>"                          // mint-once: the run that minted
}
```

`split_from` / `merged_into` are **additive and conditional** — written only on the rows
a split or merge touched, absent on every other row (consumers `.get()`). They are written
post-reconcile by `set_lineage` (not by the record builder), so the additive
`{**existing, **refreshed}` attach merge preserves them across later reconciles.

**Slug pointer row — the slug → durable lookup**

```
PK: SUBTOPIC_SLUG#aging_cellular_senescence         SK: PTR
{ "record_type": "SUBTOPIC_SLUG", "slug_id": "...", "durable_id": "st_...", "created_at": "..." }
```

## The id format

`st_` + a 26-char `[a-z0-9]` suffix. Opaque, mint-once, **lowercase**, and *never*
derived from the label/slug/membership. Lowercase is a hard constraint: SPS validates
the deep-link `subtopicId` URL param with `^[a-z0-9_]+$`, so an uppercase ULID is
silently rejected by the live router. Every minted id is asserted against that route
regex at the source (`pipeline_hierarchy/subtopic_ids.py`).

The minter's entropy source is injectable — production draws from `secrets`
(non-reproducible by design); tests pass a seeded RNG so a minted id is a
deterministic function of the seed. The minted id is not regenerable: **the store is
the source of truth**, which is why a collision guard (redraw on clash) is included
even though it effectively never triggers at this keyspace.

## Match-or-mint (and the seam for brick B)

On each publish, `reconcile_durable_ids` runs **exact-slug** match-or-mint per
subtopic (iterating `membership.json`, 1:1 with the published hierarchy):

- **known slug → `attached`**: the durable id is reused; mutable fields are refreshed
  and the mint-once provenance (`durable_id`, `created_at`, `first_run_id`,
  `label_at_mint`) is preserved.
- **unknown slug → `minted`**: a fresh opaque id + slug pointer are written.

## The match — brick B's deterministic-first reconcile

`SubtopicIdStore.match(*, slug, membership, topic_id)` was an exact-slug placeholder
in brick A; **brick B** (`pipeline_hierarchy/subtopic_reconcile.py`) replaces it with a
`SubtopicReconciler` that matches each new cluster against the **prior published
snapshot** (`load_id_store_snapshot`, a one-time scan of the `SUBTOPIC_ID#` rows) in
three deterministic-first stages, **scoped to the same topic**:

1. **Membership overlap** — min-cardinality (Szymkiewicz–Simpson) seed-PMID overlap
   (`pipeline_hierarchy/overlap.py`, shared with the D-23 dedup gate). `≥ auto_match_min`
   auto-attaches (`reason="overlap"`); the `[ambiguous_min, auto_match_min)` band
   escalates; below `ambiguous_min` mints.
2. **Embedding centroid** — Titan-v2 label cosine (`pipeline_tools/embeddings`) over the
   ambiguous band; `≥ centroid_cosine_min` attaches (`reason="centroid"`, recording the
   realized cosine).
3. **LLM arbiter** — Bedrock Sonnet → OpenAI fallback (`call_with_fallback`), only the
   residual tail, with **verdicts cached on a stable content hash** (labels + sorted
   seed_pmids + topic + prompt/model version — never an id, slug, run_id, or mint order).
   A `distinct`/unresolved verdict → `SubtopicDefer` → mint.

The whole assignment is resolved in **one deterministic `precompute()` pass** that is
**conflict-free** (no prior id is attached by two new clusters: the highest-overlap
claimant wins, the rest re-match against the unclaimed remainder or mint) and free of
the intra-run self-match hazard (it reads a pre-run snapshot, never same-run mints).
`match()` is then a pure lookup. Thresholds are config-driven
(`config/thresholds.json` → `subtopic_reconcile_*`, **provisional pending the
forward-only jitter measurement**); `subtopic_reconcile_llm_arbiter_enabled=false`
makes the whole reconcile deterministic + offline (useful for a shadow run).

The match stays **strict**: a missed match (over-mint) is recoverable by a later
brick; a false match silently corrupts identity and is not. Over-minting is the
designed-safe failure — so the arbiter is biased to keep clusters separate when unsure.

**Brick C — split/merge lineage (implemented).** Brick B's conflict resolution computes
the split/merge primitives and exposes them on the reconciler so brick C *reads* B's
actual decisions rather than replaying the greedy order: `reconciler.split_parents` (a
minted cluster → the prior it overlapped in-band but lost to a stronger sibling) and
`reconciler.unclaimed_priors` (priors no cluster claimed — merge-absorbed / quiet). After
the mint loop, `reconcile_durable_ids` turns these into additive edges via `set_lineage`:

- **Split** — each minting loser gets `split_from` = the contested prior it lost to. The
  inheritor (the stronger sibling that *attached* to that prior) keeps the id and history
  and carries no edge — it *is* the continuation. A `split_from` whose parent has no live
  row (the re-mint dangling path) is skipped, not written broken.
- **Merge** — an unclaimed prior that a successor *absorbed* (directional containment
  `|prior ∩ successor| / |prior|` ≥ `subtopic_reconcile_merge_overlap_min`, default
  `auto_match_min`) gets `merged_into` = the successor's durable id, doubling as a
  redirect. Priors below the bar are left untouched for **brick F** (demote/retire owns
  `status`). Split parents are excluded from the merge set (they have a live split-edge
  continuation).

The whole pass is best-effort and unreachable under `--dry-run` (it lives inside the
post-publish step-10 reconcile helper). It changes **no consumer-facing byte**: edges
live only in the `SUBTOPIC_ID#` store.

`compute_structural_diff` (`pipeline_hierarchy/diff_stats.py`) is extended to *accept*
this lineage (slug-space `{"split": [...], "merged": [...]}`) and emit additive
`split_subtopics` / `merged_subtopics` events, so a re-cluster reports splits and merges
as distinct events instead of add/remove churn. It is **ready but not wired**: the
published `diff.json` still calls it without lineage (byte-identical 4-key shape, no
`diff_schema_version` bump). Resolving the store-derived lineage at diff time and
surfacing the new keys is **brick D** — that schema change is a consumer-coordination
step (SPS handling + version bump), batched with the brick-D migration below.

## Not in scope (later bricks)

- **D** — migration + slug→durable alias map that repoints SPS's hierarchy and
  spotlight ETLs (the `SUBTOPIC_SLUG#` pointer rows are the seam); also wires the
  brick-C lineage into the published `diff.json` (with the `diff_schema_version` bump).
- **E** — skip-logic (matched clusters incur no Bedrock spend).
- **F** — schedule (EventBridge cron) + mint/retire policy.

## Reuse / determinism

Records are byte-stable under `json.dumps(..., sort_keys=True, ensure_ascii=False)`
for fixed inputs (the membership-sidecar posture); `seed_pmids` are int-coerced,
de-duped, and sorted. Stage 1 is fully deterministic; Stage 2 is bit-stable once
embeddings are cached by text; Stage 3 verdicts are cached by content hash so reruns
reproduce. `pipeline_hierarchy.__version__` is **not** bumped by bricks A, B, *or* C —
none changes a produced byte, and the version feeds the publish skip-cache key (the
brick-A skip-path reconcile already runs the new matcher on a content-identical
republish, so a bump would only force a wasteful re-upload). Brick C's lineage edges are
internal store rows and its `diff.json` extension stays unwired, so `diff_schema_version`
is unchanged too — both bumps belong to brick D when the new diff keys actually ship.
