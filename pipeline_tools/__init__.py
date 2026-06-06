"""
pipeline_tools — Axis 2 (tools / methods) producer.

Builds the canonical tool/method taxonomy specified in
`docs/tools-producer-model.md` (Phase 8). Resolves issues #5 (vocabulary),
#6 (discovery), #7 (cardinality), #8 (TOOL# schema).

A1 (this first stage): seed canonicalization — raw tool names → reviewed
`tool_taxonomy_v1.json`. Human-gated (D-07) before it feeds anything downstream.
"""
