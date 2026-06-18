# Tool-usage context sidecar (`tool_context.json`)

**Status:** shipped (#193, producer-side enabler) · **Consumer:** the SPS AI overview generator (downstream, separate)

## What it is

A per-publish sidecar published alongside the A2 tools artifact:

```
s3://wcmc-reciterai-artifacts/tools/tool_context.json
s3://wcmc-reciterai-artifacts/tools/latest/tool_context.json
```

(and locally at `out/.../tools.json`'s review bundle). For every canonical tool
that has one, it records the per-publication **usage snippet** — *how* a tool was
used in a given paper ("for stochastic simulation of photon transport"). Each
snippet is the ≤300-char grounding `context` the extractor already emits per
mention — a complete sentence quoted from the abstract, sentence-aligned at
extraction time so it reads standalone (#238); the sidecar restores the
`(tool, pmid) → snippet` link that the flat
`context_evidence` list on `tools.json` had dropped. See
`docs/tools-a2-architecture.md §3.7` and issue #193.

## Why a sidecar (not a field on `tools.json`)

`tools.json` is the SPS-facing bundle that the Methods lens loads in the browser.
A `{pmid: snippet}` map over ~18k tools would grow it materially for a payload the
lens never reads. The sidecar keeps the bundle **byte-identical** (the
`_enriched_records` field allowlist already excludes the per-pub map, and
`publish._split_artifacts` splits `tool_context` out of the bundle body), so only
the overview generator pays for the context. Same posture as the hierarchy
`membership.json` sidecar (#192) and the split `families.json` / `faculty.json`.

## How an overview generator uses it

`faculty.json` already carries, per scholar, each tool row with that scholar's
distinct `pmids` (#175). The join is therefore already keyed:

```
faculty.json:      cwid → tool(canonical_tool_id) → [pmids]
tool_context.json: canonical_tool_id → {pmid → snippet}
⇒  the scholar's own usage snippets, no per-scholar duplication
```

The same key (`canonical_tool_id`) also keys the `tools.json` tool records, so a
tool or family page can surface usage context without going through the rollup.

**Selection is deferred to generation time.** The producer does *not* gate snippets
by salience tier or extraction quality flags — those optimize taxonomy cleanliness,
not narrative value, and the relationship is often inverted (a tool flagged
`insufficient_specificity` can carry exactly the application-specific context an
overview wants). The overview LLM picks concrete-over-generic snippets, grounded
strictly in the provided text.

## Shape

```json
{
  "schema_version": "tools-a2-v2",
  "provenance": { "...": "same block as tools.json" },
  "tool_context_kind": "tool_usage_snippet",
  "tool_context": {
    "tool_000123": {
      "39000001": "for stochastic simulation of photon transport",
      "39000777": "to re-sensitize INH-resistant Mtb"
    }
  }
}
```

- **Keyed by `canonical_tool_id`**, then by **publication pmid**. Only tools that
  have at least one usage snippet appear (no empty entries).
- pmids are **publication** pmids (grants are excluded — they never join the
  publication-keyed faculty rollup). The key set is a subset of the tool's `pub_ids`.
- On collision (the same pmid seen via two surface forms of one tool, e.g.
  MRI ↔ magnetic resonance imaging) the **longer snippet wins** — the registry
  unions `context_by_pub` the same way it unions `pub_ids`.
- Serialized with `sort_keys=True` (recursively sorts tool ids and inner pmids),
  so it is **byte-stable** across content-identical reruns and integrity-checkable
  via `tools/latest/manifest.json` (`objects["tool_context.json"]` + `counts.tool_context`).

## Provenance / freshness

`tool_context_kind = tool_usage_snippet` marks what the values are (per-publication
usage context), so a consumer never confuses them with a definitional gloss. The
snippets come from the **current A2 producer** (Sonnet/Haiku extraction); the
legacy `reciterai_tools` MariaDB table (static POC-era `v6_production`, o3 +
gpt-4o-mini) is **not** a source and must not be relied on.
