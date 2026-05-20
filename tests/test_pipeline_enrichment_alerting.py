"""Unit tests for pipeline_enrichment.alerting.

Mocks the urllib transport so tests don't touch the live webhook.
Behavioral verification against the real webhook happens via the manual
smoke tests already run during PR 2a development (HTTP 202 from
build_card output, @-mention renders as clickable link).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline_enrichment import alerting
from pipeline_enrichment.alerting import (
    MENTION_NAME_ENV,
    MENTION_UPN_ENV,
    WEBHOOK_ENV,
    alert,
    build_card,
)


# ---------------------------------------------------------------------------
# build_card — payload shape
# ---------------------------------------------------------------------------

def _no_mention_env(monkeypatch):
    monkeypatch.delenv(MENTION_UPN_ENV, raising=False)
    monkeypatch.delenv(MENTION_NAME_ENV, raising=False)


def test_build_card_returns_workflows_envelope(monkeypatch):
    _no_mention_env(monkeypatch)
    payload = build_card("ERROR", "Cost guard", "Refused.")
    assert payload["type"] == "message"
    assert isinstance(payload["attachments"], list)
    att = payload["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = att["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert card["$schema"].startswith("http://adaptivecards.io/")


def test_build_card_title_includes_severity_prefix(monkeypatch):
    _no_mention_env(monkeypatch)
    for sev, expected in [
        ("INFO", "[INFO] ReciterAI"),
        ("WARN", "[WARN] ReciterAI"),
        ("ERROR", "[ERROR] ReciterAI"),
    ]:
        card = build_card(sev, "x", "y")["attachments"][0]["content"]
        assert expected in card["body"][0]["text"]


def test_info_severity_round_trips_through_build_card_and_alert(monkeypatch):
    """INFO severity (the #133 heartbeat) builds a no-mention card and
    POSTs successfully through the same envelope as WARN/ERROR."""
    _no_mention_env(monkeypatch)
    monkeypatch.setenv(WEBHOOK_ENV, "https://example.invalid/webhook")
    card = build_card(
        "INFO", "Daily enrichment complete — 7 PMID(s), $0.13",
        "Run mode: scheduled. Processed 7 PMID(s).",
        context={"mode": "scheduled", "delta_size": 7},
        mention=False,
    )["attachments"][0]["content"]
    assert "[INFO] ReciterAI" in card["body"][0]["text"]
    assert "msteams" not in card  # mention=False ⇒ no entities block.

    with patch.object(alerting, "_post", return_value=True) as p:
        ok = alert(
            "INFO", "x", "y",
            context={"mode": "scheduled"}, mention=False,
        )
    assert ok is True
    payload = p.call_args.kwargs["webhook_url"], p.call_args.args[0]
    assert payload[0] == "https://example.invalid/webhook"
    assert payload[1]["attachments"][0]["content"]["body"][0]["text"].startswith(
        "[INFO] ReciterAI"
    )


def test_build_card_rejects_invalid_severity():
    with pytest.raises(ValueError, match="severity"):
        build_card("FATAL", "x", "y")


def test_build_card_renders_context_as_subtle_kv_block(monkeypatch):
    _no_mention_env(monkeypatch)
    payload = build_card("WARN", "title", "msg", context={"delta": 600, "cost": "$36"})
    body = payload["attachments"][0]["content"]["body"]
    # Title + message + context = 3 blocks
    assert len(body) == 3
    ctx_block = body[2]
    assert ctx_block.get("isSubtle") is True
    assert "delta" in ctx_block["text"]
    assert "600" in ctx_block["text"]
    assert "cost" in ctx_block["text"]


def test_build_card_omits_context_block_when_no_context(monkeypatch):
    _no_mention_env(monkeypatch)
    payload = build_card("WARN", "title", "msg")
    body = payload["attachments"][0]["content"]["body"]
    assert len(body) == 2  # title + message only


# ---------------------------------------------------------------------------
# Mention handling
# ---------------------------------------------------------------------------

def test_mention_uses_first_token_of_name_and_full_upn(monkeypatch):
    monkeypatch.setenv(MENTION_UPN_ENV, "paa2013@med.cornell.edu")
    monkeypatch.setenv(MENTION_NAME_ENV, "Paul Albert")
    payload = build_card("ERROR", "x", "needs you")
    card = payload["attachments"][0]["content"]
    # Body's message block starts with <at>Paul</at>
    assert card["body"][1]["text"].startswith("<at>Paul</at>")
    # msteams.entities is wired up
    assert "msteams" in card
    entity = card["msteams"]["entities"][0]
    assert entity["type"] == "mention"
    assert entity["text"] == "<at>Paul</at>"
    assert entity["mentioned"]["id"] == "paa2013@med.cornell.edu"
    assert entity["mentioned"]["name"] == "Paul Albert"


def test_mention_disabled_when_only_upn_set(monkeypatch):
    monkeypatch.setenv(MENTION_UPN_ENV, "paa2013@med.cornell.edu")
    monkeypatch.delenv(MENTION_NAME_ENV, raising=False)
    payload = build_card("WARN", "x", "no tag")
    card = payload["attachments"][0]["content"]
    assert "msteams" not in card
    assert "<at>" not in card["body"][1]["text"]


def test_mention_disabled_when_only_name_set(monkeypatch):
    monkeypatch.delenv(MENTION_UPN_ENV, raising=False)
    monkeypatch.setenv(MENTION_NAME_ENV, "Paul Albert")
    card = build_card("WARN", "x", "no tag")["attachments"][0]["content"]
    assert "msteams" not in card


def test_mention_disabled_by_caller_even_when_env_set(monkeypatch):
    """Some alerts are FYI-only and shouldn't ping the operator."""
    monkeypatch.setenv(MENTION_UPN_ENV, "paa2013@med.cornell.edu")
    monkeypatch.setenv(MENTION_NAME_ENV, "Paul Albert")
    card = build_card("WARN", "x", "y", mention=False)["attachments"][0]["content"]
    assert "msteams" not in card
    assert "<at>" not in card["body"][1]["text"]


# ---------------------------------------------------------------------------
# alert() — transport + env handling
# ---------------------------------------------------------------------------

def test_alert_skipped_when_webhook_env_unset(monkeypatch, caplog):
    monkeypatch.delenv(WEBHOOK_ENV, raising=False)
    with patch.object(alerting, "_post") as mock_post:
        result = alert("ERROR", "title", "body")
    assert result is False
    mock_post.assert_not_called()


def test_alert_uses_env_webhook_url_by_default(monkeypatch):
    monkeypatch.setenv(WEBHOOK_ENV, "https://example/webhook")
    _no_mention_env(monkeypatch)
    with patch.object(alerting, "_post", return_value=True) as mock_post:
        result = alert("WARN", "title", "body")
    assert result is True
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["webhook_url"] == "https://example/webhook"


def test_alert_explicit_webhook_url_overrides_env(monkeypatch):
    monkeypatch.setenv(WEBHOOK_ENV, "https://env/webhook")
    _no_mention_env(monkeypatch)
    with patch.object(alerting, "_post", return_value=True) as mock_post:
        alert("WARN", "title", "body", webhook_url="https://explicit/webhook")
    assert mock_post.call_args.kwargs["webhook_url"] == "https://explicit/webhook"


def test_alert_returns_false_on_non_2xx(monkeypatch):
    monkeypatch.setenv(WEBHOOK_ENV, "https://example/webhook")
    _no_mention_env(monkeypatch)
    with patch.object(alerting, "_post", return_value=False):
        assert alert("ERROR", "t", "m") is False


def test_alert_post_payload_is_what_build_card_returns(monkeypatch):
    """The alert function must not corrupt the card on its way to _post."""
    monkeypatch.setenv(WEBHOOK_ENV, "https://example/webhook")
    _no_mention_env(monkeypatch)
    with patch.object(alerting, "_post", return_value=True) as mock_post:
        alert("ERROR", "T", "M", context={"k": "v"})
    payload = mock_post.call_args.args[0]
    # Just spot-check the envelope shape; build_card has dedicated tests.
    assert payload["type"] == "message"
    assert payload["attachments"][0]["content"]["body"][0]["text"].endswith(": T")


# ---------------------------------------------------------------------------
# _post — network error handling (best-effort contract)
# ---------------------------------------------------------------------------

def test_post_treats_non_2xx_as_failure_without_raising(monkeypatch):
    monkeypatch.setenv(WEBHOOK_ENV, "https://example/webhook")
    fake_resp = MagicMock()
    fake_resp.status = 500
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch.object(alerting.urllib.request, "urlopen", return_value=fake_resp):
        assert alerting._post({"x": 1}, webhook_url="https://example/webhook") is False


def test_post_handles_network_error_without_raising(monkeypatch):
    import urllib.error
    with patch.object(
        alerting.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        assert alerting._post({"x": 1}, webhook_url="https://example/webhook") is False


def test_post_handles_timeout_without_raising(monkeypatch):
    with patch.object(
        alerting.urllib.request, "urlopen",
        side_effect=TimeoutError("slow"),
    ):
        assert alerting._post({"x": 1}, webhook_url="https://example/webhook") is False


def test_post_accepts_202(monkeypatch):
    """Workflows webhooks return 202, not 200. Both must count as success."""
    fake_resp = MagicMock()
    fake_resp.status = 202
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    with patch.object(alerting.urllib.request, "urlopen", return_value=fake_resp):
        assert alerting._post({"x": 1}, webhook_url="https://example/webhook") is True


def test_post_serializes_payload_as_json(monkeypatch):
    """The Request body must be the JSON-encoded payload."""
    fake_resp = MagicMock()
    fake_resp.status = 202
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False
    captured = {}
    def capture_request(req, **kwargs):
        captured["data"] = req.data
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return fake_resp
    with patch.object(alerting.urllib.request, "urlopen", side_effect=capture_request):
        alerting._post({"type": "message", "x": "y"}, webhook_url="https://example/webhook")
    body = json.loads(captured["data"].decode("utf-8"))
    assert body == {"type": "message", "x": "y"}
    # urllib title-cases header names.
    assert any(k.lower() == "content-type" and v == "application/json"
               for k, v in captured["headers"].items())
    assert captured["url"] == "https://example/webhook"
