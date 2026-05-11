# Phase 5: Hierarchy Publishing Contract — Pattern Map

**Mapped:** 2026-05-06
**Files analyzed:** 11 (8 new, 3 modified)
**Analogs found:** 8 / 11 (3 files have no close codebase analog — use RESEARCH.md patterns directly)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `utils/s3_client.py` | AWS client wrapper | request-response | `utils/bedrock_client.py` | exact (same lazy-init + boto3 pattern) |
| `backfill_all.py` (MODIFY) | CLI orchestrator | batch + file-I/O | `backfill_all.py` itself (existing flags) | exact (extends existing argparse + `_run_*` pattern) |
| `docs/hierarchy.schema.json` | JSON Schema artifact | file-I/O | `hierarchy-schema.md` (source of truth) | partial (schema generated from this doc) |
| `docs/hierarchy-contract.md` | contract doc | — | `docs/data-model-and-queries.md` | role-match (reference doc, same h1/table/section style) |
| `docs/sps-integration-handoff.md` | handoff brief | — | `.planning/phases/04-subtopic-system/sps-integration-brief.md` | role-match (same audience/format, extends that doc) |
| `docs/sps-etl-reference.ts` | reference script | request-response | SPS `etl/dynamodb/index.ts` (external repo) | role-match (same TS ETL pattern, runner is `tsx`) |
| `docs/aws-iam-pipeline-policy.json` | IAM config | — | none | no analog |
| `docs/aws-bucket-policy.json` | IAM config | — | none | no analog |
| `requirements.txt` (MODIFY) | config | — | `requirements.txt` | exact (same file, additive pin) |
| `.planning/STATE.md` (MODIFY) | planning doc | — | `.planning/STATE.md` | exact (cross-ref addition only) |
| `.planning/phases/04-subtopic-system/hierarchy-schema.md` (MODIFY) | schema doc | — | `docs/taxonomy-methodology.md` | partial (same footer cross-ref style) |

---

## Pattern Assignments

### `utils/s3_client.py` (AWS client wrapper, request-response)

**Analog:** `utils/bedrock_client.py`

**Imports pattern** (`bedrock_client.py` lines 29–36):
```python
import boto3
import json
import re
import time
import logging
import os

logger = logging.getLogger(__name__)
```

**Lazy-init class skeleton** (`bedrock_client.py` lines 43–95 — the exact structure to mirror):
```python
class BedrockClient:
    def __init__(self, region: str = None):
        self.region = region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        self._client = None  # Lazy init — set on first call to _get_client()

    def _get_client(self):
        """Get or create the boto3 client (lazy initialization)."""
        if self._client is None:
            self._client = boto3.client(
                'bedrock-runtime',
                region_name=self.region,
            )
        return self._client
```

**Security docstring pattern** (`bedrock_client.py` lines 53–58):
```python
    """
    ...
    Security (T-01-02):
    - AWS credentials via default credential chain (IAM role or ~/.aws/credentials).
    - Never passed as parameters or logged.
    ...
    """
```

**Apply to `utils/s3_client.py`:** Replace `'bedrock-runtime'` with `'s3'`; replace constructor defaults with `HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"` and `HIERARCHY_REGION = "us-east-1"` constants; store bucket on `self.bucket`; keep `self._client = None` and `_get_client()` pattern verbatim. Add `put_object()` and `key_exists()` methods following the same guard pattern (`if self._client is None`). Never call `boto3.client()` at module import time or in `__init__`.

---

### `backfill_all.py` — `--publish` flag addition (CLI orchestrator, batch + file-I/O)

**Analog:** `backfill_all.py` existing flags and `_run_assemble_only()` function

**Argparse flag style** (`backfill_all.py` lines 683–743 — copy this exact docstring pattern):
```python
parser.add_argument(
    "--assemble-only",
    action="store_true",
    help=(
        "Skip the per-topic Pass 1/2/3 loop AND see-also generation; "
        "only re-assemble hierarchy_full.json from the existing "
        "per-topic augmented drafts. ... Does not call any LLM. Bypasses the D-20 verdict "
        "gate. Honors --skip-pm-copy."
    ),
)
```

`--publish` slots in here with the same `action="store_true"` + multi-line `help=` parenthesized string. New argparse entry goes between `--assemble-only` and `return parser.parse_args()`.

**`run()` signature pattern — short-circuit before verdict gate** (`backfill_all.py` lines 500–511):
```python
def run(
    dry_run: bool,
    only_verdict_check: bool,
    skip_pm_copy: bool,
    continue_on_error: bool,
    skip_all_reviews: bool = False,
    assemble_only: bool = False,
) -> int:
    # Assemble-only path: short-circuit before the verdict gate. Assembly is a
    # non-LLM operation; the D-20 verdict gate exists to guard LLM spend.
    if assemble_only:
        return _run_assemble_only(skip_pm_copy=skip_pm_copy)
```

