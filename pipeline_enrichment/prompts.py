"""Synopsis + impact prompts for the daily enrichment job.

Ported verbatim from `wcmc-its/ReCiterAI-POC` (2026-05-13). These are the
prompts the laptop script has been running against MariaDB; the equivalence
sanity check in `scripts/equivalence_check.py` confirms the ported versions
produce comparable output before the daily job ships.

Source locations in POC:
- SYNOPSIS_SYSTEM, SYNOPSIS_SCHEMA — `core/prompts.py`
- IMPACT_PROMPT_V2, get_impact_system_prompt(), get_impact_schema() —
  `pipeline_publications/prompts.py`
- build_rich_publication_prompt() — `core/impact_scoring_legacy.py`

DO NOT edit these prompts during the port. Drift between POC and this
file invalidates the equivalence check and means the daily job will
produce silently different output than the production-feeding laptop runs.
A separate prompt-revision cycle is the right way to evolve them after
the migration completes.
"""
from __future__ import annotations

import html

# =============================================================================
# Synopsis (≤95-char publication synopsis)
# =============================================================================

SYNOPSIS_SYSTEM = """You are a scientific writing assistant.
Given an abstract, produce a concise synopsis (<= 95 characters).
Requirements:
- 40+ characters
- >= 5 words
- Must NOT parrot too much of the title
- Clear and human-readable
"""

SYNOPSIS_SCHEMA = {
    "name": "synopsis_schema",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            # maxLength 95 mirrors the SYNOPSIS_SYSTEM "<= 95 characters" rule
            # at the JSON-schema level. The plain-language prompt directive
            # was insufficient on its own: the #37 step 2 bootstrap surfaced
            # a 17.1% overrun rate (310/1,815 papers, worst 141 chars).
            # See #50.
            "synopsis": {"type": "string", "maxLength": 95},
        },
        "required": ["synopsis"],
    },
}


def build_synopsis_user_content(*, title: str, journal: str | None,
                                year: int | None, abstract: str | None) -> str:
    """User-content builder for the synopsis call.

    Matches the format produced by POC `core/synopsis.py::_one` (line 147–153).

    Title + abstract are run through `html.unescape` because upstream MariaDB
    rows can carry undecoded entities (e.g., `&#x3b2;ARKnt` instead of
    `βARKnt`, `&#xa0;` for non-breaking space). 2026-05-20 #112 backfill
    confirmed: PMID 33548241's `&#x3b2;ARKnt` mouse-cardiology abstract caused
    Bedrock Sonnet 4.6 to return an empty synopsis on two consecutive calls
    — same input, same empty output, not non-determinism. Decoding restores
    the proper Unicode characters before the prompt reaches the model.
    """
    return (
        f"Title: {html.unescape(title)}\n"
        f"Journal: {journal or 'N/A'}\n"
        f"Year: {year if year is not None else 'N/A'}\n"
        f"Abstract: {(html.unescape(abstract) if abstract else '(no abstract)')}\n\n"
        "Return JSON only."
    )


# =============================================================================
# Impact (0–100 score + ≤120-char justification)
# =============================================================================

IMPACT_PROMPT_DEFAULT_VERSION = "v2"

# Version metadata for tracking. Matches POC `pipeline_publications/prompts.py`.
IMPACT_PROMPT_VERSIONS = {
    "v1": {
        "date": "pre-2025-12",
        "description": "Original prompt with clinical research bias",
        "bias_correlation": -0.342,
        "gap": 8.2,
        "notes": "~8 point gap between basic and clinical research",
    },
    "v2": {
        "date": "2025-12-28",
        "description": "Parity constraint + boosted clinical anchors + counterfactual check",
        "bias_correlation": -0.306,
        "gap": 6.3,
        # Historical: the model v2 was *tuned against*. The runtime model is
        # now per-row — Bedrock Sonnet 4.6 on the happy path, with a gpt-5.1
        # content-filter fallback (#37 D3) — and is recorded on each row by
        # `ImpactResult.model` / the MariaDB `model` column / the IMPACT# row.
        "model": "gpt-5.1",
        # Historical, GPT-5-specific: Bedrock Converse has no equivalent
        # parameter and the runtime path is plain Converse (#37 PR 2).
        "reasoning_effort": "medium",
        "notes": "Gap now better than NIH iCite. Top 25% tier: +11.2 -> +4.3",
    },
}


