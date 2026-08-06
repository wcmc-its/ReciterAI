"""Cold-run taxonomy preflight — ADR D3's blocking check against an external baseline.

The cold-run image bakes `taxonomy_v2.json` at build time from whatever commit
the operator checked out — and the runbook selects that commit by *flag* state,
with nothing checking its *taxonomy* (the live trap in the ADR's Context
section). This preflight refuses to run when the bundled taxonomy's content
hash differs from an external baseline, resolved in order:

1. `RECITERAI_EXPECTED_TAXONOMY_HASH` — injected by `scripts/run_cold_run.sh`
   at launch, computed from freshly-fetched `origin/main` (the ADR's interim
   baseline). Once D2's change record exists, its pinned hash replaces how the
   launcher computes the value; this contract does not change.
2. A full checkout's own git: `git fetch` + `git show
   origin/main:taxonomy_v2.json` — covers hand-run publishes. The container
   image carries no `.git` (dockerignored), so in-container this yields nothing.
3. Neither available → fail closed.

Deliberately running a non-main taxonomy is acknowledged by setting the env var
to the bundled hash — an explicit, audit-visible act (it lands in the ECS task's
containerOverrides). There is no other override; `--force` does not apply.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from utils.taxonomy import TAXONOMY_PATH, content_hash, current_content_hash

EXPECTED_HASH_ENV = "RECITERAI_EXPECTED_TAXONOMY_HASH"


def _expected_hash_from_git(repo_root: Path) -> str | None:
    """Content hash of origin/main's taxonomy, or None if git cannot provide it.

    A failed fetch returns None rather than falling back to the local
    `origin/main` ref — a stale local ref is exactly the stale baseline this
    preflight exists to reject.
    """
    if not (repo_root / ".git").exists():
        return None
    try:
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=repo_root, check=True, capture_output=True, timeout=60,
        )
        shown = subprocess.run(
            ["git", "show", "origin/main:taxonomy_v2.json"],
            cwd=repo_root, check=True, capture_output=True, timeout=30, text=True,
        )
        return content_hash(json.loads(shown.stdout))
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None


def taxonomy_preflight(repo_root: Path | None = None) -> tuple[bool, str]:
    """Return (ok, message). Callers abort with a non-zero exit when not ok."""
    bundled = current_content_hash()
    expected = os.environ.get(EXPECTED_HASH_ENV, "").strip() or None
    if expected:
        source = f"env {EXPECTED_HASH_ENV}"
    else:
        expected = _expected_hash_from_git(repo_root or TAXONOMY_PATH.parent)
        source = "origin/main (git)"
    if not expected:
        return False, (
            "taxonomy preflight BLOCKED — no baseline available: "
            f"{EXPECTED_HASH_ENV} is unset and no usable git checkout with a "
            "reachable origin/main was found (the container image carries no "
            ".git). Launch via scripts/run_cold_run.sh, which injects the "
            "baseline, or set the env var explicitly."
        )
    if expected != bundled:
        return False, (
            "taxonomy preflight BLOCKED — bundled taxonomy_v2.json "
            f"(content hash {bundled}) does not match {source} ({expected}). "
            "This image/checkout was built from a commit whose taxonomy "
            "differs from the baseline — rebuild from a current commit "
            "(cold-run runbook Step 2). If the divergence is deliberate, "
            f"acknowledge it explicitly: {EXPECTED_HASH_ENV}={bundled}"
        )
    return True, (
        f"taxonomy preflight OK — bundled hash matches {source} ({bundled[:12]}…)"
    )
