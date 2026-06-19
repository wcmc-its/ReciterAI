"""Normalized opportunity record shared across the grants pipeline."""
from dataclasses import dataclass, field
from typing import Optional


def make_opportunity_id(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


@dataclass
class Opportunity:
    opportunity_id: str
    source: str
    source_id: str
    source_url: str
    sponsor: str
    title: str
    synopsis: str
    program_type: str = ""
    mechanism: str = ""
    award_ceiling: Optional[int] = None
    award_floor: Optional[int] = None
    estimated_funding: Optional[int] = None
    number_of_awards: Optional[int] = None
    open_date: str = ""
    due_date: str = ""
    status: str = ""
    eligibility_raw: str = ""
    cfda_list: list = field(default_factory=list)
    ingested_at: str = ""
