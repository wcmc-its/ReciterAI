#!/usr/bin/env python3
"""Build the initial tools state, reconciled to the LIVE artifact (scheduled-refresh Phase 0/1).

Reads (never writes) the local corpus-build registries and a local copy of the live
``tools/latest/`` objects, reconciles them into a ``pipeline_tools.state.ToolsState``,
re-emits the publish set with zero new PMIDs and compares it to live object by object.
The state is saved as an immutable ``<out-root>/<version>/`` plus ``<out-root>/latest.json``
only when the re-emit is byte-identical for all seven objects (``--allow-diff`` overrides),
then loaded back from disk and re-emitted again so the saved copy is proven too.

Local only: no S3, DynamoDB, DB or LLM calls. Uploading the state dir to
``s3://wcmc-reciterai-artifacts/tools/_state/`` is a separate, reviewed step.

    python cli/tools_state_reconcile.py \\
        --registry-dir <ReciterAI>/out/tools/a2 \\
        --live-dir <copy of s3://wcmc-reciterai-artifacts/tools/latest/>
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline_tools import state as st
from pipeline_tools.registry import (
    DEFAULT_ALIAS_MAP_PATH,
    load_method_alias_map,
)

log = logging.getLogger("tools_state_reconcile")


def _load_list(path: Path, key: str) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get(key, data) if isinstance(data, dict) else data


def run(args) -> int:
    reg_dir = Path(args.registry_dir)
    live_raw = st.read_live_dir(args.live_dir)
    live = st.parse_live(live_raw)
    tools = _load_list(reg_dir / "tool_registry.json", "tools")
    fams = _load_list(reg_dir / "family_registry.json", "families")
    deny_path = Path(args.denylist) if args.denylist else reg_dir / "tool_denylist.json"
    denylist = _load_list(deny_path, "aliases") if deny_path.exists() else []
    merges, _keep_sep = load_method_alias_map(Path(args.alias_map))
    log.info("registry: %d tools, %d families, %d denylist; live: %d tools, %d families",
             len(tools), len(fams), len(denylist), len(live["bundle"]["tools"]), len(live["bundle"]["families"]))

    state = st.reconcile(tools, fams, denylist, live, merges=merges)
    items, _manifest = st.reemit(state)
    cmp = st.compare_to_live(items, live_raw)
    cmp["tool_context_meta_derived_equals_live"] = st.check_meta(state, items)
    out = {"reconcile": state.reconcile_report, "reemit_vs_live": cmp}

    if args.reemit_dir:
        d = Path(args.reemit_dir)
        d.mkdir(parents=True, exist_ok=True)
        for it in items:
            if it.key.startswith(f"{st.S3_PREFIX}latest/"):
                (d / it.key.rsplit("/", 1)[-1]).write_bytes(it.body)

    ok = cmp["all_objects_identical"] and cmp["tool_context_meta_derived_equals_live"]
    if not ok and not args.allow_diff:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        log.error("re-emit is NOT identical to live; state not saved (use --allow-diff to save anyway)")
        return 2

    store = st.LocalStateStore(args.out_root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = args.version or f"{now[:10]}-live-reconcile"
    st.save_state(store, state, version, created_at=now, notes={
        "source": "cli/tools_state_reconcile.py",
        "registry_dir": str(reg_dir),
        "live_generated_at": live["manifest"].get("generated_at"),
        "reemit_all_objects_identical": cmp["all_objects_identical"],
    })
    # Round trip: the state as saved must re-emit the same bytes.
    loaded = st.load_state(store)
    items2, _ = st.reemit(loaded)
    cmp2 = st.compare_to_live(items2, live_raw)
    out["saved"] = {"out_root": str(Path(args.out_root).resolve()), "version": version,
                    "reloaded_all_objects_identical": cmp2["all_objects_identical"],
                    "reloaded_manifest_identical": cmp2["manifest"]["identical"]}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if (ok and cmp2["all_objects_identical"]) else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile the tools registries to the live artifact.")
    ap.add_argument("--registry-dir", required=True, help="dir with tool_registry.json + family_registry.json")
    ap.add_argument("--live-dir", required=True, help="local copy of s3://wcmc-reciterai-artifacts/tools/latest/")
    ap.add_argument("--denylist", help="tool_denylist.json (default: <registry-dir>/tool_denylist.json)")
    ap.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP_PATH), help="#252 method_alias_map.json")
    ap.add_argument("--out-root", default=str(REPO_ROOT / "out" / "tools_state"), help="state root dir")
    ap.add_argument("--version", help="state version name (default: <UTC date>-live-reconcile)")
    ap.add_argument("--reemit-dir", help="also write the re-emitted latest/ objects here for inspection")
    ap.add_argument("--allow-diff", action="store_true", help="save the state even if the re-emit differs")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
