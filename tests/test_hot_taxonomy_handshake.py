"""ADR D3 — hot-path runtime taxonomy handshake (pipeline_hot/taxonomy_handshake.py)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from pipeline_hot import taxonomy_handshake as hs
from utils.taxonomy import content_hash, current_content_hash, load_taxonomy


class _StubLambdaClient:
    """get_function stub serving file:// URLs to locally built zips."""

    def __init__(self, zip_paths: dict[str, Path]):
        self._zips = zip_paths
        self.calls: list[str] = []

    def get_function(self, FunctionName: str):
        self.calls.append(FunctionName)
        if FunctionName not in self._zips:
            raise RuntimeError(f"ResourceNotFound: {FunctionName}")
        return {"Code": {"Location": self._zips[FunctionName].as_uri()}}


def _build_zip(path: Path, taxonomy: dict | None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("handler.py", "# stub")
        if taxonomy is not None:
            zf.writestr("taxonomy_v2.json", json.dumps(taxonomy))
    return path


@pytest.fixture()
def own_taxonomy() -> dict:
    return load_taxonomy()


def _matching_fleet(tmp_path, taxonomy) -> _StubLambdaClient:
    return _StubLambdaClient({
        name: _build_zip(tmp_path / f"{name}.zip", taxonomy)
        for name in hs.TAXONOMY_PEERS
    })


def test_peer_set_matches_the_bundling_manifest():
    """TAXONOMY_PEERS must track exactly the zips that bundle taxonomy_v2.json
    per scripts/build_lambda_zips.sh (the ADR's authoritative replication list)."""
    manifest = (Path(__file__).parent.parent / "scripts" / "build_lambda_zips.sh").read_text()
    bundled = {
        row.split("|")[0].strip().strip('"')
        for line in manifest.splitlines()
        if (row := line.strip().strip('"')).startswith("reciterai-")
        and "taxonomy_v2.json" in line
    }
    assert bundled == {"reciterai-hot-orchestrator", *hs.TAXONOMY_PEERS}


def test_matching_peers_pass(tmp_path, own_taxonomy):
    client = _matching_fleet(tmp_path, own_taxonomy)
    report = hs.run_handshake(lambda_client=client)
    assert report["status"] == "ok"
    assert report["taxonomy_hash"] == current_content_hash()
    assert set(report["peers"]) == set(hs.TAXONOMY_PEERS)
    assert client.calls == list(hs.TAXONOMY_PEERS)


def test_mismatched_peer_aborts_naming_both_hashes(tmp_path, own_taxonomy):
    stale = {"taxonomy_version": "taxonomy_v2",
             "topics": [{"id": "retired_topic", "label": "Retired"}]}
    client = _matching_fleet(tmp_path, own_taxonomy)
    _build_zip(tmp_path / "reciterai-hot-score.zip", stale)  # overwrite one peer
    with pytest.raises(hs.TaxonomyHandshakeMismatch) as exc:
        hs.run_handshake(lambda_client=client)
    msg = str(exc.value)
    assert "reciterai-hot-score" in msg
    assert current_content_hash() in msg
    assert content_hash(stale) in msg


def test_peer_zip_without_taxonomy_fails_closed(tmp_path, own_taxonomy):
    client = _matching_fleet(tmp_path, own_taxonomy)
    _build_zip(tmp_path / "reciterai-hot-assign.zip", None)
    with pytest.raises(hs.TaxonomyHandshakeError):
        hs.run_handshake(lambda_client=client)


def test_get_function_failure_fails_closed(tmp_path, own_taxonomy):
    zips = {
        name: _build_zip(tmp_path / f"{name}.zip", own_taxonomy)
        for name in hs.TAXONOMY_PEERS
    }
    del zips["reciterai-hot-score"]  # get_function raises for it
    with pytest.raises(hs.TaxonomyHandshakeError) as exc:
        hs.run_handshake(lambda_client=_StubLambdaClient(zips))
    assert "failing closed" in str(exc.value)


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_explicit_affirmatives_disable(monkeypatch, value):
    monkeypatch.setenv(hs.DISABLE_ENV, value)
    exploding = object()  # any attribute access would fail
    assert hs.run_handshake(lambda_client=exploding) == {"status": "disabled"}


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "disable-me"])
def test_anything_else_keeps_the_guard_armed(monkeypatch, tmp_path, own_taxonomy, value):
    """A typo'd value must NOT silently disable a safety check."""
    monkeypatch.setenv(hs.DISABLE_ENV, value)
    client = _matching_fleet(tmp_path, own_taxonomy)
    assert hs.run_handshake(lambda_client=client)["status"] == "ok"


def test_content_hash_ignores_zip_level_differences(tmp_path, own_taxonomy):
    """Same taxonomy content, differently formatted file -> same verdict.
    The comparison must be content-level, never an artifact checksum (trap 6)."""
    client = _matching_fleet(tmp_path, own_taxonomy)
    reformatted = json.loads(json.dumps(own_taxonomy))
    with zipfile.ZipFile(tmp_path / "reciterai-hot-assign.zip", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("taxonomy_v2.json", json.dumps(reformatted, indent=3, sort_keys=True))
    assert hs.run_handshake(lambda_client=client)["status"] == "ok"


@pytest.mark.parametrize("exc_type", [hs.TaxonomyHandshakeMismatch, hs.TaxonomyHandshakeError])
def test_orchestrator_runs_handshake_before_any_work(monkeypatch, exc_type):
    from pipeline_hot import orchestrator

    calls = []

    def boom():
        calls.append("handshake")
        raise exc_type("handshake abort — test")

    monkeypatch.setattr(orchestrator, "run_taxonomy_handshake", boom)
    monkeypatch.setattr(
        orchestrator, "get_table",
        lambda *a, **k: calls.append("get_table") or None,
    )
    with pytest.raises(exc_type):
        orchestrator.handler({})
    assert calls == ["handshake"]  # aborted before touching DynamoDB
