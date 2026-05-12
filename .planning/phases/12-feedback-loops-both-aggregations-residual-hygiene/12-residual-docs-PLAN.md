---
phase: 12-feedback-loops-both-aggregations-residual-hygiene
plan: residual-docs
type: execute
wave: 1
depends_on: []
files_modified:
  - docs/sensitive-topic-exclusion.md
  - GETTING_STARTED.md
autonomous: true
requirements:
  - G-24
  - G-34
  - D-19
tags: [docs, residual-hygiene, getting-started, sensitive-topic]
must_haves:
  truths:
    - "docs/sensitive-topic-exclusion.md exists and documents: where sensitive patterns live (DynamoDB SPOTLIGHT_CONFIG#sensitive_tags, NOT source), how the gate (spotlight/sensitive_gate.py) reads them, the SPOT-08 fail-closed invariant, and the rationale for DDB-resident patterns"
    - "GETTING_STARTED.md has an IAM Policy section that links docs/aws-iam-pipeline-policy.json + docs/aws-iam-pipeline-policy-artifacts.json"
    - "Both docs are committed in the same plan so the §11 residual-hygiene writing tasks ship together"
  artifacts:
    - path: "docs/sensitive-topic-exclusion.md"
      provides: "G-24 documentation: sensitive-topic exclusion rationale + DDB-resident pattern + SPOT-08 invariant"
      contains: "SPOTLIGHT_CONFIG#sensitive_tags"
    - path: "GETTING_STARTED.md"
      provides: "G-34 IAM Policy section pointing operators to the two policy JSONs"
      contains: "aws-iam-pipeline-policy"
  key_links:
    - from: "docs/sensitive-topic-exclusion.md"
      to: "spotlight/sensitive_gate.py"
      via: "doc references the source file as the implementation site"
      pattern: "sensitive_gate"
    - from: "GETTING_STARTED.md"
      to: "docs/aws-iam-pipeline-policy.json + docs/aws-iam-pipeline-policy-artifacts.json"
      via: "doc references the two policy JSONs by relative path"
      pattern: "aws-iam-pipeline-policy"
---

<objective>
Ship two §11 residual hygiene items that are pure writing tasks — no code change. G-24 documents the sensitive-topic exclusion design (where patterns live, why DDB-resident, what the fail-closed invariant means). G-34 adds an IAM Policy section to GETTING_STARTED.md that links the two existing policy JSONs so new operators don't have to discover them.

Purpose: per CONTEXT D-29, the four mechanical/writing items ship in parallel (this plan), separately from the engineering long-poles (aggregations, feedback consumer, G-36, G-37). This plan is the writing-task subset.

Output: two new/extended documentation files.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-CONTEXT.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md
@.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-PATTERNS.md

<interfaces>
<!-- G-24 facts from RESEARCH F-6 + CONTEXT -->

Sensitive-topic patterns live in DynamoDB under PK `SPOTLIGHT_CONFIG#sensitive_tags`. They are NOT hardcoded in source. The implementation gate is `spotlight/sensitive_gate.py`. SPOT-08 is the fail-closed invariant — if the gate cannot retrieve the patterns (DDB error, missing row), it MUST refuse to publish rather than allow through. Phase 12 G-24 documents this design; it does NOT change the code.

<!-- G-34 facts -->

Two existing files (verified in repo today): docs/aws-iam-pipeline-policy.json (the pipeline-runtime policy) and docs/aws-iam-pipeline-policy-artifacts.json (the artifacts-bucket policy). GETTING_STARTED.md has no IAM section as of pre-Phase-12.

<!-- Analog -->

PATTERNS.md "docs/sensitive-topic-exclusion.md" maps to docs/topic-subtopic-assignment.md as the closest analog for tone and depth: developer-oriented prose, code references with file:line citations, rationale paragraphs.

