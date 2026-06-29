"""Convert a WCM funding-opportunity-DB scrape (JSON) to the curated-CSV schema that
``ingest_curated`` consumes, so the Playwright-scraped foundation opportunities flow through
the SAME normalize -> denoise -> score -> persist path as the hand-curated awards
(``source=wcm_curated``). One step, no new ingest pipeline.

The scrape (e.g. ``wcm_funding_db_2026-06-28.json``) is a JSON list of objects with
``title``/``sponsor``/``url``/``focus_description``/``eligibility_text``/deadlines (the WCM
research-funding portal crawl). We map only what the curated pipeline reads — the
``wcm_curated.py`` ``H_*`` headers plus a ``synopsis`` passthrough column. ``opportunity_id`` is
derived downstream as ``wcm_curated:slug(name, sponsor)``, so re-runs are idempotent and any
award that overlaps the hand-curated April set upserts rather than duplicating.

Run:
    python -m pipeline_grants.funding_db_to_csv wcm_funding_db_2026-06-28.json \\
        -o pipeline_grants/data/wcm_funding_db_enriched.csv
    python -m pipeline_grants.ingest_curated --csv pipeline_grants/data/wcm_funding_db_enriched.csv
"""
import argparse
import csv
import json

# Curated CSV headers ingest_curated reads (must match wcm_curated.H_*), + synopsis passthrough.
HEADERS = ["Award Name", "Sponsoring Organization", "Award Field", "Career Stage",
           "Award Amount", "Nomination Deadline", "Website", "synopsis"]


def _row(o: dict) -> dict:
    """One scrape object -> one curated-CSV row dict."""
    title = (o.get("title") or o.get("list_title") or "").strip()
    # `Career Stage` does double duty in the curated pipeline: it drives appeal_by_stage
    # (tokenized) AND becomes eligibility_raw. Fold both eligibility prose fields in.
    stage = " ".join(
        s for s in [(o.get("eligibility_text") or "").strip(), (o.get("application_req") or "").strip()] if s
    )
    return {
        "Award Name": title,
        "Sponsoring Organization": (o.get("sponsor") or "").strip(),
        "Award Field": "",
        "Career Stage": stage,
        "Award Amount": "",
        "Nomination Deadline": (o.get("external_deadline") or "").strip(),
        "Website": (o.get("url") or "").strip(),
        # The scorable abstract. ingest_curated falls back to a field-based synopsis if blank.
        "synopsis": (o.get("focus_description") or "").strip(),
    }


def convert(json_path: str, csv_path: str) -> dict:
    """Read the scrape JSON, write the curated CSV. Skips entries with no title. Returns counts."""
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = [_row(o) for o in data if (o.get("title") or o.get("list_title"))]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)
    return {"input": len(data), "written": len(rows), "csv": csv_path}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="WCM funding-DB JSON scrape -> curated CSV for ingest_curated.")
    p.add_argument("json_path")
    p.add_argument("-o", "--out", default="pipeline_grants/data/wcm_funding_db_enriched.csv")
    args = p.parse_args(argv)
    print(convert(args.json_path, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
