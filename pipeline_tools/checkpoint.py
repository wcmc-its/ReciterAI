"""Resumable checkpoint for A2 corpus extraction (docs/tool-classifier-spec.md).

The extraction fan-out runs one Bedrock Haiku call per faculty paper over
≈8,146 papers — minutes-to-hours of wall-clock and real spend. A crash partway
through (rate limit, network blip, the cost ceiling firing) must NOT re-extract
the papers already done. This is the same idempotency the enrichment delta loop
gets from its watermark, expressed for a one-shot corpus sweep as an append-only
on-disk log of completed PMIDs.

Format: JSON Lines, one object per completed PMID:

    {"pmid": "39123456",
     "mentions": [{"raw_name": "...", "tool_category_hint": "...", "context": "...",
                   "pmid": "39123456", "cwid": "abc2001", "author_role": "lead"}],
     "usage": {"model": "us.anthropic.claude-haiku-4-5-...", "input_tokens": 1290,
               "output_tokens": 310}}

Append-only + flushed per write so a hard kill loses at most the in-flight PMID.
On resume, ``load`` rebuilds the done-set and the prior measured cost, and the
harness skips every ``is_done`` PMID. Only PMIDs that fully succeeded are
recorded — a failed extraction is never written, so a resume retries it.

Disk-backed today; the writer/reader are isolated behind this class so an S3
object-per-run or a single multipart log can drop in later without touching the
harness (the handoff's "checkpoint … to disk/S3").
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CheckpointEntry:
    """One completed PMID: its extracted mentions + the usage of the call that produced them."""

    pmid: str
    mentions: list[dict]
    model: str
    input_tokens: int
    output_tokens: int

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "pmid": self.pmid,
                "mentions": self.mentions,
                "usage": {
                    "model": self.model,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                },
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_obj(cls, obj: dict) -> "CheckpointEntry":
        usage = obj.get("usage") or {}
        return cls(
            pmid=str(obj["pmid"]),
            mentions=list(obj.get("mentions") or []),
            model=usage.get("model") or "",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )


class ExtractionCheckpoint:
    """Append-only, resumable log of completed-PMID extraction results.

    ``load`` once at run start; ``is_done`` to skip; ``record`` after each
    successful PMID (appends + flushes). ``all_mentions`` flattens the corpus
    of raw mentions for the downstream classify → registry stages; ``prior_cost``
    re-derives measured spend so a resumed run's cost ceiling bounds the whole
    run, not just the resumed segment.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._done: dict[str, CheckpointEntry] = {}  # pmid -> entry (last-wins)

    # --- load / resume -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "ExtractionCheckpoint":
        """Open a checkpoint, replaying any existing log into the done-set.

        Tolerant of a partial final line (a hard kill mid-write): a trailing
        unparseable line is logged and skipped, never fatal — the worst case is
        that one already-done PMID is re-extracted on resume.
        """
        cp = cls(path)
        if not cp.path.exists():
            return cp
        n_lines = n_bad = 0
        with cp.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n_lines += 1
                try:
                    entry = CheckpointEntry.from_obj(json.loads(line))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    n_bad += 1
                    logger.warning("checkpoint %s: skipping unparseable line (%s)", cp.path, exc)
                    continue
                cp._done[entry.pmid] = entry
        logger.info(
            "checkpoint %s: resumed %d done PMID(s) from %d line(s)%s",
            cp.path, len(cp._done), n_lines,
            f" ({n_bad} skipped)" if n_bad else "",
        )
        return cp

    # --- read --------------------------------------------------------------

    def is_done(self, pmid: str) -> bool:
        return str(pmid) in self._done

    def done_pmids(self) -> set[str]:
        return set(self._done)

    def __len__(self) -> int:
        return len(self._done)

    def all_mentions(self) -> list[dict]:
        """Every recorded raw mention, flattened across PMIDs (stable PMID order)."""
        out: list[dict] = []
        for pmid in sorted(self._done):
            out.extend(self._done[pmid].mentions)
        return out

    def prior_cost(self) -> tuple[Decimal, int]:
        """Reconstruct (observed_usd, n_calls) from recorded usage, to seed the ceiling.

        One successful call per done PMID (``call_with_fallback`` encapsulates
        its own retry/fallback and returns a single usage block), so ``n_calls``
        is the done-PMID count and ``observed_usd`` sums ``cost_for`` over each
        entry's model + tokens. A model missing from the price table (a removed
        entry) contributes 0 with a warning rather than failing the resume.
        """
        from utils.llm_cost import UnknownModelError, cost_for

        total = Decimal("0")
        calls = 0
        for entry in self._done.values():
            if not entry.model:
                continue
            calls += 1
            try:
                total += cost_for(entry.model, entry.input_tokens, entry.output_tokens)
            except UnknownModelError:
                logger.warning(
                    "checkpoint %s: model %r not in price table; counting $0 for PMID %s",
                    self.path, entry.model, entry.pmid,
                )
        return total, calls

    # --- write -------------------------------------------------------------

    def record(
        self,
        pmid: str,
        mentions: list[dict],
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Append one completed PMID's result and flush. Idempotent in-memory (last-wins)."""
        entry = CheckpointEntry(
            pmid=str(pmid),
            mentions=list(mentions),
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json_line() + "\n")
            fh.flush()
        self._done[entry.pmid] = entry
