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

from pipeline_cores.fulltext import to_plain_text
from pipeline_cores.models import METHOD_FAMILY_TIERS, CoreDefinition, SignalResult

logger = logging.getLogger(__name__)

# Acronym-style aliases (<=5 uppercase chars) must match on a word boundary to
# avoid spurious substring hits; longer names match case-insensitively.
_ACRONYM = re.compile(r"^[A-Z0-9]{2,6}$")

# The window each side of a match that the institution read looks at — 140 chars,
# the window the alias-power measurement used.
_WINDOW = 140

# Weill Cornell itself: always home, for every core.
_WCM = re.compile(r"Weill Cornell|Cornell Univ|Weill Medical|\bWCMC?\b", re.I)

# Funders are not host institutions. An acknowledgements section names the NIH far
# more often than it names anybody's university, so leaving these in would read
# almost every ack window as "a DIFFERENT institution".
_FUNDER = re.compile(
    r"National Institutes? of Health|National Cancer Institute|National Science Foundation"
    r"|Howard Hughes Medical Institute|Department of (?:Defense|Veterans Affairs)"
    r"|Rockefeller Foundation"   # the FUNDER, not the Tri-I university
    r"|\bNIH\b|\bNCI\b|\bNSF\b|\bHHMI\b", re.I)

# Some OTHER institution is named in the window (21-53% of today's matches).
_OTHER_INSTITUTION = re.compile(
    r"\bUniversit(?:y|ies|at|\u00e4t|e|\u00e9)\b|\bCollege\b|\bInstitut(?:e|es|o|ut)\b"
    r"|\bHospital\b|\bMedical Cent(?:er|re)\b|\bCancer Cent(?:er|re)\b"
    r"|\bSchool of Medicine\b|\bHealth System\b|\bClinic\b|\bAcademy of Sciences\b", re.I)

# JATS blocks for the match-location read. Same block idiom (and the same nesting
# caveat) as suggest_aliases.ack_text.
_ACK_BLOCK = re.compile(r"<ack\b.*?</ack\s*>", re.I | re.S)
_TITLED_SEC = re.compile(
    r"<sec\b[^>]*>\s*(?:<label>.*?</label>\s*)?<title>([^<]*)</title>(.*?)</sec>", re.I | re.S)
_ACK_TITLE = re.compile(r"acknowledg|funding|financial support", re.I)
_METHODS_TITLE = re.compile(r"method|material|experimental|procedure", re.I)


def _alias_pattern(name: str):
    """Acronym -> case-sensitive word boundary; anything longer -> case-insensitive."""
    if _ACRONYM.match(name):
        return re.compile(rf"\b{re.escape(name)}\b")
    return re.compile(re.escape(name), re.IGNORECASE)


def classify_institution(text: str, match, core: CoreDefinition) -> str:
    """"home" | "other" | "none" for the institution named around an alias match.

    Three states, and the third is the subtle one (2026-09-03 audit): naming WCM or
    a Tri-I partner is strong POSITIVE evidence, naming a different institution is
    strong NEGATIVE, and naming NOBODY is only ambiguous for a GENERIC alias — a
    specific name needs no qualifier (ARCH is 43% "none" with 0% "other"). So this
    reports the state and lets the weighting condition it on alias specificity;
    it is deliberately NOT a gate.
    """
    lo, hi = max(0, match.start() - _WINDOW), min(len(text), match.end() + _WINDOW)
    before, after = text[lo:match.start()], text[match.end():hi]
    window = _FUNDER.sub(" ", before + match.group(0) + after)
    if _WCM.search(window) or any(_alias_pattern(p).search(window)
                                  for p in core.partner_institutions):
        return "home"
    # The NEGATIVE read drops the matched alias: a core whose own name carries an
    # institution word ("Institute for Precision Medicine Biobank") is not evidence
    # of somebody else's institution. The positive read above keeps it, because an
    # alias that spells out "… Weill Cornell Medicine" IS the home affiliation.
    outside = _FUNDER.sub(" ", before + " " + after)
    return "other" if _OTHER_INSTITUTION.search(outside) else "none"


