"""SPIN (InfoEd) funding-opportunity source — the 3rd pipeline_grants source after
grants_gov and wcm_curated. Pulls each target funder's programs by its exact canonical
SPIN sponsor name, maps a program row to an ``Opportunity``, and applies precision-
favoring structured gates before the (costly) scoring/judge steps.

PROVENANCE / BACK-OUT (deliberate): every SPIN opportunity is ``source="spin"`` and lands
at ``PK="GRANT#spin:<spin program id>"``. Nothing else in the corpus uses that prefix, so
the entire SPIN footprint is identifiable and removable in one prefix scan
(``begins_with(PK, "GRANT#spin:")``) if we decide not to proceed with SPIN.

SPIN exposes NO award-amount field, so prestige ``size_bucket`` abstains for SPIN opps
(``sponsor_tier`` carries the score — these are foundations). Pull is exact: SPIN's SOLR
``spon_name`` phrase query returns only that sponsor's programs (verified). ``spon_code``
is NOT a queryable program field, so we pull by the canonical name from SponsorList.

Creds from env (never logged): SPIN_PUBLIC_KEY / SPIN_SIGNATURE / SPIN_INSTITUTION_CODE.
"""
import json
import os
import re

import requests

from pipeline_grants.models import Opportunity, make_opportunity_id
from utils.iso_clock import now_iso

_PROGRAM_URL = "https://spin.infoedglobal.com/Service/ProgramSearch"
_SPONSOR_URL = "https://spin.infoedglobal.com/Service/SponsorList"
_COLUMNS = [
    "id", "prog_title", "spon_name", "sponsor_type", "applicant_type", "geographic",
    "project_type", "project_location", "keyword", "objective", "synopsis",
    "deadline_date", "programurl", "sponwebsite", "spon_prog", "cfda",
]


def _creds() -> dict:
    return {
        "PublicKey": os.environ["SPIN_PUBLIC_KEY"],
        "signature": os.environ["SPIN_SIGNATURE"],
        "InstCode": os.environ["SPIN_INSTITUTION_CODE"],
    }


def search_sponsor(spin_name: str, *, max_results: int = None, timeout: int = 30) -> list:
    """Every program for an exact canonical SPIN sponsor name (paginated)."""
    params = {
        **_creds(),
        "keywords": f'[SOLR]spon_name:"{spin_name}"',
        "pageSize": 50, "responseFormat": "JSON", "isCrossDomain": "true", "callback": "",
        "columns": ",".join(_COLUMNS),
    }
    out, page_no = [], 1
    while True:
        r = requests.get(_PROGRAM_URL, params={**params, "pageNumber": page_no}, timeout=timeout)
        if not r.ok:
            raise RuntimeError(f"SPIN HTTP {r.status_code}: {r.text[:200]}")
        page = json.loads(r.text.strip("()"))
        out.extend(page.get("Programs", []))
        if max_results and len(out) >= max_results:
            return out[:max_results]
        if page.get("PageNumber", 1) >= page.get("NumberOfPages", 1):
            return out
        page_no += 1


def fetch_sponsor_list(*, timeout: int = 60) -> list:
    """The authoritative SPIN sponsor directory (~16k entries). Each record has
    ``spon_code`` (id), ``spon_name`` (canonical), ``spon_abbr``, ``ror_id``,
    ``spon_state``. This is what ``config/spin_target_funders.json`` resolves funder
    names against; pulls then use the exact ``spon_name``. Returns the whole list
    (the endpoint ignores paging and returns all sponsors in one response)."""
    params = {**_creds(), "responseFormat": "JSON", "isCrossDomain": "true", "callback": ""}
    r = requests.get(_SPONSOR_URL, params=params, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"SPIN SponsorList HTTP {r.status_code}: {r.text[:200]}")
    return json.loads(r.text.strip("()"))


# --- normalize: SPIN program row -> Opportunity -----------------------------
def _join(v) -> str:
    return "; ".join(str(x) for x in v) if isinstance(v, list) else (str(v) if v else "")


def _primary(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v else ""


def _to_iso(d) -> str:
    """SPIN deadline -> 'YYYY-MM-DD' ('' if absent/unparseable)."""
    if not d:
        return ""
    s = str(d).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)  # MM/DD/YYYY
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return ""


