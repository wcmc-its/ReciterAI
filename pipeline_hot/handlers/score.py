"""Hot-path Score Lambda handler.

Wraps `score_publications.py --emit-envelope` and returns the STAGE#
envelope — already converted to DynamoDB attribute-typed format — for the
state machine's `arn:aws:states:::dynamodb:putItem` SDK integration to
consume directly via `Item.$": "$.score_envelope"`.

Two event shapes are accepted (see `handler`):
  - the hot-path date-delta event (`{"delta": {...}, ...}`), and
  - the new-researcher onboarding CWID-scoped event (`{"pmids": [...]}`),
    added for #80 Phase 2.

The envelope parse + DDB-typing helpers live in `pipeline_common.envelope`
— shared with the other hot handlers and the onboarding package.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline_common.envelope import (
    parse_envelope_from_stdout,
    to_ddb_typed_envelope,
)

# Populate DB_* env vars in Lambda before the subprocess to
# `score_publications.py` runs — it queries ReciterDB at startup
# (extract_publications / extract_author_mapping / extract_faculty_
# _metadata). Subprocesses inherit env vars, so importing here makes
# them visible to the subprocess. No-op outside Lambda.
import utils.secrets_loader  # noqa: F401, E402

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def handler(event: dict, context: Any = None) -> dict:
    """Invoke score_publications --emit-envelope and return the envelope.

    Two event shapes are accepted.

    Hot-path date delta (the weekly hot run):
        {
          "delta": {"pmids": [...], "size": N,
                    "retry_pmids": [...], "retry_size": M},
          "last_successful_hot_run_at": "<iso8601>" | null
        }
    `delta.retry_pmids` is the orchestrator retry sweep's recovered-PMID
    list. When non-empty it is passed through as `--pmids … --additive`,
    which `score_publications` unions onto the `--delta-since` date delta
    — this is the only path by which a PMID that aged out of the date
    window reaches the scorer (the scorer recomputes the date delta itself
    and otherwise never sees `delta.pmids`).

    Onboarding CWID-scoped work set (#80 Phase 2):
        {
          "pmids": ["pmid1", "pmid2", ...],
          "allow_cost_override": true | false   (optional, default false)
        }
    Routes to `score_publications --pmids …` — an explicit work set, an
    ALTERNATIVE to `--delta-since` (the two are mutually exclusive). The
    onboarding orchestrator invokes Score only with a non-empty work set,
    so an empty `pmids` list is a contract violation and raises. When the
    operator approved a cost-guard override, `allow_cost_override` is set
    so the `score_publications` cost guard (defense-in-depth) does not
    re-block a run the orchestrator already cleared.
    """
    pmids = event.get("pmids")
    if pmids is not None:
        # Onboarding CWID-scoped event (#80). Plain `--pmids` is the
        # onboarding work set — it replaces --delta-since and takes no
        # --additive / --force modifier, so this branch builds a command
        # disjoint from the hot-path date-delta branch below.
        if not pmids:
            raise ValueError(
                "score handler: onboarding event carries an empty 'pmids' "
                "list; the onboarding orchestrator must invoke Score only "
                "with a non-empty work set"
            )
        cmd = [
            sys.executable, "-m", "score_publications", "--emit-envelope",
            "--pmids", ",".join(str(p) for p in pmids),
        ]
        if event.get("allow_cost_override"):
            cmd.append("--allow-cost-override")
        override = (
            " --allow-cost-override" if event.get("allow_cost_override") else ""
        )
        logger.info(
            f"score handler invoking (onboarding): {len(pmids)} pmids{override}"
        )
    else:
        # Hot-path date-delta event.
        delta = event.get("delta", {})
        delta_since = event.get("last_successful_hot_run_at")
        # `force_pmids` (#150 1a operator override) and `additive_pmids`
        # (retry ∪ eligibility, #150 1b) are mutually exclusive by
        # construction — the orchestrator sets one or the other. `additive_pmids`
        # falls back to `retry_pmids` for pre-1b envelopes.
        force_pmids = delta.get("force_pmids") or []
        additive_pmids = delta.get("additive_pmids")
        if additive_pmids is None:
            additive_pmids = delta.get("retry_pmids", [])

        cmd = [sys.executable, "-m", "score_publications", "--emit-envelope"]
        if force_pmids:
            # Operator override: score exactly this set, bypassing the
            # date-delta AND the PROCESSING# checkpoint (--force) so
            # cache-poisoned / already-`complete` PMIDs are re-scored. No
            # --delta-since: the override REPLACES the date delta.
            cmd += ["--pmids", ",".join(str(p) for p in force_pmids), "--force"]
        else:
            if delta_since:
                cmd += ["--delta-since", delta_since]
            if additive_pmids:
                # Recovered PMIDs (retry + eligibility) union onto the date
                # delta: --pmids + --additive (cache-respecting), never --force.
                cmd += [
                    "--pmids", ",".join(str(p) for p in additive_pmids),
                    "--additive",
                ]

        logger.info(
            f"score handler invoking: {' '.join(cmd)} "
            f"({delta.get('size', 0)} delta pmids, "
            f"{len(additive_pmids)} additive pmids, {len(force_pmids)} force pmids)"
        )

    # Stream subprocess output to Lambda stdout (CloudWatch) line-by-line so
    # an operator watching live can see Bedrock progress / error spew. Earlier
    # capture_output=True buffered everything until proc.exit; a 900s timeout
    # then meant zero diagnostic data in CloudWatch (only START→TIMEOUT). We
    # also keep a captured copy so the JSON envelope can be parsed from the
    # tail at the end.
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        captured.append(line)
    rc = proc.wait()
    if rc != 0:
        tail = "".join(captured[-50:])
        raise RuntimeError(
            f"score_publications exited {rc}; last lines:\n{tail}"
        )
    return to_ddb_typed_envelope(parse_envelope_from_stdout("".join(captured)))
