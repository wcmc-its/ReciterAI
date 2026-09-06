"""Resumable checkpoint for A2 corpus extraction (docs/tool-classifier-spec.md).

The extraction fan-out runs one Bedrock Haiku call per faculty paper over
≈8,146 papers — minutes-to-hours of wall-clock and real spend. A crash partway
through (rate limit, network blip, the cost ceiling firing) must NOT re-extract
the papers already done. This is the same idempotency the enrichment delta loop
gets from its watermark, expressed for a one-shot corpus sweep as an append-only
log of completed PMIDs.

Format: JSON Lines, one object per completed PMID:

    {"pmid": "39123456",
     "mentions": [{"raw_name": "...", "tool_category_hint": "...", "context": "...",
                   "pmid": "39123456", "cwid": "abc2001", "author_role": "lead"}],
     "usage": {"model": "us.anthropic.claude-haiku-4-5-...", "input_tokens": 1290,
               "output_tokens": 310}}

On resume, ``load`` rebuilds the done-set and the prior measured cost, and the
harness skips every ``is_done`` PMID. Only PMIDs that fully succeeded are
recorded — a failed extraction is never written, so a resume retries it.

Two storage backends, one interface
-----------------------------------
The reader/writer sits behind a small store object (``read_lines`` / ``append`` /
``flush``), so the harness never learns where the log lives:

``LocalJsonlStore`` (the DEFAULT, and unchanged from the on-disk original):
    appends one line per ``record`` and flushes it, so a hard kill loses at most
    the in-flight PMID. A torn final line is tolerated on replay.

``S3JsonlStore`` (opt-in, for a SCHEDULED run):
    a Fargate task gets a fresh container every run, so a disk-backed checkpoint
    is empty on every tick and the whole corpus is re-extracted — the incremental
    property, which is the entire cost argument for scheduling this, is lost. The
    S3 store keeps the same log at one key under the artifacts bucket, so run N+1
    resumes from run N.

Durability trade-off (S3 store)
-------------------------------
S3 has no append, so the log is buffered in memory and the WHOLE object is
re-PUT every ``flush_every`` records (default 50) or ``flush_seconds`` (default
120), whichever comes first — both constructor arguments, and both surfaced as
CLI flags, rather than magic constants. A PUT is atomic: it either replaces the
object or leaves the previous one intact, so a failed flush can never truncate
or corrupt the log, and every flush writes a strict SUPERSET of the last one
(entries are only ever added; a re-recorded PMID appends a line and wins on
replay, exactly as the local file behaves).

WHAT A CRASH COSTS: the un-flushed buffer — at most ``flush_every`` PMIDs, or
``flush_seconds`` of sweeping. Those PMIDs are simply re-extracted on the next
run at the Haiku price of one call each (~$0.0005/paper, so ~$0.03 at the
default 50). The log is never left inconsistent: the loss is repeated work, not
damage. One writer per run — there is deliberately no lock and no multipart
scheme; two concurrent sweeps against the same key would clobber each other's
tail, so don't run two.

The artifacts bucket is VERSIONED (noncurrent versions expire at 90 days,
infra/s3_lifecycle_noncurrent.json), so every flush leaves one noncurrent copy of
the whole log behind — ~160 per full sweep at the default cadence. That is the
real cost of a lower ``flush_every``, not the PUT requests: raise it to trade
noncurrent-version storage for a wider crash window, or scope a shorter
expiration at this prefix.

Reading is STRICT on the S3 store
---------------------------------
This log decides what NOT to re-extract. A failed or truncated read that
silently returned an empty checkpoint would re-sweep all ≈8,146 papers and spend
real money on a green run. So on the S3 store any read/parse failure RAISES, and
so does a well-formed object that yields zero entries — same rule and the same
reason as ``pipeline_cores.method_families.load_family_index``. An ABSENT object
is a different thing entirely: that is the first-ever run, and it starts an empty
log without complaint. The local store keeps its original tolerant replay (a
torn final line is a real outcome of a hard kill mid-append; an S3 PUT cannot
produce one).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

# Where an externalised checkpoint lives by default: beside the tools artifacts
# (`tools/latest/…`, see pipeline_tools.publish) but under a private prefix, so
# nothing SPS consumes is ever a sibling of a half-written log.
DEFAULT_S3_KEY = "tools/_checkpoint/a2_extraction_checkpoint.jsonl"

# Flush cadence for the S3 store. See "Durability trade-off" above: these bound
# what a hard kill costs in repeated work, and are overridable per run.
DEFAULT_FLUSH_EVERY = 50
DEFAULT_FLUSH_SECONDS = 120


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


# ---------------------------------------------------------------------------
# Stores. Three methods — read_lines / append / flush — plus a ``strict`` flag
# and a ``location`` for log lines. Anything duck-typed to that shape works.
# ---------------------------------------------------------------------------


class LocalJsonlStore:
    """The original on-disk log: append + flush per record, tolerant replay.

    Byte-identical to the pre-S3 behaviour — an operator run that does not ask
    for S3 writes the same file, one line at a time, with the same fsync-free
    per-write flush and the same tolerance for a torn final line.
    """

    strict = False

    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def location(self) -> str:
        return str(self.path)

    def read_lines(self) -> list[str] | None:
        """The log's lines, or None when the file does not exist yet."""
        if not self.path.exists():
            return None
        return self.path.read_text(encoding="utf-8").splitlines()

    def append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def flush(self) -> None:
        """No-op: every append already flushed."""


