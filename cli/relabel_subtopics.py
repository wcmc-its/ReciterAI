"""
One-shot relabel pass: backfill `display_name` + `short_description` (D-19)
into existing `hierarchy_draft_<id>.json` and `hierarchy_augmented_<id>.json`
files without re-clustering.

Reads each existing draft, sends Sonnet just (id, label, description) per
subtopic, and patches the two new UI-facing fields back into the draft and
its corresponding augmented file. IDs, seed_pmids, total_weight, and
activity_count are NEVER touched (D-06: subtopic IDs remain stable across
the recompute year).

Usage:
    python relabel_subtopics.py                       # all topics
    python relabel_subtopics.py --topic aging_geroscience
    python relabel_subtopics.py --limit 3 --dry-run   # smoke test
    python relabel_subtopics.py --force               # re-relabel even if populated
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from prompts.subtopic_relabel import (
    BUILD_RELABEL_USER_MESSAGE,
    RELABEL_SYSTEM_PROMPT,
)
from utils.bedrock_client import BedrockClient, SONNET_MODEL


PHASE_DIR = REPO_ROOT / ".planning/phases/04-subtopic-system"
TAXONOMY_FILE = REPO_ROOT / "taxonomy_v2.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_taxonomy() -> dict:
    with open(TAXONOMY_FILE) as f:
        data = json.load(f)
    return {t["id"]: t for t in data.get("topics", [])}


def _draft_path(topic_id: str) -> Path:
    return PHASE_DIR / f"hierarchy_draft_{topic_id}.json"


def _augmented_path(topic_id: str) -> Path:
    return PHASE_DIR / f"hierarchy_augmented_{topic_id}.json"


def _all_draft_topic_ids() -> list:
    """Discover topic IDs from `hierarchy_draft_*.json` files on disk."""
    out = []
    for p in sorted(PHASE_DIR.glob("hierarchy_draft_*.json")):
        # Strip prefix "hierarchy_draft_" and suffix ".json"
        stem = p.stem
        if stem.startswith("hierarchy_draft_"):
            out.append(stem[len("hierarchy_draft_"):])
    return out


_PARENT_PREFIX_STOPWORDS = {
    "and", "or", "of", "the", "in", "on", "for", "to", "a", "an", "&", "with", "at",
}


def _parent_tokens(topic_id: str, topic_label: str) -> set:
    """Tokens (lowercased) that the first word of display_name must not match."""
    tokens = set()
    for tok in topic_id.split("_"):
        t = tok.lower().strip()
        if t and t not in _PARENT_PREFIX_STOPWORDS:
            tokens.add(t)
    for tok in topic_label.split():
        t = "".join(c for c in tok.lower() if c.isalnum())
        if t and t not in _PARENT_PREFIX_STOPWORDS:
            tokens.add(t)
    return tokens


def find_parent_prefix_violation(
    topic_id: str, topic_label: str, display_name: str
) -> Optional[str]:
    """
    Return a short reason string if display_name's first word repeats a parent topic
    word, else None. Normalization: lowercase, strip surrounding punctuation, drop
    internal hyphens (so "Pre-Clinical" -> "preclinical").
    """
    if not display_name:
        return None
    raw_first = display_name.split()[0]
    cleaned = "".join(c for c in raw_first.lower() if c.isalnum() or c == "-")
    first_norm = cleaned.replace("-", "")
    if not first_norm:
        return None
    if first_norm in _parent_tokens(topic_id, topic_label):
        return (
            f"display_name first word {raw_first!r} repeats parent topic "
            f"(parent label: {topic_label!r}, parent id: {topic_id!r})"
        )
    return None


def _is_already_populated(subtopic: dict) -> bool:
    """A subtopic is considered populated when BOTH new fields are non-empty."""
    return bool(subtopic.get("display_name")) and bool(subtopic.get("short_description"))


def _all_populated(subtopics: list) -> bool:
    return all(_is_already_populated(s) for s in subtopics) and len(subtopics) > 0


def _call_relabel(
    client: BedrockClient,
    topic_id: str,
    topic_label: str,
    topic_description: str,
    subtopics: list,
) -> dict:
    """One Sonnet call for a single topic. Returns parsed JSON dict."""
    user_msg = BUILD_RELABEL_USER_MESSAGE(
        topic_id=topic_id,
        topic_label=topic_label,
        topic_description=topic_description,
        subtopics=subtopics,
    )
    messages = [{"role": "user", "content": user_msg}]
    parsed = client.call_json(
        model=SONNET_MODEL,
        messages=messages,
        system=RELABEL_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.0,
    )
    return parsed


def _apply_relabels(subtopics: list, relabels: list, topic_id: str) -> tuple:
    """
    Merge relabel entries (matched by id) into subtopic dicts in-place.

    Returns (patched_count, missing_ids) where:
        patched_count: number of subtopics that got non-empty new fields written
        missing_ids:   list of subtopic ids the relabel response did not cover
    """
    by_id = {r["id"]: r for r in relabels if isinstance(r, dict) and "id" in r}
    patched = 0
    missing = []
    for s in subtopics:
        sid = s.get("id")
        if sid in by_id:
            r = by_id[sid]
            new_display = (r.get("display_name") or "").strip()
            new_short = (r.get("short_description") or "").strip()
            if new_display and new_short:
                s["display_name"] = new_display
                s["short_description"] = new_short
                patched += 1
            else:
                logger.warning(
                    f"[{topic_id}] subtopic {sid}: relabel returned empty fields "
                    f"(display_name={new_display!r}, short_description={new_short!r})"
                )
                missing.append(sid)
        else:
            logger.warning(f"[{topic_id}] subtopic {sid}: not in relabel response")
            missing.append(sid)
    return patched, missing


def _collect_parent_prefix_violations(
    subtopics: list, topic_id: str, topic_label: str
) -> list:
    """Inspect every patched subtopic and return [{id, display_name, reason}, ...]."""
    out = []
    for s in subtopics:
        dn = s.get("display_name") or ""
        reason = find_parent_prefix_violation(topic_id, topic_label, dn)
        if reason:
            out.append({"id": s.get("id"), "display_name": dn, "reason": reason})
    return out


def _retry_parent_prefix_violations(
    client: BedrockClient,
    topic_id: str,
    topic_label: str,
    topic_description: str,
    subtopics: list,
    violations: list,
) -> list:
    """
    Re-prompt Sonnet for ONLY the violating subtopics, with the violation reasons
    spelled out. Apply the new labels in place. Return any violations that remain.
    """
    bad_ids = {v["id"] for v in violations}
    bad_subs = [s for s in subtopics if s.get("id") in bad_ids]
    if not bad_subs:
        return []

    violation_lines = "\n".join(
        f"- id={v['id']!r}: {v['reason']}. Current display_name={v['display_name']!r}."
        for v in violations
    )
    user_msg = BUILD_RELABEL_USER_MESSAGE(
        topic_id=topic_id,
        topic_label=topic_label,
        topic_description=topic_description,
        subtopics=bad_subs,
    )
    user_msg += (
        "\n\nIMPORTANT: the previous attempt for these subtopics produced a "
        "display_name whose first word repeats the parent topic. That is forbidden "
        "by rule 2 of the system prompt. Regenerate each entry with a display_name "
        "whose first word does NOT match any word in the parent topic. "
        "Specific failures from the previous attempt:\n"
        f"{violation_lines}"
    )
    messages = [{"role": "user", "content": user_msg}]
    parsed = client.call_json(
        model=SONNET_MODEL,
        messages=messages,
        system=RELABEL_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.0,
    )
    relabels = parsed.get("relabels", [])
    if relabels:
        _apply_relabels(bad_subs, relabels, topic_id)
    return _collect_parent_prefix_violations(bad_subs, topic_id, topic_label)


def relabel_topic(
    client: BedrockClient,
    topic_id: str,
    taxonomy: dict,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Relabel one topic's subtopics. Patches both draft and augmented files.

    Returns a per-topic result dict for the run summary.
    """
    draft_path = _draft_path(topic_id)
    if not draft_path.exists():
        logger.warning(f"[{topic_id}] no draft file at {draft_path}; skipping")
        return {"topic_id": topic_id, "skipped": "no_draft", "patched": 0}

    with open(draft_path) as f:
        draft = json.load(f)

    subtopics = draft.get("subtopics", [])
    if not subtopics:
        logger.info(f"[{topic_id}] draft has 0 subtopics; nothing to relabel")
        return {"topic_id": topic_id, "skipped": "no_subtopics", "patched": 0}

    if not force and _all_populated(subtopics):
        logger.info(
            f"[{topic_id}] all {len(subtopics)} subtopics already populated; "
            f"skipping (use --force to redo)"
        )
        return {"topic_id": topic_id, "skipped": "already_populated", "patched": 0}

    if topic_id not in taxonomy:
        logger.warning(
            f"[{topic_id}] not in taxonomy_v2.json; using draft topic_label as fallback"
        )
        topic_label = draft.get("topic_label", topic_id)
        topic_description = ""
    else:
        topic_entry = taxonomy[topic_id]
        topic_label = topic_entry.get("label", topic_id)
        topic_description = topic_entry.get("description", "")

    logger.info(
        f"[{topic_id}] relabeling {len(subtopics)} subtopics "
        f"(parent: {topic_label})"
    )

    parsed = _call_relabel(
        client=client,
        topic_id=topic_id,
        topic_label=topic_label,
        topic_description=topic_description,
        subtopics=subtopics,
    )
    relabels = parsed.get("relabels", [])
    if not relabels:
        logger.error(f"[{topic_id}] empty 'relabels' in response; skipping")
        return {"topic_id": topic_id, "skipped": "empty_response", "patched": 0}

    patched, missing = _apply_relabels(subtopics, relabels, topic_id)
    logger.info(
        f"[{topic_id}] patched {patched}/{len(subtopics)} subtopics "
        f"(missing={len(missing)})"
    )

    violations = _collect_parent_prefix_violations(subtopics, topic_id, topic_label)
    if violations:
        logger.warning(
            f"[{topic_id}] {len(violations)} parent-prefix violation(s) after first pass; "
            f"retrying those subtopics with violation context"
        )
        violations = _retry_parent_prefix_violations(
            client=client,
            topic_id=topic_id,
            topic_label=topic_label,
            topic_description=topic_description,
            subtopics=subtopics,
            violations=violations,
        )
        if violations:
            logger.error(
                f"[{topic_id}] {len(violations)} parent-prefix violation(s) STILL present "
                f"after retry; writing file but run will exit non-zero. Offenders: "
                + ", ".join(f"{v['id']}={v['display_name']!r}" for v in violations)
            )

    if dry_run:
        sample = subtopics[0]
        logger.info(
            f"[{topic_id}] [dry-run] sample: id={sample.get('id')!r} "
            f"display_name={sample.get('display_name')!r} "
            f"short_description={sample.get('short_description')!r}"
        )
        return {
            "topic_id": topic_id,
            "patched": patched,
            "missing": len(missing),
            "dry_run": True,
        }

    # Write draft
    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)
    logger.info(f"[{topic_id}] wrote draft -> {draft_path}")

    # Mirror into augmented if present (matched by subtopic id)
    aug_path = _augmented_path(topic_id)
    if aug_path.exists():
        with open(aug_path) as f:
            aug = json.load(f)
        aug_subs = aug.get("subtopics", [])
        # Build lookup from the (now patched) draft subtopics
        patches_by_id = {
            s["id"]: (s.get("display_name", ""), s.get("short_description", ""))
            for s in subtopics
        }
        aug_patched = 0
        for s in aug_subs:
            sid = s.get("id")
            if sid in patches_by_id:
                dn, sd = patches_by_id[sid]
                if dn and sd:
                    s["display_name"] = dn
                    s["short_description"] = sd
                    aug_patched += 1
        with open(aug_path, "w") as f:
            json.dump(aug, f, indent=2, ensure_ascii=False)
        logger.info(
            f"[{topic_id}] mirrored {aug_patched}/{len(aug_subs)} into augmented "
            f"-> {aug_path}"
        )
    else:
        logger.info(f"[{topic_id}] no augmented file at {aug_path}; draft only")

    return {
        "topic_id": topic_id,
        "patched": patched,
        "missing": len(missing),
        "parent_prefix_violations": [v["id"] for v in violations],
        "subtopic_count": len(subtopics),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot relabel pass: populate display_name + short_description "
            "(D-19) on existing hierarchy_draft / hierarchy_augmented files."
        )
    )
    parser.add_argument(
        "--topic",
        help="Single topic ID to relabel (e.g., aging_geroscience). "
             "Default: relabel every draft on disk.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of topics to process (0 = no limit). "
             "Useful for smoke tests.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-relabel even if all subtopics are already populated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call Sonnet and print samples but DO NOT write files.",
    )
    args = parser.parse_args()

    taxonomy = _load_taxonomy()

    if args.topic:
        topic_ids = [args.topic]
    else:
        topic_ids = _all_draft_topic_ids()

    if args.limit > 0:
        topic_ids = topic_ids[: args.limit]

    logger.info(f"Relabeling {len(topic_ids)} topic(s); dry_run={args.dry_run}")

    client = BedrockClient()
    results = []
    failures = []
    for tid in topic_ids:
        try:
            results.append(
                relabel_topic(
                    client=client,
                    topic_id=tid,
                    taxonomy=taxonomy,
                    force=args.force,
                    dry_run=args.dry_run,
                )
            )
        except Exception as exc:
            logger.error(f"[{tid}] failed: {exc.__class__.__name__}: {exc}")
            failures.append({"topic_id": tid, "error": str(exc)})

    total_patched = sum(r.get("patched", 0) for r in results)
    skipped = [r for r in results if r.get("skipped")]
    violators = [r for r in results if r.get("parent_prefix_violations")]
    total_violations = sum(len(r.get("parent_prefix_violations", [])) for r in results)
    print(
        "\n=== Relabel summary ===\n"
        f"  topics processed:        {len(topic_ids)}\n"
        f"  topics succeeded:        {len(results) - len(skipped)}\n"
        f"  topics skipped:          {len(skipped)}\n"
        f"  topics failed:           {len(failures)}\n"
        f"  subtopics patched:       {total_patched}\n"
        f"  parent-prefix violations: {total_violations} "
        f"(across {len(violators)} topic(s), after retry)\n"
        f"  dry_run:                 {args.dry_run}\n"
    )
    if failures:
        for fail in failures:
            print(f"  FAIL {fail['topic_id']}: {fail['error']}")
    if violators:
        for r in violators:
            print(
                f"  PARENT-PREFIX {r['topic_id']}: "
                f"{', '.join(r['parent_prefix_violations'])}"
            )
    if failures or violators:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