PATTERNS.md "GETTING_STARTED.md" — self (existing file); add a new section toward the end of the file but before any "Troubleshooting" / "FAQ" section if one exists, otherwise append.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Write docs/sensitive-topic-exclusion.md (G-24)</name>
  <files>docs/sensitive-topic-exclusion.md</files>
  <read_first>
    - spotlight/sensitive_gate.py FULL file (need: how patterns are fetched, what the SPOT-08 invariant is named/asserted, exact DDB PK string, error-handling paths)
    - docs/topic-subtopic-assignment.md (tone and structure analog — same documentation register, same use of code citations)
    - .planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-RESEARCH.md F-6 finding (DDB-resident patterns confirmation)
    - .planning/phases/06-spotlight-pipeline*/*-SUMMARY.md if such files exist — context on the original sensitive-gate design rationale
  </read_first>
  <action>
    Create `docs/sensitive-topic-exclusion.md` with the following structure (use the section headings verbatim; the prose fills in from your `read_first` evidence):

    ```markdown
    # Sensitive-Topic Exclusion

    **Status:** Active. Implemented in [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py); patterns sourced from DynamoDB at runtime.

    ## What this is

    A fail-closed gate that prevents the spotlight pipeline from publishing
    lede content matching sensitive-topic patterns (drugs, controversial
    biomedical claims, etc.). Patterns are not part of the published
    hierarchy or any S3 artifact; they exist only in DynamoDB and
    are read by the gate at runtime.

    ## Where patterns live

    DynamoDB row: `PK = SPOTLIGHT_CONFIG#sensitive_tags`, `SK = GLOBAL`.

    Pattern list shape (read [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py)
    for the authoritative schema; describe what fields the row carries —
    typically a list of regex or substring patterns, perhaps with a
    severity tag).

    Why DynamoDB rather than source:
    - Patterns evolve faster than the release cadence; operators update
      the row without a code deploy.
    - Patterns are partially confidential (the exact list is non-public).
      Source-tree storage would commit them to git history. DDB-resident
      storage keeps them off disk in the repo.
    - The gate already reads from DDB for other config; adding patterns
      to the same substrate matches the existing operational footprint.

    ## SPOT-08: Fail-closed invariant

    If the gate cannot retrieve the patterns row (DDB error, missing row,
    permission denied, throttling), it MUST refuse to publish — never
    let a lede through on the assumption that the absence of patterns
    means nothing to exclude. The default is restriction, not permission.

    The invariant is named SPOT-08 in [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py).
    Verify (don't trust prose) by reading the source — and if the source
    diverges from this doc, update both.

    ## What this gate is NOT

    - Not a general-purpose content filter. The patterns are biomedical
      and Weill-Cornell-specific.
    - Not a substitute for human editorial review. The
      [`SPOTLIGHT_REVIEW#`](../spotlight/review_queue.py) queue handles
      every persistently rejected lede; the sensitive gate is one of
      several routes a lede can take to that queue.
    - Not a place to encode the project's editorial voice. The critic
      gates (`spotlight/critic.py`) handle voice; the sensitive gate
      handles topical exclusion.

    ## Updating the patterns

    Operators update the DDB row directly (typically via the DDB console
    or a privileged CLI). There is intentionally no source-tree path
    for adding patterns — the design point is keeping them off the repo.

    ## Related

    - [`spotlight/sensitive_gate.py`](../spotlight/sensitive_gate.py) —
      implementation
    - [`docs/RECITERAI-SPEC.md`](RECITERAI-SPEC.md) §6 / §11 (G-24
      definition)
    ```

    Adjust the bracketed prose where your reading of `spotlight/sensitive_gate.py` reveals different specifics (e.g. the exact pattern schema, the exact retry/fail behavior, whether there's a cache layer). The structure above is the contract; the prose must reflect the code as it actually exists.

    Commit message: `docs(12-residual): document sensitive-topic exclusion design (G-24)`.
  </action>
  <verify>
    <!-- B-4 Nyquist exemption rationale (docs-only task, no behavior to test):
         G-24 ships a documentation artifact whose correctness is judged by a human reviewer (prose quality, technical accuracy, operator-actionability — see VALIDATION.md Manual-Only Verifications table). The automated verify below uses grep-presence checks to assert load-bearing tokens (SPOTLIGHT_CONFIG#sensitive_tags, SPOT-08, sensitive_gate.py, fail-closed) appear in the doc — this is necessary but not sufficient. The prose-quality review is the actual completion criterion and lives in VALIDATION.md's Manual-Only table. Nyquist Check 8a is satisfied here by the combination of (a) grep-presence automation guarding against regression of the load-bearing references and (b) the manual prose-quality review tracked separately. -->
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && [ -f docs/sensitive-topic-exclusion.md ] && grep -c "SPOTLIGHT_CONFIG#sensitive_tags" docs/sensitive-topic-exclusion.md | grep -q -E "^[1-9]" && grep -c "SPOT-08" docs/sensitive-topic-exclusion.md | grep -q -E "^[1-9]" && grep -c "sensitive_gate.py" docs/sensitive-topic-exclusion.md | grep -q -E "^[1-9]"</automated>
  </verify>
  <acceptance_criteria>
    - File `docs/sensitive-topic-exclusion.md` exists
    - `grep -c "SPOTLIGHT_CONFIG#sensitive_tags" docs/sensitive-topic-exclusion.md` returns count >= 1 (PK referenced verbatim)
    - `grep -c "SPOT-08" docs/sensitive-topic-exclusion.md` returns count >= 1 (fail-closed invariant named)
    - `grep -c "sensitive_gate.py" docs/sensitive-topic-exclusion.md` returns count >= 2 (implementation referenced multiple times)
    - `grep -c "fail-closed\|fail closed" docs/sensitive-topic-exclusion.md` returns count >= 1
    - `grep -c "DynamoDB\|DDB" docs/sensitive-topic-exclusion.md` returns count >= 2 (substrate explained)
    - `grep -c "^## " docs/sensitive-topic-exclusion.md` returns count >= 4 (at least four H2 sections)
  </acceptance_criteria>
  <done>G-24 documentation exists with PK reference, SPOT-08 invariant explained, code site cited, rationale for DDB-resident pattern captured.</done>
</task>

<task type="auto">
  <name>Task 2: Add IAM Policy section to GETTING_STARTED.md (G-34)</name>
  <files>GETTING_STARTED.md</files>
  <read_first>
    - GETTING_STARTED.md FULL file (need: current section ordering; find an appropriate insertion point — typically near other "configure your AWS access" or "prerequisites" content, or near the end before any FAQ/Troubleshooting)
    - docs/aws-iam-pipeline-policy.json (skim — confirm file exists and what it grants; the section will describe at a high level what the policy covers)
    - docs/aws-iam-pipeline-policy-artifacts.json (same)
  </read_first>
  <action>
    Read GETTING_STARTED.md. Find the right insertion point: after any "prerequisites" / "AWS setup" / "configure credentials" section if one exists, otherwise append toward the end of the file but before any final FAQ or Contact section.

    Insert a new section with the heading `## IAM Policy`:

    ```markdown
    ## IAM Policy

    Two reference IAM policies live in `docs/`. Use them as the starting
    template when provisioning the AWS role or user that runs the
    pipeline.

    | File | Use for |
    |------|---------|
    | [`docs/aws-iam-pipeline-policy.json`](docs/aws-iam-pipeline-policy.json) | The runtime role for the pipeline itself — read/write access to the DynamoDB single-table substrate, invoke Bedrock for the LLM-judge and Sonnet sweeps, read source data from RDS via Secrets Manager, read/write the cold-run state in DynamoDB. |
    | [`docs/aws-iam-pipeline-policy-artifacts.json`](docs/aws-iam-pipeline-policy-artifacts.json) | The artifacts-bucket policy — controls who reads from and writes to `s3://wcmc-reciterai-hierarchy/` and `s3://wcmc-reciterai-artifacts/`. Attach to the publish-stage role and to any downstream-consumer role (e.g. the SPS service account). |

    Both files are versioned with the codebase so any IAM change goes
    through the same review path as code. Operators with AWS console
    access can copy-paste either into the IAM policy editor; the JSON
    is valid as-is.

    If a stage fails with `AccessDenied` against a resource not covered
    by these policies, that's a real signal — either the policy needs
    extending or the stage is doing something it wasn't intended to.
    Don't paper over with `*` permissions; update the JSON, get review,
    then apply.
    ```

    If GETTING_STARTED.md already has an IAM section (extremely unlikely per RESEARCH G-34 verification, but verify before writing), update it instead of duplicating. The "verify first, write second" rule applies to docs as much as code.

    Commit message: `docs(12-residual): add IAM policy section to GETTING_STARTED (G-34)`.
  </action>
  <verify>
    <!-- B-4 Nyquist exemption rationale (docs-only task, no behavior to test):
         G-34 ships a documentation section in an existing file. Automated verify asserts the section heading and the two policy-JSON path tokens are present — load-bearing references that would silently regress if the section were lost in a future merge conflict. Prose-quality review (does the section help a new operator actually configure IAM?) is manual and tracked in VALIDATION.md's Manual-Only table. Nyquist Check 8a satisfied via grep-presence regression guard + Manual-Only entry. -->
    <automated>cd /Users/paulalbert/Dropbox/GitHub/ReciterAI && grep -c "## IAM Policy\|## IAM Policies" GETTING_STARTED.md | grep -q -E "^[1-9]" && grep -c "aws-iam-pipeline-policy.json" GETTING_STARTED.md | grep -q -E "^[1-9]" && grep -c "aws-iam-pipeline-policy-artifacts.json" GETTING_STARTED.md | grep -q -E "^[1-9]"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "## IAM Policy\|## IAM Policies" GETTING_STARTED.md` returns count >= 1
    - `grep -c "aws-iam-pipeline-policy.json" GETTING_STARTED.md` returns count >= 1
    - `grep -c "aws-iam-pipeline-policy-artifacts.json" GETTING_STARTED.md` returns count >= 1
    - `grep -c "docs/aws-iam-pipeline-policy" GETTING_STARTED.md` returns count >= 2 (both files referenced by relative path)
    - The IAM section is NOT placed before any "Prerequisites" or "AWS setup" section if one exists (verify by manual eyeball of the file ordering)
    - The two referenced files exist on disk: `[ -f docs/aws-iam-pipeline-policy.json ] && [ -f docs/aws-iam-pipeline-policy-artifacts.json ]` exits 0
  </acceptance_criteria>
  <done>GETTING_STARTED.md has an IAM Policy section linking the two existing policy files with operator-facing rationale.</done>
</task>

</tasks>

<verification>
- Task 1 + Task 2 acceptance criteria all green
- Both files committed to git
</verification>

<success_criteria>
G-24 and G-34 both shipped as part of Phase 12 §11 residual hygiene. The two writing-task items are no longer outstanding.
</success_criteria>

<output>
After completion, create `.planning/phases/12-feedback-loops-both-aggregations-residual-hygiene/12-residual-docs-SUMMARY.md`.
</output>
