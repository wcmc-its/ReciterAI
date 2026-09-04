"""Load and validate the core dictionary (config/core_dictionary.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline_cores.models import DEFAULT_PARTNER_INSTITUTIONS, CoreDefinition, CoreStaff

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
                # Both optional: an un-updated dictionary still runs. An ABSENT
                # partner list means the shared Tri-I default; an explicitly EMPTY
                # one is a core opting out of shared-institution credit.
                partner_institutions=list(
                    c.get("partner_institutions", DEFAULT_PARTNER_INSTITUTIONS) or []),
                alias_hits={str(k): int(v) for k, v in (c.get("alias_hits") or {}).items()},
                # Optional like the two above, same reason: an un-updated dictionary
                # still runs. Coerced to str so a cwid that YAML happens to read as a
                # number still compares against a byline, which is a list of strings.
                clients=[str(x) for x in (c.get("clients") or [])],
                # Absent = combine()'s module default; a core only says so when it
                # needs a different bar from everyone else.
                confirm_threshold=_opt_float(c.get("confirm_threshold")),
                triage_threshold=_opt_float(c.get("triage_threshold")),
            )
        )
    _validate(cores)
    return cores


def load_core(core_id: str, path: Path = None) -> CoreDefinition:
    for c in load_cores(path):
        if c.core_id == str(core_id):
            return c
    raise KeyError(f"core_id {core_id!r} not in dictionary")


def _opt_float(value):
    return None if value is None else float(value)


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
