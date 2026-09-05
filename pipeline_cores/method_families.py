"""A2 method-family taxonomy, joined to PMIDs, for the cores evidence model.

`pipeline_tools` already reads every faculty first/last-authored abstract since 2020
and canonicalises the tools and methods it finds into 816 method FAMILIES, published
as a versioned artifact under `s3://<artifacts bucket>/tools/latest/`. This module is
the only thing that reads that artifact on the cores side: it joins

    tools.json         .tools[]        canonical_tool_id -> method_family_label, display_name
    tool_context.json  .tool_context   canonical_tool_id -> {pmid: evidence sentence}

into {pmid: [(family_label, tool_display_name, sentence), ...]}, which
`signals.method_family_signal` matches against a core's curated `method_families:`.

NOT a new source of evidence, and it is worth being exact about that: the extractor
behind the artifact (`pipeline_tools/extract.py`) reads title + abstract, the same two
fields `signals.llm_triage` reads. What this buys is a better REPRESENTATION of that
text — canonical labels instead of surface forms, deduplicated across 18,405 tools,
comparable across papers, with a quotable sentence attached — not text the cores
pipeline could not already see. Full text reaches only the deterministic alias matcher
(signal 3), in either pipeline.

A tool whose `method_family_label` is null is DROPPED (891 of 18,405 in v2026-06-23):
no family, no chip, and nothing downstream should have to carry a None through.

THIS FUNCTION RAISES ON A FAILED READ OR PARSE. It never degrades to an empty index,
and that is not a style preference: `persist.put_core_usage` REMOVEs every attribute
in `_OWNED_ATTRS` that the run did not produce, so an empty index would strip
method_families / method_tier / method_tool / method_snippet off every row a previous
run wrote — silently, on a green run. A degraded read on a path that WRITES is a wipe,
not a degradation. Same rule as `scan_core_llm_scores` (which raises unconditionally)
and `scan_prior_core_usage` under strict=True; the fail-soft S3 tier in `fulltext.py` is
safe only because a miss there costs one paper's signal rather than every row's
attributes.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

# The published artifact. `latest/` is the live pointer the tools pipeline republishes
# to; manifest.json beside these two carries the schema_version / version / counts an
# operator needs to tell a stale artifact from a fresh one (frozen at v2026-06-23 —
# the tools pipeline has no schedule).
TOOLS_KEY = "tools/latest/tools.json"
TOOL_CONTEXT_KEY = "tools/latest/tool_context.json"


def make_s3_backend(bucket: str = None):
    """The artifacts-bucket S3 client, with boto3 imported lazily.

    Same shape and the same reason as `fulltext.make_s3_backend`: importing
    `pipeline_cores.method_families` must not drag boto3 in, so the unit tests (which
    pass local paths) and every non-S3 caller stay dependency-free. Only
    get_object_bytes is used, so any duck-typed stand-in works in tests.
    """
    from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient  # lazy: keeps this module dep-free

    return S3HierarchyClient(bucket=bucket or ARTIFACTS_BUCKET)


def load_family_index(*, tools_path=None, context_path=None, s3=None, bucket: str = None) -> dict:
    """{pmid: [(family_label, tool_display_name, sentence), ...]} from the A2 artifact.

    Reads S3 by default; `tools_path` / `context_path` override with local files for
    tests and dev. ~26 MB of JSON, so load it ONCE per run — never per publication.

    RAISES on any read or parse failure, including a missing top-level key AND a
    well-formed artifact that yields no rows at all. See the module docstring: this index feeds a write path, and `put_core_usage` turns an
    empty read into a REMOVE of the method_* attributes on every previously-scored
    row. There is deliberately no try/except anywhere in here — an operator seeing a
    traceback and re-running loses nothing, where a green run over an empty index
    loses the data.

    The family label carried through is the ARTIFACT's, not the dictionary's: it is
    the canonical display form (the YAML side is casefolded for matching), so what
    reaches DynamoDB is the taxonomy's own string rather than however a curator typed
    it.
    """
    tools = _read_json(TOOLS_KEY, tools_path, s3, bucket)["tools"]
    context = _read_json(TOOL_CONTEXT_KEY, context_path, s3, bucket)["tool_context"]

    # method_family_label is nullable (891 of 18,405 tools in v2026-06-23) — those
    # tools are dropped here, so no caller downstream ever sees a family of None.
    families = {t["canonical_tool_id"]: (t["method_family_label"], t.get("display_name") or "")
                for t in tools if t.get("method_family_label")}

    index: dict = defaultdict(list)
    for tool_id, by_pmid in context.items():
        hit = families.get(tool_id)
        if not hit:
            continue                                  # null family, or a context entry for
        family, display = hit                         # a tool no longer in the registry
        for pmid, sentence in by_pmid.items():
            index[str(pmid)].append((family, display, sentence or ""))
    # sorted(set(...)): one paper carries up to 9 families and several tools can sit in
    # the same one, so dedupe identical triples and give the list a stable order —
    # method_family_signal re-ranks by TIER, and a tie inside a tier must not depend on
    # dict iteration order.
    out = {pmid: sorted(set(rows)) for pmid, rows in index.items()}
    if not out:
        # The one degraded read the raises above do not catch: a WELL-FORMED artifact
        # carrying no usable rows — `{"tools": []}`, a half-written republish, a schema
        # change that renames method_family_label. Every read error raises, and then the
        # empty result of a successful read would have walked straight past all of it
        # into the REMOVE clause in `put_core_usage` and stripped method_* off every row
        # a previous run wrote. A floor at "not empty" rather than a fitted minimum: the
        # failure being guarded is a republish that produced nothing, not a slow drift in
        # corpus size, and a real threshold would need re-tuning every time the corpus
        # grows. v2026-06-23 indexes 6,953 PMIDs, so zero is unambiguous.
        raise ValueError(
            f"method-family index is EMPTY (tools={len(tools)}, tool_context={len(context)}) — "
            "refusing to return it: an empty index written by run.py REMOVEs the method_* "
            "attributes from every previously scored row. Check the artifact at "
            f"{TOOLS_KEY} / {TOOL_CONTEXT_KEY}."
        )
    return out


def _read_json(key: str, path, s3, bucket: str) -> dict:
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return json.loads((s3 or make_s3_backend(bucket)).get_object_bytes(key))
