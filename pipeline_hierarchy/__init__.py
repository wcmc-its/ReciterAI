"""Hierarchy artifact bundler + publisher package.

The `__version__` is part of each stage's `input_hash` schema (see
`utils/stage_records.compute_input_hash`). Bump it when changing any
bundler / generator / publish behavior that affects produced bytes;
the bump invalidates skip cache for the next publish.
"""

__version__ = "0.1.0"