IMPACT_PROMPT_V1 = """You are an expert evaluator of biomedical and life-science research impact.

Given a publication, estimate its overall research impact on a 0–100 scale.

Consider:
- **Originality / Novelty** (relative to the knowledge at time of publication)
- **Methodological Quality / Rigor**
- **Practical or Translational Relevance** (potential to improve patient care, inform policy, enable research, or impact technology/industry; if clinical, include clinical impact; if non-clinical, emphasize research or technological impact)
- **Reproducibility / Transparency**
- **Citation Potential** (use current citation count, NIH percentile rank *if available — interpret within its review field*, publication date, and field's citation half-life to infer likely future influence)
- **Venue Prestige** (journal impact and reputation; for newer papers, venue prestige should carry proportionally more weight since citation trajectories are still developing)

If a study is primarily basic, computational, or methodological, do not penalize it for lacking direct clinical relevance. Instead emphasize novelty, rigor, reproducibility, and potential influence on research or practice.

Do not apply field-specific bonuses; score relative to typical impact standards across biomedical research.

Calibrated Examples (anchor points):

Score 8 — Letter to editor with anecdotal observation
Score 12 — Case report, single patient, regional journal
Score 15 — Editorial without new data or novel insights
Score 18 — Preliminary study, n<10, no controls
Score 22 — Cross-sectional survey, n=200, regional journal
Score 28 — Case series, n=25, academic medical center
Score 32 — In-vitro study, established methods, incremental findings
Score 32 — Incremental improvement to a standard bioinformatics pipeline in a specialty journal
Score 35 — Retrospective chart review, single center, n=100
Score 42 — Multi-center retrospective study, n=1000, clear findings
Score 45 — Well-designed animal study, novel mechanism
Score 48 — Phase 2 clinical trial, n=80, promising results
Score 52 — Systematic review without meta-analysis, comprehensive
Score 55 — Prospective cohort study (n=5,000) on risk factors for heart failure
Score 55 — Well-designed in-vitro study revealing a previously unknown signaling pathway
Score 65 — Machine-learning model predicting protein folding accuracy at time of publication
Score 70 — Breakthrough method paper, widely adoptable
Score 72 — Large Phase 3 randomized controlled trial changing treatment guidelines
Score 75 — Nature/Science paper, novel mechanism, broad implications
Score 75 — NEJM clinical trial, n=2000, definitive results
Score 80 — Revolutionary technique enabling new research field
Score 82 — First-in-class therapy discovery paper
Score 83 — First-in-class CRISPR diagnostic platform enabling rapid pathogen detection
Score 84 — Practice-changing diagnostic breakthrough
Score 85 — Landmark epidemiological study changing public health policy
Score 87 — Major pathophysiology discovery with therapeutic implications
Score 90 — First successful gene therapy (ADA-SCID)
Score 91 — mRNA vaccine platform proof-of-concept
Score 92 — Discovery of HIV as AIDS causative agent
Score 93 — Original 2012 CRISPR-Cas9 genome editing paper (Doudna/Charpentier)
Score 96 — First description of PCR amplification
Score 97 — Discovery of penicillin's antibiotic properties
Score 98 — Discovery of insulin and diabetes treatment
Score 99 — Discovery of DNA structure (Watson/Crick, 1953)

Output:
Return a JSON object with:
- `"impactScore"`: 0–100 integer
- `"justification"`: ≤10 words summarizing the reasoning"""


