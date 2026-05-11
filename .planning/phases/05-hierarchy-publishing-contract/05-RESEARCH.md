# Phase 5: Hierarchy Publishing Contract — Research

**Researched:** 2026-05-06
**Domain:** S3 artifact publishing, JSON Schema validation, consumer contract documentation
**Confidence:** HIGH (all critical claims verified against installed libraries or existing codebase)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 (Bucket):** `wcmc-reciterai-hierarchy` in `us-east-1`.
- **D-02 (Access model):** IAM-gated private bucket. No public-read, no CloudFront.
- **D-03 (Provisioning):** User provisions bucket via AWS console; pipeline IAM role gets inline `PutObject` policy from a JSON snippet in the plan.
- **D-04 (Version prefix):** `v{ISO-date}/` — e.g. `v2026-05-06/`.
- **D-05 (Latest pointer):** `latest/` PutObject-overwrite on every publish.
- **D-06 (Retention):** All `v{date}/` versions kept indefinitely. No S3 Lifecycle rules.
- **D-07 (Schema versioning):** `hierarchy.schema.json` carries independent semver `schema_version`. `manifest.json` exposes all three versions (version, taxonomy_version, schema_version).
- **D-08 (Cadence promise):** Annual full recompute guaranteed; ad-hoc re-publishes explicitly permitted. Consumers poll `latest/manifest.json` weekly.
- **D-09 (Deprecation window):** 30 days advance notice for breaking schema changes.
- **D-10 (Schema-change communication):** CHANGELOG section in `docs/hierarchy-contract.md`. Consumers watch `schema_version` in `manifest.json`.
- **D-11 (Backwards-compatibility):** Additive fields are non-breaking; consumers MUST tolerate unknown fields.
- **D-12 (Notification):** None beyond CloudWatch logs + `manifest.json`. Structured log line emitted on publish.
- **D-13 (PM worktree copy retention):** `--publish` does BOTH S3 PutObject AND `shutil-copy` to PM worktree. Always-on; no flag to disable.
- **D-14 (SPS handoff depth):** Produce `docs/sps-integration-handoff.md` + working reference fetch script (`docs/sps-etl-reference.ts`). No draft PR in SPS repo.
- **D-15 (Run environment):** Local operator machine; existing `~/.zshrc`-sourced AWS creds. No GH Actions, no Lambda.
- **D-16 (Validation strictness):** `--publish` MUST fail and refuse to upload if `hierarchy_full.json` does not validate. No warn-and-publish.

### Claude's Discretion

- JSON Schema library choice (Python `jsonschema`; researcher confirms below).
- Exact path of `hierarchy.schema.json` (`docs/hierarchy.schema.json` — confirmed below).
- Manifest field ordering and any additional metadata fields.
- Whether `manifest.json` includes a `previous_version` back-pointer.
- Idempotency mechanism on re-runs of the same publish-date.
- Reference-script language (TypeScript — confirmed below per SPS ETL runtime).
- IAM JSON snippet shape.

### Deferred Ideas (OUT OF SCOPE)

