"""Identify the build this process is running (#407).

``build_id()`` names the code a process was built from, so data it writes can
be traced back to it. ``TOPIC#`` rows carry it in their ``minted_by`` attribute
(``minted_by(path)`` -> ``"<path>@<build>"``).

Resolution order, first non-empty wins:

1. ``RECITERAI_BUILD_SHA`` env var — the Fargate image sets it from
   ``docker build --build-arg BUILD_SHA=...`` (``.git`` is dockerignored, so
   git is unavailable inside the image).
2. A ``BUILD_SHA`` file at the repo root — ``scripts/build_lambda_zips.sh``
   writes one into each Lambda zip (the zip root is the handler's repo root).
3. ``git rev-parse --short HEAD`` — a local checkout.
4. ``"unknown"``.

The value is resolved once per process.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SHA_FILE = REPO_ROOT / "BUILD_SHA"


@functools.cache
def build_id() -> str:
    env = os.environ.get("RECITERAI_BUILD_SHA", "").strip()
    if env:
        return env
    try:
        sha = BUILD_SHA_FILE.read_text().strip()
        if sha:
            return sha
    except OSError:
        pass
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if sha:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def minted_by(path: str) -> str:
    """``"<path>@<build>"`` — the ``minted_by`` value for a TOPIC# row."""
    return f"{path}@{build_id()}"
