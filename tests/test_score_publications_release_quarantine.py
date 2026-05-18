"""Tests for the --release-quarantine flag on score_publications.py.

`--release-quarantine` is an operator maintenance mode (#86): it clears the
QUARANTINE# row and the PROCESSING# checkpoint for reviewed PMIDs so they
re-enter normal processing with a fresh retry budget. It runs no scoring
and short-circuits before any taxonomy / Bedrock / STAGE# setup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import score_publications as sp


def test_cli_rejects_release_quarantine_with_scoping_flags():
    """--release-quarantine is a standalone maintenance mode and cannot be
    combined with a scoping flag."""
    import asyncio
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", [
        "score_publications.py",
        "--release-quarantine", "111",
        "--delta-since", "2026-05-01T00:00:00Z",
    ]):
        with pytest.raises(SystemExit):
            asyncio.run(sp.main())


def test_release_quarantine_mode_invokes_helper_per_pmid_and_skips_scoring(monkeypatch):
    """--release-quarantine calls release_quarantine once per PMID and
    returns before any scoring setup — get_table() (STAGE# substrate) is
    never reached."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py", "--release-quarantine", "111,222",
    ])
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())

    release_calls: list = []

    def fake_release(client, table_name, pmid):
        release_calls.append(pmid)
        return {"pmid": pmid, "was_quarantined": True,
                "retry_count": 2, "last_error": ""}

    monkeypatch.setattr(sp, "release_quarantine", fake_release)

    # The scoring path calls get_table() right after the release branch;
    # assert the branch returns before reaching it.
    get_table_mock = MagicMock()
    monkeypatch.setattr(sp, "get_table", get_table_mock)

    asyncio.run(sp.main())

    assert release_calls == ["111", "222"]
    get_table_mock.assert_not_called()


def test_release_quarantine_empty_list_is_a_noop(monkeypatch):
    """An all-whitespace argument resolves to no PMIDs: the helper is never
    called and main() returns immediately."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py", "--release-quarantine", " , ",
    ])
    release_mock = MagicMock()
    monkeypatch.setattr(sp, "release_quarantine", release_mock)
    get_table_mock = MagicMock()
    monkeypatch.setattr(sp, "get_table", get_table_mock)

    asyncio.run(sp.main())

    release_mock.assert_not_called()
    get_table_mock.assert_not_called()


def test_release_quarantine_reports_released_and_skipped_counts(monkeypatch, capsys):
    """The summary tallies genuinely-released PMIDs separately from skipped
    (not-quarantined) ones."""
    import asyncio
    import sys

    monkeypatch.setattr(sys, "argv", [
        "score_publications.py", "--release-quarantine", "111,222,333",
    ])
    monkeypatch.setattr(sp, "get_dynamo_client", lambda: MagicMock())
    monkeypatch.setattr(sp, "get_table", MagicMock())

    # 111 + 333 were genuinely quarantined; 222 was not (an operator typo).
    outcomes = {"111": True, "222": False, "333": True}

    def fake_release(client, table_name, pmid):
        return {"pmid": pmid, "was_quarantined": outcomes[pmid],
                "retry_count": 4, "last_error": ""}

    monkeypatch.setattr(sp, "release_quarantine", fake_release)

    asyncio.run(sp.main())

    out = capsys.readouterr().out
    assert "[--release-quarantine] 2 released, 1 skipped" in out
