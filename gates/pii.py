"""pii_scan gate — refuses to publish a hierarchy artifact containing PII.

The hierarchy contract (`docs/hierarchy-contract.md` FAQ) asserts that
the artifact is research-domain structural data only — no per-author
identifiers, no faculty names, no `personIdentifier`. This gate makes
that assertion an enforced invariant rather than a hope.

Three patterns are checked:

1. **WCM cwid format** — `cwid_[a-z]+\\d+`, e.g. `cwid_jsmith1234`. Tight
   enough to avoid matching topic IDs like `cardiovascular_disease`
   (those end with letters, not digits).
2. **Email addresses** — RFC-5322-ish: a localpart, `@`, a domain
   ending in `.tld`. Hierarchy text shouldn't contain emails at all.
3. **`personIdentifier` key** — checked as a dict key during the
   recursive walk, not as a string-value pattern. The contract
   specifically forbids this field name even if its value were empty.

Walks the hierarchy dict recursively; scans every string value with
the two regexes and every dict key against the forbidden-keys set.
PMID integers and seed_pmids lists are ignored — they're public
publication identifiers, not PII.

False-positive guard: tested against the live `v2026-05-12` artifact
before landing (see `tests/test_gate_pii.py::test_no_false_positives_*`).
"""

from __future__ import annotations

import re
from typing import Any

from gates.registry import GateResult, SEVERITY_BLOCK, register_gate

# WCM cwid: lowercase letters followed by digits, preceded by `cwid_`.
# Bound by word boundary on the trailing side so digits-then-letter
# strings don't extend the match.
_CWID_PATTERN = re.compile(r"\bcwid_[a-z]+\d+\b")

# RFC-5322-ish email — intentionally not exhaustive. Catches obvious
# `local@domain.tld` shapes without false-positive matches on URLs
# without `@`, version strings like `1.2.3@dev`, etc.
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

_FORBIDDEN_KEYS = frozenset({"personIdentifier"})


def _scan_strings_and_keys(
    obj: Any, path: str = "$"
) -> tuple[list[dict], list[dict]]:
    """Walk `obj` and return (string_hits, key_hits).

    `path` is a $-rooted JSON-Pointer-ish string for human-readable
    reporting.
    """
    string_hits: list[dict] = []
    key_hits: list[dict] = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORBIDDEN_KEYS:
                key_hits.append({"path": f"{path}.{k}", "key": k})
            child_path = f"{path}.{k}"
            sh, kh = _scan_strings_and_keys(v, child_path)
            string_hits.extend(sh)
            key_hits.extend(kh)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            child_path = f"{path}[{i}]"
            sh, kh = _scan_strings_and_keys(item, child_path)
            string_hits.extend(sh)
            key_hits.extend(kh)
    elif isinstance(obj, str):
        for m in _CWID_PATTERN.finditer(obj):
            string_hits.append(
                {"path": path, "pattern": "cwid", "match": m.group(0)}
            )
        for m in _EMAIL_PATTERN.finditer(obj):
            string_hits.append(
                {"path": path, "pattern": "email", "match": m.group(0)}
            )
    # ints, floats, bools, None: nothing to scan.

    return string_hits, key_hits


@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="pii_scan")
def pii_scan_gate(*, hierarchy: dict[str, Any], **_: object) -> GateResult:
    """Reject hierarchy artifacts containing PII patterns.

    Walks the dict recursively; reports cwid-style identifiers, email
    addresses, and any occurrence of the `personIdentifier` key.
    """
    string_hits, key_hits = _scan_strings_and_keys(hierarchy)
    total = len(string_hits) + len(key_hits)

    if total:
        return GateResult(
            name="pii_scan",
            passed=False,
            severity=SEVERITY_BLOCK,
            summary=(
                f"PII scan found {len(string_hits)} string match(es) and "
                f"{len(key_hits)} forbidden key(s)"
            ),
            details={
                "string_hits": string_hits[:50],
                "key_hits": key_hits[:50],
                "total_string_hits": len(string_hits),
                "total_key_hits": len(key_hits),
                "truncated": total > 50,
            },
        )

    return GateResult(
        name="pii_scan",
        passed=True,
        severity=SEVERITY_BLOCK,
        summary="no PII patterns detected in hierarchy artifact",
    )
