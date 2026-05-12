"""Operator config loader for the review CLI (Phase 11).

Resolves reviewer_cwid from:
  1. ~/.reciterai/config.yaml (field: reviewer_cwid)
  2. RECITERAI_REVIEWER_CWID environment variable
  3. ReviewerCwidUnresolvable with actionable error message

Canonical config file location: ~/.reciterai/config.yaml
No other dotfiles are created or read by this module.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ENV_VAR = "RECITERAI_REVIEWER_CWID"
_CONFIG_SUBPATH = Path(".reciterai") / "config.yaml"
# Public constant for display and documentation purposes.
# NOTE: Evaluated at import time — always reflects the CURRENT home dir.
# Tests that monkeypatch Path.home must be aware that this constant
# is set once on import; use load_reviewer_cwid() directly in tests
# rather than inspecting CONFIG_PATH after patching.
CONFIG_PATH = Path.home() / _CONFIG_SUBPATH


class ReviewerCwidUnresolvable(Exception):
    """Raised when reviewer_cwid cannot be resolved from any source.

    The exception message includes actionable instructions so the operator
    knows exactly how to fix the problem.
    """


def load_reviewer_cwid() -> str:
    """Resolve reviewer_cwid from config file, then env, then raise.

    Returns:
        A reviewer_cwid string (stripped, non-empty).

    Raises:
        ReviewerCwidUnresolvable: When neither the config file nor the env
            variable contains a usable value. The error message includes
            both the config file path (~/.reciterai/config.yaml) and the
            env var name (RECITERAI_REVIEWER_CWID) with a YAML example.
    """
    config_path = Path.home() / _CONFIG_SUBPATH

    # 1. Config file
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ReviewerCwidUnresolvable(
                f"{config_path}: YAML parse error: {exc}"
            ) from exc
        cwid = data.get("reviewer_cwid")
        if isinstance(cwid, str) and cwid.strip():
            return cwid.strip()

    # 2. Env override
    env_value = os.environ.get(ENV_VAR, "").strip()
    if env_value:
        return env_value

    # 3. Actionable error
    raise ReviewerCwidUnresolvable(
        "Could not resolve reviewer_cwid.\n\n"
        f"Option 1 — create ~/.reciterai/config.yaml:\n"
        f"    reviewer_cwid: cwid_jsmith1\n\n"
        f"Option 2 — export {ENV_VAR}:\n"
        f"    export {ENV_VAR}=cwid_jsmith1\n"
    )