Add `publish: bool = False` parameter in the same position as `assemble_only`; add the short-circuit block immediately after `assemble_only` check:
```python
    if publish:
        return _run_publish(skip_pm_copy=skip_pm_copy, dry_run=dry_run)
```

**`__main__` call pattern** (`backfill_all.py` lines 747–756):
```python
if __name__ == "__main__":
    args = _parse_args()
    sys.exit(run(
        dry_run=args.dry_run,
        only_verdict_check=args.only_verdict_check,
        skip_pm_copy=args.skip_pm_copy,
        continue_on_error=args.continue_on_error,
        skip_all_reviews=args.skip_all_reviews,
        assemble_only=args.assemble_only,
    ))
```

Add `publish=args.publish` in the same call; add `args.publish` via `parser.parse_args()`.

**`_run_assemble_only()` as structural template for `_run_publish()`** (`backfill_all.py` lines 426–497):
- Function starts with a docstring explaining what it bypasses and why (verdict gate = non-LLM operation)
- Returns `int` (process exit code: 0 = success, 1 = error)
- Opens `HIERARCHY_FULL_OUT` with `json.load(f)`
- Calls `_copy_to_pm()` and `_print_pm_commit_instructions()` unless `skip_pm_copy`
- Ends with a multi-line `print(f"\n=== backfill_all.py --{flag} complete ===\n  ...")` summary block

**JSON write convention** (`backfill_all.py` lines 344–349):
```python
def _write_hierarchy_full(hierarchy: dict) -> Path:
    HIERARCHY_FULL_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(HIERARCHY_FULL_OUT, "w") as f:
        json.dump(hierarchy, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {HIERARCHY_FULL_OUT}")
    return HIERARCHY_FULL_OUT
```

`json.dump(..., indent=2, ensure_ascii=False)` — always this exact call signature for all JSON writes (manifest, schema bytes serialized to bytes via `.encode("utf-8")` after `json.dumps(..., indent=2, ensure_ascii=False)`). `sort_keys` is NEVER passed (field insertion order is canonical).

**PM copy pattern** (`backfill_all.py` lines 382–402):
```python
def _copy_to_pm() -> Path:
    PM_HIERARCHY_JSON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HIERARCHY_FULL_OUT, PM_HIERARCHY_JSON)
    logger.info(f"Copied hierarchy to {PM_HIERARCHY_JSON}")
    return PM_HIERARCHY_JSON
```

`_run_publish()` calls `_copy_to_pm()` and `_print_pm_commit_instructions()` unchanged — do not alter these functions, only add the S3 upload before them.

**Constants block** (`backfill_all.py` lines 94–114):
```python
REPO_ROOT = Path(__file__).resolve().parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "04-subtopic-system"
HIERARCHY_FULL_OUT = PHASE_DIR / "hierarchy_full.json"
PM_WORKTREE = REPO_ROOT / "ReCiter-Publication-Manager"
PM_HIERARCHY_JSON = PM_WORKTREE / "controllers" / "chatbot" / "hierarchy.json"
```

Add new constants after these lines:
```python
HIERARCHY_SCHEMA_PATH = REPO_ROOT / "docs" / "hierarchy.schema.json"
HIERARCHY_BUCKET = "wcmc-reciterai-hierarchy"
```

---

### `docs/hierarchy.schema.json` (JSON Schema artifact, file-I/O)

**No close codebase analog** — first JSON Schema file in this repo.

**Source of truth:** `.planning/phases/04-subtopic-system/hierarchy-schema.md` — the JSON Schema must match this document's TypeScript interface definitions character-for-character.

**Key constraints from RESEARCH.md (verified):**
- `"$schema": "https://json-schema.org/draft/2020-12/schema"` — Draft 2020-12
- `additionalProperties` omitted everywhere (defaults to `true`) — enforces D-11
- `prefixItems` (not `items`) for arrays per Draft 2020-12
- `schema_version` embedded at `$defs._meta.schema_version` (or as a module-level constant in `backfill_all.py` — RESEARCH.md recommends the simpler module-constant approach for a single-operator workflow; planner should decide)
- Top-level required fields: `version`, `generated_at`, `taxonomy_version`, `excluded_topics`, `topics`, `see_also`
- `SubtopicDef` required: `id`, `label`, `description`, `display_name`, `short_description`, `activity_count` (integer), `total_weight` (number)

