"""Draw the spot-check sample: a seeded random 10% of the final B/C1/C2 pairs.

Writes outputs/spot_check_sample.csv with blank reviewer columns for a reviewer to fill in
(reviewer_agrees yes/no, reviewer_note). Run after frames_bc.py; refuses to overwrite a
sample that already has reviews. Seed = SNAPSHOT_DATE digits, so a rerun draws the same rows.
The 2026-10-10 sample predates this script and was drawn separately; it is not reproducible from it.
"""

import csv
import random
import sys

from crosswalk import OUT, SNAPSHOT_DATE, read

COLS = ["frame", "area_label", "ref_name", "relation", "source", "rationale", "reviewer_agrees", "reviewer_note"]


def main():
    path = OUT / "spot_check_sample.csv"
    if path.exists() and any(r["reviewer_agrees"].strip() for r in read(path)):
        sys.exit(f"{path.name} already has reviews; move it aside to draw a new sample")
    pairs = [{"frame": k, **p} for k in ("B", "C1", "C2") for p in read(OUT / f"frame_{k}_pairs.csv")]
    sample = random.Random(int(SNAPSHOT_DATE.replace("-", ""))).sample(pairs, round(len(pairs) / 10))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows({**p, "reviewer_agrees": "", "reviewer_note": ""} for p in sample)
    print(len(sample), "of", len(pairs), "pairs ->", path.name)


if __name__ == "__main__":
    main()
