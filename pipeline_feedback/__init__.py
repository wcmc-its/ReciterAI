"""Phase 12 §9 feedback-event consumer.

Submodules:
- finding_records: pure builders + writers for CANDIDATE_TOPIC#,
  RECLUSTER_RECOMMENDATION#, SPOTLIGHT_DIAGNOSTIC# rows (D-04, D-08).
- sweep:           main run_sweep() orchestrator over UNCOVERED_PMID#,
  LOW_CONFIDENCE_ASSIGNMENT#, and CRITIC_REJECT# events
  (D-01..D-09, D-32, D-34).
- cli:             `python -m pipeline_feedback {sweep|render}` entry
  point. Operator-CLI mode is diagnosis; cold-path-stage mode is
  documentation against the cold run_id.
- markdown_render: deterministic markdown rendering for sweep findings
  (D-03 — byte-identical output for equivalent inputs).
"""