IMPACT_PROMPT_V2 = """You are an expert evaluator of biomedical and life-science research impact.

Given a publication, estimate its overall research impact on a 0–100 scale.

---

**PARITY CONSTRAINT (must follow):**
If two papers have comparable field-normalized influence signals (e.g., similar NIH iCite percentile tier, citation trajectory relative to age, or guideline/policy adoption), they should receive similar impact scores regardless of whether the work is clinical or basic. Do not apply a higher "bar" for clinical research.

---

Consider:
- **Originality / Novelty** (relative to the knowledge at time of publication)
  - Novelty includes: (a) new biological mechanism, (b) new causal clinical evidence, (c) new scalable care-delivery or implementation strategy, or (d) new population-health intervention with measurable outcomes. All four are equally valid forms of novelty.
- **Methodological Quality / Rigor**
  - For well-powered RCTs, large cohorts, or rigorous quasi-experiments: treat scale + real-world heterogeneity as a strength, not a weakness.
- **Evidence of Influence / Uptake** (guidelines, policy, practice change, platform uptake, downstream trials)
  - If strong influence evidence exists, it can outweigh mechanistic novelty.
- **Practical or Translational Relevance** (potential to improve patient care, inform policy, enable research, or impact technology/industry)
- **Citation Potential** (use current citation count, NIH percentile rank *if available*, publication date, and field's citation half-life)
- **Venue Prestige** (journal impact and reputation; for newer papers, venue prestige carries proportionally more weight)

If a study is primarily basic, computational, or methodological, do not penalize it for lacking direct clinical relevance. Instead emphasize novelty, rigor, reproducibility, and potential influence on research or practice.

**IMPORTANT: Do not systematically score clinical, epidemiological, or population health research lower than basic science.** A landmark cohort study that changes clinical guidelines, a health services study that transforms care delivery, or an implementation science paper that enables widespread adoption of evidence-based practice can be equally impactful as a molecular discovery — they represent different but equally valid forms of scientific contribution.

The highest scores (90+) should be reserved for paradigm-shifting contributions in ANY domain:
- Fundamental biological mechanisms (e.g., DNA structure)
- Transformative clinical evidence (e.g., trials establishing life-saving interventions)
- Population health breakthroughs (e.g., studies that fundamentally change disease prevention or health policy)
- Methodological innovations (e.g., methods enabling entirely new fields of inquiry)

Do not apply field-specific bonuses; score relative to typical impact standards across biomedical research.

Calibrated Examples (anchor points):

LOW TIER (0-30) — Preliminary, underpowered, or incremental work across all domains:
Score 8 — Letter to editor with anecdotal observation
Score 10 — Pilot computational model, single dataset, no external validation
Score 12 — Case report, single patient, regional journal
Score 15 — Preliminary in-vitro study, single cell line, no replication
Score 18 — Preliminary study, n<10, no controls
Score 20 — Animal study, n=6, underpowered, incremental mechanistic finding
Score 22 — Cross-sectional survey, n=200, regional journal, descriptive only
Score 25 — Bioinformatics pipeline, narrow applicability, marginal improvement over existing tools
Score 28 — Case series, n=25, single academic medical center

LOW-MID TIER (31-50) — Solid but incremental contributions:
Score 32 — In-vitro study, established methods, incremental findings
Score 35 — Retrospective chart review, single center, n=100, confirmatory
Score 36 — Cross-sectional clinical study (n=500) with validated measures, practice-informing
Score 38 — Well-conducted animal study replicating known mechanism in new model
Score 40 — Single-center prospective cohort (n=300) identifying clinically relevant associations
Score 42 — Multi-center retrospective study, n=1,000, clear findings, practice-informing
Score 44 — Quality improvement study demonstrating measurable patient outcome improvement
Score 45 — Well-designed animal study demonstrating novel mechanism
Score 46 — Diagnostic accuracy study (n=500+) for clinically important condition
Score 48 — Phase 2 clinical trial, n=80, promising efficacy and safety results
Score 50 — Rigorous qualitative study revealing new insights into patient experience or care delivery
Score 50 — Pilot RCT (n=100-200) with promising clinical efficacy signal

MID-HIGH TIER (51-70) — Strong contributions with clear influence:
Score 52 — Systematic review without meta-analysis, comprehensive, practice-informing
Score 53 — Prospective clinical registry (n=2,000+) establishing real-world treatment patterns
Score 55 — Prospective cohort study (n=5,000) identifying novel risk factors
Score 55 — Well-designed in-vitro study revealing a previously unknown signaling pathway
Score 56 — Comparative effectiveness study (n=10,000+) informing treatment selection in routine care
Score 57 — Health economics study demonstrating cost-effectiveness of clinical intervention
Score 58 — Implementation study demonstrating successful evidence-based practice adoption across multiple sites
Score 59 — Phase 3 RCT (n=300-500) with positive primary endpoint, specialty journal
Score 60 — Cost-effectiveness analysis influencing coverage or formulary decisions
Score 60 — Quality improvement collaborative achieving sustained improvement across 20+ hospitals
Score 61 — Diagnostic validation study establishing clinical utility for decision-making
Score 62 — Health services study identifying systematic disparities with policy implications
Score 62 — Behavioral intervention RCT (n=500) with durable lifestyle modification outcomes
Score 63 — Multicenter observational study (n=5,000+) influencing clinical practice
Score 65 — Machine-learning model achieving state-of-the-art prediction with clinical utility
Score 65 — Multi-site pragmatic trial demonstrating intervention effectiveness in real-world settings
Score 66 — Phase 3 RCT (n=500-1,000) published in high-impact general medical journal
Score 68 — Meta-analysis of RCTs providing definitive effect estimates, incorporated into guidelines
Score 68 — National cohort study (n=50,000+) establishing novel disease-outcome associations
Score 70 — Breakthrough method paper, widely adoptable across fields
Score 70 — Landmark screening trial (e.g., NLST-scale) changing national screening recommendations

HIGH TIER (71-89) — Major contributions changing practice, policy, or scientific understanding:
Score 72 — Large Phase 3 RCT (n=1,000+) changing treatment guidelines
Score 72 — ACCORD/ADVANCE-scale diabetes trial informing glycemic control targets
Score 74 — Cluster-randomized trial demonstrating effective health system intervention at scale
Score 74 — HPTN 052-scale trial establishing treatment-as-prevention paradigm
Score 75 — High-impact discovery paper (Nature/Science) revealing novel mechanism with broad implications
Score 75 — NEJM clinical trial, n=2,000, definitive results, practice-changing
Score 76 — DPP (Diabetes Prevention Program)-scale trial establishing lifestyle intervention efficacy
Score 77 — Large pragmatic trial demonstrating real-world effectiveness, informing policy
Score 77 — 4S/WOSCOPS-scale statin trial establishing mortality benefit
Score 78 — Major cohort study (Nurses' Health, Framingham-scale) establishing foundational risk associations
Score 78 — DCCT/UKPDS-scale trial establishing intensive treatment paradigm for chronic disease
Score 80 — Revolutionary technique enabling new research field
Score 80 — Oregon/RAND Health Insurance Experiment-scale study reshaping health policy
Score 82 — First-in-class therapy discovery with clear path to clinical application
Score 82 — START/SMART-scale HIV trial establishing treatment timing guidelines
Score 83 — CRISPR-based diagnostic platform enabling rapid, accessible pathogen detection
Score 84 — Practice-changing diagnostic breakthrough with widespread adoption
Score 84 — JUPITER/HOPE-scale cardiovascular prevention trial changing risk stratification
Score 85 — Landmark epidemiological study directly changing public health policy or clinical guidelines
Score 86 — Definitive large-scale trial resolving long-standing clinical equipoise (e.g., SPRINT, ALLHAT)
Score 87 — Major pathophysiology discovery with direct therapeutic implications
Score 88 — Implementation framework or quality measures adopted nationally (e.g., RE-AIM, HEDIS-level influence)

EXCEPTIONAL TIER (90-100) — Paradigm-shifting, field-defining contributions (any domain):
Score 90 — First successful gene therapy for inherited disease (ADA-SCID)
Score 90 — Doll and Hill studies establishing smoking-lung cancer causal link
Score 91 — mRNA vaccine platform proof-of-concept enabling COVID-19 response
Score 91 — Framingham Heart Study establishing modern cardiovascular epidemiology
Score 92 — Discovery of HIV as causative agent of AIDS
Score 92 — INTERSALT/MRFIT studies transforming global hypertension management
Score 93 — Original CRISPR-Cas9 genome editing paper (Doudna/Charpentier)
Score 94 — WHI hormone therapy trials overturning decades of clinical practice
Score 96 — First description of PCR amplification technique
Score 97 — Discovery of penicillin's antibiotic properties
Score 98 — Discovery of insulin and successful diabetes treatment
Score 99 — Discovery of DNA double helix structure (Watson/Crick, 1953)

---

**Before finalizing your score, perform this counterfactual check:**
"If this same influence evidence were attached to a basic-science paper, would I score it higher?"
If yes, raise the score until parity is achieved.

---

Output:
Return a JSON object with:
- `"impactScore"`: 0–100 integer
- `"justification"`: ≤10 words summarizing the reasoning"""