def match_section(xml: str, alias: str) -> str:
    """Which JATS block names `alias`: "ack" | "methods" | "body" ("" without XML).

    EXTRACTED, NOT WEIGHTED. <ack> vs methods vs a passing body mention are plainly
    not equal evidence, but nobody has measured by how much, so combine() must not
    price this until a labelling pass does (SPEC feature 3).

    Caveat inherited from the block idiom: a <sec> containing SUBSECTIONS is cut at
    the first </sec>, so an alias deep inside a subsection of Methods reads as
    "body". Harmless while the weight is zero; measure before trusting it.
    """
    if not xml:
        return ""                                   # plain text only: no structure to read
    pattern = _alias_pattern(alias)
    blocks = [("ack", b) for b in _ACK_BLOCK.findall(xml)]
    for title, body in _TITLED_SEC.findall(xml):
        if _ACK_TITLE.search(title):
            blocks.append(("ack", body))
        elif _METHODS_TITLE.search(title):
            blocks.append(("methods", body))
    for label, block in blocks:
        if pattern.search(to_plain_text(block)):
            return label
    return "body"


# ---------------------------------------------------------------------------
# Signal 3 — acknowledgement / alias name-match (deterministic confirmer)
# ---------------------------------------------------------------------------
def acknowledgement_signal(full_text: str, core: CoreDefinition, xml: str = "") -> SignalResult:
    """High-precision confirmer: did the full text name this core?

    ~100% precision but near-zero recall in the wild (most real users never name
    the core) — a confirmer, not a discoverer. Coverage is the PMC full-text
    subset only. `full_text` is the plain-text body (empty when unavailable).

    Also records the evidence a flat confirmer would throw away — the alias's
    global PMC hit count, the institution named around the match, and which JATS
    block it sits in. `xml` is the RAW PMC XML for the same paper (fulltext.get_xml;
    get() is just to_plain_text of it, so passing both costs no extra fetch);
    without it the section degrades to "" and everything else still works.
    """
    res = SignalResult()
    if not full_text:
        return res
    for alias in core.aliases:
        m = _alias_pattern(alias).search(full_text)
        if m:
            s, e = max(0, m.start() - 70), min(len(full_text), m.end() + 70)
            res.ack_matched = True
            res.ack_alias = alias
            res.ack_snippet = full_text[s:e].strip()
            res.ack_alias_hits = core.alias_hits.get(alias)
            res.ack_institution = classify_institution(full_text, m, core)
            res.ack_section = match_section(xml, alias)
            break
    return res


# ---------------------------------------------------------------------------
# Signal 3b — A2 method families (deterministic join, no I/O)
# ---------------------------------------------------------------------------
def method_family_signal(family_index: dict, pmid: str, core: CoreDefinition):
    """(evidence, tier) for one (publication, core) pair.

    `evidence` is [(family_label, tool_display_name, sentence), ...] — ONE entry per
    curated family that fired, ranked strongest tier first and, inside a tier, in the
    order the dictionary lists them, which is measured-lift order.

    Pure. `family_index` is `method_families.load_family_index()` — the whole join is
    already done, so this is a dict lookup and a tier walk; nothing here reads S3, a DB
    or Bedrock, which is what lets the index be loaded once per run.

    EVERY family keeps its own tool and sentence, not just the top one. Papers carry
    several families at once (median 2 on core 14's live queue, max 4; the artifact's
    ceiling is 9), and the reviewer-facing question is what the paper DID — a bare
    "Regression modeling" with the quote belonging to a different family is a label the
    reviewer cannot check. The join already holds all three fields for every family, so
    carrying them costs a list comprehension; dropping them would cost a full re-scoring
    run to recover, on an engine with no schedule.

    ONE entry per family, not per (family, tool): several tools can sit in the same
    family (up to 6 tool rows behind 4 families on the live queue) and a second tool in a
    family the paper already claims is not a second claim. The tool kept is the first in
    the index's stable sort.

    The TIER returned is the strongest that fired, and only that one. combine() emits a
    single `method:*` key from it: the tiers co-occur (a paper with a 399x family usually
    also does regression), so reporting every tier would invite summing correlated
    evidence.
    """
    rows = family_index.get(str(pmid)) or []
    if not rows or not core.method_families:
        return [], ""
    ranked, seen = [], set()
    for tier in METHOD_FAMILY_TIERS:                      # strongest first
        for label in core.method_families.get(tier, []):  # curator order = lift order
            # Casefolded on both sides: the dictionary side by load_cores, the artifact
            # side here. The label KEPT is the artifact's, so the canonical display form
            # is what reaches DynamoDB.
            hit = next((r for r in rows if r[0].casefold() == label), None)
            # `seen` guards the one duplicate the curator can create: the same label
            # listed under two tiers. It keeps the STRONGER placement, since tiers are
            # walked strongest-first.
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                ranked.append(((hit[0], hit[1], hit[2]), tier))
    if not ranked:
        return [], ""
    return [ev for ev, _tier in ranked], ranked[0][1]


