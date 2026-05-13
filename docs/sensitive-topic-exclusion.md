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

## Criteria for inclusion

A pattern belongs on the list when at least one of the following holds. The categories are deliberately *broad* — operators should err toward inclusion when in doubt, because the cost of a false negative (a sensitive lede gets published) is institutional reputation, while the cost of a false positive (a benign subtopic gets routed to manual review) is editorial overhead.

1. **Active litigation, regulatory enforcement, or congressional inquiry.** Topics where the institution faces or could plausibly face legal exposure if a faculty member's research is foregrounded in marketing language.
2. **Federally regulated or restricted research areas.** Areas with current or recently-shifting federal funding restrictions (e.g. fetal tissue research, some categories of stem cell work, dual-use biology) where institutional positioning is sensitive.
3. **Politically contested biomedical topics.** Areas where the underlying science is sound but public framing is highly polarized — abortion access, gender-affirming care, vaccine policy, gun violence as public health, climate-and-health.
4. **Dean-flagged or communications-flagged.** Anything the Dean's office or Communications has explicitly asked be kept out of public-facing summaries, regardless of category.
5. **Patient-identifiable or PII risk.** Subtopics whose label, description, or parent-topic context could surface identifiable patient data when paired with a spotlighted faculty member.

Categories that do **NOT** belong on the list:

- Voice / tone concerns (handled by `spotlight/critic.py`).
- "Low-quality subtopic" concerns (handled by hierarchy gates pre-publish).
- General research-fit questions (handled by editorial review of the `SPOTLIGHT_REVIEW#` queue).

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

## Inspecting the current list

The active list is not committed anywhere in this repo. To read it:

```bash
aws dynamodb get-item \
  --table-name reciterai \
  --region us-east-1 \
  --key '{"PK":{"S":"SPOTLIGHT_CONFIG#sensitive_tags"},"SK":{"S":"CONFIG"}}'
```

Each entry in the returned `tags` list carries a `reason` field — that's the per-entry rationale and is the source of truth for why a pattern is on the list. The schema for these entries is documented in `docs/spotlight-dynamodb-schema.md` under `SPOTLIGHT_CONFIG#sensitive_tags`.

For *historical context* on what the v1 list looked like at first launch (categories, not full per-pattern entries), see the "First-pass tag list" subsection in `docs/spotlight-dynamodb-schema.md`. Those are operator notes, not authoritative — the live row supersedes them.

## Updating the patterns

There is intentionally no source-tree path for adding patterns — the design point is keeping them off the repo. Operators update the DDB row directly.

### Process

1. **Proposing a change.** Anyone with editorial or compliance context may propose adding, removing, or modifying a pattern. There is currently no formal proposal form — the lightweight path is filing a GitHub issue against this repo (labelled `compliance-adjacent`) or emailing the editorial owner.
2. **Approval.** Today the gate is a one-person-judgment system: the operator who owns the ReciterAI pipeline (currently Paul Albert) approves and applies changes after consulting with Communications and/or the Dean's office for categories 1, 2, 3, and 4 above. Category 5 changes can be applied unilaterally as a safety measure. A more formal review board is out of scope until volume warrants it (see "Future work").
3. **Applying the change.** Use `aws dynamodb update-item` or `put-item` to mutate the `tags` attribute. The row carries two audit fields per the schema doc:
   - `last_updated_at` (ISO 8601 UTC) — REQUIRED on every update
   - `last_updated_by` (operator login) — optional but should be filled when known
4. **Audit trail.** The DynamoDB row's `last_updated_at` + `last_updated_by` is the in-band audit trail. Out-of-band, the operator should leave a one-line note in the GitHub issue (if one was filed) summarizing what changed and why, so the rationale survives outside the row's history.

### Failure mode

The `--publish` stage fails fast with a descriptive error if the config row is missing or unreadable (SPOT-08 fail-closed invariant). An operator who forgets to seed or accidentally deletes the row discovers the gap before any content is published, not after.

### Future work (out of scope for this doc)

- Move `last_updated_by` from optional to required, and validate it server-side via IAM (only specific principals may write the row).
- Introduce a formal compliance review board for additions in categories 1–4.
- Add a `proposed_by` / `approved_by` pair so the row carries its own approval lineage.

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
