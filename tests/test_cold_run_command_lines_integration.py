"""Issue #13 — Test A: cold-stage command lines parse cleanly against their
target script's argparser.

## What this defends

Phase 11 UAT-3 (2026-05-13) surfaced six cold-path defects from real
production runs. Two of them — bugs #1 and #2 in `11-HUMAN-UAT.md` line 27 —
were CLI-flag drift that would have been caught by this test:

  #1. `assign` stage missing `--skip-all-reviews` (Phase 10 omission)
  #2. `discover` / `relabel` stages referenced non-existent CLI flags
      (Phase 10 dead scaffolding)

For each `ColdStage` in `pipeline_cold.run.default_cold_stages()`, this test
parses the stage's `command` slice against the target script's argparse
parser. If the script doesn't accept those flags, argparse raises
`SystemExit(2)` and the test fails with a clear message naming the stage,
the bad arg, and the script's expected flags.

## What this does NOT defend

- Prompt-output schema drift inside a stage (covered by stage-level tests
  like `tests/test_score_publications_stage.py`).
- Data-contract drift between stages (Test B in the same issue — file
  `tests/test_cold_path_seams_integration.py`).
- Runtime correctness of each stage's logic (stage-level tests).

## How it works

We can't just call `main()` for each stage — the scripts call Bedrock and
DynamoDB after parse_args. Instead we monkey-patch
`argparse.ArgumentParser.parse_args` globally to:

  1. Capture the parser instance (so we can report it on failure).
  2. Invoke the *real* parse_args with the stage's argv slice. This
     raises `SystemExit(2)` if the parser rejects the args — that's the
     failure mode we want.
  3. Raise a sentinel exception to short-circuit `main()` before any
     downstream work runs (Bedrock calls, DDB writes, file I/O).

The sentinel is caught at the test boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from typing import Callable
from unittest.mock import patch

import pytest

from pipeline_cold.run import ColdStage, default_cold_stages


# ---------------------------------------------------------------------------
# parse_args interception harness
# ---------------------------------------------------------------------------


class _ParserCaptured(Exception):
    """Raised inside our monkey-patched parse_args to short-circuit main()
    after the parser has accepted the test argv."""

    def __init__(self, parser: argparse.ArgumentParser):
        super().__init__()
        self.parser = parser


def _intercept_and_parse(callable_: Callable, argv: list[str]) -> argparse.ArgumentParser:
    """Run `callable_`, intercepting `argparse.ArgumentParser.parse_args` to
    parse `argv` instead of whatever the script would have used.

    Returns the parser instance if argv was accepted; the underlying
    `SystemExit(2)` propagates if argparse rejects the args.
    """
    real_parse_args = argparse.ArgumentParser.parse_args

    def fake_parse_args(self, args=None, namespace=None):
        # Force-parse our test argv. argparse SystemExit(2) on rejection.
        real_parse_args(self, argv, namespace)
        raise _ParserCaptured(self)

    with patch.object(argparse.ArgumentParser, "parse_args", fake_parse_args):
        try:
            result = callable_()
            # If main is a coroutine, drive it on a fresh event loop.
            if asyncio.iscoroutine(result):
                asyncio.run(_coro_wrap(result))
        except _ParserCaptured as captured:
            return captured.parser

    raise AssertionError(
        "Target callable returned without invoking parse_args — "
        "cannot validate cold-stage command line."
    )


async def _coro_wrap(coro):
    """Helper so asyncio.run drives a coroutine that itself raises
    _ParserCaptured. We need an `async def` wrapper because `asyncio.run`
    requires a coroutine object, and we want any sentinel raised inside
    the coroutine to propagate up to the test boundary."""
    await coro


# ---------------------------------------------------------------------------
# Stage → (callable that builds the parser, argv slice extracted from command)
# ---------------------------------------------------------------------------


def _argv_after_python_module(command: list[str]) -> list[str]:
    """For `[python, -m, module, ...args]` return `...args`."""
    assert len(command) >= 3 and command[1] == "-m", (
        f"Expected `python -m module ...` form; got {command!r}"
    )
    return command[3:]


def _argv_after_python_script(command: list[str]) -> list[str]:
    """For `[python, script.py, ...args]` return `...args`."""
    assert len(command) >= 2 and command[1].endswith(".py"), (
        f"Expected `python script.py ...` form; got {command!r}"
    )
    return command[2:]


def _check_score(command: list[str]) -> None:
    """`python -m score_publications`."""
    argv = _argv_after_python_module(command)
    import score_publications

    _intercept_and_parse(score_publications.main, argv)


def _check_assign(command: list[str]) -> None:
    """`python backfill_all.py --skip-pm-copy --skip-all-reviews`.

    `backfill_all._parse_args()` reads `sys.argv`. We patch sys.argv to
    the desired flags and call _parse_args directly — it returns the
    namespace, no work runs, no sentinel needed.
    """
    argv = _argv_after_python_module(command)
    from cli import backfill_all

    with patch.object(sys, "argv", ["backfill_all.py"] + argv):
        backfill_all._parse_args()


def _check_relabel(command: list[str]) -> None:
    """`python relabel_subtopics.py`."""
    argv = _argv_after_python_module(command)
    from cli import relabel_subtopics

    _intercept_and_parse(relabel_subtopics.main, argv)


def _check_count(command: list[str]) -> None:
    """`python count_by_cwid.py` — no argparse parser; assert importable."""
    argv = _argv_after_python_module(command)
    assert argv == [], (
        f"Cold-stage `count` command carries args {argv!r}, but "
        f"count_by_cwid.py does not define an argparse parser. Either add "
        f"a parser to the script or remove the args from the cold-stage "
        f"command."
    )
    importlib.import_module("cli.count_by_cwid")


def _check_rollup(command: list[str]) -> None:
    """`python -m rollup_by_cwid` — main(argv) takes argv directly."""
    argv = _argv_after_python_module(command)
    import rollup_by_cwid

    _intercept_and_parse(lambda: rollup_by_cwid.main(argv), argv)


def _check_feedback_sweep(command: list[str]) -> None:
    """`python -m pipeline_feedback sweep --triggered-by cold_run`.

    `pipeline_feedback.cli.main(argv)` accepts argv directly.
    """
    argv = _argv_after_python_module(command)
    from pipeline_feedback import cli as feedback_cli

    _intercept_and_parse(lambda: feedback_cli.main(argv), argv)


def _check_backfill_spotlight(command: list[str]) -> None:
    """`python backfill_spotlight.py --publish` — `_parse_args` reads sys.argv."""
    argv = _argv_after_python_module(command)
    from cli import backfill_spotlight

    with patch.object(sys, "argv", ["backfill_spotlight.py"] + argv):
        backfill_spotlight._parse_args()


def _check_publish_hierarchy(command: list[str]) -> None:
    """`python -m pipeline_hierarchy.publish` — main(argv)."""
    argv = _argv_after_python_module(command)
    from pipeline_hierarchy import publish

    _intercept_and_parse(lambda: publish.main(argv), argv)


# Map stage.name → checker function. Stages absent from this map are
# explicitly skipped via STAGES_NO_PARSER.
def _check_top_topic(command: list[str]) -> None:
    """`python compute_top_topic.py --all` — top-level `main(argv)` parser."""
    argv = _argv_after_python_script(command)
    import compute_top_topic

    _intercept_and_parse(lambda: compute_top_topic.main(argv), argv)


_STAGE_CHECKERS: dict[str, Callable[[list[str]], None]] = {
    "score": _check_score,
    "assign": _check_assign,
    "relabel": _check_relabel,
    "top_topic": _check_top_topic,
    "count": _check_count,
    "rollup": _check_rollup,
    "feedback_sweep": _check_feedback_sweep,
    "backfill_spotlight": _check_backfill_spotlight,
    "publish_hierarchy": _check_publish_hierarchy,
}

# Stages with no argparse parser (or no script at all). These are
# intentionally not validated — their commands carry no parseable args.
_STAGES_NO_PARSER: set[str] = {
    "discover",  # `python -c "print(...)"` no-op placeholder
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_cold_stage_has_a_checker_or_is_explicitly_skipped():
    """Catch the case where a new stage is added to `default_cold_stages()`
    without a corresponding checker — without this guard, the new stage
    would silently bypass validation."""
    stage_names = {s.name for s in default_cold_stages()}
    covered = set(_STAGE_CHECKERS) | _STAGES_NO_PARSER
    uncovered = stage_names - covered
    assert not uncovered, (
        f"Cold stages without a parser checker or explicit skip: {sorted(uncovered)}. "
        f"Add a `_check_{{name}}` function to _STAGE_CHECKERS or list the "
        f"stage in _STAGES_NO_PARSER with a comment explaining why."
    )


@pytest.mark.parametrize(
    "stage",
    default_cold_stages(),
    ids=lambda s: s.name,
)
def test_cold_stage_command_parses_against_target_script(stage: ColdStage):
    """For each cold stage, the `command` list must parse cleanly against
    the target script's argparser. Catches the Phase 11 UAT-3 CLI-flag
    drift bug class (missing flags, non-existent flags).
    """
    if stage.name in _STAGES_NO_PARSER:
        pytest.skip(f"Stage {stage.name!r} has no argparse parser by design.")

    checker = _STAGE_CHECKERS.get(stage.name)
    if checker is None:
        pytest.fail(
            f"No parser checker registered for cold stage {stage.name!r}. "
            f"Add `_check_{stage.name}` to _STAGE_CHECKERS."
        )

    try:
        checker(stage.command)
    except SystemExit as exc:
        pytest.fail(
            f"Cold stage {stage.name!r} command {stage.command!r} was "
            f"rejected by the target script's argparser "
            f"(SystemExit code={exc.code}). Either the cold-stage command "
            f"references a flag the script does not define, or a required "
            f"flag is missing. Phase 11 UAT-3 bugs #1 and #2 were of this "
            f"class — see .planning/issues/0003-issue-13-seam-coverage.md."
        )
