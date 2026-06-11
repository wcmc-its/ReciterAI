# Subtopic membership sidecar (`membership.json`)

**Status:** shipped (#191, build-step 1) · **Consumers:** none required today — internal tooling only

## What it is

A per-run sidecar published alongside the hierarchy artifact:

```
s3://wcmc-reciterai-hierarchy/{version}/membership.json
```

(and locally at `out/hierarchy/{version}/membership.json`). It records, for every
subtopic in the published hierarchy, the discovery **seed PMIDs** that
`pipeline_hierarchy/bundler.py` deliberately strips out of `hierarchy.json`
(SPS consumers don't need per-paper membership). The sidecar exists so the data
is **persisted across cold runs** rather than discarded — which is what the
durable-ID reconcile stage and the taxonomy-jitter measurement both require.
See `docs/subtopic-lifecycle-and-evolution.md` and issue #191.

## Why a sidecar (not a field on `hierarchy.json`)

`hierarchy.json` is the SPS consumer contract; adding ~tens of PMIDs per subtopic
would bloat it ~150× and SPS does not consume it. The sidecar keeps the consumer
artifact unchanged — SPS is unaffected — while persisting membership for our own
pipeline. It is uploaded **first** in the publish sequence (before `hierarchy.json`),
so it is consumer-irrelevant to the D-11 manifest race.

## Shape

```json
{
  "version": "membership_v1",
  "membership_kind": "discovery_seed_pmids",
  "taxonomy_version": "taxonomy_v2",
  "hierarchy_version": "v2026-06-11",
  "subtopic_count": 1541,
  "subtopics": {
    "aging_age_related_lung_immunity": {
      "topic_id": "aging_geroscience",
      "seed_pmids": [32545261, 33243840, 33861685]
    }
  }
}
```

- **Keyed by `subtopic_id`**, 1:1 with the published hierarchy (a successful strict
  bundle never drops subtopics, so every published subtopic has an entry).
- `seed_pmids` are int-coerced, de-duplicated, and **sorted**; the whole artifact
  is serialized with `sort_keys=True`, so it is **byte-stable** across
  content-identical reruns (same determinism posture as `hierarchy.json`).

## Important limitation — seed, not full assignment

`membership_kind` is `discovery_seed_pmids`: this is the **discovery seed set** that
defined each cluster, **not** the full set of papers later assigned to the subtopic
(which lives in DynamoDB). For reconcile/jitter, seed-overlap is a defensible match
signal, but downstream code must read `membership_kind` and never treat seed overlap
as full-assignment overlap. Enriching to full-assignment membership (a DDB join at
publish time) is a deliberate future step, not implied here.

## Reuse

The jitter measurement and the reconcile stage can compute overlap with the existing
`cli/aging_pilot_gate.py::compute_pairwise_overlap` (min-cardinality overlap over
`pmid_sets: dict[subtopic_id -> set[pmid]]`), fed directly from two snapshots'
`membership.json` files.
