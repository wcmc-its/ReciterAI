"""Suggest new/changed core staff from co-authorship — the inverse of signal 2.

Core-staff identities are volatile (turnover, ReCiter target-set churn), so rather
than hand-maintaining the roster in config/core_dictionary.yaml, this report lets
the *stable* signal bootstrap discovery of the *people*:

  known tracked staff  ->  papers they co-author (signal-2 confirmed, ~100% precision)
                       ->  the OTHER recurring WCM authors on those papers
                       ->  ranked candidates to review and (maybe) add as staff

The discriminator between a real core staffer and a heavy core *user* is BREADTH:
a core scientist/director co-authors across many unrelated labs; a user appears only
within their own group. So each candidate is scored by:
  * papers       — # of the core's signal-2-confirmed papers they're on
  * distinct_seed — # of DISTINCT known core staff they recur with (team signature)
  * partners     — # of distinct co-authors across those papers (breadth = lab diversity)
  * recency      — papers in the recent window (default: last 5 years)

This SUGGESTS, never auto-adds — output is a review list, consistent with the
claim-queue philosophy. It is read-only (no DB writes, no AWS).

Limitations:
  * Only cores that already have >=1 `tracked: true` seed can bootstrap this way.
    Aliases-only cores (no tracked seed) need the signal-3 / full-text alias path to
    seed first; they are reported as "no seed".
  * Only ReCiter-resolved (WCM identity) authors surface. A frequent co-author who is
    NOT in `identity` cannot appear here — but that absence is itself the prioritized,
    evidence-ranked version of the upstream ReCiter target-feed ask.

Usage:
  python3 -m pipeline_cores.suggest_staff                 # all seeded cores
  python3 -m pipeline_cores.suggest_staff --core 5 --top 15 --since 2021 --min-papers 3
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import bindparam, text

from pipeline_cores.dictionary import load_cores
from utils.db import get_engine

DEFAULT_SINCE = 2021          # recent window (current cycle is ~2026)
DEFAULT_MIN_PAPERS = 3
DEFAULT_TOP = 15


def _chunks(seq, n=900):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _pmids_for_staff(conn, cwids: list) -> set:
    """Signal-2 confirmed papers: any pmid with a tracked staff CWID on the byline."""
    if not cwids:
        return set()
    stmt = text(
        "SELECT DISTINCT pmid FROM analysis_summary_author WHERE personIdentifier IN :c"
    ).bindparams(bindparam("c", expanding=True))
    return {r[0] for r in conn.execute(stmt, {"c": list(cwids)})}


def _bylines(conn, pmids: set) -> dict:
    """pmid -> list of co-author personIdentifiers (WCM + external, as resolved)."""
    out: dict = defaultdict(list)
    stmt = text(
        "SELECT pmid, personIdentifier FROM analysis_summary_author WHERE pmid IN :p"
    ).bindparams(bindparam("p", expanding=True))
    for batch in _chunks(pmids):
        for pmid, pid in conn.execute(stmt, {"p": batch}):
            if pid:
                out[pmid].append(pid)
    return out


def _years(conn, pmids: set) -> dict:
    out: dict = {}
    stmt = text(
        "SELECT pmid, articleYear FROM analysis_summary_article WHERE pmid IN :p"
    ).bindparams(bindparam("p", expanding=True))
    for batch in _chunks(pmids):
        for pmid, yr in conn.execute(stmt, {"p": batch}):
            out[pmid] = yr
    return out


def _role(rank: str, title: str, postdoc, faculty) -> str:
    """Coarse class from the WCM title. Core STAFF carry service titles (Staff
    Associate, Research Associate, Instructor, Specialist, Scientist, and
    *research-track* Professors); the noise to push down is TRAINEES (grad
    students / postdocs = lab members) and tenure-track FACULTY PIs (heavy users)."""
    r = (rank or "").lower()
    t = (title or "").lower()
    if postdoc == 1 or "postdoc" in t or "graduate student" in t or "graduate staff" in t or "predoctoral" in t:
        return "trainee"
    if any(k in t for k in ("staff associate", "research associate", "instructor",
                            "specialist", "scientist", "bioinformatic", "computational biolog")):
        return "staff"
    if "research" in t and "professor" in t:   # research-track professor = embedded scientist
        return "staff"
    if "professor" in r or "professor" in t:
        return "PI"
    if faculty == 1:
        return "faculty"
    return "staff"


def _identity(conn, cwids: set) -> dict:
    out: dict = {}
    stmt = text(
        "SELECT cwid, givenName, surname, primaryAcademicDepartment, "
        "facultyRank, primaryTitle, postdoc, faculty FROM identity WHERE cwid IN :c"
    ).bindparams(bindparam("c", expanding=True))
    for batch in _chunks(cwids):
        for cwid, gn, sn, dept, rank, title, postdoc, faculty in conn.execute(stmt, {"c": batch}):
            out[cwid] = {
                "name": f"{gn or ''} {sn or ''}".strip(),
                "dept": dept or "",
                "title": title or "",
                "role": _role(rank, title, postdoc, faculty),
            }
    return out


def suggest_for_core(conn, core, in_dict_cwids: dict, since: int, min_papers: int, top: int, staff_only: bool = False):
    seed = set(core.staff_cwids)  # untracked ones simply match no rows
    if not seed:
        print(f"\n=== core {core.core_id}  {core.name} ===")
        print("  (no tracked-staff seed — needs signal-3/alias bootstrapping first)")
        return

    S = _pmids_for_staff(conn, seed)
    bylines = _bylines(conn, S)
    years = _years(conn, S)
    existing_here = {s.cwid for s in core.staff}

    # accumulate per-candidate stats
    papers = defaultdict(int)
    recent = defaultdict(int)
    seed_partners = defaultdict(set)
    partners = defaultdict(set)
    last_yr = defaultdict(int)
    for pmid, authors in bylines.items():
        yr = years.get(pmid) or 0
        aset = set(authors)
        seeds_on = aset & seed
        for a in aset:
            if a in seed:
                continue
            papers[a] += 1
            if yr >= since:
                recent[a] += 1
            if yr:
                last_yr[a] = max(last_yr[a], yr)
            seed_partners[a] |= seeds_on
            partners[a] |= (aset - {a})

    # candidates = resolved WCM people, not already this core's staff, >= min_papers
    cand = [a for a, n in papers.items()
            if n >= min_papers and a not in existing_here]
    ident = _identity(conn, set(cand))
    cand = [a for a in cand if a in ident]   # keep only ReCiter-resolved (WCM) people
    if staff_only:
        cand = [a for a in cand if ident[a]["role"] == "staff"]

    # Staff-first: research-track staff are the maintainable signal-2 adds; faculty
    # PIs surfacing here are usually heavy *users*, so they sink (but stay visible).
    def score(a):
        is_staff = ident[a]["role"] == "staff"
        return (is_staff, recent[a], len(seed_partners[a]), len(partners[a]), papers[a])

    cand.sort(key=score, reverse=True)

    print(f"\n=== core {core.core_id}  {core.name} ===")
    print(f"  seed={sorted(seed)}  confirmed_papers={len(S)}  candidates>= {min_papers}p: {len(cand)}")
    print(f"  {'cwid':10s} {'name':24s} {'role':8s} {'pap':>4} {'rec':>4} {'seed':>4} {'brd':>5} {'last':>5}  title / flag")
    for a in cand[:top]:
        info = ident[a]
        is_staff = info["role"] == "staff"
        if is_staff and len(seed_partners[a]) >= 2 and len(partners[a]) >= 10 and recent[a] >= 1:
            tag = "** likely STAFF"
        elif is_staff and recent[a] >= 1:
            tag = " * possible staff"
        elif info["role"] == "PI":
            tag = " [PI — likely a user]"
        else:
            tag = ""
        if a in in_dict_cwids:
            tag += f"  [in dict: {','.join(in_dict_cwids[a])}]"
        detail = info["title"][:30] or info["dept"][:30]
        print(f"  {a:10s} {info['name'][:24]:24s} {info['role']:8s} "
              f"{papers[a]:>4} {recent[a]:>4} {len(seed_partners[a]):>4} "
              f"{len(partners[a]):>5} {last_yr[a]:>5}  {detail}  {tag}")


def main():
    ap = argparse.ArgumentParser(description="Suggest core staff from co-authorship (read-only).")
    ap.add_argument("--core", help="core_id to analyze (default: all seeded cores)")
    ap.add_argument("--since", type=int, default=DEFAULT_SINCE, help="recent-window start year")
    ap.add_argument("--min-papers", type=int, default=DEFAULT_MIN_PAPERS)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--staff-only", action="store_true",
                    help="hide faculty PIs (show only research-track staff / postdocs)")
    args = ap.parse_args()

    cores = load_cores()
    in_dict = defaultdict(list)
    for c in cores:
        for s in c.staff:
            in_dict[s.cwid].append(c.core_id)

    eng = get_engine()
    with eng.connect() as conn:
        for c in sorted(cores, key=lambda x: int(x.core_id)):
            if args.core and c.core_id != str(args.core):
                continue
            suggest_for_core(conn, c, in_dict, args.since, args.min_papers, args.top, args.staff_only)


if __name__ == "__main__":
    main()
