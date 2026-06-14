"""#224: detect an unexpectedly-empty / degraded ReCiterDB read.

A degraded read (a DB blip, a half-open connection, a partial result) and a
genuinely-empty result are indistinguishable at the call site -- both return
zero rows. This helper lets a call site declare which it expects: a read that
should always return a corpus-sized result (the full-corpus extracts, the
global faculty gap scan) ABORTS on an unexpected empty / under-floor result
rather than silently propagating a thinned set into the put-only DynamoDB sinks.

Per-CWID / per-PMID-set reads are deliberately NOT guarded here: an empty result
is a normal, frequent outcome there (a zero-publication CWID, a small delta with
no faculty authors), so a floor would only spam warnings.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

_log = logging.getLogger(__name__)


class DegradedReadError(RuntimeError):
    """A read that should never be (near-)empty returned under its floor.

    Carries the context to distinguish a degraded read from an arbitrary
    RuntimeError so an orchestrator/Lambda can catch it specifically, exit
    non-zero cleanly, and (later) emit a STAGE# failed row + alert.
    """

    def __init__(self, source: str, got: int, floor: int):
        self.source = source
        self.got = got
        self.floor = floor
        super().__init__(
            f"degraded read from {source!r}: got {got} rows, floor is {floor} "
            f"(suspected degraded/partial ReCiterDB read, not a genuine empty)"
        )


def guard_unexpected_empty(
    rows: Sequence,
    *,
    source: str,
    mode: str = "abort",
    floor: int = 1,
    logger: Optional[logging.Logger] = None,
) -> Sequence:
    """Guard a read whose result should meet `floor` rows.

    Pure: does no I/O and holds no policy -- the call site owns `mode` and
    `floor`. Returns `rows` unchanged when ``len(rows) >= floor``.

    - ``mode='abort'`` -- raise :class:`DegradedReadError` when under floor.
    - ``mode='warn'``  -- log a WARNING and return ``rows`` unchanged.
    """
    n = len(rows)
    if n >= floor:
        return rows
    log = logger or _log
    if mode == "abort":
        raise DegradedReadError(source, got=n, floor=floor)
    log.warning(
        "unexpected near-empty read from %s: got %d rows, floor is %d "
        "(possible degraded/partial ReCiterDB read)",
        source, n, floor,
    )
    return rows
