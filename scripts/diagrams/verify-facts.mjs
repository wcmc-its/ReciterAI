/**
 * Fact verifier — assert the constants baked into diagram TEXT still match the
 * real sources, so the diagrams cannot silently drift. Reads:
 *
 *   infra/eventbridge.json      → every rule's cron schedule_expression (exact)
 *   utils/bedrock_client.py     → HAIKU/SONNET/OPUS_MODEL ids → "Family X.Y"
 *   utils/s3_client.py          → HIERARCHY_BUCKET / ARTIFACTS_BUCKET (exact)
 *   utils/dynamodb_helpers.py   → TABLE_NAME (exact)
 *
 * Each probe FAILS LOUD if it cannot locate its source constant — a passing
 * check must mean "verified", never "couldn't look" (a static grep that passes
 * for the wrong reason is worse than no check). The generated index.html is
 * folded into the searched text too, so the hero meta is covered alongside the
 * diagrams.
 *
 * This file is ReciterAI-specific (it knows this repo's source paths), so it is
 * NOT part of the portable toolkit. build.mjs runs it automatically *only if it
 * exists*, which keeps build.mjs copyable to another project unchanged.
 *
 *   node scripts/diagrams/verify-facts.mjs     # standalone (exit 1 on mismatch)
 *   import { verifyFacts } from "./verify-facts.mjs"   # returns string[] of problems
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const REPO = join(here, "..", "..");

/** All searchable text for one diagram: node titles + sub-lines + the human-facing meta. */
function diagramText(it) {
  const nodes = Object.values(it.spec.nodes).flatMap((n) => [n.title, ...(n.sub || [])]);
  const meta = [it.meta.heading, it.meta.blurb, it.meta.footnote, it.meta.extraHtml].filter(Boolean);
  return [...nodes, ...meta].join("  ");
}

/** Pull `NAME = "value"` out of a Python source file; null if absent. */
function pyConst(relPath, name) {
  let txt;
  try { txt = readFileSync(join(REPO, relPath), "utf8"); }
  catch (e) { return { err: `could not read ${relPath} (${e.message})` }; }
  const m = txt.match(new RegExp(name + '\\s*=\\s*"([^"]+)"'));
  return m ? { value: m[1] } : { err: `${name} not found in ${relPath} (source moved or renamed?)` };
}

export function verifyFacts(items) {
  const problems = [];
  // Search across ALL diagrams plus the built index.html (so the hero meta is covered).
  let hay = items.map(diagramText).join("  ");
  try { hay += "  " + readFileSync(join(REPO, "docs/architecture/index.html"), "utf8"); } catch { /* not built yet */ }
  const has = (s) => hay.includes(s);

  // 1 — cron strings: every EventBridge rule must appear verbatim in some diagram.
  try {
    const eb = JSON.parse(readFileSync(join(REPO, "infra/eventbridge.json"), "utf8"));
    const rules = eb.rules || [];
    if (!rules.length) problems.push("cron: infra/eventbridge.json has no rules");
    for (const r of rules) {
      if (!r.schedule_expression) { problems.push(`cron: rule "${r.name}" has no schedule_expression`); continue; }
      if (!has(r.schedule_expression))
        problems.push(`cron: "${r.schedule_expression}" (${r.name}) not shown in any diagram — fix 03-aws-topology or the rule`);
    }
  } catch (e) { problems.push(`cron: could not read infra/eventbridge.json (${e.message})`); }

  // 2 — model versions: derive "Family X.Y" from each pinned id; assert it's shown.
  for (const name of ["HAIKU_MODEL", "SONNET_MODEL", "OPUS_MODEL"]) {
    const c = pyConst("utils/bedrock_client.py", name);
    if (c.err) { problems.push(`model: ${c.err}`); continue; }
    const m = c.value.match(/(haiku|sonnet|opus)-(\d+)-(\d+)/i);
    if (!m) { problems.push(`model: cannot parse family+version from ${name}="${c.value}"`); continue; }
    const friendly = `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}`; // → "Haiku 4.5"
    if (!has(friendly)) problems.push(`model: ${friendly} (from ${name}) not shown in any diagram or the hero`);
  }

  // 3 — bucket names (exact).
  for (const name of ["HIERARCHY_BUCKET", "ARTIFACTS_BUCKET"]) {
    const c = pyConst("utils/s3_client.py", name);
    if (c.err) { problems.push(`bucket: ${c.err}`); continue; }
    if (!has(c.value)) problems.push(`bucket: "${c.value}" (${name}) not shown in any diagram`);
  }

  // 4 — DynamoDB table name (exact).
  const t = pyConst("utils/dynamodb_helpers.py", "TABLE_NAME");
  if (t.err) problems.push(`table: ${t.err}`);
  else if (!has(t.value)) problems.push(`table: "${t.value}" (TABLE_NAME) not shown in any diagram`);

  return problems;
}

// Standalone run: load the definitions, verify, report, set exit code.
if (import.meta.url === `file://${process.argv[1]}`) {
  const dir = join(here, "definitions");
  const items = [];
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".mjs")).sort()) {
    const mod = await import(join(dir, f));
    items.push({ spec: mod.spec, meta: mod.meta });
  }
  const problems = verifyFacts(items);
  if (problems.length) {
    console.error(`FAIL facts: ${problems.length} mismatch(es)`);
    problems.forEach((p) => console.error("    " + p));
    process.exitCode = 1;
  } else {
    console.log("PASS facts: crons, model versions, buckets, and table name all match their sources");
  }
}
