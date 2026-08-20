"""SPIN (InfoEd) source module for the grants pipeline.

Third ingest source alongside ``grants_gov`` and ``wcm_curated``: the SPIN
funding-opportunity database, pulled per target funder via SOLR sponsor-name
queries (``docs/spin-ingest-build-spec.md`` + ``docs/spin-ingest-spike-findings.md``).
This module is the pure/source half — the client, the row->``Opportunity``
mapper, and the noise gates. Orchestration lives in ``ingest_spin``.

Owner decisions baked in (2026-08):
  1. ``project_type`` allow-list exactly as the spec proposes (keep Research
     Grant / RFA (NIH) / Fellowship / Collaborative Project / Federal Contract
     Opp; drop the listed junk types). Unknown/missing ``project_type`` is
     KEPT and counted separately (``unknown_type``) — never silently drop a
     category we haven't seen; the LLM judge still vets survivors.
  3. No size floor in v1: SPIN's ProgramSearch exposes NO award-amount field
     (spike finding 1), so every amount stays ``None`` and prestige
     ``size_bucket`` abstains. Synopsis-text amount parsing is deferred.
  6. ``geographic`` "No Restrictions"/"Any"/empty counts as US-eligible ONLY
     when ``project_location`` does not indicate an exclusively non-US
     location (the spike saw an Australian award slip through on
     ``geographic="No Restrictions"``).

Credential hygiene: auth comes from ``SPIN_PUBLIC_KEY`` / ``SPIN_SIGNATURE`` /
``SPIN_INSTITUTION_CODE`` as request query params, so the request URL is a
secret. It is never logged, and every transport error is re-raised as a fresh
``RuntimeError`` (``from None``) whose message carries only the endpoint path,
status, and page number — never the URL or the params.

``normalize_spin`` / ``keep_opportunity`` are pure (no network, no AWS) and
unit-tested offline — mirroring ``normalize`` / ``wcm_curated``.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from pipeline_grants.models import Opportunity, make_opportunity_id
from pipeline_grants.normalize import _activity_code, _strip_html
from utils.iso_clock import now_iso

SPIN_SOURCE = "spin"
SEARCH_URL = "https://spin.infoedglobal.com/Service/ProgramSearch"

# Program columns to request (the spike-confirmed ProgramSearch field list;
# unknown columns are silently dropped by SPIN, so the amount candidates the
# spike probed are deliberately absent — there is no amount field).
COLUMNS = (
    "id", "prog_title", "spon_name", "sponsor_type", "applicant_type",
    "geographic", "project_type", "project_location", "keyword", "objective",
    "synopsis", "deadline_date", "programurl", "sponwebsite", "spon_prog",
    "target", "cfda",
)

_AUTH_ENV = ("SPIN_PUBLIC_KEY", "SPIN_SIGNATURE", "SPIN_INSTITUTION_CODE")


class SpinClient:
    """Thin GET client for SPIN ProgramSearch (auth from env, never logged).

    ``search(keywords)`` yields every program row across all result pages of one
    query (SPIN pages via ``{Programs, PageNumber, NumberOfPages}``). The SOLR
    sponsor pull the ingest uses is ``[SOLR]spon_name:"<exact canonical name>"``
    (verified: returns only that sponsor's programs).
    """

    def __init__(self, *, page_size: int = 100, timeout: int = 30):
        missing = [name for name in _AUTH_ENV if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing SPIN credentials in env: {', '.join(missing)}")
        self._auth = {
            "PublicKey": os.environ["SPIN_PUBLIC_KEY"],
            "signature": os.environ["SPIN_SIGNATURE"],
            "InstCode": os.environ["SPIN_INSTITUTION_CODE"],
        }
        self.page_size = page_size
        self.timeout = timeout

    def _get_page(self, keywords: str, page_number: int) -> dict:
        params = dict(self._auth)
        params.update({
            "keywords": keywords,
            "columns": ",".join(COLUMNS),
            "pageSize": str(self.page_size),
            "pageNumber": str(page_number),
            "responseFormat": "JSON",
        })
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        # The URL embeds the auth params -> it must never reach a log line or an
        # exception message. `from None` severs the chain so a traceback cannot
        # render the original error's URL either.
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"SPIN ProgramSearch HTTP {exc.code} (page {page_number})") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"SPIN ProgramSearch unreachable: {getattr(exc, 'reason', 'network error')}") from None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"SPIN ProgramSearch returned non-JSON (page {page_number}, {len(body)} bytes)") from None

    def search(self, keywords: str, *, max_pages: int = None):
        """Yield every program row for one keywords query, paginating to NumberOfPages.

        Two independent stop conditions (an empty page, or page > NumberOfPages)
        guard against an inflated page count looping forever — same posture as
        ``grants_gov.search_all_opportunities``.
        """
        page = 1
        while True:
            data = self._get_page(keywords, page)
            programs = data.get("Programs") or []
            yield from programs
            total_pages = int(data.get("NumberOfPages") or 0)
            page += 1
            if not programs or page > total_pages:
                break
            if max_pages is not None and page > max_pages:
                break


def _as_list(value) -> list:
    """SPIN fields arrive list-typed OR scalar depending on the row — always a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


# --- deadline parsing -------------------------------------------------------
# SPIN deadline formats are not contractually documented; accept the common
# shapes (ISO, US slash, "Month DD, YYYY", DD-Mon-YYYY) and fail to "" so an
# unparseable deadline never drops a program (no-deadline == continuous).
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MDY_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MON_DY_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_D_MON_Y_RE = re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s](\d{4})\b")