# ---------------------------------------------------------------------------
# Signal 2 — core-staff co-authorship (deterministic, resolved identity)
# ---------------------------------------------------------------------------
def resolved_staff(engine, cwids) -> set:
    """The subset of `cwids` ReCiter has resolved onto at least one author row — i.e.
    the staff coauthorship_index can match TODAY. A live lookup, not a dictionary
    flag: staff come and go, and ReCiter starts (or stops) resolving people over time."""
    from sqlalchemy import bindparam, text  # lazy

    cwids = sorted(set(cwids))
    if not cwids:
        return set()
    stmt = text("SELECT DISTINCT personIdentifier FROM analysis_summary_author "
                "WHERE personIdentifier IN :cwids").bindparams(bindparam("cwids", expanding=True))
    with engine.connect() as conn:
        return {row.personIdentifier for row in conn.execute(stmt, {"cwids": cwids})}


def coauthorship_index(engine, core: CoreDefinition, pmids: list = None,
                       extra_cwids=()) -> dict:
    """Map pmid -> [core-staff CWIDs on its byline], from RESOLVED authorship.

    Source: reciterdb.analysis_summary_author.personIdentifier (resolved CWID per
    author row; mirrored into the SPS DB). Matched on personIdentifier, NEVER on
    name. Every listed staff member is asked for (the dictionary's `staff:` plus
    `extra_cwids`, SPS's curated CORE#{id}/STAFF); one ReCiter has not resolved simply
    matches nothing until it does, so coverage is live rather than a stored flag.
    A staff match is one signal: it raises the likelihood but never confirms on its
    own (combine's hold). Validated: 39% recall / 100% precision on the 237-paper
    pilot, before the large-lab PIs were included.
    """
    from sqlalchemy import bindparam, text  # lazy

    cwids = sorted(set(core.staff_cwids) | set(extra_cwids))
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
# Signal 1 — author x core affinity (repeat-user RATE)
# ---------------------------------------------------------------------------
# How much of an author's OWN output already belongs to this core, not how many
# papers of theirs do. Core users are overwhelmingly repeat users, but a raw
# confirmed-paper count cannot tell a core's regular from someone who passed
# through it four times: the hand-picked curve this replaced (0.45 + 0.15 per
# extra paper, capped at 0.85) put a single author with 4 confirms at the ceiling,
# left 83.9% of core 14's live 347-row queue on that one value, and took 6 distinct
# values in total. The rate separates where the count could not — panel B
# conditional AUC 0.8770 vs 0.8091, 39 distinct values on those same 347 rows with
# a largest tie of 19.3% (vs 83.3%) — which is what lets combine.WEIGHTS price it
# as three fitted buckets instead of a slope through a made-up curve.