def normalize_spin(row: dict, *, ingested_at: str = None) -> Opportunity:
    due = _to_iso(row.get("deadline_date"))
    cfda = row.get("cfda")
    return Opportunity(
        opportunity_id=make_opportunity_id("spin", str(row.get("id") or "")),
        source="spin",
        source_id=str(row.get("id") or ""),
        source_url=(row.get("programurl") or row.get("sponwebsite") or "").strip(),
        sponsor=(row.get("spon_name") or "").strip(),
        title=(row.get("prog_title") or "").strip(),
        synopsis=(row.get("synopsis") or row.get("objective") or "").strip(),
        program_type=_primary(row.get("project_type")),
        mechanism="",  # foundations carry no NIH activity code
        award_ceiling=None, award_floor=None, estimated_funding=None,  # SPIN has no amount field
        due_date=due,
        # no deadline -> treat as rolling so regex_gate's expired-check doesn't drop it
        status="posted" if due else "continuous",
        eligibility_raw=" | ".join(x for x in (_join(row.get("applicant_type")),
                                                _join(row.get("geographic"))) if x),
        cfda_list=[c for c in cfda if c] if isinstance(cfda, list) else [],
        ingested_at=ingested_at or now_iso(),
    )


# --- precision gate: drop ambiguous BEFORE scoring --------------------------
# Allow only unambiguous research-funding types; drop if ANY type is non-research
# (a row tagged ['Research Grant','Prize or Award'] is dropped — precision > recall).
_ALLOW_TYPES = frozenset({
    "Research Grant", "Fellowship", "Collaborative Project", "Requests For Applications (NIH)",
})
_HARD_DROP_TYPES = frozenset({
    "Prize or Award", "Conference Attendance", "Conference Hosting", "Travel",
    "Artistic or Cultural Performance", "Exhibits/Collections", "Equipment",
    "Information Dissemination", "Service Delivery", "Student Scholarship",
})
_US_ELIGIBLE_RE = re.compile(r"united states|u\.s\.|\busa\b|no restrictions|^any$", re.I)
_SUSPENDED_RE = re.compile(r"suspend|withdrawn|cancell?ed|not accepting", re.I)


def _as_list(v):
    if isinstance(v, list):
        return v
    return [v] if v else []


def _us_eligible(geographic) -> bool:
    geo = _as_list(geographic)
    if not geo:
        return True  # unrestricted/unknown — let regex_gate + judge decide
    return any(_US_ELIGIBLE_RE.search(str(g)) for g in geo)


def keep_opportunity(row: dict):
    """Structured precision gate. Returns (kept: bool, reason: str). Pure."""
    pts = _as_list(row.get("project_type"))
    dropped = [p for p in pts if p in _HARD_DROP_TYPES]
    if dropped:
        return False, f"project_type:{dropped[0]}"
    if not any(p in _ALLOW_TYPES for p in pts):
        return False, f"project_type:not-allowed({pts or '∅'})"
    if not _us_eligible(row.get("geographic")):
        return False, f"geographic:{_as_list(row.get('geographic'))}"
    if _SUSPENDED_RE.search(row.get("prog_title") or ""):
        return False, "status:suspended"
    return True, ""


def demo():
    """Self-check: gate drops non-research, keeps research; normalize maps fields."""
    keep = {"id": "1", "prog_title": "Innovation Grant", "project_type": ["Research Grant"],
            "geographic": ["United States"], "spon_name": "X Foundation", "synopsis": "s"}
    drop_prize = {**keep, "id": "2", "project_type": ["Research Grant", "Prize or Award"]}
    drop_geo = {**keep, "id": "3", "geographic": ["Australia"]}
    drop_type = {**keep, "id": "4", "project_type": ["Training and Professional Development"]}
    assert keep_opportunity(keep)[0] is True
    assert keep_opportunity(drop_prize) == (False, "project_type:Prize or Award")
    assert keep_opportunity(drop_geo)[0] is False
    assert keep_opportunity(drop_type)[0] is False
    opp = normalize_spin(keep)
    assert opp.opportunity_id == "spin:1" and opp.source == "spin" and opp.sponsor == "X Foundation"
    assert opp.award_ceiling is None and opp.status == "continuous"  # no deadline -> rolling
    assert normalize_spin({**keep, "deadline_date": "12/31/2026"}).due_date == "2026-12-31"
    print("spin.demo OK")


if __name__ == "__main__":
    demo()
