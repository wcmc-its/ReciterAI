"""The signal layers. Deterministic signals are fully implemented; the LLM and
author-affinity layers are structured against the existing utils but left as
scaffolds (prompt/calibration TODOs flagged inline).

Heavy deps (sqlalchemy, boto3, bedrock) are imported INSIDE the functions that
need them, so `acknowledgement_signal` and the combiner stay importable with
only the stdlib + PyYAML (keeps unit tests dependency-light).
"""
from __future__ import annotations

import logging
import re

from pipeline_cores.models import CoreDefinition, SignalResult

logger = logging.getLogger(__name__)

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
# Affinity strength as a function of how many confirmed/claimed papers an author
# has for a core. Core users are overwhelmingly repeat users, so even one prior
# confirmed paper is a meaningful prior; more confirmed papers -> stronger, with
# a cap below the deterministic-confirmer likelihoods.
_AFFINITY_BASE = 0.45      # one confirmed paper by this author for this core
_AFFINITY_STEP = 0.15      # per additional confirmed paper
_AFFINITY_CAP = 0.85


def affinity_strength(confirmed_count: int) -> float:
    if confirmed_count <= 0:
        return 0.0
    return min(_AFFINITY_CAP, _AFFINITY_BASE + _AFFINITY_STEP * (confirmed_count - 1))


def build_affinity_index(user_paper_counts: dict) -> dict:
    """cwid -> {core_id: strength} from per-(cwid, core) confirmed-paper counts.

    `user_paper_counts` maps cwid -> {core_id: n_confirmed_papers}, aggregated
    from this run's confirmations plus prior confirmed/claimed records.
    """
    index: dict = {}
    for cwid, by_core in user_paper_counts.items():
        index[cwid] = {core_id: affinity_strength(n) for core_id, n in by_core.items()}
    return index


def author_affinity(affinity_index: dict, byline_cwids: list, core_id: str) -> float:
    """Prior that THIS paper used the core, from its authors' confirmed history.

    Noisy-OR across the byline's per-author strengths — two repeat users of the
    core on one paper are stronger combined evidence than either alone:
    1 - Π(1 - strength_i). Clamped to _AFFINITY_CAP so the prior never reaches the
    deterministic-confirmer ceiling (a real acknowledgement/staff-coauthor must
    still outrank any stack of priors). `affinity_index` is the output of
    build_affinity_index (cwid -> {core_id: strength}).

    TODO(calibration): per-author time decay — weight each confirmation by recency
    so a 2014 paper counts less than a 2024 one. Deferred: needs the publication
    year carried into persist.scan_prior_core_usage and a half-life tuned on
    analysis/labeled_set.csv before it can be trusted.
    """
    complement = 1.0
    for cwid in byline_cwids:
        strength = affinity_index.get(cwid, {}).get(core_id, 0.0)
        if strength > 0.0:
            complement *= (1.0 - strength)
    if complement >= 1.0:                       # no author contributed any affinity
        return 0.0
    return min(_AFFINITY_CAP, 1.0 - complement)


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


def _screen_all_prompt(cores: list, title: str, abstract: str) -> str:
    catalog = "\n".join(f'  "{c.core_id}": {c.name} — {c.llm_description}' for c in cores)
    return (
        "Quickly screen which WCM institutional core facilities were plausibly "
        "USED to produce this research, judging from title+abstract only.\n\n"
        f"Cores (id: name — what it does):\n{catalog}\n\n"
        f"Title: {title}\nAbstract: {abstract or '(none)'}\n\n"
        "For EACH core id give an integer 1-10 for how plausibly that core was USED to "
        "produce this work. Be recall-first: give at least 2 to any core whose involvement "
        "is even slightly plausible from the methods the title/abstract imply, and reserve 1 "
        "only for cores clearly unrelated to the work. When unsure, lean higher. "
        'Return ONLY a JSON object mapping every core id to its integer, e.g. {"2": 7, "5": 1}. '
        "Include every core id."
    )


def _parse_core_scores(raw, cores: list) -> dict:
    """Coerce a model JSON reply into {core_id: int 1-10}; missing id -> 1."""
    raw = raw or {}
    return {c.core_id: _parse_int(raw.get(c.core_id, raw.get(str(c.core_id), 1))) for c in cores}