def parse_spin_date(text) -> str:
    """First recognizable date in the value -> ISO 'YYYY-MM-DD'; '' when none."""
    text = str(text or "")
    m = _ISO_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _MDY_RE.search(text)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = _MON_DY_RE.search(text)
    if m and _MONTHS.get(m.group(1).lower()[:3]):
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()[:3]]:02d}-{int(m.group(2)):02d}"
    m = _D_MON_Y_RE.search(text)
    if m and _MONTHS.get(m.group(2).lower()[:3]):
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()[:3]]:02d}-{int(m.group(1)):02d}"
    return ""


def _deadlines_iso(row: dict) -> list:
    """Sorted parseable ISO deadlines from ``deadline_date`` (list-typed or scalar)."""
    return sorted({parse_spin_date(v) for v in _as_list(row.get("deadline_date"))} - {""})


def _select_deadline(dates: list, today: str) -> str:
    """The next upcoming deadline when one exists, else the latest past one, else ''."""
    if not dates:
        return ""
    future = [d for d in dates if d >= today]
    return future[0] if future else dates[-1]


# --- noise gates ------------------------------------------------------------
# Gate 1: project_type allow/drop lists [decision #1]. Compared lowercased;
# both slash and "or" spellings of the junk labels are listed because SPIN's
# exact wording varies. Anything in NEITHER list is kept as "unknown_type"
# (counted, never silently dropped) — the LLM judge still vets it.
PROJECT_TYPE_ALLOW = frozenset({
    "research grant", "rfa (nih)", "fellowship", "collaborative project",
    "federal contract opp",
})
PROJECT_TYPE_DROP = frozenset({
    "prize or award", "conference attendance", "student scholarship",
    "artistic or cultural performance", "artistic/cultural",
    "exhibits or collections", "exhibits/collections", "equipment",
})

_UNRESTRICTED_GEO = frozenset({"no restrictions", "any", "unrestricted", "none"})
_US_ALIASES = frozenset({"usa", "us", "u.s.", "u.s.a."})


def _is_us(value: str) -> bool:
    v = (value or "").strip().lower()
    return "united states" in v or v in _US_ALIASES


def _is_unrestricted(value: str) -> bool:
    return (value or "").strip().lower() in _UNRESTRICTED_GEO


