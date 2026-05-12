"""schema_roundtrip gate — post-publish defense-in-depth check.

Runs *after* a publish completes. Fetches the just-published artifact +
schema from S3 (NOT from local disk — the whole point is to verify the
real bytes that consumers will read), then re-validates one against
the other. Catches:

- Torn uploads (manifest landed but version-pinned objects didn't).
- S3 eventual-consistency races (latest/manifest.json pointing at a
  version whose bytes are temporarily 404).
- ACL or IAM misconfigurations that block reads of just-uploaded objects.
- Schema version skew between what we computed locally vs what's now
  visible in the bucket.

Registered against stage `publish_post` (not `publish`) with severity
`warn`. A warn-severity failure surfaces to the operator but does NOT
roll back the publish — by the time this runs, the bytes are live. A
true rollback is a manifest rewrite (spec §3 cutover semantics), out
of scope for this gate.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from gates.registry import GateResult, SEVERITY_WARN, register_gate


@register_gate(stage="publish_post", severity=SEVERITY_WARN, name="schema_roundtrip")
def schema_roundtrip_gate(
    *,
    version: str,
    s3_client: Any,
    **_: object,
) -> GateResult:
    """Fetch {version}/hierarchy.json + {version}/hierarchy.schema.json
    from S3, validate one against the other.

    `version`: published version label, e.g. "v2026-05-12".
    `s3_client`: an object with `get_object_bytes(key) -> bytes`. Production
                 callers pass `S3HierarchyClient()`; tests pass a mock.
    """
    hierarchy_key = f"{version}/hierarchy.json"
    schema_key = f"{version}/hierarchy.schema.json"

    try:
        hierarchy_bytes = s3_client.get_object_bytes(hierarchy_key)
    except Exception as err:
        return GateResult(
            name="schema_roundtrip",
            passed=False,
            severity=SEVERITY_WARN,
            summary=f"failed to fetch {hierarchy_key}: {err.__class__.__name__}",
            details={"s3_key": hierarchy_key, "error": str(err)},
        )

    try:
        schema_bytes = s3_client.get_object_bytes(schema_key)
    except Exception as err:
        return GateResult(
            name="schema_roundtrip",
            passed=False,
            severity=SEVERITY_WARN,
            summary=f"failed to fetch {schema_key}: {err.__class__.__name__}",
            details={"s3_key": schema_key, "error": str(err)},
        )

    try:
        hierarchy = json.loads(hierarchy_bytes)
        schema = json.loads(schema_bytes)
    except (json.JSONDecodeError, TypeError, ValueError) as err:
        # JSONDecodeError covers malformed JSON; TypeError/ValueError cover
        # the case where the S3 client returned something that isn't bytes
        # or str (defensive — the gate contract is "always return GateResult,
        # never raise").
        return GateResult(
            name="schema_roundtrip",
            passed=False,
            severity=SEVERITY_WARN,
            summary=f"published artifact could not be parsed as JSON: {err.__class__.__name__}",
            details={"error": str(err)},
        )

    try:
        jsonschema.validate(instance=hierarchy, schema=schema)
    except jsonschema.ValidationError as err:
        return GateResult(
            name="schema_roundtrip",
            passed=False,
            severity=SEVERITY_WARN,
            summary=(
                f"published hierarchy.json fails validation against "
                f"published hierarchy.schema.json at {list(err.absolute_path)}"
            ),
            details={
                "json_pointer": list(err.absolute_path),
                "validator": err.validator,
                "message": err.message,
            },
        )

    return GateResult(
        name="schema_roundtrip",
        passed=True,
        severity=SEVERITY_WARN,
        summary=f"published {version} hierarchy validates against published schema",
        details={"version": version, "hierarchy_bytes": len(hierarchy_bytes)},
    )
