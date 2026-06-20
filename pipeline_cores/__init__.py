"""Core-facility usage inference.

A third classification axis alongside Topics and Subtopics: "Which WCM core
facility (Biomedical Imaging, Flow Cytometry, Genomics, ...) was used to
produce this publication?"

Multi-signal candidate-generation feeding a human claim (see README.md):
  1. author_affinity  — derived per (cwid, core) prior; repeat users recur
  2. coauthorship     — core-staff CWID resolved on the byline (deterministic)
  3. acknowledgement  — core named in full text (deterministic confirmer)
  4. llm_triage       — two-pass Bedrock score from title+abstract (ranking only)
  5. human claim      — written in SPS (ADR-005 override); the source of truth

The validation that shaped this design lives in
`ReCiter-Publication-Manager` / `Projects/Inferring Cores and Services`:
acknowledgement recall is ~0% in the wild (high precision, near-zero recall),
LLM is a triage filter (not an auto-labeler), and resolved co-authorship is the
best scalable recall lever (39% recall / 100% precision on the pilot).
"""
