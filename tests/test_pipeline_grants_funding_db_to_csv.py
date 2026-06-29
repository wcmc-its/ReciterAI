"""funding_db_to_csv: WCM funding-DB scrape (JSON) -> curated CSV. Pure file transform,
so the field mapping + title-skip is verifiable offline."""
import csv
import json

import pipeline_grants.funding_db_to_csv as f


def test_maps_fields_and_skips_untitled(tmp_path):
    src = tmp_path / "fdb.json"
    src.write_text(json.dumps([
        {
            "title": "Hartwell Foundation - Individual Biomedical Research Award",
            "sponsor": "Hartwell Foundation",
            "url": "https://research.weill.cornell.edu/x",
            "focus_description": "Supports early-stage biomedical research benefiting children.",
            "eligibility_text": "early-career assistant professor",
            "application_req": "WCM limited to submitting 1 nominee",
            "external_deadline": "Nov 2026",
        },
        {"sponsor": "No Title Co", "url": "https://y"},  # no title -> skipped
    ]))
    out = tmp_path / "out.csv"
    summary = f.convert(str(src), str(out))
    assert summary == {"input": 2, "written": 1, "csv": str(out)}

    rows = list(csv.DictReader(open(out)))
    assert len(rows) == 1
    r = rows[0]
    assert r["Award Name"].startswith("Hartwell Foundation")
    assert r["Sponsoring Organization"] == "Hartwell Foundation"
    assert r["Website"] == "https://research.weill.cornell.edu/x"
    assert "biomedical" in r["synopsis"]
    # Career Stage carries both eligibility prose fields (appeal + eligibility_raw).
    assert "early-career assistant professor" in r["Career Stage"]
    assert "limited to submitting 1 nominee" in r["Career Stage"]
    # Header is exactly the curated schema ingest_curated reads.
    assert list(r.keys()) == f.HEADERS
