"""SSRF-guarded fetch of a staff-submitted opportunity URL (SPS /edit intake).

Submitted URLs come from an authenticated, audited WCM staff population, but
this fetch runs on pipeline compute (laptop or, later, Fargate) so the guard is
defense-in-depth: https only, every DNS answer must be globally routable (which
rejects loopback, RFC1918, link-local/metadata 169.254.*, CGNAT 100.64/10, and
the other reserved ranges via ``ipaddress.is_global``), redirects are followed
manually with each hop re-validated, the response is size/time-capped, and only
text content is accepted. The page text is returned tag-stripped, ready for the
extraction prompt.

ponytail: resolve-then-connect leaves a DNS-rebinding TOCTOU (urllib re-resolves
after our check); pinning the connection to the validated IP needs a custom
HTTPSConnection. Add that if this ever serves an adversarial submitter
population — today's callers are dev-role staff whose every submission is
audit-logged upstream.
"""
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 15
MAX_REDIRECTS = 3
MIN_TEXT_CHARS = 500  # thinner than this = JS-rendered or empty; reject with reason

_ACCEPTED_TYPES = ("text/html", "text/plain")
_USER_AGENT = "ReciterAI-opportunity-ingest/1.0 (Weill Cornell Medicine research development)"

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class FetchRejected(Exception):
    """A guarded fetch that refused or failed; ``code`` is the machine-readable
    reason mirrored onto the SUBMISSION item's ``reject_reason``."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _require_public_https(url: str) -> str:
    """Validate one hop's URL; returns the hostname. Raises FetchRejected."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise FetchRejected("https_required", url)
    host = parsed.hostname
    if not host:
        raise FetchRejected("invalid_url", url)
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchRejected("fetch_failed", f"DNS failure for {host}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchRejected("ssrf_blocked", f"{host} resolves to non-public {ip}")
    return host


def strip_html_text(body: str) -> str:
    """Script/style-blocks out, tags to whitespace, entities decoded, ws collapsed."""
    no_blocks = _SCRIPT_STYLE_RE.sub(" ", body)
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", no_blocks))).strip()


def fetch_page_text(url: str) -> str:
    """Fetch ``url`` under the guard and return its visible text.

    Raises FetchRejected with code ``https_required`` / ``invalid_url`` /
    ``ssrf_blocked`` / ``too_many_redirects`` / ``content_type_unsupported`` /
    ``content_too_thin`` / ``fetch_failed``.
    """
    current = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        _require_public_https(current)
        request = urllib.request.Request(current, headers={"User-Agent": _USER_AGENT})
        try:
            # Cookies/auth are never attached: a bare opener per hop, no CookieJar.
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                content_type = response.headers.get_content_type()
                if content_type not in _ACCEPTED_TYPES:
                    raise FetchRejected("content_type_unsupported", f"{content_type} at {current}")
                raw = response.read(MAX_BYTES)  # cap; a longer page is truncated
                charset = response.headers.get_content_charset() or "utf-8"
        except FetchRejected:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                if not location:
                    raise FetchRejected("fetch_failed", f"redirect without Location at {current}") from exc
                current = urllib.parse.urljoin(current, location)
                continue
            raise FetchRejected("fetch_failed", f"HTTP {exc.code} at {current}") from exc
        except OSError as exc:  # URLError, socket timeout, TLS failure
            raise FetchRejected("fetch_failed", f"{exc} at {current}") from exc

        body = raw.decode(charset, errors="replace")
        text = strip_html_text(body) if content_type == "text/html" else _WS_RE.sub(" ", body).strip()
        if len(text) < MIN_TEXT_CHARS:
            raise FetchRejected(
                "content_too_thin",
                f"only {len(text)} chars of text at {current} (JS-rendered or empty page?)",
            )
        return text
    raise FetchRejected("too_many_redirects", url)
