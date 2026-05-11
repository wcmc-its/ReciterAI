# Phase 5: Hierarchy Publishing Contract - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-06
**Phase:** 05-hierarchy-publishing-contract
**Areas discussed:** S3 substrate & access, Versioning scheme, Contract scope & promises, Operational integration

---

## S3 substrate & access

### Q1: Access model for the hierarchy artifact bucket?

| Option | Description | Selected |
|--------|-------------|----------|
| IAM-gated | Private bucket. Each consumer gets a tightly-scoped IAM role with GetObject. Matches WCM's data-stewardship posture. | ✓ |
| Public-read on bucket policy | Anyone with URL can fetch. Removes IAM friction; risky if hierarchy ever embeds institutional knowledge. | |
| Private + CloudFront with signed URLs | Production-grade; most infrastructure. Overkill for 1.1 MB JSON. | |

### Q2: Which AWS region for the bucket?

| Option | Description | Selected |
|--------|-------------|----------|
| us-east-1 | Where existing DynamoDB and Bedrock calls live. Co-located, simple IAM. | ✓ |
| us-east-2 | Sometimes used as Cornell secondary. | |
| us-west-2 | Common for ML/data; no reason here. | |

### Q3: Bucket naming pattern?

| Option | Description | Selected |
|--------|-------------|----------|
| wcmc-reciterai-hierarchy | Matches wcmc-its public-repo convention; self-describing. | ✓ |
| reciterai-public-artifacts | Generic; reusable for future artifacts. Less specific. | |
| reciterai-chatbot-hierarchy | Mirrors existing DynamoDB table name. | |

### Q4: Who provisions the bucket and grants the pipeline IAM PutObject?

| Option | Description | Selected |
|--------|-------------|----------|
| User provisions via AWS console | Quickest path; ship Phase 5 today. | ✓ |
| Mohammad's team via CDK/Terraform | IaC-only path; adds coordination beat. | |
| Sketch policy + IAM JSON; user applies | Captures intent in code without IaC tooling. | |

---

## Versioning scheme

### Q1: Primary version-prefix scheme for v{X}/ in the S3 layout?

| Option | Description | Selected |
|--------|-------------|----------|
| v{ISO-date} | Human-readable, sortable; handles ad-hoc re-publishes cleanly. | ✓ |
| v{taxonomy_version} | Semantic version meaning; collisions on intra-version edits. | |
| Both: v{taxonomy_version}-{date} | Most expressive; longest paths. | |

### Q2: How does latest/ get updated on a new publish?

| Option | Description | Selected |
|--------|-------------|----------|
| PutObject overwrite | Simple, atomic-per-object; brief consumer-fetch race acceptable. | ✓ |
| S3 versioning + alias manifest | Stronger consistency; extra round trip per fetch. | |
| No latest/, consumers always read v{date}/ | Most-pure, most-painful coordination. | |

### Q3: Retention policy for old versioned prefixes?

| Option | Description | Selected |
|--------|-------------|----------|
| Keep all versions indefinitely | ~13 MB/year; full historical rollback. | ✓ |
| Keep last 12 + lifecycle-delete older | Bounded cost; loses deep history. | |
| Keep last 12 + Glacier-archive older | Cheaper; restore-time friction. | |

### Q4: Schema version field — separate from hierarchy version?

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, schema_version semver | Independent semantic versioning of the schema itself. | ✓ |
| No, inherit from publish date | Loses schema-vs-data change distinction. | |
| Yes, but bound to schema-file SHA256 | Self-evidently consistent; loses semver semantics. | |

---

## Contract scope & promises

### Q1: What cadence does the contract guarantee?

| Option | Description | Selected |
|--------|-------------|----------|
| Annual recompute + ad-hoc re-publishes | Contract reflects actual pipeline behavior. | ✓ |
| Annual only — ad-hoc treated as exceptions | Cleaner promise; harder to reconcile with relabel pattern. | |
| Continuous — consumers fetch latest/ on every ETL run | Strongest promise; pushes polling burden to consumers. | |

### Q2: Breaking-change deprecation window?

| Option | Description | Selected |
|--------|-------------|----------|
| 30 days advance notice | Matches SPS's existing schema-change protocol. | ✓ |
| 60 days advance notice | Longer window; trades velocity for safety. | |
| 90 days advance notice | Very generous; signals high stability commitment. | |

### Q3: How are schema changes communicated to consumers?

| Option | Description | Selected |
|--------|-------------|----------|
| CHANGELOG.md in repo + bump schema_version semver | Self-serve, no out-of-band notification. | ✓ |
| CHANGELOG + email notification to Mohammad's team | Adds reliability for the 30-day window. | |
| CHANGELOG only — consumers must poll | Minimal infrastructure; less safe. | |

### Q4: Backwards-compatibility default policy for additive fields?

| Option | Description | Selected |
|--------|-------------|----------|
| Additive fields are always non-breaking | Codifies display_name precedent; risk-free additions. | ✓ |
| All schema changes treated as breaking by default | Conservative; slows velocity here. | |
| Additive non-breaking, but consumers can declare strict-mode | Most flexible; adds documentation burden. | |

---

## Operational integration

### Q1: Notification mechanism on a successful publish?

| Option | Description | Selected |
|--------|-------------|----------|
| CloudWatch log + manifest.json only | Quiet, self-serve, no extra infra. | ✓ |
| Above + Slack webhook | Adds heads-up post; webhook secret to manage. | |
| Above + email to Mohammad's team | Maximum reach; distribution list to maintain. | |

### Q2: Should the publish step also keep the existing PM worktree copy?

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, keep it — backwards-compat | PM keeps reading from disk; SPS reads from S3. Zero risk to PM. | ✓ |
| No, remove the PM copy in this phase | Cleaner long-term; expands scope to PM migration. | |
| Yes, but gate behind a flag | Default-on flag; adds config knob. | |

### Q3: Depth of the SPS handoff brief produced in this phase?

| Option | Description | Selected |
|--------|-------------|----------|
| Architecture brief + sample fetch script | Working reference SPS coding agent adapts. | ✓ |
| Architecture brief only | Forces SPS team to implement from spec. | |
| Above + open a draft PR in SPS repo | Strongest hand-off; expands to cross-repo work. | |

### Q4: Where does the publish step actually run?

| Option | Description | Selected |
|--------|-------------|----------|
| Locally on the operator's machine | Same as backfill_all.py today; zero new infra. | ✓ |
| GitHub Actions workflow | Multiple operators can publish; secrets in GH. | |
| Lambda triggered by CloudWatch event | Fully automated; overkill for annual cadence. | |

---

## Claude's Discretion

User did not explicitly delegate any decision to Claude during this discussion. CONTEXT.md `<decisions>` section's "Claude's Discretion" subsection captures areas where downstream agents (researcher, planner) have latitude — primarily implementation-level details (JSON Schema library choice, exact paths, manifest field ordering, idempotency mechanism, IAM JSON snippet shape).

## Deferred Ideas

Captured in CONTEXT.md `<deferred>` section:

- PM migration off the worktree-file pattern (future phase)
- SPS-side ETL implementation (separate session in SPS repo)
- DynamoDB hierarchy load step (rejected — 400KB DDB item limit)
- Multi-environment artifacts (dev/staging/prod prefixes)
- CloudFront / public-read access (rejected for Phase 5)
- GitHub Actions or Lambda automation for publish (rejected for Phase 5)
- Slack/email publish notifications (rejected for Phase 5)
- Schema-change-triggered consumer push notification (future)
