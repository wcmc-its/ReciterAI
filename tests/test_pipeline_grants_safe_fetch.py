import urllib.error

import pytest

from pipeline_grants.safe_fetch import (
    FetchRejected,
    MIN_TEXT_CHARS,
    fetch_page_text,
    strip_html_text,
)

PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(2, 1, 6, "", ("10.20.30.40", 443))]
METADATA = [(2, 1, 6, "", ("169.254.169.254", 443))]

LONG_BODY = "<html><body><p>" + "Research grant details. " * 40 + "</p></body></html>"


class _FakeHeaders:
    def __init__(self, content_type="text/html", charset="utf-8"):
        self._ct = content_type
        self._cs = charset

    def get_content_type(self):
        return self._ct

    def get_content_charset(self):
        return self._cs


class _FakeResponse:
    def __init__(self, body: str, content_type="text/html"):
        self._body = body.encode("utf-8")
        self.headers = _FakeHeaders(content_type)

    def read(self, n):
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _resolver(table):
    def fake_getaddrinfo(host, port, **kwargs):
        return table[host]
    return fake_getaddrinfo


def test_rejects_http():
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("http://skincancer.org/grants")
    assert exc.value.code == "https_required"


def test_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", _resolver({"internal.example.org": PRIVATE}))
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("https://internal.example.org/grants")
    assert exc.value.code == "ssrf_blocked"


def test_happy_path_strips_tags(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", _resolver({"skincancer.org": PUBLIC}))
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _FakeResponse(LONG_BODY))
    text = fetch_page_text("https://skincancer.org/research-grants")
    assert "Research grant details." in text
    assert "<p>" not in text


def test_redirect_hop_to_metadata_ip_is_blocked(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        _resolver({"skincancer.org": PUBLIC, "metadata.internal": METADATA}),
    )

    def redirecting(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 302, "Found", {"Location": "https://metadata.internal/latest"}, None,
        )

    monkeypatch.setattr("urllib.request.urlopen", redirecting)
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("https://skincancer.org/grants")
    assert exc.value.code == "ssrf_blocked"


def test_redirect_loop_bounded(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", _resolver({"a.org": PUBLIC}))

    def always_redirect(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 301, "Moved", {"Location": "https://a.org/again"}, None,
        )

    monkeypatch.setattr("urllib.request.urlopen", always_redirect)
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("https://a.org/grants")
    assert exc.value.code == "too_many_redirects"


def test_rejects_unsupported_content_type(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", _resolver({"a.org": PUBLIC}))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: _FakeResponse(LONG_BODY, content_type="application/pdf"),
    )
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("https://a.org/grants.pdf")
    assert exc.value.code == "content_type_unsupported"


def test_rejects_thin_pages(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", _resolver({"a.org": PUBLIC}))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: _FakeResponse("<html><body>Loading…</body></html>"),
    )
    with pytest.raises(FetchRejected) as exc:
        fetch_page_text("https://a.org/spa-page")
    assert exc.value.code == "content_too_thin"


def test_strip_html_text_removes_script_blocks():
    text = strip_html_text(
        "<script>var x = 'never shown';</script><p>Visible &amp; decoded</p>"
        "<style>p { color: red }</style>",
    )
    assert text == "Visible & decoded"
    assert len("x" * MIN_TEXT_CHARS) == MIN_TEXT_CHARS  # constant exported for callers
