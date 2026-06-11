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
  "status": "active",                               // brick C owns transitions
  "label_at_mint": "Cellular Senescence",           // mint-once: never rewritten
  "created_at": "2026-06-11T12:22:19Z",             // mint-once
  "first_run_id": "<uuid>"                          // mint-once: the run that minted
}
```

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

`SubtopicIdStore.match(*, slug, membership, topic_id)` is a deliberate placeholder —
**exact slug equality and nothing else**. Brick B replaces only the *body* of
`match()` with the deterministic-first reconcile (membership-overlap →
embedding-centroid → LLM arbiter, verdicts cached by input hash); `membership` and
`topic_id` are already parameters so no signature change is needed, and a third
"flagged/defer" return state can be added without touching `mint`/`attach`. This
mirrors the proven `pipeline_tools.registry.ToolRegistry.match_or_mint` seam.

The match is intentionally **strict**: a missed match (over-mint) is recoverable by a
later brick; a false match silently corrupts membership and is not. Over-minting is
the designed-safe failure.

## Not in scope (later bricks)

- **B** — the real reconcile/match key (membership overlap → centroid → LLM tail).
- **C** — split/merge lineage (`status` transitions, `split_from` / `merged_into`).
- **D** — migration + slug→durable alias map that repoints SPS's hierarchy and
  spotlight ETLs (the `SUBTOPIC_SLUG#` pointer rows are the seam).
- **E** — skip-logic (matched clusters incur no Bedrock spend).
- **F** — schedule (EventBridge cron) + mint/retire policy.

## Reuse / determinism

Records are byte-stable under `json.dumps(..., sort_keys=True, ensure_ascii=False)`
for fixed inputs (the membership-sidecar posture); `seed_pmids` are int-coerced,
de-duped, and sorted. `pipeline_hierarchy.__version__` is **not** bumped — brick A
changes no produced byte, and the version feeds the publish skip-cache key, so a bump
would needlessly force a re-publish.
