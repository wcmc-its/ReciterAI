"""Dataclasses shared across the cores pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CoreStaff:
    cwid: str
    name: str = ""
    dept: str = ""
    # False when the person is in `identity` but NOT a ReCiter target person, so
    # their author rows carry personIdentifier=NULL and the coauthorship signal
    # cannot see them. Surfacing them requires adding them to ReCiter's target
    # feed (an upstream fix) — never surname matching.
    tracked: bool = True


@dataclass
class CoreDefinition:
    core_id: str
    name: str
    facility: str = ""
    owner_cwid: Optional[str] = None
    aliases: list = field(default_factory=list)        # signal 3 (full-text match)
    staff: list = field(default_factory=list)          # list[CoreStaff], signal 2
    grant_ids: list = field(default_factory=list)      # usually empty; no shared core grant
    llm_description: str = ""                           # signal 4 prompt context

    @property
    def staff_cwids(self) -> list:
        return [s.cwid for s in self.staff]

    @property
    def tracked_staff_cwids(self) -> list:
        return [s.cwid for s in self.staff if s.tracked]


@dataclass
class SignalResult:
    """Per (publication, core) evidence from each signal layer."""
    coauthor_cwids: list = field(default_factory=list)   # matched core-staff CWIDs on the byline
    ack_matched: bool = False
    ack_alias: str = ""
    ack_snippet: str = ""
    llm_score: Optional[int] = None                       # 1-10 dense triage (None = not scored)
    llm_rationale: str = ""                               # short Sonnet rationale (<=80 chars)
    author_affinity: float = 0.0                          # 0-1 prior from claims/ack/coauthorship


# Status lifecycle for a (publication, core) pair.
STATUS_CONFIRMED = "confirmed"      # deterministic: core named OR core-staff co-author
STATUS_CANDIDATE = "candidate"      # probabilistic: routed to the claim queue for review
STATUS_BELOW = "below_threshold"    # scored but too low to surface
STATUS_CLAIMED = "claimed"          # human-claimed in SPS (read back; never set by engine)
STATUS_REJECTED = "rejected"        # human-rejected in SPS


@dataclass
class CoreUsageRecord:
    pmid: str
    core_id: str
    likelihood: float                 # 0-1
    status: str
    signals: SignalResult
    scored_at: str = ""
