"""Load and validate the core dictionary (config/core_dictionary.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

from pipeline_cores.models import (
    DEFAULT_PARTNER_INSTITUTIONS,
    METHOD_FAMILY_TIERS,
    CoreDefinition,
    CoreStaff,
)

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
                # number still compares against a byline, which is a list of strings —
                # and CASE-FOLDED + stripped, because unlike `staff` (resolved by a DB
                # join) this list is hand-typed into YAML. A stray "ABC1234 " would
                # otherwise match no byline, fire no signal, and raise no error
                # anywhere: an undiagnosable silent no-op. Bylines are lowercased at
                # the comparison in run.py to match.
                clients=sorted({str(x).strip().lower() for x in (c.get("clients") or []) if str(x).strip()}),
                # Optional on the same contract as `clients` above — absent = {}, and a
                # dictionary written before this key existed still loads. Stripped and
                # CASEFOLDED for the same reason too: these labels are hand-typed into
                # YAML against a 816-entry published taxonomy, and "Regression Modeling "
                # would match no family, fire no signal and raise nothing anywhere. The
                # artifact side is casefolded at the comparison in signals to match, and
                # the label that reaches DynamoDB is the ARTIFACT's, so casefolding here
                # costs no display fidelity. A typo'd TIER raises in _validate.
                method_families={str(tier).strip().lower(): _labels(labels)
                                 for tier, labels in (c.get("method_families") or {}).items()},
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


def _labels(seq) -> list:
    """Hand-typed family labels: stripped, casefolded, de-duplicated, ORDER KEPT.

    Order is kept (unlike `clients`, which sorts) because the tier lists are written
    strongest-lift-first, and `signals.method_family_signal` reports the tool and
    evidence sentence of the FIRST family that fires inside the winning tier. Sorting
    would hand that slot to whichever label happens to come first alphabetically.
    """
    if isinstance(seq, str):
        # `strong: "Regression modeling"` instead of a one-item LIST. Without this a
        # string iterates CHARACTER by character and loads 10 one-letter labels that
        # match nothing, fire nothing and raise nothing — a green load carrying curation
        # that can never work. The same silent-no-op this function's stripping and
        # casefolding exist to prevent, one level up in the YAML shape.
        raise ValueError(f"method_families tier must be a list, got a string: {seq!r}")
    out = []
    for raw in seq or []:
        label = str(raw).strip().casefold()
        if label and label not in out:
            out.append(label)
    return out


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
        # A tier nobody scores is another silent no-op: combine() would look up
        # "method:medium", find nothing, and the core would carry curation that can
        # never fire. Raise here so a typo costs a failed load, not a dark signal.
        for tier in c.method_families:
            if tier not in METHOD_FAMILY_TIERS:
                raise ValueError(
                    f"core {c.core_id} method_families tier {tier!r} is not one of "
                    f"{METHOD_FAMILY_TIERS}")
