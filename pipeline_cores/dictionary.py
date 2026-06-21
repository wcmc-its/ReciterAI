"""Load and validate the core dictionary (config/core_dictionary.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline_cores.models import CoreDefinition, CoreStaff

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "core_dictionary.yaml"


def load_cores(path: Path = None) -> list:
    """Return list[CoreDefinition] from the YAML dictionary."""
    path = Path(path) if path else _DEFAULT_PATH
    raw = yaml.safe_load(path.read_text())
    cores = []
    for c in raw.get("cores", []):
        staff = [
            CoreStaff(
                cwid=s["cwid"],
                name=s.get("name", ""),
                dept=s.get("dept", ""),
                tracked=bool(s.get("tracked", True)),
            )
            for s in c.get("staff", [])
        ]
        cores.append(
            CoreDefinition(
                core_id=str(c["core_id"]),
                name=c["name"],
                facility=c.get("facility", ""),
                owner_cwid=c.get("owner_cwid"),
                aliases=list(c.get("aliases", [])),
                staff=staff,
                grant_ids=list(c.get("grant_ids", [])),
                llm_description=(c.get("llm_description", "") or "").strip(),
            )
        )
    _validate(cores)
    return cores


def load_core(core_id: str, path: Path = None) -> CoreDefinition:
    for c in load_cores(path):
        if c.core_id == str(core_id):
            return c
    raise KeyError(f"core_id {core_id!r} not in dictionary")


def _validate(cores: list) -> None:
    seen = set()
    for c in cores:
        if not c.core_id or not c.name:
            raise ValueError(f"core missing id/name: {c}")
        if c.core_id in seen:
            raise ValueError(f"duplicate core_id {c.core_id}")
        seen.add(c.core_id)
        if not c.aliases:
            raise ValueError(f"core {c.core_id} has no aliases (signal 3 disabled)")
