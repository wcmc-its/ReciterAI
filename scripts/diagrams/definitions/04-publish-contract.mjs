/**
 * View 4 — Publish contract (the one-way hand-off to the Scholars Profile System).
 * The two S3 channels (versioned + latest/, JSON Schema, manifest sha256) and the
 * DynamoDB record types, and how the SPS ETLs pull each. The integration surface.
 * Source: docs/hierarchy-contract.md, docs/spotlight-contract.md,
 * pipeline_hierarchy/publish.py, spotlight/publish.py, utils/s3_client.py.
 */
import { A } from "../lib.mjs";

const nodes = {
  pub: { x: 40, y: 250, w: 210, h: 96, kind: "app", title: "ReciterAI publishers",
         sub: ["pipeline_hierarchy/publish.py", "spotlight/publish.py", "load_dynamodb.py"] },

  // ----- published channels -----
  s3h: { x: 330, y: 110, w: 320, h: 140, kind: "data", title: "S3 · wcmc-reciterai-hierarchy",
         sub: ["v{ISO-date}/ hierarchy.json", "v{ISO-date}/ hierarchy.schema.json",
               "v{ISO-date}/ diff.json · manifest.json", "latest/ (overwritten) · sha256"] },
  s3a: { x: 330, y: 276, w: 320, h: 92, kind: "data", title: "S3 · wcmc-reciterai-artifacts",
         sub: ["spotlight/v{ISO-date}/spotlight.json", "spotlight/latest/spotlight.json"] },
  ddb: { x: 330, y: 394, w: 320, h: 132, kind: "data", title: "DynamoDB · reciterai",
         sub: ["TOPIC# — scores + subtopics", "FACULTY# — rollups",
               "IMPACT# — synopsis + impact", "SPOTLIGHT# — records"] },

  // ----- SPS ETLs (pull) -----
  etlh: { x: 730, y: 120, w: 300, h: 120, kind: "net", title: "SPS · etl/hierarchy",
          sub: ["GET latest/manifest.json", "sha256 unchanged → skip", "else validate vs schema → upsert"] },
  etls: { x: 730, y: 276, w: 300, h: 72, kind: "net", title: "SPS · etl/spotlight",
          sub: ["read spotlight/latest/"] },
  etld: { x: 730, y: 400, w: 300, h: 96, kind: "net", title: "SPS · etl/dynamodb",
          sub: ["scheduled scan / query", "→ Prisma / MySQL"] },

  sps: { x: 1090, y: 250, w: 210, h: 120, kind: "edge", title: "Scholars Profile System",
         sub: ["~9,000 public profiles", "topics · subtopics", "spotlights · impact"] },
};

const groups = [
  { x: 24, y: 232, w: 242, h: 132, kind: "app", title: "Producer" },
  { x: 310, y: 92, w: 360, h: 452, kind: "data", title: "Published channels (AWS)" },
  { x: 710, y: 100, w: 340, h: 420, kind: "net", title: "SPS ETLs (pull)" },
  { x: 1074, y: 232, w: 242, h: 160, kind: "edge", title: "Consumer" },
];

const edges = [
  // producer → channels
  { p0: A(nodes.pub, "r", 0.2), p1: A(nodes.s3h, "l", 0.5), color: "amber", label: "versioned + latest/" },
  { p0: A(nodes.pub, "r", 0.5), p1: A(nodes.s3a, "l", 0.5), color: "amber" },
  { p0: A(nodes.pub, "r", 0.8), p1: A(nodes.ddb, "l", 0.5), color: "amber", label: "upsert" },
  // channels → ETLs (pull)
  { p0: A(nodes.s3h, "r"), p1: A(nodes.etlh, "l", 0.5), color: "maroon", label: "manifest" },
  { p0: A(nodes.s3a, "r"), p1: A(nodes.etls, "l", 0.5), color: "maroon" },
  { p0: A(nodes.ddb, "r"), p1: A(nodes.etld, "l", 0.5), color: "maroon", label: "scan" },
  // ETLs → consumer
  { p0: A(nodes.etlh, "r"), p1: A(nodes.sps, "l", 0.25), color: "indigo", label: "load" },
  { p0: A(nodes.etls, "r"), p1: A(nodes.sps, "l", 0.55), color: "indigo" },
  { p0: A(nodes.etld, "r"), p1: A(nodes.sps, "l", 0.85), color: "indigo" },
];

