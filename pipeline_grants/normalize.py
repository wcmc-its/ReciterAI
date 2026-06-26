"""Map a Grants.gov fetchOpportunity response into an Opportunity."""
import html
import re

from pipeline_grants.models import Opportunity, make_opportunity_id
from utils.iso_clock import now_iso

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DETAIL_DATE_RE = re.compile(r"([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})")
_ACTIVITY_RE = re.compile(r"\b([A-Z]\d{2}|[A-Z]{2}\d)\b")  # best-effort: R01/K23/F31 + DP2/UM1 if present
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _strip_html(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _to_int(value):
    if value is None:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not m:
        return None
    return int(float(m.group(0).replace(",", "")))


def _parse_detail_date(text: str) -> str:
    m = _DETAIL_DATE_RE.search(text or "")
    if not m:
        return ""
    mon, day, year = m.group(1), int(m.group(2)), m.group(3)
    if mon not in _MONTHS:
        return ""
    return f"{year}-{_MONTHS[mon]:02d}-{day:02d}"


def _eligibility_text(syn: dict) -> str:
    parts = []
    desc = syn.get("applicantEligibilityDesc")
    if desc:
        parts.append(_strip_html(desc))
    types = [t.get("description", "") for t in (syn.get("applicantTypes") or []) if t.get("description")]
    if types:
        parts.append("; ".join(types))
    return " | ".join(parts)


def _activity_code(opp_number: str) -> str:
    m = _ACTIVITY_RE.search(opp_number or "")
    return m.group(1) if m else ""


def normalize_grantsgov(detail_resp: dict) -> Opportunity:
    data = detail_resp.get("data", {})
    syn = data.get("synopsis") or {}
    source_id = str(data.get("id") or syn.get("opportunityId") or "")
    sponsor = syn.get("agencyName") or (data.get("agencyDetails") or {}).get("agencyName", "") or ""
    cfdas = [c.get("cfdaNumber") for c in (data.get("cfdas") or []) if c.get("cfdaNumber")]
    return Opportunity(
        opportunity_id=make_opportunity_id("grants_gov", source_id),
        source="grants_gov",
        source_id=source_id,
        source_url=f"https://www.grants.gov/search-results-detail/{source_id}",
        sponsor=sponsor,
        title=data.get("opportunityTitle", "") or "",
        synopsis=_strip_html(syn.get("synopsisDesc", "")),
        program_type=data.get("docType", "") or "",
        mechanism=_activity_code(data.get("opportunityNumber", "")),
        award_ceiling=_to_int(syn.get("awardCeiling")),
        award_floor=_to_int(syn.get("awardFloor")),
        estimated_funding=_to_int(syn.get("estimatedFunding")),
        number_of_awards=_to_int(syn.get("numberOfAwards")),
        open_date=_parse_detail_date(syn.get("postingDate", "")),
        due_date=_parse_detail_date(syn.get("responseDate", "")),
        status="forecasted" if data.get("forecast") else "posted",
        eligibility_raw=_eligibility_text(syn),
        cfda_list=cfdas,
        ingested_at=now_iso(),
    )
