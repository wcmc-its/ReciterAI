# When does a compound research area warrant splitting?

Companion to [`taxonomy-methodology.md`](./taxonomy-methodology.md) (design principles) and
[`topic-subtopic-assignment.md`](./topic-subtopic-assignment.md) (generation/assignment mechanics).
Those describe how the taxonomy is built; this doc gives a repeatable test for one specific,
recurring question — **"should this compound `X & Y` topic be two separate top-level research
areas?"** — so a reviewer complaint about area boundaries gets a defensible answer instead of an ad
hoc one.

## Why this needs its own test

50 of 67 (49 of 66 active) research areas use a compound "X & Y" (or "X, Y & Z") label — 75% of the
taxonomy. That's the taxonomy's normal naming convention, not a defect signal, so "it has an
ampersand" can't be the test. Splitting only makes sense where a label is bundling two genuinely
distinguishable domains, not where it's naming one coherent domain that happens to use "&"
(`biochemistry_biophysics` is literally one WCM department's actual name) or restating one field
twice (`aging_geroscience`, `biostatistics_quantitative_sciences`, `mental_health_psychiatry`).

## Two precedents this is *not*

Both were raised as precedent for a proposed split and turned out to be a different thing:

- **The Oncology → 8 cancer-type subareas split is a subtopic-level split within one topic**
  (Design Principle 3 / Step 4 in `taxonomy-methodology.md`), not a precedent for splitting a
  top-level *topic* into two topics.
- **`hematology_medical_oncology` (taxonomy 67→68) was not a split of anything.** It was an
  *additive mint* of a brand-new topic via `cli/score_new_topics.py` (issue #225 — see
  `topic-subtopic-assignment.md` § "Adding a single research area later"), a different mechanism
  from splitting an existing bundled topic. It also wasn't a clean, risk-free precedent: the tooling
  had to score the new area "against a small set of 'context' competitor areas for **contrastive
  routing** (so a heme/onc paper isn't mis-routed to a neighboring cancer area)," plus a
  `--verify-fidelity` step quantifying the assignment delta on a sample before any write — precisely
  because scoring a new area in isolation risks overlap contamination with its neighbors. That
  mechanism (contrastive scoring + fidelity check before write) is the right tool to reuse when
  *acting* on a split candidate identified below — not a mechanical rename/divide.
  **`hematology_medical_oncology` was subsequently retired outright** (`cli/retire_topic.py`,
  commit `02d3445`, 2026-07-10; taxonomy 68→67) — direct evidence that an isolated-scoring additive
  mint carries enough real risk that ReciterAI has since had to build tooling to walk one back, not
  just to create one.

## The five-part test

A compound topic is a split candidate only if **all** of the following hold. Default to **false** —
this exists specifically to resist pattern-matching on "&" in the label.

1. **Real components.** The label must name 2+ domains a dean or researcher would recognize as
   distinct specialties — not a natural single-domain name, not a near-synonym/appositive pair.
2. **Clean subtopic partition.** Every subtopic sorts onto one component or the other; ambiguous
   (spans-both or belongs-to-neither) weight must be **<25%** of the topic's total.
3. **Independent volume floor.** Each component's subtopics must sum to **≥21.8 total_weight and
   ≥46 activity_count** on their own — the empirical floor of the smallest currently-active topic
   (`sleep_medicine_circadian_biology` as of `out/hierarchy/v2026-06-19/`). This floor moves with
   each cold-run re-cluster; re-derive it rather than hard-coding these numbers going forward.
   Necessary, not sufficient.
4. **Distinct real institutional identity per component.** Each component should correspond to a
   real, recognizable department, division, or program — not just a plausible-sounding sub-label.
   This operationalizes Design Principle 3's own carve-out: small/component categories stay merged
   "unless they represent distinct departmental identity."
5. **No shared institutional home.** If both proposed components resolve to the *same* department
   or program, that's evidence for one area, not two.

## Worked results (2026-07-31 audit)

Applied to all 49 active compound-named topics in `out/hierarchy/v2026-06-19/hierarchy.json` /
`taxonomy_v2.json`. **2 of 49 pass:**

| Topic | Component A | Component B |
|---|---|---|
| Pain Management & Anesthesiology | Pain Medicine — 53.0 wt / 144 act (ACGME-accredited subspecialty; no single WCM dept code) | Anesthesiology — 31.5 wt / 80 act (WCM Dept of Anesthesiology) |
| Neuroscience & Neurology | Basic Neuroscience — 133.9 wt / 234 act (home: Brain and Mind Research) | Clinical Neurology — 310.8 wt / 484 act (WCM Dept of Neurology) |

Both have <18% ambiguous-subtopic share. Representative failures and the criterion that killed them:

- **Criterion 1** (no real second component — appositive naming): `aging_geroscience`,
  `biostatistics_quantitative_sciences`, `mental_health_psychiatry`.
- **Criteria 4/5** (real domains, but no distinct institutional identity, or both sides share one):
  `biochemistry_biophysics` (both halves live under one department), `autoimmune_rheumatologic_disease`
  (Rheumatology is a division of Medicine, not its own department — and neither half clears
  criterion 3 alone either, 39/27 activity against the 46 floor).

Full audit trail, department roster used, and the raising complaint: cross-repo issue
[wcmc-its/Scholars-Profile-System → ReciterAI#339](https://github.com/wcmc-its/ReciterAI/issues/339).

## Limitations

- Criterion 3's floor is a snapshot; it shifts on every annual cold-run re-cluster.
- Criterion 4 was checked against WCM's Scholars-Profile-System department roster
  (`lib/department-categories.ts` there), which is SPS's own hand-curated list and can drift from
  the actual org chart independently of this taxonomy.
- Subtopic-to-component classification for the 2026-07-31 audit was done by LLM agents against
  topic/subtopic labels, not manually reviewed subtopic-by-subtopic for all 49 — treat a borderline
  verdict as advisory, not final, and spot-check before acting on it. (One flagged case: within
  Pain Management & Anesthesiology, "Headache Disorders & Surgical Nerve Decompression" sits closer
  to a neurology/surgery boundary than a clean pain-vs-anesthesia split.)
- The test as written evaluates a binary partition; a genuine 3-way bundle (e.g. "Gastroenterology,
  Hepatology & Pancreatic Disease") was included in the 49 audited but would need the test
  generalized before trusting a "false" verdict on one.
- This document records a **recommendation and a reusable test**, not an implementation decision.
  Whether/how to act (mechanical relabel vs. re-running `score_new_topics.py`-style additive scoring
  with contrastive routing against the sibling component) is tracked in ReciterAI#339.
