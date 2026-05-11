# Spotlight Lede Generator: Opus 4.7 vs Sonnet 4.6 Evaluation

**Date:** 2026-05-08
**Decision:** Ship Opus 4.7 (`us.anthropic.claude-opus-4-7`).
**Closes:** wcmc-its/ReciterAI#2 §4.

## Setup

Same prompt (post-A/B/C, all forbidden-pattern + opener-rotation rules in place). Same paper pool. Same 10 subtopic selections (rotation history hadn't shifted enough in 24h to re-pick). Only the lede generator's model varies. Critic stays on Haiku-judge + deterministic regex bundle.

| File | Model | Notes |
|---|---|---|
| `out/spotlight-baseline-sonnet.json` (== `out/spotlight-2026-05-07.json`) | Sonnet 4.6 | 2026-05-07 `--dry-run-full` after C shipped, 10/10 spotlights, 10 distinct openers |
| `out/spotlight-2026-05-08.json` | Opus 4.7 | 2026-05-08 `--dry-run-full` with model swapped, 10/10 spotlights, 10 distinct openers |

## Verdict (operator analysis, 2026-05-08)

**May 8 is meaningfully better.** It wins 7 of 10 ledes clearly, ties on 2, and only loses one on cadence. The pattern is consistent enough that I'd ship May 8 over May 7.

### Why May 8 wins

**It actually looks at the papers.** May 8 names specific mechanisms and findings; May 7 stays at the level of generic science journalism. Some of the clearest examples:

*Healthcare Financing & Payment Policy* — papers cover ACO spending, same-day discharge nephrectomy costing, and insurer-PBM pharmacy steering.
- May 7: "testing which financing models actually bend the cost curve without compromising outcomes or access" — could describe any health-services-research group anywhere.
- May 8: "testing accountable care savings, same-day surgical pathways, and insurer-PBM pharmacy steering that quietly reshapes Medicare markets" — maps 1:1:1 to the three papers.

*Neuronal & Synaptic Biology* — papers cover SARS-CoV-2 dopaminergic senescence, VPS35/retinal α-synuclein, and synaptic vesicle-omics in synucleinopathy.
- May 7: "viral infection, protein aggregation, and synaptic aging…molecular checkpoints where damage begins" — abstract.
- May 8: "vesicle dynamics, viral insults, and trafficking defects that push dopamine and retinal circuits toward Parkinson's and other synucleinopathies" — names the actual circuits (dopamine, retinal) and the actual disease class (synucleinopathies, which is literally in paper 3's title).

*Breast Cancer Risk & Screening* — this is the most clear-cut. The three papers are C-peptide+mammography (metabolic), breast arterial calcification + CVD risk, and digital genetic risk assessment in underserved populations.
- May 7 mentions "screening images and metabolic biomarkers" — completely misses the cardiovascular calcification paper.
- May 8: "metabolic signals, vascular calcifications, and inherited risk all leave traces" — captures all three.

**It avoids editorializing.** May 7 has a few phrases that drift past what the papers actually show: "show policymakers where the system falls short" (the ACO paper isn't really an indictment), "sharpening diagnosis before patients run out of options" (melodramatic), "fragmented care and delayed surgery cost lives" (papers measure outcomes, don't establish causality). May 8's voice is more measured.

### Where May 7 has an edge

**Cadence on the two cancer-genomics ledes (#6 and #9).** "Tumors don't follow a fixed script" and "they shift lineage, silence suppressors, and rewrite their own vulnerabilities" have nicer parallel structure than May 8's equivalents. If you wanted to A/B by hand, I'd consider lifting these openers into May 8's overall frame.

## Issues that affect both versions equally

A few things worth flagging that aren't really about which run wins:

- **Spotlights #6 and #9 share two of three papers** (Gardner/Varmus and Ferrarone/Varmus appear in both "Cancer Genomics & Somatic Mutation Profiling" and "Cancer Genomics & Tumor Evolution"). Neither lede differentiates them well because the underlying subtopics overlap. This is a taxonomy issue, not a writing issue — worth checking whether your subtopic discovery pass is producing near-duplicate clusters in cancer genomics specifically.

- **Both versions slip in "drug resistance" / "treatment escape" framing on the cancer-genomics ledes** when none of the cited papers are about drug resistance. Worth a prompt nudge if you're tuning the generator.

- **Style mixing on "WCM" vs. "Weill Cornell Medicine" vs. "Weill Cornell"** within a single page. Both runs do this. For a public-facing profile, I'd pin one form per page.

- **Both use the same two-sentence pattern** (hook → "WCM faculty are doing X"). That's fine for a grid of cards, but if these stack vertically the rhythm gets monotonous. Worth seeing if the prompt can permit one or two ledes to break form.

## Cost

| | Sonnet 4.6 | Opus 4.7 |
|---|---|---|
| Per `--publish` run (10 ledes + ~12 critic calls) | ~$0.30 | ~$1.50 |
| Annual (52 weekly publishes) | ~$15.60 | ~$78 |

Cost delta is negligible against the editorial-quality lift on a faculty-facing surface.

## Code change shipped

Single-file diff: `spotlight/lede_generator.py` and `utils/bedrock_client.py`. The Bedrock client now treats `temperature=None` as "omit from inferenceConfig" because Opus 4.7 deprecates the temperature parameter. Lede generator passes `temperature=None`; the model uses its built-in default.

The `LEDE_TEMPERATURE = 0.5` constant is preserved as historical documentation of the Sonnet-era setting.

Critic stays on Haiku — structured rule-checking is well within Haiku's range and the deterministic regex bundle does the first pass anyway.