def build_affinity_index(user_paper_counts: dict, author_totals: dict) -> dict:
    """cwid -> {core_id: rate}: each author's SHARE of their own corpus output
    that already belongs to the core.

    `user_paper_counts` maps cwid -> {core_id: n_confirmed_papers}, aggregated
    from this run's confirmations plus prior confirmed/claimed records.
    `author_totals` maps cwid -> that author's TOTAL papers in the scoreable WCM
    corpus (ingest.fetch_author_totals). The denominator is the CORPUS (80,203
    pubs / 13,960 resolved authors), NOT all of analysis_summary_author (392,769
    pmids): the unrestricted form deflates every rate by ~4x, which walks real
    core regulars down out of the top bucket.

    An author with NO corpus total is DROPPED, not floored. "We could not measure this
    author's output" and "this author's entire output is this core's work" are opposite
    claims, and flooring conflated them into rate 1.0 = `aff:core` = +4.93 nats, which
    alone clears DEFAULT_CONFIRM_THRESHOLD — so an unmeasurable author auto-confirmed
    every paper they touched, with no acknowledgement, no staff co-author and no LLM
    score behind it. Absent evidence contributes nothing here, as everywhere else.

    n > total should be impossible once the caller gates its numerator through
    ingest.filter_corpus_pmids (both sides of the ratio on the corpus), so it is clamped
    AND logged rather than silently floored: it means a caller skipped the gate.
    """
    index: dict = {}
    missing, over = [], []
    for cwid, by_core in user_paper_counts.items():
        total = author_totals.get(cwid, 0)
        if not total:
            missing.append(cwid)
            continue
        if any(n > total for n in by_core.values()):
            over.append(cwid)
        index[cwid] = {core_id: min(n / total, 1.0) for core_id, n in by_core.items()}
    if missing:
        logger.warning("build_affinity_index: %d authors have no corpus paper total and "
                       "were DROPPED from the affinity index (rate unknown, not maximal)",
                       len(missing))
    if over:
        logger.warning("build_affinity_index: %d authors have more core confirms than "
                       "corpus papers — the numerator was not gated through "
                       "ingest.filter_corpus_pmids; rates clamped to 1.0", len(over))
    return index


def author_affinity(affinity_index: dict, byline_cwids: list, core_id: str) -> float:
    """Prior that THIS paper used the core: the MAX rate across its byline.

    MAX, not noisy-OR. Noisy-OR over the byline is a monotone function of HOW MANY
    authors fire, and that count is the one thing measured NOT to separate here
    (panel B AUC 0.6505, and its curve turns over past k=2). One author whose work
    is largely this core's work is the evidence; four people who each used the core
    once are not four times that, and under noisy-OR they outscored the core's
    heaviest single user. Returns 0.0 when no author on the byline has any history
    with the core. `affinity_index` is the output of build_affinity_index
    (cwid -> {core_id: rate}).

    TODO(calibration): per-author time decay — weight each confirmation by recency
    so a 2014 paper counts less than a 2024 one. Deferred: needs the publication
    year carried into persist.scan_prior_core_usage and a half-life tuned on
    analysis/labeled_set.csv before it can be trusted.
    """
    return max((affinity_index.get(cwid, {}).get(core_id, 0.0) for cwid in byline_cwids),
               default=0.0)


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
    raw = raw if isinstance(raw, dict) else {}    # a valid-but-non-dict reply (array/int) -> screen out
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


# ---------------------------------------------------------------------------
# Batched one-core screen (the batch_screen run-mode) — Sonnet, title-only
# ---------------------------------------------------------------------------
# Validated mechanism (analysis/prototype_one_core_synopsis.py + the cores-subheadings
# FINDINGS): ONE core per call, ~40 papers/batch, TITLE input recovers 95-100% recall
# where the all-cores batch capped at 88% — focus is the lever (Sonnet ~= Opus). Returns
# a graded confidence 1-10 per pmid that feeds the candidate/curator/drop bands. Title
# keeps it affordable (the ~$30-280 frontier); abstract buys precision, not recall.

def _batch_screen_prompt(core: CoreDefinition, papers: list) -> str:
    body = "\n\n".join(f"PMID {p['pmid']}\nTitle: {p.get('title', '')}" for p in papers)
    return (
        "You screen WCM publications for use of ONE specific institutional core "
        f"facility.\n\nCORE: {core.name} — {core.llm_description}\n\n"
        f"PAPERS:\n{body}\n\n"
        "For EACH paper, judge from its title whether it plausibly USED this core's "
        "instruments/services. Be recall-first (include if plausibly used). Reply ONLY "
        "with JSON mapping each PMID (string) to a confidence integer 1-10 "
        '(10=clearly used, 1=clearly not), e.g. {"12345678": 8, "23456789": 1}. '
        "Include every PMID."
    )


