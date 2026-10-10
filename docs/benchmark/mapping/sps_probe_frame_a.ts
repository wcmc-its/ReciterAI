// Read-only: research-area x org-unit paper counts for the research-area benchmark (Frame A).
// Aggregates only — no cwids or names leave the DB.
(async () => {
  const out = (k: string, rows: unknown) =>
    console.log(`@@@${k}@@@` + JSON.stringify(rows, (_k, v) => (typeof v === "bigint" ? Number(v) : v)));
  try {
    const m: any = await import("@/lib/db");
    const db = m.db ?? m.default?.db;
    const q = (sql: string) => db.read.$queryRawUnsafe(sql);
    const base = `FROM publication_topic pt
      JOIN scholar s ON s.cwid = pt.cwid AND s.deleted_at IS NULL AND s.status = 'active'
      JOIN topic tp ON tp.id = pt.parent_topic_id
      WHERE pt.score >= COALESCE(tp.display_threshold, 0.5)`;
    console.log("@@@BEGIN@@@");
    out("topics", await q(`SELECT id, label, description, display_threshold, source FROM topic ORDER BY id`));
    out("depts", await q(`SELECT code, name, category, scholar_count FROM department ORDER BY name`));
    out("divs", await q(`SELECT code, dept_code, name, scholar_count FROM division ORDER BY dept_code, name`));
    out("area_totals", await q(`SELECT pt.parent_topic_id t, COUNT(DISTINCT pt.pmid) n, COUNT(DISTINCT pt.cwid) people ${base} GROUP BY 1`));
    out("area_dept", await q(`SELECT pt.parent_topic_id t, s.dept_code d, COUNT(DISTINCT pt.pmid) n, COUNT(DISTINCT pt.cwid) people ${base} GROUP BY 1,2`));
    out("area_div", await q(`SELECT pt.parent_topic_id t, s.div_code v, COUNT(DISTINCT pt.pmid) n, COUNT(DISTINCT pt.cwid) people ${base} AND s.div_code IS NOT NULL GROUP BY 1,2`));
    out("dept_totals", await q(`SELECT s.dept_code d, COUNT(DISTINCT pt.pmid) n ${base} GROUP BY 1`));
    out("div_totals", await q(`SELECT s.div_code v, COUNT(DISTINCT pt.pmid) n ${base} AND s.div_code IS NOT NULL GROUP BY 1`));
    console.log("@@@END@@@");
    await m.disconnectPrisma?.();
    process.exit(0);
  } catch (e: any) {
    console.log("@@@BEGIN@@@");
    console.log("FAILED: " + e.message);
    console.log("@@@END@@@");
    process.exit(1);
  }
})();
