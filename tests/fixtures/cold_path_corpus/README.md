# cold_path_corpus — Phase 12 G-37 E2E fixture

Minimal stripped-down corpus for the one bounded end-to-end test in
`tests/test_cold_path_e2e.py`. Not representative of production
scale; representative of the production *shape* through every stage.

## Shape

| Element | Count | Why this count |
|---------|-------|----------------|
| Topics | 2 | One is enough to exercise the chain; two surface any "this only worked because there was a single topic" assumption |
| Subtopics per topic | 3 | Smallest count that exercises the tie-resolution logic in `assign_subtopics.py` (need ≥2 to have a tie, +1 to verify the resolver picks correctly) |
| Pubs per subtopic | 5 | Smallest count above the `spotlight_dirty_pubs_per_subtopic_min` threshold (5 in current config — see `config/thresholds.json`) |
| Total pubs | 30 | 2 × 3 × 5 |

## Files

- `pubs.json` — the publication corpus; one record per PMID with title, abstract, and mesh terms.
  This file documents the INTENT of the corpus (which subtopic each pub should score into)
  but is NOT the direct input to the aggregation stage. The E2E test uses it to build
  synthetic SCORE# rows (the output format of `assign_subtopics.py`) so that Bedrock and
  RDS are not required.
- `taxonomy.json` — the taxonomy fixture (consumed by score/assign stages; in this minimal
  test, it is an INPUT, not the output of `generate_taxonomy.py` — the E2E test starts by
  reading this fixture, not by regenerating taxonomy from scratch).

## Why fixture taxonomy is INPUT not OUTPUT

`generate_taxonomy.py` is non-deterministic (Bedrock LLM call). E2E
testing it would require either a recorded-LLM-response replay or
extensive mocking. Out of scope for one bounded test. Instead, the
fixture treats taxonomy as INPUT and exercises everything DOWNSTREAM
of taxonomy generation: scoring, assignment, aggregation, publish.

If a future plan wants to also exercise `generate_taxonomy.py`, that is
a separate test (and probably a separate fixture corpus or recorded-
response cassette).

## Why score_publications + assign_subtopics are bypassed

Both stages require active Bedrock LLM calls and RDS/DynamoDB connections.
The E2E test builds synthetic `SCORE#` rows directly — the output format that
`aggregate_subtopic_scores.py` reads — so the aggregation, reconciliation, and
publish stages can be exercised with MagicMock tables and no network calls.

This follows the "one bounded test" principle (D-21): the test buys the most
architectural confidence per unit of work by exercising the aggregation and
publish layers, which are the load-bearing stages with the most inter-phase
dependencies.

## Updating the corpus

Update both `pubs.json` and `taxonomy.json` together — they must
stay schema-consistent. After any update, re-run
`tests/test_cold_path_e2e.py` to confirm the chain still flows
cleanly.

## Synthetic PMIDs

PMIDs are synthetic strings like `TEST_PMID_001` — explicitly
non-numeric so they cannot collide with real PMIDs in any live
dataset.
