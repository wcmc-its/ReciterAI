"""Compile a funding opportunity into the grant->researcher matcher's cached inputs.

Promotes the validated scratch compiler (``sign_grant.py``) into the ingest path so the
DSL + relevance query are compiled ONCE per opportunity and cached on the ``GRANT#`` row,
letting the SPS matcher read them with no per-request LLM call.

Two products, both grant-agnostic (the prompts are the scratch ones, unchanged):
  * ``compile_dsl``   -> ``{require, penalize, pediatric_markers, pediatric_required}``
                         substring patterns over the subtopic-id vocabulary (the SET + gate).
  * ``compile_query`` -> ``[{q, w}]`` weighted BM25 relevance terms (the in-pool re-rank BOOST).

Two deliberate differences from the scratch version:
  * The subtopic vocabulary comes from ReciterAI's OWN durable ``SUBTOPIC_SLUG#`` store
    (``load_subtopic_vocab``), NOT the SPS DB — so ingest stays self-contained. This drops
    the scratch ``sch>=2 or n>=3`` per-pool singleton filter (those counts are SPS-only); the
    candidate menu is every live slug instead. Validated DSL quality was at ~1520 filtered
    slugs vs ~1541 here, so the menus are comparable, but this is the one cross-repo contract
    risk to watch (a slug rename/retire upstream can stale a cached DSL — recompile on
    taxonomy bumps).
  * Production opportunities carry only ``title`` + ``synopsis`` (no full scraped solicitation),
    so ``compile_query`` runs on the thinner synopsis. The richer-solicitation query (the
    scratch ``grant_solicitations.json`` path) yielded more grant-faithful terms; synopsis-only
    quality is the accepted v1 tradeoff until the scrape corpus is joined to opportunities.

Cost: each call adds 2 Sonnet calls per opportunity (dsl + query) on top of judge + score.
Gated off by default at the ingest CLIs (``--compile-match``).
"""
import json
import logging

from pipeline_hierarchy.subtopic_id_store import load_slug_pointer_map
from utils.bedrock_client import SONNET_MODEL
from utils.dynamodb_helpers import get_table

log = logging.getLogger("pipeline_grants.match_compile")

# --- prompts: copied verbatim from the validated scratch compiler (sign_grant.py). ---
SYS = (
    "You compile a funding opportunity into a SUBTOPIC-MATCHING DSL. Given the grant and the list of snake_case research "
    "subtopic ids candidate researchers publish in, emit compact lowercase SUBSTRING patterns (matched against the ids):\n"
    "- require: substrings marking the DISTINCTIVE right-fit research for THIS grant (the specific signal, not an entire "
    "broad field). Derive it from the grant text.\n"
    "- penalize: substrings marking the grant's stated EXCLUSIONS / wrong-fit research types.\n"
    "- pediatric_markers + pediatric_required: ONLY if the grant is domain-restricted to a population (e.g. children). "
    "Otherwise pediatric_required=false and an empty/short marker list.\n"
    "CRITICAL: do NOT emit bare branch prefixes (e.g. 'biomedical_','health_') that match a whole taxonomy branch — they "
    "over-capture. Use SPECIFIC multi-word substrings. When a grant wants experimental/bench/engineering/device work, do "
    "NOT sweep in health-informatics / clinical-NLP / EHR-text-mining / registry / observational subtopics (penalize or omit those).\n"
    "Keep each list focused (~12–30 substrings) and prefer substrings that occur in the provided ids. "
    'Output strict JSON: {"require":[..],"penalize":[..],"pediatric_markers":[..],"pediatric_required":bool}'
)

SYS_QUERY = (
    "You compile a funding opportunity into a WEIGHTED RELEVANCE QUERY SET for BM25 publication search. Given the grant, "
    "emit short free-text search queries that retrieve publications whose TOPIC is on-target for THIS grant. Cover EVERY "
    "method/domain facet the grant funds (if it funds computational AND big-data AND omics work, include queries for "
    "each). Use the words researchers actually put in titles/abstracts, INCLUDING synonyms the grant text doesn't use "
    "(e.g. 'deep learning','neural network','foundation model' for a machine-learning grant). 2-4 word lowercase noun "
    "phrases, no sentences. Do NOT include the grant's EXCLUSIONS.\n"
    "For EACH query rate its IMPORTANCE to THIS grant's central intent:\n"
    "- 'core'       = defines the grant's distinctive method/domain (the thing it specifically funds).\n"
    "- 'supporting' = genuinely relevant but broader/secondary, OR a generic phrasing that also matches adjacent fields.\n"
    "- 'peripheral' = loosely related; would also retrieve a lot of off-grant work.\n"
    "Be honest: a broad phrase like 'quantitative methods biomedical research' is supporting/peripheral, not core. "
    '~10-16 queries. Output strict JSON: {"queries":[{"q":"...","w":"core|supporting|peripheral"}, ...]}'
)
_W = {"core": 1.0, "supporting": 0.5, "peripheral": 0.25}