def screen_all_cores(bedrock, cores: list, pubs: list) -> dict:
    """One Haiku call per pub screens it against ALL cores at once.

    Returns {pmid: {core_id: screen_int}}. This is the production cost lever:
    ~13x fewer Haiku screen calls than screening each core separately (one call
    per pub instead of one per pub*core). A pub the safety filter drops, or whose
    reply omits a core, defaults that core to 1 (screened out) — dense scoring is
    where anything at/above SCREEN_CUTOFF is re-judged per core.

    CALIBRATION (237-paper pilot, 2026-06-21, analysis/calibrate_all_cores_screen.py):
    this lost screen recall vs the per-core screen — 89.1% (vs 100%), 15 TP
    regressions that collapse to score 1 (judging 13 cores at once makes Haiku too
    decisive, erasing the recall-first "unsure -> 2" band). A recall-first prompt
    floor recovered it only to 93.4% (9 regressions) while ~doubling dense fan-out
    (2.93 -> 4.85 cores/pub). So this path is OPT-IN (run.py --all-cores-screen) and
    NOT the default until it reaches per-core parity; the per-core screen carries the
    full run. Re-run the calibration before flipping it on.
    """
    from utils.bedrock_client import HAIKU_MODEL, BedrockEmptyContentError  # lazy

    out: dict = {}
    for pub in pubs:
        pmid, title, abstract = str(pub["pmid"]), pub.get("title", ""), pub.get("abstract", "")
        try:
            raw = bedrock.call_json(
                model=HAIKU_MODEL,
                messages=[{"role": "user", "content": _screen_all_prompt(cores, title, abstract)}],
            )
            out[pmid] = _parse_core_scores(raw, cores)
        except BedrockEmptyContentError:
            out[pmid] = {c.core_id: 1 for c in cores}  # safety filter -> screen all out
    return out


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


def _triage_one(bedrock, core: CoreDefinition, pub: dict, screen_map: dict):
    """Score a single pub: Haiku screen (or shared screen) then Sonnet dense pass."""
    from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL, BedrockEmptyContentError  # lazy

    pmid, title, abstract = str(pub["pmid"]), pub.get("title", ""), pub.get("abstract", "")
    if screen_map is not None:
        screen = screen_map.get(pmid, {}).get(core.core_id, 1)  # shared all-cores screen
    else:
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
    return pmid, rec


def llm_triage(bedrock, core: CoreDefinition, pubs: list, screen_map: dict = None,
               max_workers: int = 8) -> dict:
    """Map pmid -> {"screen", "score", "rationale"} from title+abstract.

    Two-pass, mirroring score_publications.py: Pass 1 Haiku screen (recall-first,
    cheap); Pass 2 Sonnet dense score + rationale only for pubs at/above
    SCREEN_CUTOFF. `score` is the dense score when computed, else the screen score.
    Conservative in validation (4% FP on clear negatives) — used to RANK the claim
    queue, never to auto-label.

    `screen_map` is the precomputed all-cores screen ({pmid: {core_id: int}} from
    screen_all_cores): when supplied, Pass 1 reads the shared screen for THIS core
    instead of a per-core Haiku call. Omit it for the per-core Haiku screen (the
    calibration-validated default).

    THREADED: pubs are scored concurrently with `max_workers` threads (Bedrock
    calls are I/O-bound). A serial loop over a full corpus is both slow and
    fragile — one slow/hung Converse call would block every subsequent pub up to
    the client read_timeout — so per-pub scoring is fanned out and any pub that
    errors (timeout/throttle/etc.) is logged and screened out (score 1) rather
    than killing the batch. Pair with a short-read_timeout BedrockClient (run.py
    builds one) so a hung call fails fast instead of stalling a worker. Use
    max_workers<=1 for deterministic serial scoring.
    """
    if not pubs:
        return {}

    out: dict = {}
    failures = 0

    def _safe(pub):
        return _triage_one(bedrock, core, pub, screen_map)

    if max_workers and max_workers > 1 and len(pubs) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed  # lazy

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_safe, p): str(p["pmid"]) for p in pubs}
            for fut in as_completed(futs):
                pmid = futs[fut]
                try:
                    pmid, rec = fut.result()
                except Exception as exc:  # noqa: BLE001 — resilience: never let one pub kill the batch
                    failures += 1
                    logger.warning("triage failed for pmid %s (%s) -> screened out", pmid, type(exc).__name__)
                    rec = {"screen": 1, "score": 1, "rationale": ""}
                out[pmid] = rec
    else:
        for pub in pubs:
            pmid = str(pub["pmid"])
            try:
                pmid, rec = _safe(pub)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning("triage failed for pmid %s (%s) -> screened out", pmid, type(exc).__name__)
                rec = {"screen": 1, "score": 1, "rationale": ""}
            out[pmid] = rec

    if failures:
        logger.warning("llm_triage: %d/%d pubs failed scoring and were screened out", failures, len(pubs))
    return out


def _parse_int(raw) -> int:
    if isinstance(raw, (int, float)):
        return max(1, min(10, int(round(raw))))
    m = re.search(r"\b(10|[1-9])\b", str(raw or ""))
    return int(m.group(1)) if m else 1