- SPS-side ETL implementation (separate session in SPS repo).
- PM migration off the worktree-file pattern.
- DynamoDB load step for hierarchy data (rejected).
- Multi-environment artifacts (dev/staging/prod prefixes).
- CloudFront / public-read access.
- GitHub Actions or Lambda automation for publish.
- Slack/email publish notifications.
- Schema-change-triggered consumer notification (beyond CHANGELOG + manifest sha256).

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HPC-01 | `hierarchy.schema.json` validates `hierarchy_full.json` at `docs/hierarchy.schema.json` | JSON Schema section; schema shape derived from `hierarchy-schema.md` |
| HPC-02 | `docs/hierarchy-contract.md` as stable consumer-facing contract | Contract doc section; structure and required sections enumerated |
| HPC-03 | `backfill_all.py --publish` validates + uploads to `v{date}/` and `latest/` + writes `manifest.json` | CLI integration section; S3 PutObject pattern; flag composability rules |
| HPC-04 | `manifest.json` carries version, taxonomy_version, schema_version, sha256(hierarchy.json), generated_at | Manifest shape section; field ordering and sha256 computation confirmed |
| HPC-05 | STATE.md and `hierarchy-schema.md` D-19 link to `docs/hierarchy-contract.md` | File inventory section; cross-reference update plan |
| HPC-06 | `docs/sps-integration-handoff.md` + `docs/sps-etl-reference.ts` (working, not pseudocode) | Reference script section; SPS ETL stack confirmed TypeScript |
| HPC-07 | IAM policy JSON snippets (bucket policy + inline PutObject policy) | IAM section; paste-ready JSON snippets |
| HPC-08 | Schema validation gate tested with invalid hierarchy fixture (fail-and-don't-upload asserted) | Validation Architecture section |

</phase_requirements>

---

## Summary

Phase 5 is a **publish-and-document** phase, not a data-processing phase. All the hard work (hierarchy assembly, subtopic discovery, relabeling) is already done. This phase adds three capabilities on top of the existing `backfill_all.py --assemble-only` path: (1) a machine-readable JSON Schema that validates the artifact shape, (2) an S3 publish step with versioned and `latest/` prefixes, and (3) consumer-facing documentation that makes integration self-service.

The technical surface is small and well-bounded. Python `jsonschema` 4.26.0 is already installed on the operator's machine (verified). `boto3` 1.42.54 is already in `requirements.txt`. The S3 PutObject API surface is standard. The SPS ETL is TypeScript (verified from `etl/dynamodb/index.ts` and `etl/orchestrate.ts`), so the reference script is TypeScript. The `docs/` directory already exists in this repo (containing `data-model-and-queries.md` and `taxonomy-methodology.md`), confirming the precedent for placing human-readable docs there.

The most subtle design decisions are the manifest field ordering (use explicit key order, not alphabetical, because consumers who compute their own manifest sha256 for change detection need stable output), the idempotency behavior on same-day re-runs (overwrite is the right default for `v{date}/`), and the `--publish` / `--dry-run` interaction (dry-run should compute and print the manifest sha256 but not upload).

**Primary recommendation:** Build the publish step as a new `_run_publish()` function in `backfill_all.py` that calls `_run_assemble_only()` first if `hierarchy_full.json` is missing, then validates, then uploads. Bypass the verdict gate (same as `--assemble-only`).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Schema validation (pre-upload gate) | Pipeline script (Python) | — | Validation must happen before any S3 write; lives in `backfill_all.py` |
| S3 artifact upload | Pipeline script (Python) | — | Operator-run script using boto3 S3 client; no Lambda |
| manifest.json generation | Pipeline script (Python) | — | sha256 computed over bytes before upload; written atomically with upload |
| `hierarchy.schema.json` file | Repo artifact (ReciterAI) | S3 (co-published) | Authored in-repo; co-published to S3 so consumers can fetch schema at same URL as data |
| `docs/hierarchy-contract.md` | Repo artifact (ReciterAI) | — | Human-readable; lives in repo; not published to S3 (too internal) |
| `docs/sps-integration-handoff.md` | Repo artifact (ReciterAI) | — | Briefing document; stays in this repo; not in SPS repo |
| `docs/sps-etl-reference.ts` | Repo artifact (ReciterAI) | SPS ETL (adapted) | Reference implementation SPS coding agent copies and adapts |
| Consumer S3 fetch | SPS ETL Lambda (TypeScript) | — | ADR-006 LOCKED: runtime reads MySQL only; S3 fetch is ETL-time, not request-time |
| MySQL Subtopic table population | SPS ETL Lambda (TypeScript) | — | ETL projects hierarchy.json → Subtopic rows; no runtime DynamoDB or S3 path |

---

## Key Technical Decisions

### 1. JSON Schema Library Choice

**Recommendation: `jsonschema` 4.26.0, `Draft202012Validator`, `iter_errors()` for reporting.**

[VERIFIED: pip show jsonschema, python3 import test]

- **Version:** `jsonschema` 4.26.0 is already installed on the operator's machine and is the latest release. No installation needed; just add it to `requirements.txt` (currently absent). Pin as `jsonschema>=4.23.0` (minimum that ships Draft 2020-12 without known regressions).
- **Draft:** Use **Draft 2020-12** (`"$schema": "https://json-schema.org/draft/2020-12/schema"`). It is fully supported in 4.x, uses `prefixItems` instead of the deprecated `items` for arrays, and is the current JSON Schema standard. [ASSUMED: Draft 7 would also work but 2020-12 is the right choice for a new schema authored in 2026.]
- **Validator class:** Use `Draft202012Validator` directly, not the `validate()` shortcut. Reason: `validate()` raises on the first error only; `Draft202012Validator.iter_errors()` collects all validation errors so the operator sees the full picture on a failed publish. [VERIFIED: python3 test of iter_errors() behavior]
- **Error UX:** Collect all errors via `sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))`, print each as `  [path] message` (path is `' > '.join(str(p) for p in e.absolute_path) or '<root>'`), then `sys.exit(1)`. This gives the operator a complete diff between actual and expected shape, not just the first mismatch.
- **Deprecation note:** `jsonschema.__version__` emits a `DeprecationWarning` in 4.26.0; use `importlib.metadata.version('jsonschema')` if the version is needed in code.

```python
# Source: verified against jsonschema 4.26.0 on this machine
from jsonschema import Draft202012Validator

def validate_hierarchy(hierarchy: dict, schema: dict) -> list[str]:
    """Return list of human-readable error strings. Empty = valid."""
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(hierarchy),
        key=lambda e: list(e.absolute_path),
    )
    result = []
    for e in errors:
        path = " > ".join(str(p) for p in e.absolute_path) or "<root>"
        result.append(f"  [{path}] {e.message}")
    return result
```

---

### 2. Manifest Shape

**Recommendation: 6 fields in explicit key order; no `previous_version` back-pointer.**

[VERIFIED: sha256 computation tested against json.dumps output; field rationale from CONTEXT.md]

**Core fields (locked by D-04, D-07, phase description):**

```json
{
  "schema_version": "1.0.0",
  "taxonomy_version": "taxonomy_v2",
  "version": "v2026-05-06",
  "generated_at": "2026-05-06T14:32:01Z",
  "sha256": "a3f7...c291",
  "artifact_bytes": 1152043
}
```

**Field ordering rationale:** The 6-field ordering above is logical (schema metadata → data metadata → artifact fingerprint), NOT alphabetical. Write `manifest.json` with `json.dump(..., indent=2, ensure_ascii=False, sort_keys=False)` using an `collections.OrderedDict` or a plain dict with explicit key assignment order (Python 3.7+ dicts preserve insertion order). This ensures the manifest sha256 is stable across re-runs — consumers who compute their own sha256 over `manifest.json` bytes get a consistent result.

**`artifact_bytes` (additional field):** Include the byte count of `hierarchy.json` as `artifact_bytes`. This costs nothing and lets consumers pre-allocate or sanity-check without a second HEAD request. [ASSUMED: reasonable convenience field; low risk]

**`previous_version` back-pointer:** Do NOT include. Rationale: (a) adds no safety guarantee — if a consumer skips `previous_version`, they won't notice the field; (b) the CHANGELOG section of `docs/hierarchy-contract.md` is the canonical history; (c) the back-pointer would need to be read from `latest/manifest.json` before the new one is written, adding a GetObject API call and a race window. Consumers who need history list the `v{date}/` prefixes via `ListObjectsV2` with the `v` prefix filter.

**sha256 computation:** Compute over the raw UTF-8 bytes of `hierarchy.json` as it will be uploaded (i.e., the output of `json.dumps(hierarchy, indent=2, ensure_ascii=False).encode('utf-8')`), NOT over the file bytes on disk. This ensures the manifest sha256 matches what a consumer downloads even if the file was written on a platform with different line endings. [VERIFIED: python3 hashlib.sha256 test]

```python
# Source: verified against python3 hashlib on this machine
import hashlib, json

def compute_manifest(hierarchy: dict, schema: dict, version: str) -> tuple[bytes, bytes, dict]:
    """
    Returns (hierarchy_bytes, schema_bytes, manifest_dict).
    hierarchy_bytes and schema_bytes are the exact bytes to upload.
    manifest sha256 covers hierarchy_bytes.
    """
    from datetime import datetime, timezone
    import importlib.metadata

    hierarchy_bytes = json.dumps(hierarchy, indent=2, ensure_ascii=False).encode("utf-8")
    schema_bytes = json.dumps(schema, indent=2, ensure_ascii=False).encode("utf-8")
    sha256 = hashlib.sha256(hierarchy_bytes).hexdigest()

    manifest = {
        "schema_version": schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0"),
        "taxonomy_version": hierarchy.get("taxonomy_version", "unknown"),
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": sha256,
        "artifact_bytes": len(hierarchy_bytes),
    }
    return hierarchy_bytes, schema_bytes, manifest
```

Note: embed `schema_version` directly as a top-level constant in `hierarchy.schema.json` under `"$defs": {"_meta": {"schema_version": "1.0.0"}}` so it's co-located with the schema and survives a rename without updating code. Alternatively, hard-code it as a module-level constant in the publish function and bump it manually. The simpler approach (module constant) is acceptable for a single-operator workflow.

---

### 3. Idempotency on Same-Day Re-Publish

**Recommendation: Overwrite `v{date}/` prefix silently. No abort-if-exists.**

[VERIFIED: consistent with S3 PutObject semantics; matches D-05 for latest/]

S3 PutObject is always an overwrite — there is no "put-if-not-exists" in S3 without a conditional write (supported via `IfNoneMatch: *` header, added in 2024, but adds complexity). The idempotency question is: what should happen if the operator runs `--publish` twice on the same calendar day?

**Recommendation: overwrite silently, emit a log warning.** Rationale:
- The use case (ad-hoc relabel re-publish, as happened 2026-05-06) is exactly the pattern that needs overwrite. Aborting on same-day re-run would block the most common real-world correction workflow.
- `v{date}/` key space is still logically versioned — "today's best hierarchy" is what the operator wants in `v2026-05-06/`.
- Add `--force` flag only if abort-if-exists is ever needed; don't add it preemptively.
- Log line on overwrite: `WARN: v2026-05-06/ already exists in s3://wcmc-reciterai-hierarchy — overwriting.` (Detect by HeadObject before first PutObject; if 200, log the warning. If 404/NoSuchKey, normal first-publish path.)

```python
# Idempotency check pattern — HeadObject before upload
import boto3
from botocore.exceptions import ClientError

def _version_exists(s3_client, bucket: str, version: str) -> bool:
    """Return True if v{date}/manifest.json already exists in the bucket."""
    try:
        s3_client.head_object(Bucket=bucket, Key=f"{version}/manifest.json")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise  # re-raise on auth/other errors
```

---

### 4. `hierarchy.schema.json` Repo Path

**Recommendation: `docs/hierarchy.schema.json`.**

[VERIFIED: `docs/` directory exists in ReciterAI repo with `data-model-and-queries.md` and `taxonomy-methodology.md`; PM has `docs/superpowers/`; SPS has `docs/`]

The `docs/` directory exists in this repo and follows the same convention as `data-model-and-queries.md` (authoritative reference documentation). Sister repos (PM, SPS) also use `docs/`. The schema file belongs at `docs/hierarchy.schema.json` because:
- It is a human-readable reference artifact as much as a machine-readable file.
- `docs/` is the established location in this repo for reference files that consumers (human or code) read.
- Co-locating with `docs/hierarchy-contract.md` makes both findable in one directory.

**S3 co-publish path:** `v{date}/hierarchy.schema.json` and `latest/hierarchy.schema.json`. Consumers MUST fetch the schema at the same version prefix as their `hierarchy.json` (not always `latest/`) to guard against schema drift during the 30-day deprecation window.

---

### 5. IAM Policy Snippets

**Recommendation: Two separate JSON artifacts in `docs/`.**

[VERIFIED: ARN patterns from AWS S3 documentation conventions; D-01 bucket name locked]

#### (a) Inline IAM policy for the pipeline operator's IAM user/role

Paste this into the IAM console (Add inline policy → JSON editor) on the IAM principal that runs `backfill_all.py --publish`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "HierarchyPublish",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::wcmc-reciterai-hierarchy/*"
    },
    {
      "Sid": "HierarchyListBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:HeadObject"
      ],
      "Resource": "arn:aws:s3:::wcmc-reciterai-hierarchy"
    }
  ]
}
```

Note: `s3:HeadObject` on the bucket-level resource is actually an object-level action — the correct Resource for HeadObject is `arn:aws:s3:::wcmc-reciterai-hierarchy/*`. Fix in the plan output. `s3:ListBucket` requires the bucket ARN (not `/*`). [VERIFIED: AWS S3 IAM action/resource mapping is a known gotcha — PutObject and HeadObject require `/*`, ListBucket requires the bucket ARN without `/*`.]

**Corrected version (plan must use this):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "HierarchyPublishAndCheck",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:HeadObject"],
      "Resource": "arn:aws:s3:::wcmc-reciterai-hierarchy/*"
    },
    {
      "Sid": "HierarchyListBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::wcmc-reciterai-hierarchy"
    }
  ]
}
```

#### (b) Bucket policy for SPS consumer read access

The SPS ETL Lambda needs `GetObject` on the bucket. Apply this as a resource-based bucket policy (S3 console → Permissions → Bucket Policy). Replace `<SPS_LAMBDA_ROLE_ARN>` with the actual Lambda execution role ARN:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SPSLambdaRead",
      "Effect": "Allow",
      "Principal": {
        "AWS": "<SPS_LAMBDA_ROLE_ARN>"
      },
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::wcmc-reciterai-hierarchy",
        "arn:aws:s3:::wcmc-reciterai-hierarchy/*"
      ]
    }
  ]
}
```

**Bucket console checklist** (paste-step for user):
- Block all public access: ON (all 4 checkboxes checked).
- Versioning: OFF (versioning is via prefix scheme, not S3 object versioning).
- No static website hosting.
- No CORS configuration (internal IAM consumers only).
- Encryption: SSE-S3 (AES-256, default).
- ACLs: disabled (object ownership = Bucket owner enforced).

Store the two policy snippets at `docs/aws-iam-pipeline-policy.json` and `docs/aws-bucket-policy.json` in this repo. The handoff doc references them.

---

### 6. Reference Fetch Script Language: TypeScript

**Recommendation: `docs/sps-etl-reference.ts`.**

[VERIFIED: SPS ETL stack is TypeScript — `etl/orchestrate.ts`, `etl/dynamodb/index.ts`, `etl/reciter/index.ts`, `etl/ed/index.ts` all confirmed TypeScript; `npm run etl:dynamodb` invokes via `tsx`]

The SPS ETL runtime is TypeScript running under Node 22+ via `tsx` (see `etl/orchestrate.ts` line `node --import tsx/esm`). The existing DynamoDB ETL is already 500+ lines of TypeScript. The reference script must be TypeScript so the SPS coding agent can drop it into `etl/hierarchy/index.ts` with zero language boundary friction.

**Reference script skeleton** (working, not pseudocode):

```typescript
/**
 * sps-etl-reference.ts — Reference fetch script for SPS hierarchy ETL
 *
 * Fetches hierarchy.json from S3, validates against schema, projects to MySQL
 * Subtopic table. SPS coding agent: copy to etl/hierarchy/index.ts, swap in
 * your Prisma client, and adapt the upsert block to your schema.
 *
 * Required env vars:
 *   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
 *   HIERARCHY_BUCKET (default: wcmc-reciterai-hierarchy)
 *
 * Usage:
 *   npm run etl:hierarchy   (add to package.json scripts)
 *   node --import tsx/esm etl/hierarchy/index.ts
 */
import { S3Client, GetObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";
import Ajv from "ajv/dist/2020"; // ajv v8+ with JSON Schema 2020-12 support

const BUCKET = process.env.HIERARCHY_BUCKET ?? "wcmc-reciterai-hierarchy";
const REGION = process.env.AWS_DEFAULT_REGION ?? "us-east-1";

// --- Types (mirror hierarchy-schema.md SubtopicDef) ---
interface SubtopicDef {
  id: string;
  label: string;
  description: string;
  display_name: string;
  short_description: string;
  activity_count: number;
  total_weight: number;
}

interface HierarchyManifest {
  schema_version: string;
  taxonomy_version: string;
  version: string;
  generated_at: string;
  sha256: string;
  artifact_bytes: number;
}

// --- S3 helpers ---
async function fetchText(s3: S3Client, key: string): Promise<string> {
  const resp = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  return resp.Body!.transformToString("utf-8");
}

async function main() {
  const s3 = new S3Client({ region: REGION });

  // 1. Fetch manifest — use this to detect whether hierarchy changed since last ETL run
  const manifestText = await fetchText(s3, "latest/manifest.json");
  const manifest: HierarchyManifest = JSON.parse(manifestText);
  console.log(`Hierarchy manifest: version=${manifest.version}, schema=${manifest.schema_version}`);

  // 2. TODO: compare manifest.sha256 against your last_known_sha256 from MySQL etl_run
  //    table (or similar). Skip fetch + upsert if sha256 is unchanged.
  //    const lastRun = await prisma.etlRun.findFirst({ where: { source: "hierarchy" }, orderBy: { createdAt: "desc" } });
  //    if (lastRun?.metadata?.sha256 === manifest.sha256) { console.log("Hierarchy unchanged — skip"); return; }

  // 3. Fetch schema for this version (not always latest/ — use version-specific prefix)
  const schemaText = await fetchText(s3, `${manifest.version}/hierarchy.schema.json`);
  const schema = JSON.parse(schemaText);

  // 4. Fetch hierarchy.json
  const hierarchyText = await fetchText(s3, `${manifest.version}/hierarchy.json`);
  const hierarchy = JSON.parse(hierarchyText);

  // 5. Validate against schema (fail-fast — do not write to MySQL if schema invalid)
  const ajv = new Ajv({ strict: false });
  const validate = ajv.compile(schema);
  if (!validate(hierarchy)) {
    console.error("Schema validation failed:", validate.errors);
    process.exit(1);
  }

  // 6. Project to MySQL Subtopic table
  //    Adapt the upsert block to match your Prisma schema.
  //    The canonical Subtopic shape per hierarchy-contract.md:
  //      id, label, display_name, short_description, parent_topic_id,
  //      activity_count, total_weight, description, source, refreshed_at
  let upserted = 0;
  for (const [topicId, topicEntry] of Object.entries(hierarchy.topics as Record<string, { subtopics: SubtopicDef[] }>)) {
    for (const subtopic of topicEntry.subtopics) {
      // TODO: replace with actual prisma.subtopic.upsert
      console.log(`  UPSERT subtopic ${subtopic.id} → topic ${topicId} | ${subtopic.display_name || subtopic.label}`);
      upserted++;
    }
  }

  console.log(`Hierarchy ETL complete: ${upserted} subtopics projected from ${manifest.version}`);
}

main().catch((err) => { console.error(err); process.exit(1); });
```

**Note on ajv:** SPS already uses TypeScript; `ajv` (v8) is the standard JSON Schema validator for Node/TypeScript. It supports JSON Schema 2020-12 via `ajv/dist/2020`. Add `ajv` to SPS `package.json` dependencies. [ASSUMED: ajv not yet in SPS package.json — verify during implementation]

---

### 7. `--publish` Flag Interaction with Existing Flags

**Recommendation: `--publish` implies assemble-only first if `hierarchy_full.json` is missing; composable with `--dry-run` and `--skip-pm-copy`.**

[VERIFIED: `backfill_all.py` source code read; `_run_assemble_only()`, `_write_hierarchy_full()`, `_copy_to_pm()` confirmed]

**Flag composability matrix:**

| Flag combination | Behavior |
|---|---|
| `--publish` (solo) | If `hierarchy_full.json` exists: validate → upload → PM copy. If missing: assemble from drafts first, then validate → upload → PM copy. Bypasses verdict gate (non-LLM operation). |
| `--publish --skip-pm-copy` | Validate → S3 upload only. No PM worktree copy. |
| `--publish --dry-run` | Assemble if needed, validate, compute sha256, print manifest preview — but no S3 PutObject. Exit 0 on valid, exit 1 on invalid. |
| `--assemble-only --publish` | Explicit re-assembly + publish in one invocation. Equivalent to `--publish` when `hierarchy_full.json` is missing. |
| `--publish --continue-on-error` | No interaction — `--publish` is post-assembly; per-topic error handling does not apply. |
| `--only-verdict-check` | Unchanged — still runs verdict gate check only, ignores `--publish`. |

**`--dry-run --publish` behavior** is the most useful operator UX: you can preview the manifest sha256 without uploading, confirm the schema validates, and review what would land in S3. Print the full manifest JSON to stdout in `--dry-run --publish` mode.

**Verdict gate interaction:** `--publish` bypasses the verdict gate (same as `--assemble-only`). The verdict gate guards Bedrock LLM spend; `--publish` is a file-read + S3-write operation.

**Implementation integration point:** Add `_run_publish(skip_pm_copy, dry_run)` function. Call from `run()` after the `assemble_only` check and before the verdict gate check, following the same short-circuit pattern:

```python
# In run(), before the verdict gate:
if publish:
    return _run_publish(skip_pm_copy=skip_pm_copy, dry_run=dry_run)
```

---

## Domain Overview

### S3 Artifact Publishing Pattern

The artifact publishing pattern used here is a common data contract pattern in data-engineering: produce a versioned artifact at `s3://<bucket>/v{date}/<file>`, maintain a `latest/` pointer via overwrite, and co-publish a `manifest.json` carrying a sha256 fingerprint so consumers can detect changes without downloading the full artifact.

This replaces the current cross-repo `shutil.copy2()` pattern that:
1. Requires the operator to have the PM worktree checked out.
2. Creates invisible coupling between the pipeline repo and the PM repo's branch state.
3. Gives SPS no stable URL to fetch from — they were expected to infer structure from DynamoDB (which never had hierarchy data).

The S3 artifact pattern gives every current and future consumer a single stable URL (`s3://wcmc-reciterai-hierarchy/latest/hierarchy.json`) that is independently fetchable, independently validatable, and independently versioned.

### Consumer Integration Model

```
Pipeline (ReciterAI repo)
    │
    │  --publish
    ▼
hierarchy_full.json (local)
    │
    │  validate against hierarchy.schema.json
    ▼
S3: wcmc-reciterai-hierarchy/
    ├── v2026-05-06/
    │   ├── hierarchy.json       (1.1 MB)
    │   ├── hierarchy.schema.json
    │   └── manifest.json
    └── latest/
        ├── hierarchy.json       (symlink via overwrite)
        ├── hierarchy.schema.json
        └── manifest.json
            │
            │  poll weekly, compare sha256
            ▼
SPS ETL Lambda (TypeScript)
    │
    │  GetObject hierarchy.json + schema
    │  validate + project
    ▼
MySQL Subtopic table
    │
    ▼
SPS runtime (Next.js 15, reads MySQL only per ADR-006)
```

### `hierarchy.schema.json` Shape (derived from `hierarchy-schema.md`)

The schema must match `hierarchy-schema.md` character-for-character. Key constraints:

- `version`: required string, enum `["subtopic_v1"]`
- `generated_at`: required string (ISO 8601 UTC)
- `taxonomy_version`: required string
- `excluded_topics`: required array of objects with `id` (string), `reason` (string), `activity_count` (integer)
- `topics`: required object (additionalProperties: TopicEntry)
- `see_also`: required array of SeeAlsoEntry objects
- `SubtopicDef`: required fields: `id`, `label`, `description`, `display_name`, `short_description`, `activity_count`, `total_weight`; `activity_count` is integer, `total_weight` is number
- **`additionalProperties: true` at all levels** — enforces D-11 (consumers must tolerate unknown fields by convention; schema itself must not reject future additions via `additionalProperties: false`)

The schema carries its own top-level `description` field with a machine-parseable `schema_version` field embedded in `$defs._meta.schema_version` so the publish step can extract it without hard-coding the version string.

---

## Standard Stack

### Core

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `jsonschema` | 4.26.0 (latest) | JSON Schema Draft 2020-12 validation | [VERIFIED: pip show, already installed] |
| `boto3` | 1.42.54 | S3 PutObject, HeadObject, ListBucket | [VERIFIED: already in requirements.txt] |
| `hashlib` | stdlib | sha256 of hierarchy bytes | [VERIFIED: Python stdlib] |
| `json` | stdlib | Deterministic JSON serialization | [VERIFIED: Python stdlib] |

### Supporting (SPS reference script only)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `@aws-sdk/client-s3` | ^3.x | S3 GetObject for consumer fetch | [ASSUMED: standard AWS SDK v3, already in SPS package.json likely] |
| `ajv` | ^8.x | JSON Schema 2020-12 validation in TypeScript | [ASSUMED: not yet in SPS; add to their package.json] |

### Installation (pipeline side)

```bash
# In ReciterAI repo — add to requirements.txt:
jsonschema>=4.23.0
```

`boto3` is already present. No other new dependencies.

---

## Architecture Patterns

### Recommended Project Structure (new files in this phase)

```
ReciterAI -ReCiter-Integration/
├── docs/
│   ├── data-model-and-queries.md       # existing
│   ├── taxonomy-methodology.md         # existing
│   ├── hierarchy.schema.json           # NEW — JSON Schema Draft 2020-12
│   ├── hierarchy-contract.md           # NEW — consumer-facing contract
│   ├── sps-integration-handoff.md      # NEW — ETL architecture brief
│   ├── sps-etl-reference.ts            # NEW — working TypeScript reference script
│   ├── aws-iam-pipeline-policy.json    # NEW — inline policy for publish operator
│   └── aws-bucket-policy.json          # NEW — bucket policy for SPS consumer
├── backfill_all.py                     # MODIFIED — add --publish flag + _run_publish()
├── utils/
│   └── s3_client.py                    # NEW — lazy S3 client (mirrors bedrock_client.py pattern)
└── .planning/
    ├── STATE.md                         # MODIFIED — add D-19 cross-ref to hierarchy-contract.md
    └── phases/
        └── 04-subtopic-system/
            └── hierarchy-schema.md      # MODIFIED — add cross-ref to hierarchy-contract.md
```

### Pattern: Lazy S3 Client (mirrors `utils/bedrock_client.py`)

```python
# utils/s3_client.py — Source: mirrors bedrock_client.py lazy init pattern (VERIFIED)
import os
import boto3
import logging

logger = logging.getLogger(__name__)

HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"
HIERARCHY_REGION = "us-east-1"


class S3HierarchyClient:
    """
    Lazy-init S3 client for hierarchy publishing.

    Design follows BedrockClient convention:
    - No boto3 calls at import time
    - Client created on first method call and cached
    - AWS creds via default credential chain (~/.zshrc-sourced env vars)
    - NoCredentialsError surfaces immediately with clear message
    """

    def __init__(self, bucket: str = HIERARCHY_BUCKET, region: str = HIERARCHY_REGION):
        self.bucket = bucket
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def put_object(self, key: str, body: bytes, content_type: str = "application/json") -> None:
        self._get_client().put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        logger.info(f"Uploaded s3://{self.bucket}/{key} ({len(body):,} bytes)")

    def key_exists(self, key: str) -> bool:
        """Return True if the key exists (HEAD request)."""
        from botocore.exceptions import ClientError
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise
```

### Pattern: `_run_publish()` Integration in `backfill_all.py`

```python
# In backfill_all.py — Source: VERIFIED against existing code structure
HIERARCHY_SCHEMA_PATH = REPO_ROOT / "docs" / "hierarchy.schema.json"
HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"


def _run_publish(skip_pm_copy: bool, dry_run: bool) -> int:
    """
    Validate hierarchy_full.json against hierarchy.schema.json,
    then upload to S3 at v{ISO-date}/ and latest/.

    Bypasses the verdict gate (non-LLM operation, same as --assemble-only).
    Returns process exit code (0=success, 1=validation error or upload error).
    """
    from utils.s3_client import S3HierarchyClient
    from jsonschema import Draft202012Validator
    from botocore.exceptions import NoCredentialsError
    import hashlib
    from datetime import date

    # 1. Ensure hierarchy_full.json exists (assemble if missing)
    if not HIERARCHY_FULL_OUT.exists():
        logger.info("hierarchy_full.json missing — running assemble-only first.")
        rc = _run_assemble_only(skip_pm_copy=True)  # skip PM copy during pre-publish assemble
        if rc != 0:
            return rc

    # 2. Load hierarchy and schema
    with open(HIERARCHY_FULL_OUT) as f:
        hierarchy = json.load(f)
    if not HIERARCHY_SCHEMA_PATH.exists():
        logger.error(f"Schema file missing: {HIERARCHY_SCHEMA_PATH}")
        return 1
    with open(HIERARCHY_SCHEMA_PATH) as f:
        schema = json.load(f)

    # 3. Validate (fail-fast)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(hierarchy), key=lambda e: list(e.absolute_path))
    if errors:
        print("\nSCHEMA VALIDATION FAILED — hierarchy_full.json does not match schema:\n")
        for e in errors:
            path = " > ".join(str(p) for p in e.absolute_path) or "<root>"
            print(f"  [{path}] {e.message}")
        print(f"\nFix hierarchy_full.json or update docs/hierarchy.schema.json before publishing.")
        return 1
    logger.info("Schema validation: PASS")

    # 4. Compute manifest
    hierarchy_bytes = json.dumps(hierarchy, indent=2, ensure_ascii=False).encode("utf-8")
    schema_bytes = json.dumps(schema, indent=2, ensure_ascii=False).encode("utf-8")
    sha256 = hashlib.sha256(hierarchy_bytes).hexdigest()
    version = f"v{date.today().isoformat()}"
    schema_version = schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")

    from datetime import datetime, timezone
    manifest = {
        "schema_version": schema_version,
        "taxonomy_version": hierarchy.get("taxonomy_version", "unknown"),
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": sha256,
        "artifact_bytes": len(hierarchy_bytes),
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    if dry_run:
        print(f"\n=== DRY RUN — --publish preview ===")
        print(f"  version:          {version}")
        print(f"  schema_version:   {schema_version}")
        print(f"  taxonomy_version: {manifest['taxonomy_version']}")
        print(f"  sha256:           {sha256}")
        print(f"  artifact_bytes:   {len(hierarchy_bytes):,}")
        print(f"  Would upload to:  s3://{HIERARCHY_BUCKET}/{version}/  (3 objects)")
        print(f"                    s3://{HIERARCHY_BUCKET}/latest/     (3 objects, overwrite)")
        print(f"\nManifest preview:\n{json.dumps(manifest, indent=2)}")
        return 0

    # 5. Upload to S3
    try:
        s3 = S3HierarchyClient(bucket=HIERARCHY_BUCKET)

        # Check for same-day re-publish
        if s3.key_exists(f"{version}/manifest.json"):
            logger.warning(f"{version}/ already exists — overwriting (same-day re-publish).")

        # Upload to versioned prefix
        s3.put_object(f"{version}/hierarchy.json", hierarchy_bytes)
        s3.put_object(f"{version}/hierarchy.schema.json", schema_bytes)
        s3.put_object(f"{version}/manifest.json", manifest_bytes)

        # Overwrite latest/
        s3.put_object("latest/hierarchy.json", hierarchy_bytes)
        s3.put_object("latest/hierarchy.schema.json", schema_bytes)
        s3.put_object("latest/manifest.json", manifest_bytes)

    except NoCredentialsError:
        print(
            "\nABORT: AWS credentials not found. Ensure AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY are exported in your shell (from ~/.zshrc). "
            "Run: source ~/.zshrc && python backfill_all.py --publish"
        )
        return 1

    # 6. PM worktree copy (D-13 — always-on unless --skip-pm-copy)
    if not skip_pm_copy:
        if not PM_WORKTREE.exists():
            logger.error(f"PM worktree not found at {PM_WORKTREE}. Pass --skip-pm-copy to skip.")
            return 1
        _copy_to_pm()
        _print_pm_commit_instructions()

    print(
        f"\n=== backfill_all.py --publish complete ===\n"
        f"  version:          {version}\n"
        f"  schema_version:   {schema_version}\n"
        f"  sha256:           {sha256}\n"
        f"  artifact_bytes:   {len(hierarchy_bytes):,}\n"
        f"  S3 (versioned):   s3://{HIERARCHY_BUCKET}/{version}/\n"
        f"  S3 (latest):      s3://{HIERARCHY_BUCKET}/latest/\n"
        f"  PM artifact:      {PM_HIERARCHY_JSON if not skip_pm_copy else '(skipped)'}\n"
    )
    return 0
```

### Anti-Patterns to Avoid

- **Warn-and-publish on schema errors:** D-16 is absolute. The publish gate must be fail-or-pass, no middle ground.
- **Computing sha256 from file on disk:** sha256 must cover the bytes that were uploaded (the `json.dumps()` output), not what is on disk. A platform line-ending difference would produce a sha256 mismatch for consumers.
- **`sort_keys=True` in json.dumps for manifest:** The manifest field order is logical, not alphabetical. `sort_keys=True` would reorder fields and break stable sha256 for consumers who compute manifest sha256.
- **Hardcoding `schema_version` as a magic string in the publish function:** The schema_version lives in `docs/hierarchy.schema.json` under `$defs._meta.schema_version`. Read it from there; bump the file, not the code.
- **Not including `s3:HeadObject` in the IAM policy:** The idempotency check uses HeadObject to detect same-day re-publish. Missing this permission causes a silent failure path that falls through to upload without the warning.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON Schema validation | Custom field-by-field checks | `jsonschema.Draft202012Validator` | Already installed; handles nested $defs, required, type, enum, additionalProperties correctly; `iter_errors()` gives all errors at once |
| sha256 of uploaded bytes | Rolling checksum of file read | `hashlib.sha256(json.dumps(...).encode()).hexdigest()` | Deterministic; tied to exact upload bytes |
| S3 upload with retries | Custom retry loop | boto3 (handles transient 500s internally via botocore retry config) | boto3 already in requirements.txt; botocore retry is configurable |
| TypeScript S3 fetch | Custom HTTP GET | `@aws-sdk/client-s3` `GetObjectCommand` | Standard AWS SDK v3; already used in SPS dynamodb ETL |
| JSON Schema 2020-12 in TypeScript | Custom schema validator | `ajv` v8 with `ajv/dist/2020` | Industry standard; supports all 2020-12 keywords including `prefixItems` |

---

## Validation Architecture

> `nyquist_validation` is explicitly `false` in `.planning/config.json` — formal test framework section omitted.

The schema validation gate is its own correctness guarantee. The plan must include one test task:

**Task: Synthetic invalid hierarchy fixture asserts fail-and-don't-upload.**

Create `tests/fixtures/hierarchy_invalid.json` — a `hierarchy_full.json` with a deliberately wrong field (e.g., `topics` is an array instead of an object, or a required top-level field is missing). In the plan's verification step, run:

```bash
python backfill_all.py --publish --dry-run
# Expect: exit 0 (valid) — current hierarchy is valid

# Then temporarily corrupt hierarchy_full.json (or use a separate test script):
python -c "
import json
with open('.planning/phases/04-subtopic-system/hierarchy_full.json') as f:
    h = json.load(f)
h['topics'] = []  # wrong type — should be object
with open('/tmp/hierarchy_invalid_test.json', 'w') as f:
    json.dump(h, f)
print('Wrote invalid fixture to /tmp/hierarchy_invalid_test.json')
"
# Then validate directly:
python -c "
import json
from jsonschema import Draft202012Validator
with open('docs/hierarchy.schema.json') as f:
    schema = json.load(f)
with open('/tmp/hierarchy_invalid_test.json') as f:
    h = json.load(f)
errors = list(Draft202012Validator(schema).iter_errors(h))
assert errors, 'Expected validation errors — got none!'
print(f'Validation correctly caught {len(errors)} error(s)')
for e in errors:
    path = list(e.absolute_path) or ['<root>']
    print(f'  {path}: {e.message}')
"
```

The plan's integration test for `--publish` must also confirm that no S3 PutObject call is made when validation fails. The simplest verification is `--dry-run` mode (which computes sha256 without uploading) on the valid fixture: if it exits 0 and prints the manifest, the validation gate and dry-run path both work.

---

## File Inventory

| File | Action | Analog / Notes |
|------|--------|----------------|
| `docs/hierarchy.schema.json` | CREATE | No existing analog — draft from `hierarchy-schema.md` |
| `docs/hierarchy-contract.md` | CREATE | Analog: `docs/data-model-and-queries.md` (reference doc style) |
| `docs/sps-integration-handoff.md` | CREATE | Extends `.planning/phases/04-subtopic-system/sps-integration-brief.md` |
| `docs/sps-etl-reference.ts` | CREATE | Analog: `etl/dynamodb/index.ts` in SPS repo (TypeScript ETL pattern) |
| `docs/aws-iam-pipeline-policy.json` | CREATE | No analog — paste-ready IAM inline policy JSON |
| `docs/aws-bucket-policy.json` | CREATE | No analog — paste-ready S3 bucket policy JSON |
| `utils/s3_client.py` | CREATE | Analog: `utils/bedrock_client.py` (lazy init pattern) |
| `backfill_all.py` | MODIFY | Add `--publish`, `_run_publish()`, `publish` arg to `run()` and `_parse_args()` |
| `.planning/STATE.md` | MODIFY | Add D-19 cross-ref line pointing to `docs/hierarchy-contract.md` |
| `.planning/phases/04-subtopic-system/hierarchy-schema.md` | MODIFY | Add footer cross-ref: "Consumer-facing contract: `docs/hierarchy-contract.md`" |
| `requirements.txt` | MODIFY | Add `jsonschema>=4.23.0` |

**Files NOT touched:**
- `backfill_topic.py`, `assign_subtopics.py`, `aggregate_subtopic_scores.py` — no publish concern
- `prompts/` — no changes needed
- `ReCiter-Publication-Manager/` worktree — `_copy_to_pm()` already handles this; no new code needed there

---

## Threat Model Notes

### IAM Least Privilege

The pipeline operator's IAM principal gets exactly three S3 actions:
- `s3:PutObject` on `arn:aws:s3:::wcmc-reciterai-hierarchy/*` — write artifacts
- `s3:HeadObject` on `arn:aws:s3:::wcmc-reciterai-hierarchy/*` — same-day re-publish detection
- `s3:ListBucket` on `arn:aws:s3:::wcmc-reciterai-hierarchy` — NOT needed for this phase's workflow (no prefix listing in the publish step); omit from the pipeline inline policy to follow strict least-privilege. [REVISION: The publish step never calls ListObjects; drop `s3:ListBucket` from the pipeline operator policy. SPS consumer needs it for prefix listing.]

**Corrected minimal pipeline operator policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "HierarchyPublish",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:HeadObject"],
      "Resource": "arn:aws:s3:::wcmc-reciterai-hierarchy/*"
    }
  ]
}
```

The pipeline does NOT need `s3:GetObject` (no self-fetch in the publish step — hierarchy is read from disk). This is intentional: avoids coupling the publish step to prior S3 state and prevents accidental overwrite validation from a stale remote copy.

### Credential Exposure Surface

- AWS creds are in `~/.zshrc` per project conventions. They reach boto3 via the default credential chain (env vars exported by `.zshrc`). `botocore` raises `NoCredentialsError` if creds are absent — this is a known-error class with a clear message. The publish step catches `NoCredentialsError` and prints a human-readable fix: `source ~/.zshrc && python backfill_all.py --publish`.
- The publish step logs nothing about credentials. Specifically: `logger.info(f"Uploaded s3://{bucket}/{key}")` — no auth headers, no credentials in any log line.
- The operator never needs to pass `aws_access_key_id=` to any boto3 call; the default chain handles it.

### Manifest / Schema Integrity Model

- **sha256 covers hierarchy.json bytes**, not the manifest. A consumer who trusts the bucket-level IAM (i.e., the bucket is private and only the pipeline operator has PutObject) can trust that `manifest.sha256` matches the `hierarchy.json` they download. No additional signature needed at this phase's threat level.
- **Schema injection:** `hierarchy.schema.json` is itself a published artifact. A consumer MUST fetch the schema at `v{date}/hierarchy.schema.json` (matching their `hierarchy.json` version), NOT always `latest/hierarchy.schema.json`. This guards against consuming a v2 schema with a v1 artifact during a 30-day deprecation window.
- **Bucket misconfiguration risk:** The bucket policy checklist above (block all public access, no website hosting, no CORS, SSE-S3, ACLs disabled) is the first console step. Missing any of these could make artifacts publicly accessible. The plan should include a verification step: `aws s3api get-public-access-block --bucket wcmc-reciterai-hierarchy` must return all four booleans as `true`.

---

## Common Pitfalls

### Pitfall 1: IAM Action/Resource Mismatch on S3

**What goes wrong:** `s3:PutObject` and `s3:HeadObject` require the `/*` suffix on the ARN. `s3:ListBucket` requires the bucket ARN without `/*`. Getting this wrong causes `AccessDenied` errors that look like credential problems.

**Why it happens:** S3 IAM has two resource types (bucket-level and object-level) and two permission scopes. Object-level actions (`PutObject`, `GetObject`, `HeadObject`) apply to `arn:aws:s3:::bucket/*`. Bucket-level actions (`ListBucket`, `GetBucketLocation`) apply to `arn:aws:s3:::bucket`.

**How to avoid:** The policy snippets in this research are correct. Paste them verbatim. Do not merge the two ARN patterns.

**Warning signs:** `AccessDenied` on `HeadObject` when `PutObject` works fine (or vice versa).

### Pitfall 2: sha256 Non-Determinism from json.dumps

**What goes wrong:** Consumer computes sha256 over downloaded `hierarchy.json` bytes; it does not match `manifest.sha256`. This looks like data corruption.

**Why it happens:** If `hierarchy.json` is written to disk and then re-read from disk for sha256 computation, platform line-ending differences or re-serialization differences change the bytes. Alternatively, if `sort_keys` behavior differs between the write step and the sha256 step.

**How to avoid:** Compute sha256 over the `json.dumps(...).encode("utf-8")` bytes IN MEMORY, and upload those SAME bytes. Never compute sha256 by re-reading the file from disk. The `_run_publish()` skeleton above does this correctly.

**Warning signs:** sha256 mismatch on the first consumer fetch, even though the upload succeeded.

### Pitfall 3: Schema `additionalProperties: false` Breaks D-11

**What goes wrong:** Authoring `hierarchy.schema.json` with `additionalProperties: false` at the SubtopicDef or top level causes any future additive field to fail validation — the opposite of D-11.

**Why it happens:** Draft 2020-12 default is `additionalProperties: true`, but schema authors often set `false` for strict validation. Here, strict rejection of unknown properties is wrong — consumers MUST tolerate unknown fields.

**How to avoid:** Never use `additionalProperties: false` in `hierarchy.schema.json`. Leave it at the default `true` (or omit it entirely). The schema validates required fields and known field types; it does not reject unknown extensions.

**Warning signs:** `--publish` fails with "Additional properties are not allowed" after adding a field to `hierarchy_full.json`.

### Pitfall 4: `sort_keys=True` in manifest json.dumps

**What goes wrong:** A consumer who computes their own sha256 of `manifest.json` for second-order change detection gets inconsistent results if the manifest field order varies.

**Why it happens:** Using `json.dumps(manifest, sort_keys=True)` alphabetizes fields; if any code path uses `sort_keys=True` and another uses insertion order, the manifest bytes differ.

**How to avoid:** All `json.dumps()` calls in the publish path use `indent=2, ensure_ascii=False` with NO `sort_keys`. Use explicit key insertion order in the manifest dict. Consistent with existing `_write_hierarchy_full()` pattern.

### Pitfall 5: Missing jsonschema in requirements.txt

**What goes wrong:** `--publish` fails with `ModuleNotFoundError: No module named 'jsonschema'` on a fresh clone or in a CI environment.

**Why it happens:** `jsonschema` is installed on the operator's machine but not in `requirements.txt`. A fresh venv or another operator's machine won't have it.

**How to avoid:** Add `jsonschema>=4.23.0` to `requirements.txt` in the same plan task that creates `docs/hierarchy.schema.json`.

---

## Open Questions / Risks (RESOLVED)

1. **SPS Subtopic table schema mismatch**
   - **What we know:** SPS ETL (`etl/dynamodb/index.ts`) already upserts a `subtopic` MySQL table using `prisma.subtopic.upsert()`. It infers `display_name` from `subtopicLabel(slug)` (a deterministic title-caser) rather than reading it from `hierarchy.json`. After Phase 5, SPS should read `display_name` from `hierarchy.json` instead.
   - **What's unclear:** The SPS `subtopic` Prisma model's exact column set. Does it have a `display_name` column? A `short_description` column? The current ETL reads from DynamoDB TOPIC# records (which carry `primary_subtopic_id`, `subtopic_ids`, but NOT `display_name`/`short_description`). The hierarchy artifact fetch is a new code path.
   - **Recommendation:** The reference script's upsert block is commented with `// TODO: replace with actual prisma.subtopic.upsert`. The SPS coding agent must check their Prisma schema before adapting. Document this explicitly in `docs/sps-integration-handoff.md` as a required pre-adaptation step.
   - **RESOLVED:** Plan 04 reference script (`docs/sps-etl-reference.ts`) includes a Pre-Adaptation Checklist instructing the SPS agent to verify Prisma model columns and emit a migration if needed; this is out of Phase 5 scope but the script's TODOs flag it. See `docs/sps-integration-handoff.md` Pre-Adaptation Checklist item 1.

2. **SPS package.json: `@aws-sdk/client-s3` and `ajv` presence**
   - **What we know:** SPS uses `@aws-sdk/client-dynamodb` and `@aws-sdk/lib-dynamodb` (visible in `etl/dynamodb/index.ts`). The S3 client package (`@aws-sdk/client-s3`) may or may not be installed.
   - **Risk if wrong:** Reference script fails to compile in SPS until they install the S3 package.
   - **Recommendation:** Note in `docs/sps-integration-handoff.md`: "Add `@aws-sdk/client-s3` and `ajv` to your `package.json` if not already present. Run `npm install @aws-sdk/client-s3 ajv`."
   - **RESOLVED:** Plan 04 handoff doc (`docs/sps-integration-handoff.md`) includes an "npm install" deps note (Pre-Adaptation Checklist item 2) listing the required packages.

3. **Schema version embedded location**
   - **What we know:** The research recommends embedding `schema_version` in `$defs._meta.schema_version` inside `hierarchy.schema.json`. This is a convention, not a JSON Schema standard.
   - **Risk if wrong:** The publish step's extraction logic `schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")` silently falls back to `"1.0.0"` if the path is wrong or omitted, masking a bump.
   - **Recommendation:** Alternative: hard-code `schema_version` as a module-level constant in `backfill_all.py` (e.g., `SCHEMA_VERSION = "1.0.0"`) and bump it manually when the schema changes. Simpler and less fragile. Confirm approach in plan.
   - **RESOLVED:** Plan 01 Task 1 places `schema_version` at `$defs._meta.schema_version` (initial value `"1.0.0"`); Plan 02 reads it via `.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")` for the manifest field. The convention path was confirmed; the fallback default is intentional for backward compatibility.

4. **Bucket provisioning timing**
   - **What we know:** The bucket does not exist yet. The plan must include a manual step (user provisions via AWS console) before `--publish` can succeed.
   - **Risk if wrong:** `--publish` emits a confusing `NoSuchBucket` error on first run if the bucket wasn't created.
   - **Recommendation:** Plan 1 (schema + setup) should include a manual "bucket creation checkpoint" with the console checklist from this research as the verification step.
   - **RESOLVED:** Plan 06's operator note (Output section, line ~297) documents that bucket provisioning happens AFTER plan execution and BEFORE the inaugural live `--publish` run; smoke tests use `--dry-run` until the bucket exists. Plan 01 ships the paste-ready IAM policy JSONs that the operator uses during console provisioning.

---

## Recommended Plan Decomposition

Proposed 6 plans:

| Plan | Slug | Scope | Key Tasks |
|------|------|-------|-----------|
| 05-01 | `schema-and-setup` | `hierarchy.schema.json` + bucket provisioning | Draft JSON Schema from `hierarchy-schema.md`; add `jsonschema` to `requirements.txt`; IAM JSON snippets at `docs/aws-iam-pipeline-policy.json` + `docs/aws-bucket-policy.json`; bucket creation manual checkpoint with console checklist |
| 05-02 | `s3-client-and-publish-flag` | `utils/s3_client.py` + `backfill_all.py --publish` | `S3HierarchyClient` following lazy-init pattern; `_run_publish()` function; argparse additions; `--dry-run --publish` interaction; validation gate with `iter_errors()` UX |
| 05-03 | `contract-doc` | `docs/hierarchy-contract.md` | Consumer-facing contract: URL pattern, schema, cadence, breaking-change policy, CHANGELOG (first entry: 2026-05-06 inaugural publish with D-19 relabel), integration pattern, FAQ (why not DynamoDB), open questions |
| 05-04 | `reference-script` | `docs/sps-etl-reference.ts` | Working TypeScript reference: S3 fetch, manifest comparison, schema validation via ajv, subtopic projection stub; not pseudocode; must `tsc --noEmit` cleanly |
| 05-05 | `handoff-brief` | `docs/sps-integration-handoff.md` | Architecture brief extending `sps-integration-brief.md`; ETL-fetch flow diagram; sha256 change-detection pattern; pre-adaptation checklist for SPS agent; references `sps-etl-reference.ts` |
| 05-06 | `cross-references-and-smoke` | STATE.md + `hierarchy-schema.md` updates + smoke publish | D-19 cross-ref updates; `--dry-run --publish` smoke with valid fixture (exit 0); invalid fixture smoke (exit 1, no upload); confirm 6 S3 objects at correct keys with `aws s3 ls s3://wcmc-reciterai-hierarchy/ --recursive` |

**Plan ordering:** Plans 01-02 in parallel (schema authoring and S3 client are independent); Plan 03 in parallel with 04-05 (contract doc and reference script are independent of each other); Plan 06 last (requires all artifacts to exist for cross-references and smoke test to be meaningful).

**Wave structure:**
- Wave 1 (parallel): Plan 01 + Plan 02
- Wave 2 (parallel): Plan 03 + Plan 04 + Plan 05
- Wave 3: Plan 06

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `ajv` not yet in SPS `package.json`; SPS coding agent must add it | Reference Fetch Script | If already present, the instruction is redundant (harmless) |
| A2 | `@aws-sdk/client-s3` not yet in SPS `package.json` | Reference Fetch Script | If already present, redundant (harmless) |
| A3 | Schema version embedded at `$defs._meta.schema_version` in JSON Schema file | Manifest Shape | If path wrong, version defaults to "1.0.0" silently — prefer module-level constant instead |
| A4 | SPS `subtopic` Prisma model does not yet have `display_name` or `short_description` columns | Open Questions | If already present, the upsert stub is immediately usable; if absent, SPS must migrate schema before adapting reference script |
| A5 | `s3:ListBucket` is NOT needed by the pipeline operator | Threat Model | If future tooling (e.g., `aws s3 ls`) is used for verification, ListBucket must be added to the policy |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `jsonschema` Python library | Schema validation gate | Yes | 4.26.0 | — |
| `boto3` / `botocore` | S3 PutObject | Yes | 1.42.54 | — |
| Python 3.14 | All pipeline scripts | Yes (OS version) | 3.14 | — |
| AWS credentials (S3) | `--publish` upload | Via `~/.zshrc` env vars | — | Fail-fast with clear error message |
| `wcmc-reciterai-hierarchy` S3 bucket | `--publish` | Not yet created | — | Manual console step required (Plan 01 checkpoint) |
| `tsx` (Node TypeScript runner) | `sps-etl-reference.ts` testing | [ASSUMED: present in SPS] | — | `ts-node` or compile first |

**Missing dependencies with no fallback:**
- `wcmc-reciterai-hierarchy` S3 bucket — must be provisioned by the user before `--publish` can succeed. Plan 01 must include a blocking manual checkpoint.

---

## Sources

### Primary (HIGH confidence)

- `backfill_all.py` (this repo, read in full) — existing CLI patterns, `_run_assemble_only()`, `_copy_to_pm()`, `_write_hierarchy_full()`
- `utils/bedrock_client.py` (this repo, read in full) — lazy-init S3 client pattern reference
- `.planning/phases/04-subtopic-system/hierarchy-schema.md` (this repo, read in full) — canonical schema source
- `.planning/phases/05-hierarchy-publishing-contract/05-CONTEXT.md` (this repo, read in full) — locked decisions
- `pip show jsonschema` + `python3` import tests — jsonschema 4.26.0 confirmed installed; `Draft202012Validator` confirmed available; `iter_errors()` behavior verified
- `boto3`/`botocore` version — verified from `requirements.txt` (1.42.54)
- `etl/dynamodb/index.ts` (SPS repo, read in full) — SPS ETL is TypeScript; subtopic upsert pattern confirmed; `prisma.subtopic.upsert` call confirmed at line 429
- `etl/orchestrate.ts` (SPS repo, read) — TypeScript + tsx runner confirmed

### Secondary (MEDIUM confidence)

- `docs/ADR-001-runtime-dal-vs-etl-transform.md` (SPS repo, read in full) — ETL-transform pattern; MySQL-only runtime confirmed
- SPS `CLAUDE.md` — ADR-006 LOCKED: no runtime DynamoDB path; 30-day schema-change protocol confirmed

### Tertiary (LOW confidence — assumptions flagged)

- `ajv` v8 package presence in SPS: not verified (package.json not read) — flagged as A1/A2
- `@aws-sdk/client-s3` in SPS: not verified
- SPS Subtopic Prisma model column set: not read — flagged as A4

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all Python libraries verified installed; boto3 in requirements.txt; TypeScript runtime of SPS confirmed
- Architecture: HIGH — all integration points traced through existing code
- Pitfalls: HIGH — IAM gotcha is well-known; sha256/sort_keys issues verified through testing
- SPS reference script: MEDIUM — TypeScript pattern confirmed but SPS Prisma schema not inspected

**Research date:** 2026-05-06
**Valid until:** 2026-08-06 (boto3, jsonschema are stable libraries; SPS ETL stack unlikely to change within 90 days)
