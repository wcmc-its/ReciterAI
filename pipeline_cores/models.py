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


# Tri-Institutional partners (+ NYP). An alias found next to one of these is HOME
# evidence, not a miss: these cores are genuinely shared, and requiring a Weill
# Cornell affiliation in the window would discard ~47% of core 4's legitimate hits
# (2026-09-03 institution audit). A core that is NOT shared opts out with an
# explicit `partner_institutions: []` in the dictionary. Weill Cornell itself is
# always home and is not listed here.
DEFAULT_PARTNER_INSTITUTIONS = [
    "NewYork-Presbyterian", "New York-Presbyterian", "New York Presbyterian", "NYP",
    "Rockefeller", "Memorial Sloan Kettering", "Memorial Sloan-Kettering", "MSKCC", "MSK",
    "Hospital for Special Surgery", "HSS",
]


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
    # Institutions that count as HOME for this core's alias matches (above).
    partner_institutions: list = field(
        default_factory=lambda: list(DEFAULT_PARTNER_INSTITUTIONS))
    # alias -> global PMC hit count, cached by `python3 -m pipeline_cores.refresh_alias_hits`.
    # Alias SPECIFICITY predicts precision: over 10 aliases spanning 1 -> 23,544 global
    # hits, Pearson r = -0.852 between log10(hits) and the share of matches that mean OUR
    # core (100% home at 1 hit, 24% at 23,544). Empty until the refresh runs.
    alias_hits: dict = field(default_factory=dict)
    # Per-core overrides of combine()'s status bands. None = the module default.
    # Status is a THRESHOLD on the score now, so this is where a core that needs a
    # different bar says so — e.g. one whose aliases are all generic, or whose
    # affinity index has saturated to the point of carrying no information.
    confirm_threshold: Optional[float] = None
    triage_threshold: Optional[float] = None

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
    # --- ack evidence, EXTRACTED but not yet priced (evidence-scoring SPEC phase 1) ---
    ack_alias_hits: Optional[int] = None   # matched alias's global PMC hits (None = uncached)
    ack_institution: str = ""              # "home" | "other" | "none"  ("" = no ack match)
    ack_section: str = ""                  # "ack" | "methods" | "body" ("" = no XML/no match)


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
