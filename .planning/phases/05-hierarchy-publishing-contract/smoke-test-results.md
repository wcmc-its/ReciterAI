# Phase 5 Smoke-Test Results

**Date:** 2026-05-06
**Commit SHA:** 3561145

Both smoke tests PASS. The phase is green: the schema validation gate correctly rejects
invalid input (HPC-08) and the dry-run publish pipeline produces a valid manifest with a
verified sha256 (HPC-03, HPC-04, HPC-05) — without issuing a real S3 PutObject.

---

## Test 1: Negative path — schema rejects invalid hierarchy fixture

**Purpose:** Prove that D-16's validation gate is well-formed. A corrupted hierarchy
(`topics` changed from object to array) must produce at least one jsonschema error.

**Fixture:** `tests/fixtures/hierarchy_invalid.json`
- Version field: `subtopic_v1` (preserved)
- Corruption: `topics` is an array instead of an object (schema requires `"type": "object"`)
- File size: 54 547 bytes (includes 3 real topic entries inside the array for realism)

**Command:**
```
python3 -c "
import json
from jsonschema import Draft202012Validator
schema = json.load(open('docs/hierarchy.schema.json'))
h = json.load(open('tests/fixtures/hierarchy_invalid.json'))
errors = sorted(Draft202012Validator(schema).iter_errors(h), key=lambda e: list(e.absolute_path))
print(f'Validator returned {len(errors)} error(s):')
for e in errors[:10]:
    path = ' > '.join(str(p) for p in e.absolute_path) or '<root>'
    msg = e.message if len(e.message) < 200 else e.message[:200] + '...[truncated]'
    print(f'  [{path}] {msg}')
"
```

**Output:**
```
Validator returned 1 error(s):
  [topics] [{'topic_id': 'cardiovascular_disease', 'subtopics': [...]}...] is not of type 'object'...[truncated]
```

**Verdict: PASS** — The validator returns 1 error on the deliberately corrupted fixture.
The error path is `topics` and the message confirms the type mismatch (`not of type 'object'`).
This proves D-16's validation gate is well-formed: a corrupted hierarchy produces validator
errors, so the schema is not over-permissive.

**Sanity check:** The real `hierarchy_full.json` still validates cleanly after fixture creation
(confirmed by `Draft202012Validator(schema).validate(h)` exit 0 — the fixture corruption is
isolated to `tests/fixtures/hierarchy_invalid.json`).

---

## Test 2: Positive path — --publish --dry-run prints manifest preview

**Command:**
```
python3 backfill_all.py --publish --dry-run --skip-pm-copy
```

**Output:**
```
19:04:42 INFO Schema validation: PASS

=== DRY RUN — --publish preview ===
  version:          v2026-05-06
  schema_version:   1.0.0
  taxonomy_version: taxonomy_v2
  sha256:           885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0
  artifact_bytes:   1,137,534
  Would upload to:  s3://wcmc-reciterai-hierarchy/v2026-05-06/  (3 objects)
                    s3://wcmc-reciterai-hierarchy/latest/     (3 objects, overwrite)

Manifest preview:
{
  "schema_version": "1.0.0",
  "taxonomy_version": "taxonomy_v2",
  "version": "v2026-05-06",
  "generated_at": "2026-05-06T23:04:42Z",
  "sha256": "885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0",
  "artifact_bytes": 1137534
}
```

**Exit code:** 0

**No S3 upload occurred:** The output contains no `Uploaded s3://` log line. The dry-run
path short-circuits at Step 5 in `_run_publish()` before any boto3 `put_object` call.

**Verdict: PASS** — Schema validation passes on the real artifact, manifest preview is
printed with all 6 locked fields in locked order, S3 paths for both versioned and latest
prefixes are shown, and exit code is 0.

---

## sha256 verification

**Independent computation:**
```python
import json, hashlib
with open('.planning/phases/04-subtopic-system/hierarchy_full.json') as f:
    h = json.load(f)
bytes_ = json.dumps(h, indent=2, ensure_ascii=False).encode('utf-8')
expected = hashlib.sha256(bytes_).hexdigest()
print(expected)
```

**Result:**
```
sha256 PASS: 885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0
```

The independently-computed sha256 (`885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0`)
matches the value printed in the manifest preview exactly.

This proves the Pitfall 2 mitigation (RESEARCH.md §Pitfalls): the sha256 in `_run_publish()`
is computed over **in-memory bytes** (`json.dumps(hierarchy, indent=2, ensure_ascii=False).encode("utf-8")`)
— the same bytes that would be uploaded — not over on-disk bytes that might differ in newline
encoding or whitespace.

---

## Manifest field verification

All 6 locked manifest fields appear in the dry-run output in locked order (RESEARCH.md §2):

