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


# grants.gov `synopsis.agencyName` is sometimes a contact block — "<Person>\n<Role>"
# ("Linton C Browning\nGrants Management Specialist") — instead of the agency (#294). The
# structured `agencyDetails.agencyName` / `topAgencyDetails.agencyName` carry the real agency,
# so skip a contact-shaped value and fall through to them. A newline is the reliable tell
# (every observed case is two lines); a bare trailing grants-role title is the secondary one.
_CONTACT_ROLE_RE = re.compile(
    r"\b(grantor|grants?\s+(management\s+)?(specialist|manager|officer|analyst)|"
    r"procurement\s+analyst|contracting\s+(specialist|officer)|program\s+(analyst|official))\b",
    re.I)


def _is_contact_blob(name: str) -> bool:
    return "\n" in name or bool(_CONTACT_ROLE_RE.search(name))


def _select_sponsor(data: dict, syn: dict) -> str:
    """First clean agency across synopsis → agencyDetails → topAgencyDetails; else ''."""
    for cand in (
        syn.get("agencyName"),
        (data.get("agencyDetails") or {}).get("agencyName"),
        (data.get("topAgencyDetails") or {}).get("agencyName"),
    ):
        cand = (cand or "").strip()
        if cand and not _is_contact_blob(cand):
            return cand
    return ""


# Near-variant sponsor labels fragment the facet: one funder appears under an "& / and"
# swap, a "(ACS)" / ", Inc." suffix, or an outright typo. A corpus pass over the 316 distinct
# live labels found only these, so a short explicit map (not a fuzzy normalizer that could
# merge distinct orgs) collapses them to one canonical label at persist time. Refs #294 item 3.
_SPONSOR_CANONICAL = {
    "American Cancer Society (ACS)": "American Cancer Society",
    "American Cancer Society, Inc.": "American Cancer Society",
    "Leukemia & Lymphoma Society": "Leukemia and Lymphoma Society",
    "AmerisourceBergen Foundtion": "AmerisourceBergen Foundation",
    "NewYork Presbyterian Hospital William Rodes Center for Glioblastoma":
        "The NewYork-Presbyterian Hospital William Rhodes Center for Glioblastoma",
}


def canonical_sponsor(name: str) -> str:
    """Collapse a known near-variant sponsor label to its canonical form (else unchanged)."""
    name = (name or "").strip()
    return _SPONSOR_CANONICAL.get(name, name)


def normalize_grantsgov(detail_resp: dict) -> Opportunity:
    data = detail_resp.get("data", {})
    # A forecasted NOFO carries its body under `forecast`, not `synopsis` — same key names
    # except the two `or`-chained below. Reading only `synopsis` handed every forecast to the
    # scorer with an empty body, so 100% of them failed screening and none ever reached the
    # corpus (#269). `status` below already read `data.forecast`; only the content was missed.
    syn = data.get("synopsis") or data.get("forecast") or {}
    source_id = str(data.get("id") or syn.get("opportunityId") or "")
    sponsor = _select_sponsor(data, syn)
    cfdas = [c.get("cfdaNumber") for c in (data.get("cfdas") or []) if c.get("cfdaNumber")]
    return Opportunity(
        opportunity_id=make_opportunity_id("grants_gov", source_id),
        source="grants_gov",
        source_id=source_id,
        source_url=f"https://www.grants.gov/search-results-detail/{source_id}",
        sponsor=sponsor,
        title=data.get("opportunityTitle", "") or "",
        synopsis=_strip_html(syn.get("synopsisDesc") or syn.get("forecastDesc") or ""),
        program_type=data.get("docType", "") or "",
        # NIH FOA numbers (PAR-23-065) rarely carry the activity code; the title
        # ("... (R01 Clinical Trial Not Allowed)") does ~58% of the time. Fall back to it.
        mechanism=_activity_code(data.get("opportunityNumber", "")) or _activity_code(data.get("opportunityTitle", "")),
        award_ceiling=_to_int(syn.get("awardCeiling")),
        award_floor=_to_int(syn.get("awardFloor")),
        estimated_funding=_to_int(syn.get("estimatedFunding")),
        number_of_awards=_to_int(syn.get("numberOfAwards")),
        open_date=_parse_detail_date(syn.get("postingDate", "")),
        due_date=_parse_detail_date(syn.get("responseDate") or syn.get("estApplicationResponseDate") or ""),
        status="forecasted" if data.get("forecast") else "posted",
        eligibility_raw=_eligibility_text(syn),
        cfda_list=cfdas,
        ingested_at=now_iso(),
    )
