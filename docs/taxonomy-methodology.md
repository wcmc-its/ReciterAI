# Taxonomy Design Methodology

## Overview

The ReCiter AI Chatbot uses a **multi-axis scoring architecture** to classify research publications and enable faculty expertise discovery:

- **Axis 1 — Domain Taxonomy** (`taxonomy_v2.json`): Disease areas, organ systems, basic science fields, clinical specialties, population contexts, and research infrastructure domains. Scored per-publication via LLM.
- **Axis 2 — Research Tools & Methods** (TOOL# records in DynamoDB): Techniques, instruments, datasets, models, and software, **extracted by an LLM (Haiku) from each faculty-led publication and grant abstract**, then deduplicated into canonical tools, classified into ~13 supercategories, grouped into ~878 method families, and tiered by cross-faculty salience. See [`tools-a2-architecture.md`](./tools-a2-architecture.md) and [`tool-classifier-spec.md`](./tool-classifier-spec.md) for the full pipeline. *(Historical note: an earlier POC sourced tools from the static `reciterai_keyword_relevance` table; the current A2 pipeline is LLM-extraction-based.)*

A query like *"who does immunotherapy for lung cancer?"* hits both axes: `lung_cancer` (Axis 1) + immunotherapy keywords (Axis 2).

## Design Principles

### 1. Data-Driven Topics Only

Taxonomy topics must reflect what is actually present in the publication data, not what we think should exist. The LLM scoring naturally assigns low/zero scores to topics absent from synopses. Empty topics add noise to screening prompts without adding signal. Topics should not be added speculatively.

### 2. Domains, Not Methods

The taxonomy (Axis 1) focuses on **domains** — disease areas, organ systems, basic science fields, population contexts, and research infrastructure. Methods and techniques (genomics, CRISPR, machine learning, clinical trials methodology) are handled by TOOL# records (Axis 2). Exception: methods that represent real departmental identity at an academic institution (e.g., biomedical engineering, biomedical informatics) are included in the taxonomy.

### 3. Granularity Calibrated by Publication Volume

Topic granularity is calibrated against actual publication volume using Science-Metrix journal classification data as a reference:

- **Large categories (>5,000 publications)** are candidates for **splitting** into subtopics (e.g., Oncology → 8 cancer-type subtopics)
- **Medium categories (1,000–5,000)** are generally right-sized as single topics
- **Small categories (100–1,000)** are monitored for merge candidates
- **Tiny categories (<100)** are merged into related topics unless they represent distinct departmental identity

This approach ensures that large research areas get appropriate granularity for expert discovery, while small areas are not over-fragmented.

### 4. Overlap Is Acceptable

This taxonomy functions as an **institutional research map**, not a strict classification ontology. Publications can (and should) score against multiple topics. A cardiology paper in a general medicine journal should score on `cardiovascular_disease`, not be silently classified as "General Internal Medicine." Boundary descriptions include "distinct from X" notes to help the LLM discriminate, but overlapping scores are a feature, not a bug.

### 5. Institutional Scalability

The taxonomy is designed to extend beyond a single institution. Cancer type subtopics, basic science domains, and engineering fields support expansion to other campuses (e.g., Cornell Ithaca) without restructuring the scoring pipeline.

## Generation Process

### Step 1: Synopsis-Based Topic Extraction

Publication synopses from ReciterDB are batch-processed through Bedrock Sonnet (50 synopses per batch) to extract raw domain clusters. The prompt explicitly requests domains, not methods.

### Step 2: LLM Consolidation

Raw clusters (~1,400 from ~140 batches) are consolidated into ~65 final domain topics via a single Sonnet call with explicit guidance on:
- Focusing on domains, not methods (methods are Axis 2)
- Including basic science fields, not just clinical specialties
- Including research infrastructure domains (implementation science, health economics, translational science)

### Step 3: Expert Review and Calibration

The generated taxonomy undergoes expert review for:
- **Coverage**: Are major institutional research areas represented?
- **Boundary clarity**: Can the LLM discriminate between overlapping topics?
- **Volume calibration**: Are large SM categories appropriately split? Are small ones merged?
- **Institutional realism**: Do topics correspond to recognizable departments, divisions, or research programs?

### Step 4: Volume-Based Splitting

Science-Metrix journal classification data (publication counts per subfield) is used to identify categories requiring finer granularity. Oncology (18,813 publications across WCM) was split into 8 cancer-type subtopics based on synopsis keyword analysis confirming sufficient volume per subtype.

### Step 5: Automated Validation

The final taxonomy is validated against 12 sample research dean queries to confirm ≥80% high-relevance match rate.

## Current Taxonomy (v2)

- **67 domain topics** across disease areas, basic science, clinical specialties, population health, and research infrastructure
- **8 oncology subtopics** (breast, lung, prostate/urologic, GI, neuro-oncology, gynecologic, melanoma/skin, general cancer biology)
- **Calibrated against Science-Metrix** publication volumes for granularity decisions
- **Validated at 100% match rate** (12/12 sample queries) before volume-based refinement

## Multi-Axis Query Architecture

| User Query | Axis 1 (Domain) | Axis 2 (Tools/Methods) |
|------------|-----------------|------------------------|
| "Who works on breast cancer?" | `breast_cancer` | — |
| "Who uses CRISPR?" | — | CRISPR keywords |
| "Who does immunotherapy for lung cancer?" | `lung_cancer` | immunotherapy keywords |
| "Who works on aging?" | `aging_geroscience` | — |
| "Find AI researchers in cardiology" | `cardiovascular_disease` | machine learning, AI keywords |

## Versioning

- `taxonomy_v1`: Initial 50-topic flat taxonomy (mixed domains and methods). Superseded.
- `taxonomy_v2`: 67-topic domain-focused taxonomy with volume-calibrated oncology split, boundary clarifications, and multi-axis design. Current.