def batched_one_core_screen(bedrock, core: CoreDefinition, pubs: list, *,
                            batch_size: int = 40, max_workers: int = 4) -> dict:
    """Map pmid -> confidence 1-10 for ONE core, batching ~`batch_size` pubs per Sonnet call.

    Title-only. Threaded across batches (Bedrock is I/O-bound); a batch that errors or a
    pmid the reply omits defaults to 1 (screened low) rather than killing the run. Returns
    {} for empty input. Pair with a short-read_timeout BedrockClient so a hung call fails
    fast. Use max_workers<=1 for deterministic serial scoring (tests).
    """
    from utils.bedrock_client import SONNET_MODEL  # lazy

    if not pubs:
        return {}
    if bedrock is None:                            # dry-run / no --with-llm: screen everything low
        return {str(p["pmid"]): 1 for p in pubs}
    batches = [pubs[i:i + batch_size] for i in range(0, len(pubs), batch_size)]

    def _run(batch):
        try:
            raw = bedrock.call_json(
                model=SONNET_MODEL,
                messages=[{"role": "user", "content": _batch_screen_prompt(core, batch)}],
                max_tokens=4096,
            )
        except Exception as exc:  # noqa: BLE001 — one bad batch must not kill the run
            logger.warning("batch screen failed for core %s (%s) -> %d pmids screened low",
                           core.core_id, type(exc).__name__, len(batch))
            raw = {}
        if not isinstance(raw, dict):              # a valid-but-non-dict reply (array/int) must not crash the run
            logger.warning("batch screen for core %s returned non-dict %s -> %d pmids screened low",
                           core.core_id, type(raw).__name__, len(batch))
            raw = {}
        result = {str(p["pmid"]): _parse_int(raw.get(str(p["pmid"]), 1)) for p in batch}
        omitted = sum(1 for p in batch if str(p["pmid"]) not in raw)
        if raw and omitted:                        # genuine omission (e.g. max_tokens truncation), not a failed batch
            logger.warning("batch screen for core %s omitted %d/%d pmids -> screened low",
                           core.core_id, omitted, len(batch))
        return result

    scores: dict = {}
    if max_workers and max_workers > 1 and len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed  # lazy

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed([ex.submit(_run, b) for b in batches]):
                scores.update(fut.result())
    else:
        for b in batches:
            scores.update(_run(b))
    return scores


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


_RATIONALE_MAX = 80


def _fit(text: str, width: int = _RATIONALE_MAX) -> str:
    """`text` capped at `width`, cut on a word boundary rather than mid-word.

    The prompt already asks for <=80 chars (line ~421), so this only fires when
    the model overruns its own budget. Under the cap the string is returned
    untouched — including its internal whitespace, which is why this is a plain
    slice and not textwrap.shorten (shorten also drops every word after an
    oversized token, which is the failure this guards against).
    """
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    head = cut.rpartition(" ")[0]
    # Prefer the word boundary, but not at any price. "Uses core: <90-char URL>"
    # has its only space at index 10, so backing up to it yields a 3-character
    # evidence chip — strictly worse than the mid-word slice this replaced.
    # ponytail: half the budget is the give-up line; it only has to separate
    # "trimmed a partial word" from "threw the rationale away".
    return (head if len(head) >= width // 2 else cut) + "…"


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
            # Cut on a word boundary: the bare [:80] slice this replaces left
            # rationales ending "retrospective coho" in the live claim queue.
            # NOT textwrap.shorten — it drops every word after an oversized
            # token, so "Uses core: <100-char URL>" comes back as "Uses core:…",
            # a 3-character evidence chip strictly worse than the slice it
            # replaced. Trimming back to the last space in the slice has no such
            # case: with no space to find it degrades to exactly the old 80 chars.
            rec["rationale"] = _fit(str(dense.get("rationale", "")))
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