class S3JsonlStore:
    """The same log at one S3 key, buffered and re-PUT whole.

    ``s3`` is any object exposing ``key_exists`` / ``get_object_bytes`` /
    ``put_object`` — the three methods ``utils.s3_client.S3HierarchyClient``
    provides and the only ones used here, so a stand-in works in tests (same
    posture as ``pipeline_cores.fulltext``).

    Reads are strict; see the module docstring. Writes buffer and flush on
    ``flush_every`` records or ``flush_seconds`` elapsed, whichever comes first.
    """

    strict = True

    def __init__(
        self,
        key: str,
        *,
        s3,
        flush_every: int = DEFAULT_FLUSH_EVERY,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
    ):
        self.key = key
        self.s3 = s3
        self.flush_every = max(1, int(flush_every))
        self.flush_seconds = float(flush_seconds)
        self._lines: list[str] = []      # what is already durable at self.key
        self._buffer: list[str] = []     # written but not yet PUT
        self._loaded = False
        self._last_flush = time.monotonic()

    @property
    def location(self) -> str:
        return f"s3://{getattr(self.s3, 'bucket', '?')}/{self.key}"

    # --- read ---------------------------------------------------------------

    def read_lines(self) -> list[str] | None:
        """The log's lines, or None when the object does not exist yet.

        No try/except: an AccessDenied, a network failure or a decode error
        propagates, because the alternative — an empty done-set — silently buys
        a full re-extraction of the corpus. Only a genuine 404 (``key_exists``
        False; that method re-raises everything that is not a 404) means
        "first run", and only that returns None.
        """
        if not self.s3.key_exists(self.key):
            self._lines = []
            self._loaded = True
            return None
        text = self.s3.get_object_bytes(self.key).decode("utf-8")
        self._lines = text.splitlines()
        self._loaded = True
        return list(self._lines)

    # --- write --------------------------------------------------------------

    def append(self, line: str) -> None:
        # A flush re-PUTs the whole object, so it must never run before the
        # existing log has been read — that would replace run N's log with run
        # N+1's tail. Reading here as well as in load() makes the store safe
        # whatever order the caller uses it in.
        if not self._loaded:
            self.read_lines()
        self._buffer.append(line)
        if len(self._buffer) >= self.flush_every or (
            time.monotonic() - self._last_flush >= self.flush_seconds
        ):
            self.flush()

    def flush(self) -> None:
        """PUT the full log (durable lines + buffer). No-op on an empty buffer."""
        if not self._buffer:
            return
        if not self._loaded:
            self.read_lines()
        merged = self._lines + self._buffer
        body = ("\n".join(merged) + "\n").encode("utf-8")
        self.s3.put_object(self.key, body, content_type="application/x-ndjson")
        self._lines = merged
        self._buffer = []
        self._last_flush = time.monotonic()
        logger.info("checkpoint %s: flushed — %d line(s) durable", self.location, len(self._lines))


def make_s3_store(
    key: str = DEFAULT_S3_KEY,
    *,
    bucket: str = None,
    s3=None,
    flush_every: int = DEFAULT_FLUSH_EVERY,
    flush_seconds: float = DEFAULT_FLUSH_SECONDS,
) -> S3JsonlStore:
    """Build the artifacts-bucket S3 store, with boto3 imported LAZILY.

    Same shape and the same reason as ``pipeline_cores.fulltext.make_s3_backend``:
    ``import pipeline_tools.checkpoint`` must stay boto3-free so the local path
    and the unit tests carry no AWS dependency. Pass ``s3=`` to inject a
    duck-typed client and the import never happens.
    """
    if s3 is None:
        from utils.s3_client import ARTIFACTS_BUCKET, S3HierarchyClient  # lazy: keeps this module dep-free

        s3 = S3HierarchyClient(bucket=bucket or ARTIFACTS_BUCKET)
    return S3JsonlStore(key, s3=s3, flush_every=flush_every, flush_seconds=flush_seconds)