export const spec = { id: "publish-contract", vb: [1340, 580], groups, nodes, edges };

export const meta = {
  nav: "④ Publish contract",
  kicker: "View 4 · the integration surface",
  heading: "Publish contract — the hand-off to SPS",
  dot: "#7d1c1c",
  blurb:
    "The interface that matters to anyone integrating: ReciterAI <b>publishes</b>, the Scholars Profile " +
    "System <b>pulls</b>. Two S3 channels carry the canonical hierarchy and spotlight artifacts " +
    "(versioned for production, <code>latest/</code> for dev, a co-published JSON Schema, and a " +
    "<code>manifest.json</code> whose <b>sha256</b> lets the ETL short-circuit when nothing changed); " +
    "DynamoDB carries the record-level data. ReciterAI never calls SPS.",
  legend: [
    { fill: "#e3faf3", stroke: "#0ca678", label: "Producer" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Published channel" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "SPS ETL" },
    { fill: "#fbeaea", stroke: "#7d1c1c", label: "Consumer" },
  ],
  edgeLegend: [
    { color: "amber", label: "ReciterAI writes" },
    { color: "maroon", label: "SPS ETL pulls" },
    { color: "indigo", label: "loads into SPS" },
  ],
  seeAlso: [
    { id: "processing-pipeline", label: "② the producers (pipeline stages)" },
    { id: "aws-topology", label: "③ where these stores live in AWS" },
  ],
  extraHtml: `
    <div class="grid2">
      <div class="panel">
        <h3>S3 publish contract</h3>
        <ol class="agenda" style="padding-left:18px">
          <li><b>Versioned + latest.</b> Every publish writes an immutable <code>v{ISO-date}/</code> prefix
            (retained indefinitely) and overwrites <code>latest/</code>. Production pins a version; dev tracks
            <code>latest/</code>.</li>
          <li><b>sha256 short-circuit.</b> <code>manifest.json</code> carries the artifact hash; the SPS ETL
            reads the manifest first and skips the whole upsert when the hash is unchanged.</li>
          <li><b>Schema-validated.</b> The JSON Schema is co-published with the artifact; the ETL validates
            before it writes, so a malformed publish fails closed.</li>
          <li><b>diff.json.</b> A structured change signal — added / removed / renamed subtopics, reassigned-PMID
            count, and an <code>editorial_only</code> flag — so consumers can react proportionally.</li>
        </ol>
      </div>
      <div class="panel">
        <h3>DynamoDB record types (single table <code>reciterai</code>)</h3>
        <table class="env">
          <tr><th>PK prefix</th><th>Carries</th></tr>
          <tr><td class="k">TOPIC#{id}</td><td>per-pub topic scores + subtopic assignments</td></tr>
          <tr><td class="k">FACULTY#{cwid}</td><td>ranked topic / subtopic rollups, h-index</td></tr>
          <tr><td class="k">IMPACT#{pmid}</td><td>synopsis (≤95 ch) + impact score (0–100)</td></tr>
          <tr><td class="k">SPOTLIGHT#…</td><td>curated lede + supporting publications</td></tr>
          <tr><td class="k">STAGE#…</td><td>run audit + content-addressed skip-cache (internal)</td></tr>
        </table>
        <p class="foot">Records carry <code>taxonomy_version</code>, so a taxonomy bump triggers targeted
          recompute rather than a full rebuild.</p>
      </div>
    </div>`,
  footnote:
    "One-way hand-off: ReciterAI is upstream and ships independently; it does not call SPS, depend on SPS " +
    "at runtime, or know about Prisma / MySQL. Contract source-of-truth lives in " +
    "<code>docs/hierarchy-contract.md</code> and <code>docs/spotlight-contract.md</code>.",
  source: "docs/hierarchy-contract.md · docs/spotlight-contract.md · pipeline_hierarchy/publish.py · spotlight/publish.py · utils/s3_client.py",
};
