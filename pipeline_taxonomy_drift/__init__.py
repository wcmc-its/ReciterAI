"""Taxonomy-vs-data drift detection (ADR D5 layer 2).

Distinct from `pipeline_drift/`, which evaluates model-quality drift (uncovered
rate, low-confidence assignments) over a rolling event window. This package
answers a different question: does the `TOPIC#` partition set in DynamoDB match
the taxonomy the code claims to run?
"""
