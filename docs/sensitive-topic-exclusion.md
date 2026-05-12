# Sensitive-Topic Exclusion

**Status:** Active. Implemented in [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py); patterns sourced from DynamoDB at runtime.

## What this is

A fail-closed gate that prevents the spotlight pipeline from publishing
lede content matching sensitive-topic patterns (drugs, controversial
biomedical claims, etc.). Patterns are not part of the published
hierarchy or any S3 artifact; they exist only in DynamoDB and
are read by the gate at runtime.

The gate operates on subtopic metadata — label, description, and parent
topic label — not on lede text. Checking the lede directly would be
redundant: the lede generator is constrained to anchor content in
synopses, and topic-level exclusion is where topical sensitivity is
determined, not at the prose layer.

## Where patterns live

DynamoDB row: `PK = SPOTLIGHT_CONFIG#sensitive_tags`, `SK = CONFIG`.

Each row carries a `tags` attribute — a DynamoDB List (`L`) of Maps
(`M`), one map per pattern. Each map has the following fields:

| Field | Type | Meaning |
|---|---|---|
| `pattern` | String | The substring pattern to match against |
| `match_type` | String | Matching strategy; v1 implements `substring` only |
| `reason` | String | Human-readable rationale for this exclusion |

Match strategy (v1): case-insensitive substring against the concatenation
`subtopic.label + " " + subtopic.description + " " + parent_topic_label`
(see [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py)
`is_sensitive()` for the exact implementation).

Tags with an unrecognized `match_type` are treated as `substring` with a
logged warning — the design point is fail-safe at the match layer, not
fail-open on forward-compat rows.

Why DynamoDB rather than source:

- Patterns evolve faster than the release cadence; operators update
  the row without a code deploy.
- Patterns are partially confidential (the exact list is non-public).
  Source-tree storage would commit them to git history. DDB-resident
  storage keeps them off disk in the repo.
- The gate already reads from DDB for other config; adding patterns
  to the same substrate matches the existing operational footprint.

Note: the module logs the *count* of loaded patterns, never the pattern
values, to avoid leaking operationally sensitive strings into log
aggregators (see `T-06-04-04` in the Phase 6 threat model).

## SPOT-08: Fail-closed invariant

If the gate cannot retrieve the patterns row (DDB error, missing row,
permission denied, throttling), it MUST refuse to publish — never
let a lede through on the assumption that the absence of patterns
means nothing to exclude. The default is restriction, not permission.

The invariant is enforced in [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py)
`load_sensitive_tags()`, which raises `RuntimeError` on two conditions:

1. `ClientError` from boto3 `GetItem` — the DDB call itself failed.
2. `"Item" not in resp` — the config record is missing (not yet seeded).

Both conditions produce the same outcome: a `RuntimeError` that surfaces
to the caller, which must abort rather than continue without the patterns.
Silent fail-open (returning `[]`) is explicitly forbidden in the
`load_sensitive_tags()` docstring.

The invariant is named SPOT-08 in the module docstring of
[`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py).
Verify (don't trust prose) by reading the source — and if the source
diverges from this doc, update both.

## What this gate is NOT

- Not a general-purpose content filter. The patterns are biomedical
  and Weill-Cornell-specific.
- Not a substitute for human editorial review. The
  `SPOTLIGHT_REVIEW#` queue (implemented in
  `spotlight/review_queue.py`) handles every persistently rejected
  lede; the sensitive gate is one of several routes a lede can take
  to that queue.
- Not a place to encode the project's editorial voice. The critic
  gates (`spotlight/critic.py`) handle voice; the sensitive gate
  handles topical exclusion.
- Not a lede-text filter. Match targets are subtopic metadata
  (label + description + parent topic label), not the generated prose.

## Updating the patterns

Operators update the DDB row directly (typically via the DDB console
or a privileged CLI using `aws dynamodb put-item`). The seeding
instructions and the exact DynamoDB item shape are documented in
`docs/spotlight-dynamodb-schema.md` under `SPOTLIGHT_CONFIG#sensitive_tags`.

There is intentionally no source-tree path for adding patterns — the
design point is keeping them off the repo. The `--publish` stage will
fail with a descriptive error if the config row is missing, so an
operator who forgets to seed it discovers the gap before any content
is published, not after.

## Related

- [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py) —
  implementation; `load_sensitive_tags()` for the DDB fetch,
  `is_sensitive()` for the matching logic
- `spotlight/review_queue.py` — the `SPOTLIGHT_REVIEW#` writer that
  the lede generator calls when `is_sensitive()` returns `True`
- `docs/spotlight-dynamodb-schema.md` — DDB schema including the
  `SPOTLIGHT_CONFIG#sensitive_tags` partition shape and operator
  seeding instructions
- `docs/RECITERAI-SPEC.md` §6 / §11 (G-24 definition)
