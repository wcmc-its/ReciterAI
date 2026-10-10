"""Frame A: research areas vs WCM departments and divisions, from publication data.

Reads the frozen aggregates in inputs/ (paper counts per research area x org unit,
pulled read-only from the SPS prod DB; see README.md) and writes outputs/frame_a_*.csv.

Definitions (n = distinct PMIDs scoring >= the area's display threshold, authored by an
active scholar whose primary appointment is in the unit):
  share(a,u) = n(a,u) / n(a)                       fraction of the area's papers with a unit author
  base(u)    = sum_a n(a,u) / sum_a n(a)           the unit's share of all area assignments
  lift(a,u)  = share(a,u) / base(u)                >1 means the area is over-represented in the unit

An area has a HOME in a unit when lift >= LIFT and n(a,u) >= MIN_N.
A unit is COVERED when at least one area has a home in it.
LOW_VOLUME (area or unit) = the best lift >= LIFT exists but only below MIN_N papers: aligned,
but too few papers to call it, so it is counted separately rather than as a pass or a gap.
Run: python3 docs/benchmark/frame_a.py
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
IN, OUT = HERE / "inputs", HERE / "outputs"
LIFT, MIN_N = 2.0, 25
SENSITIVITY = [(1.5, 25), (2.0, 25), (3.0, 25), (2.0, 50)]


def read(name):
    with open(IN / name, newline="") as f:
        return list(csv.DictReader(f))


def unit_table(rows, key, names):
    n = {(r["t"], r[key]): int(r["n"]) for r in rows}
    tot_a = {r["t"]: int(r["n"]) for r in read("frameA_area_totals.csv")}
    per_u = defaultdict(int)
    for (_, u), v in n.items():
        per_u[u] += v
    all_pairs = sum(tot_a.values())
    out = []
    for (a, u), v in n.items():
        share = v / tot_a[a]
        base = per_u[u] / all_pairs
        out.append({"area": a, "unit": u, "unit_name": names.get(u, u), "n": v,
                    "share": round(share, 4), "lift": round(share / base, 3)})
    return out, tot_a


def homes(rows, lift, min_n):
    return [r for r in rows if r["lift"] >= lift and r["n"] >= min_n]


def main():
    areas = {r["id"]: r["label"] for r in read("research_areas.csv")}
    depts = {r["code"]: r["name"] for r in read("wcm_departments.csv")}
    divs = {r["code"]: f'{depts.get(r["dept_code"], r["dept_code"])} / {r["name"]}'
            for r in read("wcm_divisions.csv")}
    dept_rows, tot_a = unit_table(read("frameA_area_dept.csv"), "d", depts)
    div_rows, _ = unit_table(read("frameA_area_div.csv"), "v", divs)
    for r in dept_rows:
        r["level"] = "department"
    for r in div_rows:
        r["level"] = "division"
    rows = dept_rows + div_rows
    assert set(tot_a) == set(areas), "area totals and research_areas.csv disagree"

    OUT.mkdir(exist_ok=True)
    cols = ["area", "level", "unit", "unit_name", "n", "share", "lift"]
    with open(OUT / "frame_a_area_unit.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["area"], -r["lift"])))

    # Forward: one row per area, its strongest home (or strongest unit if none qualifies).
    fwd = []
    for a, label in sorted(areas.items(), key=lambda x: x[1]):
        mine = sorted((r for r in rows if r["area"] == a), key=lambda r: -r["lift"])
        h = [r for r in mine if r["lift"] >= LIFT and r["n"] >= MIN_N]
        top_share = max((r for r in dept_rows if r["area"] == a), key=lambda r: r["share"])
        fwd.append({
            "area": a, "label": label, "papers": tot_a[a],
            "status": "home" if h else ("low_volume" if mine and mine[0]["lift"] >= LIFT else "no_home"),
            "homes": "; ".join(f'{r["unit_name"]} (lift {r["lift"]}, n={r["n"]})' for r in h[:4]),
            "largest_dept": f'{top_share["unit_name"]} ({top_share["share"]:.0%})',
        })
    with open(OUT / "frame_a_forward.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(fwd[0]))
        w.writeheader()
        w.writerows(fwd)

    # Reverse: one row per unit, the areas that make it distinctive.
    rev = []
    for level, names in (("department", depts), ("division", divs)):
        for u, name in sorted(names.items(), key=lambda x: x[1]):
            mine = sorted((r for r in rows if r["unit"] == u and r["level"] == level),
                          key=lambda r: -r["lift"])
            if not mine:
                rev.append({"level": level, "unit": u, "unit_name": name, "status": "no_papers", "areas": ""})
                continue
            h = [r for r in mine if r["lift"] >= LIFT and r["n"] >= MIN_N]
            rev.append({
                "level": level, "unit": u, "unit_name": name,
                "status": "covered" if h else ("low_volume" if mine[0]["lift"] >= LIFT else "not_covered"),
                "areas": "; ".join(f'{areas[r["area"]]} (lift {r["lift"]}, n={r["n"]})' for r in (h or mine)[:4]),
            })
    with open(OUT / "frame_a_reverse.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(rev[0]))
        w.writeheader()
        w.writerows(rev)

    # Summary + threshold sensitivity.
    for name, tbl in (("areas", fwd), ("units", rev)):
        c = defaultdict(int)
        for r in tbl:
            c[r.get("level", "area") + ":" + r["status"]] += 1
        print(name, dict(sorted(c.items())))
    print("lift  min_n  areas_with_home  depts_covered  divisions_covered")
    for lift, min_n in SENSITIVITY:
        hs = homes(rows, lift, min_n)
        ah = len({r["area"] for r in hs})
        dc = len({r["unit"] for r in hs if r["level"] == "department"})
        vc = len({r["unit"] for r in hs if r["level"] == "division"})
        print(f"{lift:<5} {min_n:<6} {ah}/{len(areas):<14} {dc}/{len(depts):<12} {vc}/{len(divs)}")


if __name__ == "__main__":
    sys.exit(main())
