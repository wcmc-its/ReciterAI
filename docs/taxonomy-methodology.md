# Research-Area Taxonomy — Design Methodology

## Overview

The ReCiter AI Chatbot uses a **multi-axis scoring architecture** to classify research publications and enable faculty expertise discovery:

- **Axis 1 — Research-Area Taxonomy** (`taxonomy_v2.json`): Disease areas, organ systems, basic science fields, clinical specialties, population contexts, and research infrastructure domains. Scored per-publication via LLM.
- **Axis 2 — Research Tools & Methods** (TOOL# records in DynamoDB): Techniques, instruments, datasets, models, and software, **extracted by an LLM (Haiku) from each faculty-led publication and grant abstract**, then deduplicated into canonical tools, classified into ~13 supercategories, grouped into ~878 method families, and tiered by cross-faculty salience. See [`tools-a2-architecture.md`](./tools-a2-architecture.md) and [`tool-classifier-spec.md`](./tool-classifier-spec.md) for the full pipeline. *(Historical note: an earlier POC sourced tools from the static `reciterai_keyword_relevance` table; the current A2 pipeline is LLM-extraction-based.)*

A query like *"who does immunotherapy for lung cancer?"* hits both axes: `lung_cancer` (Axis 1) + immunotherapy keywords (Axis 2).

> **Terminology.** The Axis-1 entries are displayed in the Scholars UI as **research areas** (and their inductive children as **subareas**). The code and identifiers keep the older names (`taxonomy_v2.json`, `TOPIC#`/`SUBTOPIC#`, `topic_id`); prose here uses "research area"/"subarea." These are distinct from the SPS profile **"Topics"** section, which is MeSH keywords from ReCiterDB — not the AI-derived research areas. See [`topic-subtopic-assignment.md`](./topic-subtopic-assignment.md) for the mechanics.

## Design Principles

### 1. Data-Driven Research Areas Only

Research areas must reflect what is actually present in the publication data, not what we think should exist. The LLM scoring naturally assigns low/zero scores to research areas absent from synopses. Empty research areas add noise to screening prompts without adding signal. Research areas should not be added speculatively.

### 2. Domains, Not Methods

The taxonomy (Axis 1) focuses on **domains** — disease areas, organ systems, basic science fields, population contexts, and research infrastructure. Methods and techniques (genomics, CRISPR, machine learning, clinical trials methodology) are handled by TOOL# records (Axis 2). Exception: methods that represent real departmental identity at an academic institution (e.g., biomedical engineering, biomedical informatics) are included in the taxonomy.

### 3. Granularity Calibrated by Publication Volume

Research-area granularity is calibrated against actual publication volume using Science-Metrix journal classification data as a reference:

- **Large categories (>5,000 publications)** are candidates for **splitting** into subareas (e.g., Oncology → 8 cancer-type subareas)
- **Medium categories (1,000–5,000)** are generally right-sized as single research areas
- **Small categories (100–1,000)** are monitored for merge candidates
- **Tiny categories (<100)** are merged into related research areas unless they represent distinct departmental identity

This approach ensures that large research areas get appropriate granularity for expert discovery, while small areas are not over-fragmented.

For the reverse question — does an existing compound `X & Y` topic bundle two domains that should
be *un*-merged into separate top-level areas? — see
[`research-area-split-criteria.md`](./research-area-split-criteria.md), which operationalizes this
principle's "distinct departmental identity" carve-out into a repeatable test.

### 4. Overlap Is Acceptable

This taxonomy functions as an **institutional research map**, not a strict classification ontology. Publications can (and should) score against multiple research areas. A cardiology paper in a general medicine journal should score on `cardiovascular_disease`, not be silently classified as "General Internal Medicine." Boundary descriptions include "distinct from X" notes to help the LLM discriminate, but overlapping scores are a feature, not a bug.

### 5. Institutional Scalability

The taxonomy is designed to extend beyond a single institution. Cancer type subareas, basic science domains, and engineering fields support expansion to other campuses (e.g., Cornell Ithaca) without restructuring the scoring pipeline.

## Generation Process

### Step 1: Synopsis-Based Research-Area Extraction

Publication synopses from ReciterDB are batch-processed through Bedrock Sonnet (50 synopses per batch) to extract raw domain clusters. The prompt explicitly requests domains, not methods.

### Step 2: LLM Consolidation

Raw clusters (~1,400 from ~140 batches) are consolidated into ~65 final research areas via a single Sonnet call with explicit guidance on:
- Focusing on domains, not methods (methods are Axis 2)
- Including basic science fields, not just clinical specialties
- Including research infrastructure domains (implementation science, health economics, translational science)

### Step 3: Expert Review and Calibration

The generated taxonomy undergoes expert review for:
- **Coverage**: Are major institutional research areas represented?
- **Boundary clarity**: Can the LLM discriminate between overlapping research areas?
- **Volume calibration**: Are large SM categories appropriately split? Are small ones merged?
- **Institutional realism**: Do research areas correspond to recognizable departments, divisions, or research programs?

### Step 4: Volume-Based Splitting

Science-Metrix journal classification data (publication counts per subfield) is used to identify categories requiring finer granularity. Oncology (18,813 publications across WCM) was split into 8 cancer-type subareas based on synopsis keyword analysis confirming sufficient volume per subtype.

### Step 5: Automated Validation

The final taxonomy is validated against 12 sample research dean queries to confirm ≥80% high-relevance match rate.

## Current Taxonomy (v2)

- **67 research areas** in the `taxonomy_v2.json` baseline (as of 2026-07-10; was 68 until
  `hematology_medical_oncology` — a prior additive mint, see § "Adding a single research area later"
  in `topic-subtopic-assignment.md` — was retired via `cli/retire_topic.py`, commit `02d3445`;
  see [`research-area-split-criteria.md`](./research-area-split-criteria.md) for why that history
  isn't a precedent for splitting an existing bundled topic) across disease areas, basic science,
  clinical specialties, population health, and research infrastructure
- **8 oncology subareas** (breast, lung, prostate/urologic, GI, neuro-oncology, gynecologic, melanoma/skin, general cancer biology)
- **Calibrated against Science-Metrix** publication volumes for granularity decisions
- **Validated at 100% match rate** (12/12 sample queries) before volume-based refinement

## Multi-Axis Query Architecture

| User Query | Axis 1 (Research area) | Axis 2 (Methods & tools) |
|------------|-----------------|------------------------|
| "Who works on breast cancer?" | `breast_cancer` | — |
| "Who uses CRISPR?" | — | CRISPR keywords |
| "Who does immunotherapy for lung cancer?" | `lung_cancer` | immunotherapy keywords |
| "Who works on aging?" | `aging_geroscience` | — |
| "Find AI researchers in cardiology" | `cardiovascular_disease` | machine learning, AI keywords |

## Versioning

- `taxonomy_v1`: Initial 50-research-area flat taxonomy (mixed domains and methods). Superseded.
- `taxonomy_v2`: 67-research-area domain-focused taxonomy with volume-calibrated oncology split, boundary clarifications, and multi-axis design. Current.
