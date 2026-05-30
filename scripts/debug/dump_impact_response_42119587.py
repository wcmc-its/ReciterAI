"""One-off debug: fetch PMID 42119587 from MariaDB, call Bedrock for the
impact prompt, print the raw text. Throwaway — not for prod."""
from __future__ import annotations

from pipeline_enrichment.llm_call import call_with_fallback
from pipeline_enrichment.prompts import build_impact_user_content, get_impact_system_prompt
from utils.db import get_engine
from utils.sql_queries import fetch_publications_for_enrichment


def main():
    engine = get_engine()
    rows = fetch_publications_for_enrichment(engine, ["42119587"])
    if not rows:
        print("PMID 42119587 not in analysis_summary_article")
        return
    pub = rows[0]
    print(f"title:    {pub.get('articleTitle')}")
    print(f"journal:  {pub.get('journalTitleVerbose')}")
    print(f"year:     {pub.get('articleYear')}")
    print(f"abstract: {(pub.get('abstractVarchar') or '')[:200]}...")
    print()

    system = get_impact_system_prompt(None)
    user = (
        "Please analyze the following publication and provide an impact score:\n\n"
        + build_impact_user_content(pub)
    )

    print(">>> calling Bedrock for impact at max_tokens=4096 (post-fix) ...")
    result = call_with_fallback(
        system_prompt=system, user_prompt=user, max_tokens=4096,
    )
    print(f"model:         {result.model}")
    print(f"input_tokens:  {result.input_tokens}")
    print(f"output_tokens: {result.output_tokens}")
    print(f"text length:   {len(result.text)} chars")
    print("---raw text---")
    print(repr(result.text))
    print("---end---")


if __name__ == "__main__":
    main()
