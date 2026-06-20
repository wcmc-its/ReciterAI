"""The signal layers. Deterministic signals are fully implemented; the LLM and
author-affinity layers are structured against the existing utils but left as
scaffolds (prompt/calibration TODOs flagged inline).

Heavy deps (sqlalchemy, boto3, bedrock) are imported INSIDE the functions that
need them, so `acknowledgement_signal` and the combiner stay importable with
only the stdlib + PyYAML (keeps unit tests dependency-light).
"""
from __future__ import annotations

import re

from pipeline_cores.models import CoreDefinition, SignalResult

# Acronym-style aliases (<=5 uppercase chars) must match on a word boundary to
# avoid spurious substring hits; longer names match case-insensitively.
_ACRONYM = re.compile(r"^[A-Z0-9]{2,6}$")


# ---------------------------------------------------------------------------
# Signal 3 — acknowledgement / alias name-match (deterministic confirmer)
# ---------------------------------------------------------------------------
def acknowledgement_signal(full_text: str, core: CoreDefinition) -> SignalResult:
    """High-precision confirmer: did the full text name this core?

    ~100% precision but near-zero recall in the wild (most real users never name
    the core) — a confirmer, not a discoverer. Coverage is the PMC full-text
    subset only. `full_text` is the plain-text body (empty when unavailable).
    """
    res = SignalResult()
    if not full_text:
        return res
    for alias in core.aliases:
        if _ACRONYM.match(alias):
            m = re.search(rf"\b{re.escape(alias)}\b", full_text)  # case-sensitive acronym
        else:
            m = re.search(re.escape(alias), full_text, re.IGNORECASE)
        if m:
            s, e = max(0, m.start() - 70), min(len(full_text), m.end() + 70)
            res.ack_matched = True
            res.ack_alias = alias
            res.ack_snippet = full_text[s:e].strip()
            break
    return res


# ---------------------------------------------------------------------------
# Signal 2 — core-staff co-authorship (deterministic, resolved identity)
# ---------------------------------------------------------------------------
def coauthorship_index(engine, core: CoreDefinition, pmids: list = None) -> dict:
    """Map pmid -> [core-staff CWIDs on its byline], from RESOLVED authorship.

    Source: reciterdb.analysis_summary_author.personIdentifier (resolved CWID per
    author row; mirrored into the SPS DB). Matched on personIdentifier, NEVER on
    name. Only `tracked` staff can be found here; untracked staff (personIdentifier
    NULL) need the upstream ReCiter-target fix. Validated: 39% recall / 100%
    precision on the 237-paper pilot.
    """
    from sqlalchemy import bindparam, text  # lazy

    cwids = core.tracked_staff_cwids
    if not cwids:
        return {}
    sql = (
        "SELECT DISTINCT pmid, personIdentifier FROM analysis_summary_author "
        "WHERE personIdentifier IN :cwids"
    )
    binds = [bindparam("cwids", expanding=True)]
    params = {"cwids": list(cwids)}
    if pmids:
        sql += " AND pmid IN :pmids"
        binds.append(bindparam("pmids", expanding=True))
        params["pmids"] = [int(p) for p in pmids]
    stmt = text(sql).bindparams(*binds)
    out: dict = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, params):
            out.setdefault(str(row.pmid), []).append(row.personIdentifier)
    return out


# ---------------------------------------------------------------------------
# Signal 1 — author x core affinity (repeat-user prior; compounding)
# ---------------------------------------------------------------------------
def author_affinity(confirmed_pairs: dict, byline_cwids: list, core_id: str) -> float:
    """Prior that THIS paper used the core, from its authors' history.

    Core users are overwhelmingly repeat users: once a CWID is confirmed (claim /
    acknowledgement / staff-coauthorship) for a core, all of that author's other
    papers inherit a prior. `confirmed_pairs` maps cwid -> {core_id: strength}
    accumulated from prior runs + SPS claims. Returns the max affinity across the
    paper's byline (noisy-OR across authors could replace `max` once calibrated).

    TODO(calibration): add light time decay and weight by how many confirmed
    papers the author has for the core.
    """
    best = 0.0
    for cwid in byline_cwids:
        best = max(best, confirmed_pairs.get(cwid, {}).get(core_id, 0.0))
    return best


