"""Area signals: research-area scores for grant and clinical-trial text.

SPS writes one ``PK=AREAINPUT`` item per distinct grant or trial text (keyed by
a hash of the normalized text; no person or award identifiers). This package
drains the pending ones, writes a synopsis where there is enough text, scores
the text against the research-area taxonomy with the production two-pass scorer,
and persists ``PK=AREASIGNAL#{kind}:{text_key}`` / ``SK=META`` for SPS to
project into its ranking.

Modules:

- ``prompts``: the grant and trial synopsis prompts, verbatim from the pilot.
- ``signals``: one input -> one output row (synopsis, scores, cost metering).
- ``drain``: the ``AREAINPUT`` queue (selection, status re-read, marking).
- ``run``: the daily entrypoint (``python -m pipeline_area_signals.run``).
"""
