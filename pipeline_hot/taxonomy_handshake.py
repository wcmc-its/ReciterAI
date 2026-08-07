"""ADR D3 — hot-path runtime taxonomy handshake.

At run start the orchestrator verifies that every taxonomy-bearing hot-path
Lambda ships the SAME taxonomy content as its own bundle. Peers are NOT asked
to self-report: old peer code receiving a "report your hash" event would run a
real scoring pass — a deployment-order hazard. Instead the orchestrator
downloads each peer's DEPLOYED zip via `lambda:GetFunction` and content-hashes
the `taxonomy_v2.json` actually inside it — artifact truth, zero peer code
change, and valid against peers deployed before this module existed.

Comparison is `utils.taxonomy.content_hash` of the PARSED file, never a zip
checksum — two builds of one source tree produce different zip sha256s while
being content-identical (measured 2026-08-06; ADR trap 6).

A mismatch (or any handshake-infrastructure failure — fail closed: a checker
that cannot check must not report clean) raises out of `handler()`, which the
Orchestrate state's Catch routes through WriteHotRunFailed -> NotifyError ->
Teams — the ADR's intended abort path. This check detects DISAGREEMENT among
components; a uniformly stale fleet agrees with itself and is the cold-run
preflight's + D5's job, not this one's.

Emergency escape hatch: set env `RECITERAI_HOT_HANDSHAKE_DISABLED=1` on the
orchestrator Lambda — an env change, no rebuild (note
`update-function-configuration --environment` is a FULL-MAP replace: merge
the existing vars via the `--query Environment --output json > file` +
`file://` idiom, never pass the one var alone). The skip is WARN-logged.

DEPLOY ORDERING (matters): apply the updated infra/lambda_iam_policy.json to
the live execution role (`aws iam put-role-policy`) BEFORE redeploying the
orchestrator zip. The other order fails the next hot run closed on
AccessDenied despite matching taxonomies.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from utils.taxonomy import content_hash, current_content_hash

logger = logging.getLogger(__name__)

# The taxonomy-bearing Lambda zips per the scripts/build_lambda_zips.sh
# manifest (the authoritative bundling list — ADR "Replication sites").
# top_topic and rollup do not bundle the taxonomy and are not handshaken.
# The drift checker IS handshaken: the ADR explicitly refuses it an
# exemption ("a stale checker is a checker that reports clean"), and until
# D5 layer 1 exists nothing else would catch its staleness. The Docker
# image is excluded by design — the cold run has its own blocking preflight
# against an external baseline (#362), which peer comparison cannot provide.
TAXONOMY_PEERS = (
    "reciterai-hot-score",
    "reciterai-hot-assign",
    "reciterai-taxonomy-drift",
)

DISABLE_ENV = "RECITERAI_HOT_HANDSHAKE_DISABLED"

_DOWNLOAD_TIMEOUT_S = 60


class TaxonomyHandshakeMismatch(RuntimeError):
    """A peer's deployed bundle carries a different taxonomy."""


class TaxonomyHandshakeError(RuntimeError):
    """The handshake could not be performed at all (fail closed)."""


def _peer_bundled_hash(lambda_client, function_name: str) -> str:
    """Content hash of the taxonomy_v2.json inside a peer's DEPLOYED zip."""
    # No Qualifier: reads $LATEST, which today is also what the ASL invokes
    # (bare function ARNs, no aliases). If ADR D4 lands (published versions +
    # alias flips), $LATEST moves before the alias does — this read must gain
    # a qualifier then, or the handshake goes falsely clean in exactly the
    # alias-flip window D3 is meant to backstop.
    resp = lambda_client.get_function(FunctionName=function_name)
    url = resp["Code"]["Location"]
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "bundle.zip"
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as src, \
                open(zip_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        with zipfile.ZipFile(zip_path) as zf:
            try:
                raw = zf.read("taxonomy_v2.json")
            except KeyError as exc:
                raise TaxonomyHandshakeError(
                    f"{function_name}: deployed zip contains no taxonomy_v2.json "
                    "— it is not a taxonomy-bearing bundle or the manifest "
                    "changed; fix TAXONOMY_PEERS or the zip."
                ) from exc
    return content_hash(json.loads(raw))


def run_handshake(lambda_client=None, peers: tuple[str, ...] = TAXONOMY_PEERS) -> dict:
    """Compare this bundle's taxonomy hash against every peer's deployed zip.

    Returns {"status": "ok"|"disabled", ...}. Raises TaxonomyHandshakeMismatch
    on disagreement and TaxonomyHandshakeError when the check itself cannot
    run — both abort the hot run via the caller.
    """
    # Only explicit affirmatives disable the guard — a typo'd value must not
    # silently turn a safety check off.
    if os.environ.get(DISABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        logger.warning(
            "taxonomy handshake DISABLED via %s — the hot run proceeds "
            "unguarded against a split taxonomy", DISABLE_ENV,
        )
        return {"status": "disabled"}

    own = current_content_hash()
    if lambda_client is None:
        import boto3  # local import: tests inject a stub client

        lambda_client = boto3.client("lambda")

    peer_hashes: dict[str, str] = {}
    for name in peers:
        try:
            peer_hashes[name] = _peer_bundled_hash(lambda_client, name)
        except TaxonomyHandshakeError:
            raise
        except Exception as exc:
            raise TaxonomyHandshakeError(
                f"taxonomy handshake could not read {name}'s deployed bundle "
                f"({type(exc).__name__}: {exc}) — failing closed. The role "
                "needs lambda:GetFunction on the hot-path functions."
            ) from exc

    mismatched = {n: h for n, h in peer_hashes.items() if h != own}
    if mismatched:
        detail = ", ".join(f"{n}={h}" for n, h in sorted(mismatched.items()))
        raise TaxonomyHandshakeMismatch(
            f"split taxonomy detected — orchestrator bundle={own} but {detail}. "
            "The fleet must not score on disagreeing taxonomies: redeploy the "
            "stale bundle(s) from the same commit before rerunning."
        )
    logger.info(
        "taxonomy handshake OK — %d peer(s) match %s…", len(peer_hashes), own[:12]
    )
    return {"status": "ok", "taxonomy_hash": own, "peers": peer_hashes}