IMPACT_PROMPTS = {
    "v1": IMPACT_PROMPT_V1,
    "v2": IMPACT_PROMPT_V2,
}


IMPACT_SCHEMA = {
    "name": "impact_schema",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "impactScore": {"type": "integer", "minimum": 0, "maximum": 100},
            "justification": {"type": "string", "maxLength": 120},
        },
        "required": ["impactScore", "justification"],
    },
}


def get_impact_system_prompt(version: str | None = None) -> str:
    """Return the impact-scoring system prompt for the named version.

    Defaults to IMPACT_PROMPT_DEFAULT_VERSION (v2 as of 2025-12-28).
    """
    version = version or IMPACT_PROMPT_DEFAULT_VERSION
    if version not in IMPACT_PROMPTS:
        raise ValueError(
            f"Unknown impact prompt version: {version}. "
            f"Available: {list(IMPACT_PROMPTS.keys())}"
        )
    return IMPACT_PROMPTS[version]


def build_impact_user_content(pub_data: dict) -> str:
    """User-content builder for the impact call.

    Mirrors POC `core/impact_scoring_legacy.py::build_rich_publication_prompt`.
    Accepts the bibliometric column names produced by the upstream MariaDB
    query (`articleTitle`, `journalTitleVerbose`, etc.); see
    `core/impact.py` in POC for the SQL.
    """
    parts: list[str] = []

    # html.unescape on title + abstract — upstream MariaDB rows can carry
    # undecoded entities (see build_synopsis_user_content docstring for the
    # 2026-05-20 #112 backfill incident). Idempotent on clean text.
    if pub_data.get("articleTitle"):
        parts.append(f"Title: {html.unescape(pub_data['articleTitle'])}")

    journal = pub_data.get("journalTitleVerbose", "Unknown journal")
    year = pub_data.get("articleYear", "Unknown year")
    parts.append(f"Journal: {journal} ({year})")

    citations = pub_data.get("citationCountNIH")
    if citations is not None:
        parts.append(f"Citation Count (NIH): {citations}")

    percentile = pub_data.get("percentileNIH")
    if percentile is not None:
        parts.append(f"NIH iCite Percentile: {percentile}")

    rcr = pub_data.get("relativeCitationRatioNIH")
    if rcr is not None:
        parts.append(f"Relative Citation Ratio: {rcr:.2f}")

    pub_date = pub_data.get("datePublicationAddedToEntrez")
    if pub_date:
        parts.append(f"Publication Date: {pub_date}")

    abstract = pub_data.get("abstractVarchar")
    if abstract:
        abstract = html.unescape(abstract)
        # POC truncates very long abstracts to 3000 chars; keep behavior.
        if len(abstract) > 3000:
            abstract = abstract[:3000] + "..."
        parts.append(f"\nAbstract:\n{abstract}")

    return "\n".join(parts)