**No-side-effects discipline** (from `prompts/subtopic_discovery.py` lines 1–17):
```python
"""
...
No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""
```

The schema JSON file is purely declarative — no code. The discipline that applies is: the file is authored once and mutated only when schema bumps occur (explicit CHANGELOG entry required per D-10).

---

### `docs/hierarchy-contract.md` (contract doc)

**Analog:** `docs/data-model-and-queries.md`

**H1 heading style** (`data-model-and-queries.md` line 1):
```markdown
# Data Model & Query Architecture
```

Mirror with: `# Hierarchy Artifact — Consumer Contract`

**Table style** (`data-model-and-queries.md` lines 7–16):
```markdown
| Type | PK Pattern | SK Pattern | Count | Source |
|------|-----------|------------|-------|--------|
| TOPIC# | `TOPIC#{topic_id}` | `SCORE#...` | ~78K | LLM-scored |
```

Use same pipe-table format with code-fenced values for paths and keys.

**Section heading style** (`data-model-and-queries.md` line 36, `taxonomy-methodology.md` lines 1–17):
```markdown
## Supported Query Patterns
### Single-Dimension Queries
#### "Who works on [topic]?"
```

For the contract doc: `## Artifact URLs`, `## Schema`, `## Cadence`, `## Breaking-Change Policy`, `## Integration Pattern`, `## Changelog`, `## FAQ`.

**Tone** — both existing docs use:
- Present tense, active voice ("The system provides", "Access:", "Returns:", "Strength:", "Weakness:")
- Concrete examples with actual values (DynamoDB key patterns, PMIDs, etc.)
- Bullet lists for enumerated rules; tables for structured comparisons
- No second-person "you should" — imperative ("Consumers MUST", "Do NOT use")

The contract doc MUST include a `## Changelog` section with the inaugural 2026-05-06 entry (D-19 relabel re-publish) and a `## FAQ` section with the "Why not DynamoDB?" entry (per CONTEXT.md deferred section).

---

### `docs/sps-integration-handoff.md` (handoff brief)

**Analog:** `.planning/phases/04-subtopic-system/sps-integration-brief.md`

**Header pattern** (`sps-integration-brief.md` lines 1–11):
```markdown
# SPS Integration Brief — `display_name` + `short_description` Subtopic Card Fields

**Audience:** Coding agent working in the Scholars Profile System (Cornell's VIVO
replacement, separate codebase).

**Authoritative schema:** `hierarchy-schema.md` rule **D-19** in this directory.

**Status:** Upstream pipeline change shipped 2026-05-06. ...
```

Mirror with:
```markdown
# SPS Integration Handoff — Hierarchy Artifact ETL

**Audience:** Coding agent working in the Scholars Profile System (`wcmc-its/Scholars-Profile-System`).

**Authoritative contract:** `docs/hierarchy-contract.md` in `ReciterAI -ReCiter-Integration`.

**Status:** S3 artifact published 2026-05-06. Reference fetch script at `docs/sps-etl-reference.ts`.
```

**Background + What Changed section style** (`sps-integration-brief.md` lines 14–27):
```markdown
## Background

SPS consumes a `hierarchy.json` artifact produced by the ReCiter AI pipeline. ...

## What changed upstream (data model)

`SubtopicDef` (snake_case in JSON) gained two fields:

```ts
interface SubtopicDef { ... }
```

Use the same `## Background` → `## What Changed` → `## What You Need to Change` → `## Implementation Checklist` section flow, adapted for the ETL-fetch architecture.

---

### `docs/sps-etl-reference.ts` (reference script, request-response)

**No in-repo analog** — the only TypeScript in this repo is in the PM worktree.

**External analog:** SPS `etl/dynamodb/index.ts` (TypeScript ETL, `tsx` runner, AWS SDK v3 pattern).

**SPS SDK import pattern** (from RESEARCH.md verified observation):
```typescript
import { S3Client, GetObjectCommand, HeadObjectCommand } from "@aws-sdk/client-s3";
```

**Module-level env-var pattern** (mirrors SPS ETL convention per RESEARCH.md):
```typescript
const BUCKET = process.env.HIERARCHY_BUCKET ?? "wcmc-reciterai-hierarchy";
const REGION = process.env.AWS_DEFAULT_REGION ?? "us-east-1";
```

**Runner pattern** (SPS `etl/orchestrate.ts` invokes via `node --import tsx/esm`):
```typescript
main().catch((err) => { console.error(err); process.exit(1); });
```

The reference script MUST compile with `tsc --noEmit`. It must NOT be pseudocode — the S3 fetch, manifest comparison, and ajv validation sections must be working code. The MySQL upsert section is the only `// TODO` stub (SPS coding agent adapts it to their Prisma schema).

