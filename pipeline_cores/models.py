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
    # Curated "known clients": CWIDs of people this core asserts are its users.
    # CWIDs ONLY — never names, and never surname-matched (511 resolved "Voss" rows
    # are the oncologist mhv9001, not CBIC's hev2006). This is the only evidence in
    # the model that is ASSERTED rather than inferred, which is the point: the
    # affinity prior derives its users from prior confirmations, those need a
    # reviewer, and 9 of the 10 cores with a claim queue in SPS have no owner to be
    # that reviewer — so a curated list is the one signal that works on day one for a
    # core with no confirmations at all. The signal it is there to relieve is thin:
    # a 2026-09-04 scan of all 21,202 CORE# rows found the acknowledgement match —
    # the heaviest weight in the model at +6.37 nats — firing exactly 11 times.
    # No projection property (cf. staff_cwids): these are already bare CWIDs.
    clients: list = field(default_factory=list)
    # Curated A2 method families this core's work is characterised by, as
    # {tier: [family_label]} over METHOD_FAMILY_TIERS. Absent = {} and the core simply
    # never fires the signal. ASSERTED curation like `clients` above — a person decided
    # that "used a clinical data warehouse" means Research Informatics — and the
    # families come from pipeline_tools' published taxonomy, so the labels are the
    # artifact's, not free text.
    #
    # PER FAMILY, on purpose. A flat "some curated tool was mentioned" boolean would
    # repeat the `client`-weight mistake in a new costume: measured on core 14
    # (2026-09-05), "used a clinical data warehouse" runs at 399x the background rate
    # and "does regression" at 1.7x — one is nearly proof and the other is barely
    # distinguishable from any quantitative paper at WCM. Averaged into one feature
    # they price each other wrong in both directions.
    method_families: dict = field(default_factory=dict)
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
    client_cwids: list = field(default_factory=list)     # matched curated-client CWIDs on the byline
    ack_matched: bool = False
    ack_alias: str = ""
    ack_snippet: str = ""
    llm_score: Optional[int] = None                       # 1-10 dense triage (None = not scored)
    llm_rationale: str = ""                               # short Sonnet rationale (<=80 chars)
    author_affinity: float = 0.0                          # 0-1 repeat-user RATE: the largest share
                                                          # of their own corpus output any author on
                                                          # this byline has already given to this core
    # --- ack evidence, EXTRACTED but not yet priced (evidence-scoring SPEC phase 1) ---
    ack_alias_hits: Optional[int] = None   # matched alias's global PMC hits (None = uncached)
    ack_institution: str = ""              # "home" | "other" | "none"  ("" = no ack match)
    ack_section: str = ""                  # "ack" | "methods" | "body" ("" = no XML/no match)
    # --- A2 method families, EXTRACTED but not yet priced (ReciterAI #394) ---
    # [(family_label, tool_display_name, sentence), ...] — one per curated family that
    # fired, STRONGEST tier first then measured-lift order inside the tier. Every family
    # carries its OWN tool and quote: a label whose sentence belongs to a different
    # family is one the reviewer cannot check, and the join already holds all three.
    # `method_families` (the labels alone) and the top family's tool/snippet are both
    # derivable from this, so neither is stored a second time.
    method_evidence: list = field(default_factory=list)
    method_tier: str = ""                  # "strong" | "moderate" | "weak" ("" = nothing fired)
    # --- MeSH E-tree descriptors, EXTRACTED but not yet priced (HANDOFF-7 section 3) ---
    # [(descriptor_ui, descriptor_label, tree_prefix), ...] under this core's
    # `prefilter.CORE_MESH_TREE_PREFIXES`. Self-contained like `method_evidence`: the UI is
    # what a per-descriptor lift is computed over, the label makes the chip checkable, the
    # prefix says which branch it hit. The ONLY place a MeSH descriptor is recorded anywhere
    # in ReciterAI — the prefilter has only ever kept a boolean of the same join, blended
    # into one float. No tier field on purpose: tiering is a specificity scheme, and none is
    # justified before the lift table exists.
    mesh_evidence: list = field(default_factory=list)


# Method-family tiers, STRONGEST FIRST — the order is load-bearing (signals picks the
# first tier that fired) and it is the vocabulary dictionary._validate checks a core's
# `method_families:` keys against, so a typo'd tier raises instead of silently matching
# nothing. Three bands rather than a per-family weight because 816 families cannot be
# fitted; see combine.WEIGHTS.
METHOD_FAMILY_TIERS = ("strong", "moderate", "weak")


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
