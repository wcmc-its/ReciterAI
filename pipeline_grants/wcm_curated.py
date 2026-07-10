"""WCM-curated awards source for the grants pipeline.

A second ingest source alongside ``grants_gov``: a hand-curated CSV of funding
*awards/prizes* that WCM wants surfaced (``pipeline_grants/data/``). Unlike the
Grants.gov firehose, this list is human-vetted, so the curated ingest
(``ingest_curated``) deliberately **bypasses the denoise gates** — ``regex_gate``
would drop these on the word "prize" and ``judge_opportunity`` is prompted to
mark prizes ``is_research=false``. We trust the curation (``is_research=True``)
and derive ``appeal_by_stage`` from the curated ``Career Stage`` column rather
than the LLM judge.

Pure + side-effect-free (parsing, mapping, the stage heuristic) so it is
unit-tested without network or AWS — mirroring ``normalize`` / the mapper modules.
Synopsis text is produced separately by ``enrich_wcm_curated`` (the scorer needs
descriptive text; these rows carry only a title + a coarse field).
"""
import csv
import hashlib
import re

from pipeline_grants.models import Opportunity, make_opportunity_id
from pipeline_grants.normalize import _activity_code

CURATED_SOURCE = "wcm_curated"

# Real CSV headers (the export carries a banner row above them).
H_NAME = "Award Name"
H_SPONSOR = "Sponsoring Organization"
H_FIELD = "Award Field"
H_STAGE = "Career Stage"
H_AMOUNT = "Award Amount"
H_DEADLINE = "Nomination Deadline"
H_WEBSITE = "Website"

# The five SPS CareerStage buckets (mirrors denoise._STAGES / SPS lib/career-stage).
_STAGES = ("grad", "postdoc", "early", "mid", "senior")


def _find_header_row(rows: list) -> int:
    """Index of the header row (the export prefixes a 'Document Last Updated' banner).

    Robust to the banner being present or absent: the header is the first row that
    contains the ``Award Name`` column.
    """
    for i, row in enumerate(rows):
        if any((c or "").strip() == H_NAME for c in row):
            return i
    raise ValueError(f"no header row containing {H_NAME!r} found")


def read_curated_csv(path: str) -> list:
    """Read the curated CSV into a list of dict rows, skipping the banner row.

    Works for both the raw export (banner + header) and the enriched file we
    write ourselves (clean header). Extra columns (e.g. ``synopsis``) pass through.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.reader(fh))
    if not raw:
        return []
    h = _find_header_row(raw)
    header = [(c or "").strip() for c in raw[h]]
    out = []
    for cells in raw[h + 1:]:
        if not any((c or "").strip() for c in cells):
            continue  # blank line
        row = {header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))}
        if not (row.get(H_NAME) or "").strip():
            continue  # require an award name
        out.append(row)
    return out


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")


def make_source_id(award_name: str, sponsor: str) -> str:
    """Stable, collision-proof id from name + sponsor.

    The export's row number is not stable across re-exports, so we key on
    name+sponsor. A short content hash is appended so the readable-slug truncation
    can never collide two *distinct* awards; identical (name, sponsor) inputs hash
    to the same id (so genuine duplicate rows dedup rather than fork).
    """
    base = f"{award_name.strip()} {sponsor.strip()}"
    digest = hashlib.sha1(base.lower().encode("utf-8")).hexdigest()[:6]
    return f"{slugify(base)[:72].strip('-')}-{digest}"


_AMOUNT_RE = re.compile(r"\$?\s*([0-9][0-9,]{2,})")


def parse_amount(text: str):
    """Largest dollar figure in the cell → int; None when there's no number."""
    if not text:
        return None
    nums = [int(m.replace(",", "")) for m in _AMOUNT_RE.findall(text)]
    return max(nums) if nums else None


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.IGNORECASE
)