def us_eligible(geographic, project_location) -> bool:
    """Gate 2: keep US-eligible rows [decision #6].

    An explicit "United States" in ``geographic`` always keeps. An exclusively
    non-US ``geographic`` always drops. The permissive middle ("No
    Restrictions"/"Any"/empty) keeps ONLY when ``project_location`` does not
    pin an exclusively non-US location — the spike's Australian-award leak.
    """
    geo = [g for g in (str(v).strip() for v in _as_list(geographic)) if g]
    if any(_is_us(g) for g in geo):
        return True
    if geo and not any(_is_unrestricted(g) for g in geo):
        return False
    locations = [l for l in (str(v).strip() for v in _as_list(project_location)) if l]
    if not locations:
        return True
    return any(_is_us(l) or _is_unrestricted(l) for l in locations)


def keep_opportunity(row: dict, *, today: str = None) -> "tuple[bool, str]":
    """Apply the three noise gates to a raw SPIN row -> (kept, reason).

    ``reason`` is '' for a clean keep, ``"unknown_type"`` for a keep whose
    ``project_type`` we have not classified (counted by the orchestrator), and
    the drop reason (``project_type`` / ``geo_non_us`` / ``suspended`` /
    ``past_deadline``) otherwise. Pure — no network; ``today`` (ISO date)
    defaults to the current UTC date, injectable for tests.
    """
    today = today or now_iso()[:10]

    # 1. project_type allow-list [decision #1]
    types = [t for t in (str(v).strip().lower() for v in _as_list(row.get("project_type"))) if t]
    allowed = any(t in PROJECT_TYPE_ALLOW for t in types)
    if not allowed and any(t in PROJECT_TYPE_DROP for t in types):
        return False, "project_type"

    # 2. geographic US-eligibility with the project_location cross-check [decision #6]
    if not us_eligible(row.get("geographic"), row.get("project_location")):
        return False, "geo_non_us"

    # 3. status: suspended programs and wholly past deadlines
    if "temporarily suspended" in (row.get("prog_title") or "").lower():
        return False, "suspended"
    deadlines = _deadlines_iso(row)
    if deadlines and deadlines[-1] < today:
        return False, "past_deadline"

    return True, ("" if allowed else "unknown_type")


# --- normalization ----------------------------------------------------------


def _eligibility_text(row: dict) -> str:
    """``applicant_type`` + ``geographic`` joined, mirroring normalize's ' | ' style."""
    applicant = "; ".join(
        s for s in (str(v).strip() for v in _as_list(row.get("applicant_type"))) if s)
    geographic = "; ".join(
        s for s in (str(v).strip() for v in _as_list(row.get("geographic"))) if s)
    return " | ".join(p for p in (applicant, geographic) if p)


def normalize_spin(row: dict, *, ingested_at: str) -> Opportunity:
    """One SPIN program row -> an ``Opportunity`` (the build spec's field map).

    Deterministic given its inputs: "today" for the status/deadline derivation
    is ``ingested_at``'s date. Every amount stays ``None`` (SPIN exposes no
    amount field — prestige ``size_bucket`` abstains) [decision #3].
    """
    today = (ingested_at or "")[:10]
    source_id = str(row.get("id") or "").strip()
    title = (row.get("prog_title") or "").strip()
    types = [t for t in (str(v).strip() for v in _as_list(row.get("project_type"))) if t]
    synopsis = _strip_html(row.get("synopsis") or "") or _strip_html(row.get("objective") or "")
    deadlines = _deadlines_iso(row)
    due_date = _select_deadline(deadlines, today)
    status = "open" if (not deadlines or deadlines[-1] >= today) else "closed"
    return Opportunity(
        opportunity_id=make_opportunity_id(SPIN_SOURCE, source_id),
        source=SPIN_SOURCE,
        source_id=source_id,
        source_url=(row.get("programurl") or "").strip() or (row.get("sponwebsite") or "").strip(),
        sponsor=(row.get("spon_name") or "").strip(),
        title=title,
        synopsis=synopsis,
        program_type=types[0] if types else "",
        # Foundations rarely carry an NIH-style activity code, but recover one
        # from the title when present — the same fallback the other sources use.
        mechanism=_activity_code(title),
        open_date="",
        due_date=due_date,
        status=status,
        eligibility_raw=_eligibility_text(row),
        cfda_list=[c for c in (str(v).strip() for v in _as_list(row.get("cfda"))) if c],
        ingested_at=ingested_at,
    )