def load_subtopic_vocab(table) -> list:
    """The candidate subtopic-id menu compile_dsl matches substrings against: every live
    ``SUBTOPIC_SLUG#`` slug from the durable store. Sorted for a stable prompt."""
    return sorted(load_slug_pointer_map(table).keys())


def load_vocab_or_disable(logger) -> list:
    """Load the subtopic vocab; return ``[]`` (caller treats as "compilation disabled") on any
    failure or an empty store, so a vocab problem never aborts ingest or burns a Bedrock call."""
    try:
        vocab = load_subtopic_vocab(get_table())
    except Exception as exc:  # noqa: BLE001 - vocab failure must not abort ingest
        logger.warning("match-compile: vocab load failed (%s); skipping compilation", exc)
        return []
    if not vocab:
        logger.warning("match-compile: subtopic vocab empty; skipping compilation")
    else:
        logger.info("match-compile: loaded %d subtopic slugs", len(vocab))
    return vocab


def _parse_query(out: dict) -> list:
    """LLM JSON -> ``[{q, w}]`` (pure; the testable half of compile_query)."""
    res = []
    for item in (out or {}).get("queries", []):
        q = (item.get("q") if isinstance(item, dict) else item) or ""
        w = _W.get((item.get("w") if isinstance(item, dict) else "core"), 0.5)
        if q.strip():
            res.append({"q": q.strip().lower(), "w": w})
    return res


def _parse_dsl(out: dict) -> dict:
    """LLM JSON -> ``{require, penalize, pediatric_markers, pediatric_required}`` (pure)."""
    dsl = {k: list((out or {}).get(k, []) or []) for k in ("require", "penalize", "pediatric_markers")}
    dsl["pediatric_required"] = bool((out or {}).get("pediatric_required", False))
    return dsl


def compile_dsl(title: str, synopsis: str, subtopic_vocab: list, *, bedrock) -> dict:
    """Compile the SET+gate DSL from the grant text + the subtopic-id menu. One Sonnet call."""
    user = (f"GRANT TITLE: {title}\n\nGRANT: {synopsis}\n\n"
            f"CANDIDATE SUBTOPIC IDS ({len(subtopic_vocab)}):\n" + "\n".join(subtopic_vocab))
    out = bedrock.call_json(model=SONNET_MODEL, system=SYS,
                            messages=[{"role": "user", "content": user}], max_tokens=2000, temperature=0.0)
    return _parse_dsl(out)


def compile_query(title: str, synopsis: str, *, bedrock) -> list:
    """Compile the weighted BM25 relevance query from the grant text. One Sonnet call."""
    user = f"GRANT TITLE: {title}\n\nGRANT: {synopsis}"
    out = bedrock.call_json(model=SONNET_MODEL, system=SYS_QUERY,
                            messages=[{"role": "user", "content": user}], max_tokens=1200, temperature=0.0)
    return _parse_query(out)


def compile_match(title: str, synopsis: str, subtopic_vocab: list, *, bedrock):
    """Compile (dsl, query) for one opportunity.

    FAIL-OPEN: returns ``(None, None)`` on any error or empty output, so the grant row still
    persists (just unmatchable — the matcher is fail-closed on missing fields). Adds 2 Sonnet
    calls per opportunity.
    """
    label = (title or "")[:60]
    try:
        dsl = compile_dsl(title, synopsis, subtopic_vocab, bedrock=bedrock)
        query = compile_query(title, synopsis, bedrock=bedrock)
    except Exception as exc:  # noqa: BLE001 - fail-open: a compile error must not lose the grant row
        log.warning("match-compile failed for %r: %s", label, exc)
        return None, None
    if not dsl.get("require") or not query:
        log.info("match-compile: empty require/query for %r — omitting match fields", label)
        return None, None
    return dsl, query


def match_attrs(match_dsl, match_query) -> dict:
    """The two ``GRANT#`` attributes (compact-JSON ``S`` blobs the SPS matcher decodes), or an
    empty dict when compilation was off/failed (so the matcher stays fail-closed). Pure."""
    attrs = {}
    if match_dsl is not None:
        attrs["match_dsl"] = {"S": json.dumps(match_dsl, separators=(",", ":"))}
    if match_query is not None:
        attrs["match_query"] = {"S": json.dumps(match_query, separators=(",", ":"))}
    return attrs