---

### `docs/aws-iam-pipeline-policy.json` and `docs/aws-bucket-policy.json` (IAM config)

**No codebase analog** — these are pure paste-ready JSON artifacts.

**Content is fully specified in RESEARCH.md** (lines 281–323). The corrected minimal pipeline operator policy (RESEARCH.md lines 879–893) supersedes the earlier draft at lines 281–299.

Use minimal pipeline policy (2 actions: `s3:PutObject` + `s3:HeadObject` on `arn:aws:s3:::wcmc-reciterai-hierarchy/*` only — `s3:ListBucket` is NOT included per threat model section).

---

### `requirements.txt` (MODIFY)

**Analog:** `requirements.txt` itself (lines 1–4):
```
boto3>=1.42.0
tqdm>=4.67.0
pymysql>=1.1.0
sqlalchemy>=2.0.0
```

Add `jsonschema>=4.23.0` on a new line following the same `package>=version` format. No other changes.

---

## Shared Patterns

### Lazy AWS Client Init
**Source:** `utils/bedrock_client.py` lines 68–95
**Apply to:** `utils/s3_client.py`
```python
def __init__(self, region: str = None):
    self.region = region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    self._client = None  # Lazy init — set on first call to _get_client()

def _get_client(self):
    if self._client is None:
        self._client = boto3.client('s3', region_name=self.region)
    return self._client
```
No boto3 calls at import time. No credentials passed as parameters. Client created on first method call and cached on `self._client`.

### NoCredentialsError Handling
**Source:** `utils/bedrock_client.py` pattern (implicit — `botocore` surfaces this on first API call)
**Apply to:** `backfill_all.py:_run_publish()` — wrap the S3 upload block in:
```python
from botocore.exceptions import NoCredentialsError
try:
    ...  # S3 PutObject calls
except NoCredentialsError:
    print(
        "\nABORT: AWS credentials not found. Ensure AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY are exported in your shell (from ~/.zshrc). "
        "Run: source ~/.zshrc && python backfill_all.py --publish"
    )
    return 1
```

### JSON Serialization Convention
**Source:** `backfill_all.py` lines 346–348
**Apply to:** All JSON writes in `_run_publish()` (manifest, hierarchy bytes for upload, schema bytes for upload)
```python
json.dump(obj, f, indent=2, ensure_ascii=False)          # for file writes
json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")  # for S3 body bytes
```
`sort_keys` is NEVER passed. Python 3.7+ dict insertion order is the canonical field order.

### `_run_*()` Function Return Convention
**Source:** `backfill_all.py` lines 426–497 (`_run_assemble_only`)
**Apply to:** `_run_publish()` must follow the same contract:
- Returns `int` (exit code: 0 = success, 1 = error)
- Ends with a structured `print(f"\n=== backfill_all.py --{flag} complete ===\n  ...")` block
- Uses `logger.info()` for progress; `logger.error()` for fatal conditions; `logger.warning()` for overwrite conditions

### Logging Setup (module-level, not in functions)
**Source:** `backfill_all.py` lines 84–90
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
```
`utils/s3_client.py` uses `logger = logging.getLogger(__name__)` only — no `basicConfig` call (that belongs in the script entry point).

### No-Side-Effects Module Discipline
**Source:** `prompts/subtopic_discovery.py` lines 1–17 (module docstring)
**Apply to:** `utils/s3_client.py` docstring — state explicitly: "No boto3 calls at import time. Client created lazily on first method call."

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `docs/hierarchy.schema.json` | JSON Schema | file-I/O | First JSON Schema file in this repo — draft directly from `hierarchy-schema.md` per RESEARCH.md section "hierarchy.schema.json Shape" |
| `docs/aws-iam-pipeline-policy.json` | IAM policy | — | No IAM JSON files exist in this repo — use paste-ready snippets from RESEARCH.md lines 281–298 (corrected version at lines 879–893) |
| `docs/aws-bucket-policy.json` | bucket policy | — | No S3 bucket policies in this repo — use RESEARCH.md lines 303–323 |

---

## Metadata

**Analog search scope:** `utils/`, `docs/`, `prompts/`, `backfill_all.py`, `requirements.txt`, `.planning/phases/04-subtopic-system/`
**Files scanned:** 9 (bedrock_client.py, backfill_all.py, requirements.txt, subtopic_discovery.py, data-model-and-queries.md, taxonomy-methodology.md, sps-integration-brief.md, hierarchy-schema.md, sps-integration-brief.md)
**Pattern extraction date:** 2026-05-06
