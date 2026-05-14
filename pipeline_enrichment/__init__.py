"""Upstream enrichment pipelines — synopsis + impact for new publications.

Ported from `wcmc-its/ReCiterAI-POC` as part of #37. Source-of-truth prompts
are mirrored in `prompts.py`; equivalence vs. the POC laptop runs is verified
by `scripts/equivalence_check.py`.

This package is intentionally narrow:
- `synopsis.py` — generate a ≤95-char synopsis from (title, abstract)
- `impact.py` — generate impact score (0–100) + justification from
  (title, abstract, bibliometric fields)

Both are pure per-PMID workers. The scheduled-job wiring (watermark,
cost guard, MariaDB/DDB writes, alerting) lands in step 2 of #37 and
lives elsewhere.
"""
