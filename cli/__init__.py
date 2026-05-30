"""CLI entrypoint scripts for the ReciterAI pipeline.

These modules are runnable directly (``python -m cli.<name>``) and were moved
here from the repo root to declutter the top level. Each adds the repo root to
``sys.path`` at import time so ``utils.*`` and sibling top-level modules resolve
regardless of the current working directory.
"""
