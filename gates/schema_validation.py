"""schema_validation gate — wraps the existing jsonschema check.

`pipeline_hierarchy.generator.validate()` already runs jsonschema
validation against `docs/hierarchy.schema.json` and raises on failure.
This gate wraps it as a registered check so the publish stage's gate
runner gets a uniform interface across all gates.

Both call sites coexist initially (inline call inside `generate()` and
the registered gate inside `publish.py`). The inline call inside
`generate()` is removed once `publish.py` is wired to the substrate
(Phase 9 task 9) — at that point the registered gate is the only
schema check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from gates.registry import GateResult, SEVERITY_BLOCK, register_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "docs/hierarchy.schema.json"


@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="schema_validation")
def schema_validation_gate(
    *,
    hierarchy: dict[str, Any],
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    **_: object,
) -> GateResult:
    """Validate `hierarchy` against the JSON Schema.

    Returns passed=True with a one-line summary on success; passed=False
    with the failing JSON Pointer + validator message in `details` on
    failure. Does not raise.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=hierarchy, schema=schema)
    except jsonschema.ValidationError as err:
        return GateResult(
            name="schema_validation",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=f"hierarchy failed schema validation at {list(err.absolute_path)}",
            details={
                "json_pointer": list(err.absolute_path),
                "validator": err.validator,
                "validator_value": err.validator_value,
                "message": err.message,
            },
        )
    return GateResult(
        name="schema_validation",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="hierarchy is schema-valid",
    )