class ExtractionCheckpoint:
    """Append-only, resumable log of completed-PMID extraction results.

    ``load`` once at run start; ``is_done`` to skip; ``record`` after each
    successful PMID. ``all_mentions`` flattens the corpus of raw mentions for
    the downstream classify → registry stages; ``prior_cost`` re-derives measured
    spend so a resumed run's cost ceiling bounds the whole run, not just the
    resumed segment.

    Constructed with ``path=`` for the local log (the default everywhere) or
    ``store=`` for any other backend — ``make_s3_store()`` for the scheduled
    run. Callers that buffer (the S3 store) need ``flush()`` at the end of the
    sweep; ``with ExtractionCheckpoint.load(...) as cp:`` does it for you.
    """

    def __init__(self, path: Path = None, *, store=None):
        if (path is None) == (store is None):
            raise ValueError("ExtractionCheckpoint needs exactly one of path= or store=")
        self._store = store if store is not None else LocalJsonlStore(path)
        self._done: dict[str, CheckpointEntry] = {}  # pmid -> entry (last-wins)

    @property
    def path(self) -> Path | None:
        """The local log path, or None when the checkpoint is not disk-backed."""
        return getattr(self._store, "path", None)

    @property
    def location(self) -> str:
        return getattr(self._store, "location", repr(self._store))

    # --- load / resume -----------------------------------------------------

    @classmethod
    def load(cls, path: Path = None, *, store=None) -> "ExtractionCheckpoint":
        """Open a checkpoint, replaying any existing log into the done-set.

        An ABSENT log (no file, no object) is the first run: an empty checkpoint,
        no warning, no error.

        A log that EXISTS is read under the store's strictness. Local: a trailing
        unparseable line is logged and skipped, never fatal — the worst case is
        that one already-done PMID is re-extracted on resume. S3 (``strict``):
        an unparseable line, or an object that yields no entries at all, RAISES
        — a PUT is atomic so neither can happen legitimately, and swallowing
        either would buy a silent full re-sweep of the corpus.
        """
        cp = cls(path=path, store=store)
        lines = cp._store.read_lines()
        if lines is None:
            logger.info("checkpoint %s: no existing log — starting fresh", cp.location)
            return cp

        strict = getattr(cp._store, "strict", True)
        n_lines = n_bad = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            try:
                entry = CheckpointEntry.from_obj(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                if strict:
                    raise ValueError(
                        f"checkpoint {cp.location}: unparseable line {n_lines} ({exc}) — refusing "
                        "to resume from a partial done-set. Skipping it would silently re-extract "
                        "every PMID this log was protecting."
                    ) from exc
                n_bad += 1
                logger.warning("checkpoint %s: skipping unparseable line (%s)", cp.location, exc)
                continue
            cp._done[entry.pmid] = entry

        if strict and not cp._done:
            raise ValueError(
                f"checkpoint {cp.location}: the log EXISTS but yields 0 done PMID(s) "
                f"(from {n_lines} line(s)) — refusing to treat that as 'nothing done yet'. "
                "An empty done-set re-extracts the whole corpus and spends real money on a "
                "green run; a genuine first run has NO object at this key."
            )
        logger.info(
            "checkpoint %s: resumed %d done PMID(s) from %d line(s)%s",
            cp.location, len(cp._done), n_lines,
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
                    self.location, entry.model, entry.pmid,
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
        """Append one completed PMID's result. Idempotent in-memory (last-wins).

        Durable immediately on the local store; on the S3 store, durable at the
        next flush (at most ``flush_every`` records / ``flush_seconds`` later —
        see the module docstring for what a kill in that window costs).
        """
        entry = CheckpointEntry(
            pmid=str(pmid),
            mentions=list(mentions),
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
        )
        self._store.append(entry.to_json_line())
        self._done[entry.pmid] = entry

    def flush(self) -> None:
        """Make every recorded PMID durable. A no-op on the local store."""
        self._store.flush()

    # A sweep that ends — cleanly, on the cost ceiling, or on an exception —
    # must still persist what it bought.
    def __enter__(self) -> "ExtractionCheckpoint":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.flush()
        return False
