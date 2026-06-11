"""Brick B (#191): the deterministic-first reconcile stage for durable subtopic IDs.

Replaces brick A's exact-slug `SubtopicIdStore.match()` with a 3-stage match-or-mint
against the PRIOR published snapshot, so a re-cluster keeps stable IDs (a relabel is
a rename, deep-links + rotation history survive). Design: `docs/subtopic-lifecycle-
and-evolution.md` §6 (Option C "match key"), §10 #2.

Three stages, deterministic-first (departs from tools/families' embedding+LLM-first
to keep almost every decision bit-stable):
  1. **Membership overlap** (min-cardinality, `pipeline_hierarchy.overlap`) vs the
     prior snapshot — cheap, fully deterministic, resolves the large majority.
  2. **Label-embedding cosine** (`pipeline_tools.embeddings`) — only the Stage-1
     ambiguous band; embeds the LABEL (Stage 1 already adjudicated the PMID corpus).
  3. **LLM arbiter** (`call_with_fallback`, Bedrock Sonnet → OpenAI fallback) — only
     the residual tail; verdicts cached on a STABLE content hash (never an id / slug /
     run_id / mint order), so reruns reproduce.

All matching runs in `precompute()` in one deterministic pass that also resolves
conflicts (no prior id claimed by two new clusters). `match()` is then a pure lookup
— no per-call ordering hazard, no live-table reads (the intra-run self-match brick A
warned about is structurally impossible: the snapshot is captured before any mint).

Scope: brick B produces the conflict-free assignment (attach → prior id | mint) and
the per-decision REASON. It does NOT write split/merge LINEAGE records, mutate
`status`, or touch any consumer-facing byte — those are bricks C/D. `embed` and
`arbiter` are injectable so unit tests run offline with no AWS.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pipeline_hierarchy.bundler import DEFAULT_THRESHOLDS_PATH
from pipeline_hierarchy.overlap import best_overlap, min_card_overlap
from pipeline_hierarchy.subtopic_id_store import (
    META_SK,
    SUBTOPIC_ID_PK_PREFIX,
    SubtopicDefer,
    SubtopicMatch,
    _sorted_pmids,
)
from pipeline_tools.embeddings import EmbeddingCache, EmbedFn, nearest_match
from prompts.subtopic_reconcile import (
    ARBITER_PROMPT_VERSION,
    RECONCILE_SYSTEM_PROMPT,
    build_reconcile_user_message,
)
from utils.bedrock_client import MODEL_IDS_BY_STAGE
from utils.stage_records import compute_input_hash

_log = logging.getLogger(__name__)

ARBITER_STAGE = "subtopic_reconcile_arbiter"
# Max prior subtopics offered to the LLM arbiter per ambiguous cluster.
_MAX_LLM_CANDIDATES = 5

# An arbiter takes the new cluster + candidate priors and returns a verdict dict
# {"verdict": "same"|"distinct", "durable_id": <id-or-null>}. Injectable for tests.
ArbiterFn = Callable[..., dict]


@dataclass(frozen=True)
class ReconcileThresholds:
    """The brick-B match thresholds. All PROVISIONAL — to be sized by the
    forward-only Phase-1 jitter measurement (not yet runnable). Read from
    `config/thresholds.json`; fail-loud on a missing key (config contract)."""

    auto_match_min: float
    ambiguous_min: float
    centroid_cosine_min: float
    llm_arbiter_enabled: bool
    # Brick C: directional-absorb bar for marking an unclaimed prior `merged_into` a
    # successor. Optional knob (defaults to auto_match_min — a merge is "at least as
    # confident as an auto-match") so the forward-only jitter measurement can size it
    # independently. PROVISIONAL like the rest.
    merge_overlap_min: Optional[float] = None

    @property
    def effective_merge_overlap_min(self) -> float:
        return self.merge_overlap_min if self.merge_overlap_min is not None else self.auto_match_min

    @classmethod
    def from_config(cls, thresholds_path=DEFAULT_THRESHOLDS_PATH) -> "ReconcileThresholds":
        with open(thresholds_path) as f:
            cfg = json.load(f)

        def _req(key: str):
            if key not in cfg:
                raise ValueError(f"{thresholds_path}: missing required key {key!r}")
            return cfg[key]

        auto = float(_req("subtopic_reconcile_overlap_auto_match_min"))
        amb = float(_req("subtopic_reconcile_overlap_ambiguous_min"))
        cos = float(_req("subtopic_reconcile_centroid_cosine_min"))
        llm_raw = _req("subtopic_reconcile_llm_arbiter_enabled")
        if not isinstance(llm_raw, bool):
            # Fail loud, not bool("false") == True — a coerced string would silently
            # ENABLE the LLM/Bedrock stage in what an operator set as a deterministic run.
            raise ValueError(
                f"{thresholds_path}: subtopic_reconcile_llm_arbiter_enabled must be a JSON "
                f"boolean (true/false), got {type(llm_raw).__name__} {llm_raw!r}"
            )
        llm = llm_raw
        # Brick C: optional — absent means "use auto_match_min" (resolved lazily by
        # effective_merge_overlap_min), so existing configs need no change.
        merge_raw = cfg.get("subtopic_reconcile_merge_overlap_min")
        merge = float(merge_raw) if merge_raw is not None else None
        checks = [("auto_match_min", auto), ("ambiguous_min", amb), ("centroid_cosine_min", cos)]
        if merge is not None:
            checks.append(("merge_overlap_min", merge))
        for name, v in checks:
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{thresholds_path}: {name}={v} outside [0, 1]")
        if amb > auto:
            raise ValueError(
                f"{thresholds_path}: ambiguous_min ({amb}) must be <= auto_match_min ({auto})"
            )
        return cls(
            auto_match_min=auto,
            ambiguous_min=amb,
            centroid_cosine_min=cos,
            llm_arbiter_enabled=llm,
            merge_overlap_min=merge,
        )


def load_id_store_snapshot(table: Any) -> dict[str, dict]:
    """Scan the durable-ID store ONCE into a pre-run snapshot.

    Returns {durable_id: {slug_id, topic_id, seed_pmids(set[int]), label_at_mint,
    status}}. Filters SUBTOPIC_ID#/META rows (scoped to META — forward-safe vs the
    foreshadowed MEMBER#{chunk} overflow SKs). The repo defers GSIs at this scale;
    a paginated Scan over ~1,500 rows is the documented idiom. seed_pmids are
    int-coerced (DDB returns Decimal) into a set for the overlap math.
    """
    from boto3.dynamodb.conditions import Attr

    snapshot: dict[str, dict] = {}
    scan_kwargs = {
        "FilterExpression": Attr("PK").begins_with(SUBTOPIC_ID_PK_PREFIX) & Attr("SK").eq(META_SK)
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            durable_id = item.get("durable_id")
            if not durable_id:
                continue
            snapshot[durable_id] = {
                "slug_id": item.get("slug_id"),
                "topic_id": item.get("topic_id"),
                "seed_pmids": set(_sorted_pmids(item.get("seed_pmids"))),
                "label_at_mint": item.get("label_at_mint", ""),
                "status": item.get("status"),
            }
        # Canonical boto3 pagination: terminate when no continuation key is present.
        # Use membership (`not in`) rather than truthiness so a mock table whose
        # .get() returns a truthy MagicMock cannot spin this loop forever.
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return snapshot


def _default_arbiter(*, new_label, new_seed_pmids, candidates, topic_id) -> dict:
    """Default Stage-3 arbiter: Bedrock Sonnet (→ OpenAI fallback) + lenient parse."""
    from pipeline_enrichment.llm_call import call_with_fallback, parse_json_lenient

    result = call_with_fallback(
        system_prompt=RECONCILE_SYSTEM_PROMPT,
        user_prompt=build_reconcile_user_message(
            new_label=new_label,
            new_seed_pmids=new_seed_pmids,
            candidates=candidates,
            topic_id=topic_id,
        ),
        model=MODEL_IDS_BY_STAGE[ARBITER_STAGE],
        max_tokens=256,
    )
    return parse_json_lenient(result.text)


class SubtopicReconciler:
    """Holds the pre-run snapshot + the precomputed conflict-free assignment.

    Inject into `SubtopicIdStore(table, reconciler=...)`; `match()` then delegates
    here. `embed`/`arbiter`/`verdict_cache` are injectable so tests run offline.
    """

    def __init__(
        self,
        snapshot: dict[str, dict],
        *,
        thresholds: ReconcileThresholds,
        embed: Optional[EmbedFn] = None,
        arbiter: Optional[ArbiterFn] = None,
        verdict_cache: Optional[dict] = None,
        sample_cap: int = 10,
    ):
        self._snapshot = snapshot
        self._t = thresholds
        self._embed_cache = EmbeddingCache(embed=embed)  # embed=None -> live Titan
        self._arbiter = arbiter or _default_arbiter
        self._verdict_cache = verdict_cache if verdict_cache is not None else {}
        self._sample_cap = sample_cap
        self._assignment: dict[str, Optional[object]] = {}
        self.tally: Counter = Counter()
        # Seam for brick C (split/merge lineage), populated by precompute():
        #   split_parents: {minted slug -> the prior durable_id it overlapped in-band
        #     but lost to a stronger sibling (the primitive-split parent)}.
        #   unclaimed_priors: prior ids no new cluster claimed this run (merge-absorbed
        #     / quiet candidates). Brick C reads B's ACTUAL decisions here rather than
        #     replaying the greedy claim-ordering.
        self.split_parents: dict[str, str] = {}
        self.unclaimed_priors: set[str] = set()
        # Prior seed sets grouped by topic (a subtopic only inherits an id within
        # its own topic) — computed once.
        self._prior_by_topic: dict[Any, dict[str, set]] = {}
        for did, row in snapshot.items():
            self._prior_by_topic.setdefault(row.get("topic_id"), {})[did] = row["seed_pmids"]
        # This run's new-cluster seed sets by topic, retained by precompute() so brick C
        # can decide whether a successor absorbed an unclaimed prior (best_successor_overlap).
        self._new_by_topic: dict[Any, dict[str, set]] = {}

    # ---- public API ----

    def precompute(self, *, membership: dict, labels: dict) -> None:
        """Resolve every new cluster -> attach|mint in ONE deterministic pass.

        Greedy by descending best Stage-1 overlap (tie: slug), so the strongest
        continuation claims a contested prior id first; a claimed id is never
        reused (conflict-free). Each cluster then runs Stages 1→2→3 against the
        UNCLAIMED priors in its topic.
        """
        subs = membership.get("subtopics") or {}
        new = {
            slug: {
                "topic_id": e.get("topic_id"),
                "pmids": set(_sorted_pmids(e.get("seed_pmids"))),
                "label": labels.get(slug, ""),
            }
            for slug, e in subs.items()
        }
        # Retain this run's clusters by topic for brick C's merge-absorb decision.
        self._new_by_topic = {}
        for s, nc in new.items():
            self._new_by_topic.setdefault(nc["topic_id"], {})[s] = nc["pmids"]
        # Processing order: strongest best-overlap (claim-agnostic) first, then slug.
        order = sorted(
            new,
            key=lambda s: (
                -best_overlap(new[s]["pmids"], self._prior_by_topic.get(new[s]["topic_id"], {}))[1],
                s,
            ),
        )
        claimed: set[str] = set()
        for slug in order:
            self._assignment[slug] = self._resolve_one(slug, new[slug], claimed)
        # Priors no new cluster claimed this run — the merge-absorbed / quiet set
        # brick C and the retire policy (brick F) consume.
        self.unclaimed_priors = set(self._snapshot) - claimed

    def match(self, *, slug: str, membership=None, topic_id=None):
        """Pure lookup of the precomputed verdict (SubtopicMatch | SubtopicDefer |
        None). `membership`/`topic_id` are accepted for the store seam but unused —
        the decision was made deterministically in precompute()."""
        return self._assignment.get(slug)

    def best_successor_overlap(self, prior_id: str):
        """Brick C: the successor (this run's cluster) that best ABSORBED an unclaimed
        prior, or None.

        Uses directional containment ``|prior ∩ successor| / |prior|`` — how much of
        the PRIOR moved into one new cluster — NOT the symmetric min-cardinality, so a
        small new cluster fully inside a large prior does not falsely read as a merge.
        Restricted to the prior's own topic (an id only continues within its topic).
        Returns ``(winning slug, fraction)`` when the best successor clears
        ``effective_merge_overlap_min``, else None — leave it for brick F. Valid only
        after precompute()."""
        row = self._snapshot.get(prior_id)
        if not row:
            return None
        prior_pmids = row.get("seed_pmids") or set()
        if not prior_pmids:
            return None
        cands = self._new_by_topic.get(row.get("topic_id"), {})
        best_slug, best_frac = None, 0.0
        for slug in sorted(cands):
            frac = len(prior_pmids & cands[slug]) / len(prior_pmids)
            if frac > best_frac:
                best_slug, best_frac = slug, frac
        if best_slug is not None and best_frac >= self._t.effective_merge_overlap_min:
            return best_slug, best_frac
        return None

    # ---- stages ----

    def _resolve_one(self, slug: str, nc: dict, claimed: set):
        priors = self._prior_by_topic.get(nc["topic_id"], {})
        did, ov = best_overlap(nc["pmids"], priors, exclude=claimed)
        t = self._t
        # Stage 1 — auto-match.
        if did is not None and ov >= t.auto_match_min:
            claimed.add(did)
            self.tally["attached_overlap"] += 1
            return SubtopicMatch(durable_id=did, score=round(ov, 4), reason="overlap")
        # Below the ambiguous floor (or no UNCLAIMED candidate) -> genuinely new.
        if did is None or ov < t.ambiguous_min:
            self._note_split_loser(slug, nc["pmids"], priors, claimed)
            self.tally["minted"] += 1
            return None
        # Ambiguous band -> Stage 2/3 over the unclaimed candidates in this topic.
        cands = self._ambiguous_candidates(nc["pmids"], priors, claimed)
        # Stage 2 — label-embedding cosine, against ALL unclaimed in-band priors
        # (embedding is cheap + cached; capping by overlap rank here could drop the
        # label-true match — the cap belongs to the LLM stage only).
        centroid = self._centroid_match(nc["label"], cands)
        if centroid is not None:
            cid, cscore = centroid
            claimed.add(cid)
            self.tally["attached_centroid"] += 1
            return SubtopicMatch(durable_id=cid, score=round(float(cscore), 4), reason="centroid")
        # Stage 3 — LLM arbiter (only if enabled; candidate list capped inside).
        if t.llm_arbiter_enabled:
            chosen = self._llm_match(nc, cands)
            if chosen is not None:
                claimed.add(chosen)
                self.tally["attached_llm"] += 1
                # The arbiter returns a categorical verdict, not a numeric confidence;
                # 1.0 marks "arbiter-affirmed" (the reason field carries the "how").
                return SubtopicMatch(durable_id=chosen, score=1.0, reason="llm")
        # Unresolved -> defer -> mint (conservative: over-mint is safe, false-match is not).
        self._note_split_loser(slug, nc["pmids"], priors, claimed)
        self.tally["deferred_minted"] += 1
        return SubtopicDefer(
            reason="ambiguous",
            candidates=tuple((d, round(min_card_overlap(nc["pmids"], priors[d]), 4)) for d in cands),
        )

    def _note_split_loser(self, slug: str, pmids: set, priors: dict, claimed: set) -> None:
        """If a MINTING cluster's strongest in-band prior was already claimed by a
        stronger sibling, record that prior as its primitive-split parent (the seam
        brick C reads to write split_from, instead of replaying the claim order)."""
        did_all, ov_all = best_overlap(pmids, priors)  # claim-AGNOSTIC best
        if did_all is not None and did_all in claimed and ov_all >= self._t.ambiguous_min:
            self.split_parents[slug] = did_all

    def _ambiguous_candidates(self, pmids: set, priors: dict, claimed: set) -> list:
        """ALL unclaimed in-band priors (overlap >= ambiguous_min), overlap-ranked.
        No cap — Stage 2 sees every candidate; Stage 3 caps its own prompt list."""
        scored = [
            (did, min_card_overlap(pmids, pset))
            for did, pset in priors.items()
            if did not in claimed
        ]
        scored = [(d, o) for d, o in scored if o >= self._t.ambiguous_min]
        scored.sort(key=lambda x: (-x[1], x[0]))  # deterministic
        return [d for d, _ in scored]

    def _centroid_match(self, label: str, cand_ids: list) -> Optional[tuple[str, float]]:
        if not label or not cand_ids:
            return None
        candidates = {did: [self._snapshot[did].get("label_at_mint", "")] for did in cand_ids}
        match = nearest_match(
            label, candidates, threshold=self._t.centroid_cosine_min, cache=self._embed_cache
        )
        return (match.key, match.score) if match is not None else None

    def _llm_match(self, nc: dict, cand_ids: list) -> Optional[str]:
        # Cap the ARBITER prompt (token budget) to the top in-band candidates by
        # overlap; Stage 2 already saw the full set.
        cand_ids = cand_ids[:_MAX_LLM_CANDIDATES]
        if not cand_ids:
            return None
        # Cache key: STABLE CONTENT only (full memberships + normalized labels +
        # topic + prompt/model version). NEVER an id, slug, run_id, mint order, or
        # timestamp — the cache-by-stable-content rule. The verdict's chosen id is
        # re-validated against the live candidate set below.
        cache_key = compute_input_hash(
            ARBITER_STAGE,
            {
                "topic_id": nc["topic_id"],
                "new_label": (nc["label"] or "").strip().lower(),
                "new_seed_pmids": sorted(nc["pmids"]),
                "candidates": sorted(
                    [
                        {
                            "label": (self._snapshot[d].get("label_at_mint", "") or "").strip().lower(),
                            "seed_pmids": sorted(self._snapshot[d]["seed_pmids"]),
                        }
                        for d in cand_ids
                    ],
                    key=lambda c: (c["seed_pmids"], c["label"]),
                ),
                "prompt_version": ARBITER_PROMPT_VERSION,
                "model_ids": [MODEL_IDS_BY_STAGE[ARBITER_STAGE]],
            },
        )
        if cache_key in self._verdict_cache:
            verdict = self._verdict_cache[cache_key]
        else:
            new_sample = sorted(nc["pmids"])[: self._sample_cap]
            candidates = [
                {
                    "durable_id": d,
                    "label": self._snapshot[d].get("label_at_mint", ""),
                    "seed_pmids": sorted(self._snapshot[d]["seed_pmids"])[: self._sample_cap],
                }
                for d in cand_ids
            ]
            try:
                verdict = self._arbiter(
                    new_label=nc["label"],
                    new_seed_pmids=new_sample,
                    candidates=candidates,
                    topic_id=nc["topic_id"],
                )
            except Exception as exc:  # noqa: BLE001 — arbiter blip => keep separate (safe)
                _log.warning("reconcile arbiter failed (treating as distinct -> mint): %s", exc)
                verdict = {"verdict": "distinct", "durable_id": None}
            self._verdict_cache[cache_key] = verdict
        if (verdict or {}).get("verdict") == "same":
            chosen = (verdict or {}).get("durable_id")
            if chosen in cand_ids:  # only honor an id it was actually offered
                return chosen
        return None
