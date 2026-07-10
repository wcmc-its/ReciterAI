"""Reference eligibility extraction over the 100-item sample.

Step 2 of the eligibility-capture audit. Reuses the ReciterAI Bedrock pattern
(boto3 bedrock-runtime Converse, SONNET_MODEL, temperature 0.0, JSON-fence
stripping, throttle backoff) as a standalone script — deliberately not imported
from utils/bedrock_client.py so the audit has zero coupling to pipeline code.
Reads out/sample.json (from scan_grants.py) and writes out/extractions.json
keyed by item pk, plus out/extraction_errors.json.

Runnable from anywhere:  python scripts/eligibility_audit/extract.py
Cost: one Sonnet Converse call per sampled item (~100 calls).
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config

OUT_DIR = Path(__file__).resolve().parent / "out"
SONNET_MODEL = "us.anthropic.claude-sonnet-4-6"  # matches SONNET_MODEL in utils/bedrock_client.py
RETRYABLE = {"ThrottlingException", "ModelTimeoutException", "InternalServerException", "ServiceUnavailableException"}

SYSTEM = """You extract STRUCTURED ELIGIBILITY facts from funding-opportunity (NOFO) eligibility prose for a US medical college. Base every field on the ELIGIBILITY text; use title/sponsor/synopsis only to disambiguate. Never guess: when the text does not state a fact, use the explicit "not stated" value defined below.

Respond ONLY with strict JSON matching exactly this schema:
{
 "applicant_org_types": [..],   // who may APPLY (the institution/entity), subset of:
   // "higher_ed", "nonprofit", "for_profit", "small_business", "state_government",
   // "local_government", "tribal_government", "federal_agency", "hospital",
   // "foreign_org", "individual", "other", "unrestricted"
   // Use "unrestricted" alone when the text says anyone/all orgs may apply.
   // Empty list = eligibility text does not describe applicant org types.
 "career_stages": [..],         // which CAREER STAGES the funded person must be in, subset of:
   // "undergraduate", "graduate_student", "postdoc", "early_career_faculty",
   // "mid_career_faculty", "senior_faculty", "any_faculty", "clinician"
   // Empty list = no person-level career-stage restriction stated.
 "degree_required": [..],       // degrees the PI/candidate must hold, subset of:
   // "phd", "md", "md_or_phd_either", "other_doctoral", "nursing_degree", "other_clinical_doctorate"
   // Empty list = no degree requirement stated.
 "citizenship_requirement": "", // one of:
   // "us_citizen_or_permanent_resident_required"  (person must be citizen/PR)
   // "visa_holders_eligible"                      (explicitly open to temporary visa holders)
   // "foreign_institutions_eligible"              (non-US orgs may apply)
   // "foreign_institutions_ineligible"            (explicitly US orgs only)
   // "not_stated"
 "esi_targeted": bool,          // true only if Early Stage Investigators / new investigators are
                                // explicitly targeted, prioritized, or the award is restricted to them
 "limited_submission": bool,    // institution may submit only N applications / internal competition required
 "small_business_only": bool,   // restricted to small businesses (e.g. SBIR/STTR)
 "government_only": bool,       // restricted to government entities (state/local/tribal/federal)
 "cost_sharing_required": bool, // cost sharing / matching funds REQUIRED (not "encouraged" or "none")
 "individual_award": bool       // the award is made to/for a NAMED INDIVIDUAL (fellowship, career
                                // development, dissertation award) rather than an institutional project grant
}
No markdown fences. No commentary."""


def build_user(item):
    elig = (item.get("eligibility_raw") or "")[:9000]
    syn = (item.get("synopsis") or "")[:1000]
    return (
        f"Title: {item.get('title','')}\n"
        f"Sponsor: {item.get('sponsor','')}\n"
        f"Mechanism: {item.get('mechanism','') or '(none)'}\n"
        f"ELIGIBILITY TEXT:\n{elig}\n\n"
        f"Synopsis (context only, first 1000 chars):\n{syn}"
    )


client = boto3.client(
    "bedrock-runtime",
    region_name="us-east-1",
    config=Config(read_timeout=120, retries={"max_attempts": 0}),
)


def call_one(item):
    from botocore.exceptions import ClientError
    user = build_user(item)
    body = None
    for attempt in range(5):
        try:
            resp = client.converse(
                modelId=SONNET_MODEL,
                messages=[{"role": "user", "content": [{"text": user}]}],
                system=[{"text": SYSTEM}],
                inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
            )
            blocks = resp.get("output", {}).get("message", {}).get("content") or []
            if not blocks:
                raise RuntimeError(f"empty content stopReason={resp.get('stopReason')}")
            body = blocks[0]["text"]
            break
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in RETRYABLE or attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 30))
    cleaned = re.sub(r"```json\n?|\n?```", "", body).strip()
    return json.loads(cleaned)


def main():
    with open(OUT_DIR / "sample.json") as f:
        sample = json.load(f)
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(call_one, it): it["pk"] for it in sample}
        done = 0
        for fut in as_completed(futs):
            pk = futs[fut]
            try:
                results[pk] = fut.result()
            except Exception as e:  # noqa: BLE001 — record and continue
                errors[pk] = f"{type(e).__name__}: {e}"
            done += 1
            if done % 10 == 0:
                print(f"progress {done}/{len(sample)}", file=sys.stderr)
    with open(OUT_DIR / "extractions.json", "w") as f:
        json.dump(results, f, indent=2)
    with open(OUT_DIR / "extraction_errors.json", "w") as f:
        json.dump(errors, f, indent=2)
    print(f"ok={len(results)} errors={len(errors)}")
    if errors:
        print(json.dumps(errors, indent=2))


if __name__ == "__main__":
    main()