def parse_deadline(text: str) -> str:
    """First 'Month DD, YYYY' in the cell → ISO 'YYYY-MM-DD'; '' when none/unparseable."""
    if not text:
        return ""
    m = _DATE_RE.search(text)
    if not m:
        return ""
    month = _MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    year = int(m.group(3))
    if not (1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def career_stage_to_appeal(text: str) -> dict:
    """Map the curated free-text ``Career Stage`` to an approximate appeal-by-stage map.

    Heuristic, not exact: matched stages score high (0.9), unmatched low (0.1);
    "all stages" spreads broad. Token-driven so it degrades gracefully on the long
    tail of curated phrasings. Returns the five SPS buckets.
    """
    t = (text or "").lower()
    if not t:
        return {"grad": 0.2, "postdoc": 0.3, "early": 0.5, "mid": 0.5, "senior": 0.5}
    if "all stage" in t or "all career" in t or "all faculty" in t or "all ranks" in t:
        return {"grad": 0.6, "postdoc": 0.7, "early": 0.8, "mid": 0.8, "senior": 0.8}

    tokens = {
        "grad": ("graduate", "predoctoral", "pre-doctoral", "phd student", "doctoral student",
                 "medical student", "student", "trainee", "postbaccalaureate", "postbacc"),
        "postdoc": ("postdoc", "post-doc", "postdoctoral", "fellow"),
        "early": ("early-career", "early career", "early-to", "junior", "young investigator",
                  "young scientist", "young scientists", "assistant professor", "pre-tenure",
                  "pretenured", "pre-tenured", "recent phd", "rising", "emerging", "under 45",
                  "under the age of", "within 5", "within 7", "within 10", "within 15"),
        "mid": ("mid-career", "mid career", "middle-career", "middle career", "mid- to",
                "mid-to", "middle to", "associate professor", "mid-level"),
        "senior": ("senior", "established", "tenured", "full professor", "distinguished",
                   "lifetime", "leading", "leader", "sustained", "major contribution",
                   "outstanding", "nam member", "established investigator"),
    }
    appeal = {s: 0.1 for s in _STAGES}
    for stage, keys in tokens.items():
        if any(k in t for k in keys):
            appeal[stage] = 0.9
    if all(v == 0.1 for v in appeal.values()):
        # No token matched — neutral independent-stage spread.
        return {"grad": 0.2, "postdoc": 0.3, "early": 0.5, "mid": 0.5, "senior": 0.5}
    return appeal


def appeal_for_row(row: dict) -> dict:
    return career_stage_to_appeal(row.get(H_STAGE, ""))


def fallback_synopsis(row: dict) -> str:
    """Field-based synopsis when website enrichment is unavailable (coarse but scorable)."""
    name = (row.get(H_NAME) or "").strip()
    field = (row.get(H_FIELD) or "").strip()
    sponsor = (row.get(H_SPONSOR) or "").strip()
    parts = [name]
    if field:
        parts.append(f"Research field: {field}.")
    if sponsor:
        parts.append(f"Sponsored by {sponsor}.")
    return " ".join(p for p in parts if p)


def make_curated_opportunity(row: dict, *, synopsis: str, ingested_at: str) -> Opportunity:
    """One curated CSV row → an ``Opportunity`` (synopsis supplied by enrichment)."""
    name = (row.get(H_NAME) or "").strip()
    sponsor = (row.get(H_SPONSOR) or "").strip()
    source_id = make_source_id(name, sponsor)
    return Opportunity(
        opportunity_id=make_opportunity_id(CURATED_SOURCE, source_id),
        source=CURATED_SOURCE,
        source_id=source_id,
        source_url=(row.get(H_WEBSITE) or "").strip(),
        sponsor=sponsor,
        title=name,
        synopsis=(synopsis or "").strip(),
        program_type="award",
        # Most curated foundation awards have no NIH-style activity code — empty is
        # correct for them — but recover one when the title carries it (#288), the
        # same title fallback normalize_grantsgov and backfill_prestige use.
        mechanism=_activity_code(name),
        estimated_funding=parse_amount(row.get(H_AMOUNT, "")),
        open_date="",
        due_date=parse_deadline(row.get(H_DEADLINE, "")),
        status="open",
        eligibility_raw=(row.get(H_STAGE) or "").strip(),
        cfda_list=[],
        ingested_at=ingested_at,
    )
