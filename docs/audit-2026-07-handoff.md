# ReciterAI audit — 2026-07-10 handoff

**Start here.** This is the complete record of a full-repo audit of ReciterAI (grants excluded — that work runs in parallel). Everything below is either *shipped*, *filed*, or *waiting on a decision*. Nothing was left undocumented.

Method: 8 subsystem reviewers over `origin/main` (952b78f) plus a read-only probe of live staging DynamoDB/S3, with every finding independently adversarially verified before it was recorded. **40 findings filed, 35 confirmed, 3 refuted, 2 merged as duplicates.**

---

## 1. What already landed (merged to `main`, CI green on each)

| PR | Change | Why it mattered |
|---|---|---|
| [#301](https://github.com/wcmc-its/ReciterAI/pull/301) | pytest CI + fix a suite that was red on any fresh clone | No CI ran the tests at all |
| [#302](https://github.com/wcmc-its/ReciterAI/pull/302) | `batch_write` raises on retry-exhausted `UnprocessedItems` | Silent permanent `TOPIC#` row loss |
| [#305](https://github.com/wcmc-its/ReciterAI/pull/305) | Fail wrong-shape LLM responses; fix the `[user,user]` JSON retry | PMIDs checkpointed `complete` with zero scores |
| [#303](https://github.com/wcmc-its/ReciterAI/pull/303) | Stamp `created_at` on `TOPIC#` rows | Spotlight refresh was a no-op since its first run |
| [#304](https://github.com/wcmc-its/ReciterAI/pull/304) | Key rotation history per subtopic, not per publish date | Rotation decay never applied, ever |
| [#306](https://github.com/wcmc-its/ReciterAI/pull/306) | Prompt-cache the static screening prefix | ~$25–30 of the ~$65 full-corpus run |

`main` is green at `b9b59e5`. The combined suite runs **2,431 passed / 0 failed** in a clean venv with no AWS or MariaDB credentials.

### Data fix applied directly to staging

`aging_geroscience` had **2,013 scored activities but all 30 `SUBTOPIC_SCORE` partitions empty** — `aggregate_subtopic_scores` had simply never materialized them. Re-ran it (no LLM cost; activities were already tagged). Verified **30/30 exclusive + 30/30 inclusive** partitions populated across **430 faculty**.

---

## 2. Open work, in the order I'd do it

### 2.1 Rollout steps for what already merged — **do these first, they are half-finished**

1. **Run the spotlight history fold.** `#304` shipped `scripts/fold_spotlight_history_versions.py` but it has **not been run**. It must run *after* the code is deployed, or the old code re-fragments the partitions on its next publish.
   ```
   scripts/fold_spotlight_history_versions.py --dry-run   # expect: 159 rows, 91 subtopics, 38 recovered, 0 malformed
   scripts/fold_spotlight_history_versions.py
   ```
2. **Verify the spotlight cron actually fires** on its next scheduled run. `#303` makes the dirty gate able to see rows, but only rows written *after* deploy carry `created_at`. Until enough new activity lands, the gate correctly reports nothing to do — confirm it is skipping for the right reason, not the old one.
3. **Deploy the image** so `numpy`/`requests` (declared in `#301`) are present. The Axis-2 tools pipeline would `ModuleNotFoundError` in any container built before that commit.
4. **Clean up 98 junk `STAGE#` rows** in staging: PK `STAGE#assign_subtopics#topic:cardiovascular_disease`, `records_written: 1`, dated 2026-05-30 → 2026-07-10. Written by a test that called `run(dry_run=False)` without patching `get_table` (fixed in `#301`). Inert — the skip cache keys on `input_hash`, which a real run computes differently — but they pollute the partition alongside the one genuine row (`records_written: 816`). **Not deleted:** shared data, wants an explicit go-ahead.

### 2.2 The decision that blocks a taxonomy change

**[#307] Retire `hematology_medical_oncology`.** The operator's instinct that it is redundant with `hematology` is right, and the evidence is stronger than that framing:

- **88.6%** of its 669 PMIDs are already scored by an existing cancer topic.
- Only **76 (11.4%)** are unique; only **6** of those score ≥0.7.
- Overlap: `cancer_biology_general` 50.2%, `hematology` 38.4%, `immunology_inflammation` 30.8%, then the organ cancers.
- Faculty overlap with `hematology` alone: **197 of 358 (55%)**.
- Its unique papers are survivorship, fellowship training, COVID care delivery, and policy — clinical **practice**, not a research domain.

It is precisely the "union of existing research-domain topics → near-clone umbrella" that #225's own decision table used to reject the *other six* Bucket A candidates. It survived on the rationale that `hematology` is blood *biology*, but the live `hematology` description explicitly covers "blood cancers (leukemia, lymphoma, myeloma)."

Retirement is destructive and cross-repo — full checklist in [#307]. Key ordering constraint: **SPS #690's `count == 68` assertion must drop to 67 before or with the deletion**, or the SPS ETL breaks.

Do **not** build its subtopic layer in the meantime; that was the original framing of #307 and is now superseded.

### 2.3 Correctness bugs that destroy or regress live data

**[#308] Tools/entities — three ways a rebuild silently destroys the live artifact.**
- `rebuild_tool_context --republish` overwrites live `entities.json` / `entity_context.json` with **empty objects**, destroying the #1166 Methods Surface-B entity dimension. The freeze check logs "byte-identical to live ✓" because it does not cover the two files being destroyed. (`cli/rebuild_tool_context.py:306`)
- `publish_entity_sidecar` fresh-build path republishes **pre-#254 mid-clause fragments**, silently regressing sentence-aligned snippets. No metric abort.
- Classify resume-cache **persists LLM-error placeholders**, so a transient throttle drops those tools from the taxonomy permanently. (`pipeline_tools/corpus_run.py:295`)
- `form_families` **re-mints `fam_XXXX` ids** in member-count order on every rebuild, breaking the durability the `FamilyRegistry` contract promises.

**[#310] `complete` STAGE# rows written on total failure, blocking re-runs.**
- `assign_subtopics.py:1068` writes `complete` with no check of `failed_count`. A Bedrock outage leaves PMIDs unassigned but the topic marked done; `--resume` skips it forever (same `input_hash`).
- `score_publications.py:1603` does the same. A 100%-failed `--pmids` batch writes `status=complete, records_written=0`; `should_skip` matches on `input_hash` alone and ignores `records_written`. Only `--force` recovers.

### 2.4 Failures that reach nobody

**[#309] Orchestration alerting.**
- Every `dynamodb:putItem` state in **both** state machines has no `Retry`/`Catch` (Lambda tasks all do). A single DDB throttle on `WriteHotRunComplete` — *after* all work succeeded — fails the execution without writing a failed `STAGE#` row or firing a Teams card.
- Four `pipeline_hot/orchestrator.py` alerts (lock collision, retry/eligibility/drift sweep failures) route through `pipeline_common.alert`, which **no-ops without `RECITERAI_SLACK_WEBHOOK_URL`** — an env var provisioned nowhere. The three sweeps are the only mechanism rescuing failed/drifted PMIDs; if one breaks permanently, the orchestrator proceeds delta-only forever with zero signal.

### 2.5 Spotlight quality (beyond the two structural bugs already fixed)

**[#311]**
- Dirty gate counts **activity rows, not distinct publications**. One paper with 5 co-authors sharing a subtopic alone satisfies the "≥5 new pubs" floor. Three such papers trigger a full Opus regeneration.
- `--regen-only` — the human-in-the-loop fix path for a bad lede — grounds the regenerated lede on the **subtopic description repeated 3×** instead of real paper synopses, and the critic's `anchored_in_synopses` check validates against that same fake text.
- The Phase 12 D-08 `CRITIC_REJECT#` audit trail and vocabulary-drift WARN are **dead code**: no production caller passes `stage_table`.

### 2.6 Everything else, grouped so nothing is lost

**[#312]** — correctness smalls and efficiency:
- `diff.json` `reassigned_pmid_count` is **structurally always 0** (queries a PK nothing writes), so `editorial_only=true` can be reported on a publish that reassigned thousands of PMIDs.
- Hierarchy **shrink guard fails open** exactly when needed: an S3 blip on the prev-artifact GET makes `_prev_subs == _new_subs`, letting a truncated hierarchy overwrite `latest/`. It also ratchets.
- **Same-day republish mutates the "immutable" versioned prefix**, opening a ≤60s manifest-sha mismatch window.
- Faculty who lose all rows in a topic **keep a stale `subtopic_scores.<topic>` entry** forever.
- `SUBTOPIC_SCORE#` partitions for **retired subtopic ids are never deleted**.
- The **D-33 reconciliation is near-tautological** — it validates item construction, not aggregation, so a real aggregation bug corrupts both sides identically and passes.
- Cores affinity prior does **13 full-table scans** (one per core) and `except Exception: return []`, silently zeroing the prior on a throttle.
- Daily enrichment **re-pays LLM cost for already-succeeded PMIDs** on retry (~$18/1,000-pub delta, twice).
- `update_activity_subtopics` builds a **fresh boto3 resource per row** (new TLS handshake per UpdateItem).
- **Tools publish has no shrink/content gate**, unlike hierarchy and spotlight.

**Add regardless:** a post-aggregation invariant that **every non-empty `TOPIC#` partition yields ≥1 `SUBTOPIC_SCORE` partition.** Neither the `aging_geroscience` nor the heme/onc gap alerted anyone.

---

## 3. Things that were checked and are genuinely fine

Worth recording so nobody re-audits them:

- **Publish contract.** Manifest sha256s verify against live artifacts; PUT ordering is correct (`latest/manifest.json` last); schema validation precedes any PutObject; the post-publish `schema_roundtrip` gate runs against the real bytes.
- **Spotlight critic loop** is hard-bounded, fails closed on parse errors, and routes exhaustion to `needs_review`. Lede text is **never** programmatically truncated — the 22–38-word band is critic-enforced with regeneration.
- **Cost guards** are layered with no bypass found.
- **BatchGetItem helpers** all correctly re-queue `UnprocessedKeys` and paginate `LastEvaluatedKey`.
- **Bedrock content-filter fallback** (→ OpenAI gpt-5.1) is narrowly scoped and well-instrumented.
- **65 of 66** published topics have complete subtopic rollups; the spotlight artifact is clean (18 cards, 0 truncated ledes, 0 empty `papers[]`).
- `utils/db.py` bounds wedged MariaDB connections; `read_guards.DegradedReadError` cleanly separates degraded from empty reads.

## 4. Findings that were refuted on verification — do not re-file

- **"Hierarchy artifact 57 days stale."** The facts verify (`latest` = v2026-05-13) but the publish path is **event-driven by design**; staleness is not a reliability defect.
- **"STAGE# queries never paginate, so `reassigned_pmid_count` undercounts."** Unreachable: nothing ever writes the `STAGE#assign_subtopics#GLOBAL` PK, so the partition is empty by construction. The *real* bug is that the count is always 0 — captured in #312.
- **"10.5% of entity usage sentences are truncated."** The numbers reproduce (847/8,088 `sentence_complete=false`) but the pipeline **never truncates** — those are verbatim contiguous spans of the source text.

---

## 5. Incidental discovery worth remembering

The test suite had been **writing to the live `reciterai` table**. `test_p11_run_propagates_hierarchy_version_to_update_activity` called `run(dry_run=False)` without patching `get_table`, so `write_complete()` issued a real `PutItem` on every credentialed local `pytest` run — 98 rows in staging. Its own comment said it meant to patch `get_table`. Fixed in #301.

Two consequences worth internalizing:
1. **Run the suite with no credentials** before trusting it. `env -i ... AWS_CONFIG_FILE=/dev/null python -m pytest` is what CI does, and it is what caught this.
2. Use `python -m pytest`, not the bare `pytest` script — only the former puts the repo root on `sys.path`.

CI paid for itself before it was merged: its first run surfaced **`numpy` and `requests` missing from `requirements.txt`**, the only file the Dockerfile installs. Both were invisible locally because they sit in the operator's global environment.