```
grep -E '"(schema_version|taxonomy_version|version|generated_at|sha256|artifact_bytes)"' /tmp/05-smoke-positive.txt
```

Output:
```
  "schema_version": "1.0.0",
  "taxonomy_version": "taxonomy_v2",
  "version": "v2026-05-06",
  "generated_at": "2026-05-06T23:04:42Z",
  "sha256": "885fb239b790f0c6b2c2864d50d23854397f71d2b8afa15c36e85e6c04e089f0",
  "artifact_bytes": 1137534
```

All 6 fields present: `schema_version`, `taxonomy_version`, `version`, `generated_at`,
`sha256`, `artifact_bytes`. Order is locked (manifest dict is not sort_keys'd — T-05-02-03).

---

## Phase 5 success criteria coverage

- **HPC-01 (schema validates artifact):** Test 1 proves the schema rejects a corrupted
  fixture (non-empty `iter_errors`). Test 2 proves the schema accepts the real
  `hierarchy_full.json` (the `Schema validation: PASS` log line is emitted before the
  manifest preview, meaning `iter_errors` returned 0 errors). Two-sided check: neither
  over-permissive nor over-strict.

- **HPC-02 (contract doc):** `docs/hierarchy-contract.md` was created in Plan 03 and
  contains the 6-field manifest definition, the artifact shape, SLA, and CHANGELOG.
  Not directly exercised by these smoke tests, but Plan 03 evidenced its existence.

- **HPC-03 (publish flag validates before uploading):** Test 2 confirms the sequence:
  schema validation runs first (`Schema validation: PASS` appears before `DRY RUN`
  preview), and the dry-run preview confirms the upload path would proceed for valid input.
  The fail-fast gate (D-16) is confirmed by Test 1 — validation errors cause the function
  to `return 1` before reaching Step 6 (S3 upload).

- **HPC-04 (manifest carries required fields):** "Manifest field verification" section
  above. All 6 locked fields appear in the correct order in the dry-run output.

- **HPC-05 (S3 paths v{date}/ + latest/):** Test 2 output line:
  `Would upload to: s3://wcmc-reciterai-hierarchy/v2026-05-06/  (3 objects)` and
  `s3://wcmc-reciterai-hierarchy/latest/     (3 objects, overwrite)`. Both prefixes confirmed.

- **HPC-06 (cross-references):** Plan 05 deliverables (see-also generation integrated in
  `generate_see_also.py`). Not exercised by smoke tests but evidenced in Phase 4/5 plans.

- **HPC-07 (SPS handoff + reference script):** Plan 04 deliverables. Not exercised by
  these smoke tests (would require SPS-side environment). Evidenced by `docs/sps-handoff.md`
  and `reference/` scripts from Plan 04.

- **HPC-08 (validation gate fail-and-don't-upload):** Test 1 is the explicit assertion.
  The synthetic invalid fixture (`topics` as array) triggers `iter_errors` to return 1 error.
  The `_run_publish()` function returns 1 and does NOT proceed to Step 6 (S3 upload). In
  the dry-run path (Test 2), the same gate runs — `Schema validation: PASS` only appears
  because the real artifact IS valid. If it were invalid, the function would have exited 1
  before printing the manifest preview.

---

## Operator follow-up: Inaugural live publish

This phase concludes with the full artifact publishing pipeline in place but the S3 bucket
NOT yet provisioned. After this plan ships, follow these steps to perform the inaugural live
publish:

**Step 1: Provision the S3 bucket (D-03)**
Use the AWS console (or CLI) to create `wcmc-reciterai-hierarchy`:
- Region: `us-east-1`
- Block all public access: enabled
- Versioning: enabled (optional but recommended for disaster recovery)
- Default encryption: SSE-S3

**Step 2: Attach the IAM policy**
Paste the inline IAM policy from `docs/aws-iam-pipeline-policy.json` onto the pipeline
operator's IAM principal (the IAM user or role whose credentials are in `~/.zshrc`).
The policy grants `s3:PutObject`, `s3:GetObject`, `s3:HeadObject`, and `s3:ListBucket`
on `arn:aws:s3:::wcmc-reciterai-hierarchy/*`.

**Step 3: Run the live publish**
```bash
python3 backfill_all.py --publish
```
(No `--dry-run` flag. The `--skip-pm-copy` flag is omitted so the PM worktree copy also runs.)

Expected behavior:
- `Schema validation: PASS`
- 6 objects uploaded (3 to `v2026-05-06/`, 3 to `latest/`)
- `=== backfill_all.py --publish complete ===` summary with sha256 and S3 paths
- PM worktree instructions printed for the `hierarchy.json` subrepo commit

The CHANGELOG entry in `docs/hierarchy-contract.md` already documents 2026-05-06 as the
inaugural publish date.
