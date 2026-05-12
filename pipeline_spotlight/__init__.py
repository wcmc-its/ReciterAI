"""Phase 10 spotlight path: monthly cron + dirty-check gate (D-03).

Spotlight is editorial/narrative, not real-time. Decoupling its cadence
from hot-path PMID-delta arrival bounds Opus cost by the spotlight's
own monthly cadence. Failure isolation: spotlight breakage cannot take
down weekly scoring.

Components:
- `dirty_gate.py`: pure function that decides whether enough new
  publications have landed in top-50 subtopics to justify a regen.
- `orchestrator.py`: monthly Lambda handler that reads the substrate,
  applies the gate, and either short-circuits with a `STAGE#spotlight_refresh#GLOBAL`
  skipped row or shells out to `backfill_spotlight.py --publish`.
"""

__version__ = "0.1.0"