# ---------------------------------------------------------------------------
# Signal 4 — LLM triage (two-pass Bedrock; ranking only, never auto-label)
# ---------------------------------------------------------------------------
# Recall-first screen cutoff: a pub whose Haiku screen score is >= this advances
# to Sonnet dense scoring. Calibrated on the 237-paper pilot (Bedrock Haiku 4.5):
# cutoff 2 retains 100% of true positives at the screen, deliberately below the
# combiner's surface threshold (score>=3) so dense scoring, not the cheap screen,
# decides what is surfaced. Dense (Sonnet 4.6) AUC vs human labels = 0.933;
# at score>=3 -> 94% precision / 83% recall.
SCREEN_CUTOFF = 2


def _screen_prompt(core: CoreDefinition, title: str, abstract: str) -> str:
    return (
        "Quickly screen whether a WCM institutional core facility was plausibly "
        f"USED to produce this research.\n\nCore: \"{core.name}: {core.llm_description}\"\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "From title+abstract only, reply with a single integer 1 (clearly unrelated) "
        "to 10 (clearly used the core). When unsure, lean higher. Integer only."
    )


def _dense_prompt(core: CoreDefinition, title: str, abstract: str) -> str:
    return (
        "Score whether a specific WCM core facility was USED to produce this "
        f"research.\n\nCore facility: \"{core.name}: {core.llm_description}\"\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "Judge ONLY from title+abstract. A paper whose methods plainly required "
        "the core's instrumentation scores high; incidental/clinical mentions of "
        "the modality, or use of a different facility, score low.\n"
        'Return ONLY JSON: {"score": <int 1-10>, "rationale": "<=80 chars"}'
    )


def llm_triage(bedrock, core: CoreDefinition, pubs: list) -> dict:
    """Map pmid -> {"screen", "score", "rationale"} from title+abstract.

    Two-pass, mirroring score_publications.py: Pass 1 Haiku screen (recall-first,
    cheap); Pass 2 Sonnet dense score + rationale only for pubs at/above
    SCREEN_CUTOFF. `score` is the dense score when computed, else the screen score.
    Conservative in validation (4% FP on clear negatives) — used to RANK the claim
    queue, never to auto-label.

    Production optimization (TODO): screen ALL cores in one Haiku call per pub
    (as score_publications screens all topics at once) instead of once per core.
    """
    from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL, BedrockEmptyContentError  # lazy

    out: dict = {}
    for pub in pubs:
        pmid, title, abstract = str(pub["pmid"]), pub.get("title", ""), pub.get("abstract", "")
        try:
            screen = _parse_int(bedrock.call(model=HAIKU_MODEL,
                                             messages=[{"role": "user", "content": _screen_prompt(core, title, abstract)}]))
        except BedrockEmptyContentError:
            screen = 1  # safety-filter on a biomedical abstract -> treat as screened out
        rec = {"screen": screen, "score": screen, "rationale": ""}
        if screen >= SCREEN_CUTOFF:
            try:
                dense = bedrock.call_json(model=SONNET_MODEL,
                                          messages=[{"role": "user", "content": _dense_prompt(core, title, abstract)}])
                rec["score"] = _parse_int(dense.get("score"))
                rec["rationale"] = str(dense.get("rationale", ""))[:80]
            except BedrockEmptyContentError:
                pass  # keep the screen score; no rationale
        out[pmid] = rec
    return out


def _parse_int(raw) -> int:
    if isinstance(raw, (int, float)):
        return max(1, min(10, int(round(raw))))
    m = re.search(r"\b(10|[1-9])\b", str(raw or ""))
    return int(m.group(1)) if m else 1
