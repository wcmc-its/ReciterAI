/**
 * View 2 — Processing pipeline (the data flow inside the service).
 * The seven stages from corpus to published artifact, the model used at each,
 * and what each stage reads and writes. The data tier sits along the bottom.
 * Source: ARCHITECTURE.md, score_publications.py, discover_subtopics.py,
 * assign_subtopics.py, rollup_by_cwid.py, pipeline_enrichment/, spotlight/,
 * pipeline_hierarchy/, utils/bedrock_client.py (MODEL_IDS_BY_STAGE).
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- inputs -----
  rdb: { x: 40, y: 104, w: 250, h: 72, kind: "ext", title: "ReciterDB",
         sub: ["MariaDB · pubs + abstracts", "faculty · keyword-relevance"] },
  tax: { x: 40, y: 200, w: 250, h: 56, kind: "ext", title: "taxonomy_v2.json",
         sub: ["frozen · 67 topics"] },

  // ----- stage row 1: score → subtopics → rollups -----
  score: { x: 350, y: 120, w: 264, h: 124, kind: "app", title: "score_publications",
           sub: ["Pass 1 — Haiku screen ≥0.3", "Pass 2 — Sonnet dense 0–1", "+ rationale, per topic"] },
  discover: { x: 684, y: 110, w: 246, h: 80, kind: "app", title: "discover_subtopics",
              sub: ["Sonnet · per-topic", "~8–15 subtopics"] },
  assign: { x: 684, y: 214, w: 246, h: 80, kind: "app", title: "assign_subtopics",
            sub: ["Haiku · per-publication", "primary + confidences"] },
  agg: { x: 1000, y: 110, w: 250, h: 80, kind: "app", title: "aggregate_subtopic_scores",
         sub: ["subtopic-level rollup"] },
  rollup: { x: 1000, y: 214, w: 250, h: 80, kind: "app", title: "rollup_by_cwid",
            sub: ["faculty profiles", "ranked topics + subtopics"] },

  // ----- stage row 2: enrichment, spotlight, publish -----
  enrich: { x: 350, y: 336, w: 264, h: 110, kind: "app", title: "daily enrichment",
            sub: ["Sonnet synopsis (≤95 ch)", "Sonnet impact 0–100", "gpt-5.1 content-filter fallback"] },
  spotlight: { x: 684, y: 336, w: 246, h: 110, kind: "app", title: "spotlight",
               sub: ["pool_ranker → assembler", "Opus lede · Haiku critic"] },
  pubh: { x: 1000, y: 336, w: 250, h: 110, kind: "app", title: "hierarchy publish",
          sub: ["compose + JSON Schema", "manifest sha256", "versioned + latest/"] },

  // ----- data tier -----
  ddb: { x: 360, y: 566, w: 330, h: 88, kind: "data", title: "DynamoDB · reciterai",
         sub: ["TOPIC# · FACULTY# · IMPACT#", "SPOTLIGHT# · STAGE#"] },
  s3a: { x: 730, y: 566, w: 220, h: 88, kind: "data", title: "S3 · artifacts",
         sub: ["spotlight.json"] },
  s3h: { x: 985, y: 566, w: 215, h: 88, kind: "data", title: "S3 · hierarchy",
         sub: ["hierarchy.json + schema"] },
};

const groups = [
  { x: 20, y: 84, w: 290, h: 192, kind: "ext", title: "Inputs" },
  { x: 330, y: 88, w: 940, h: 372, kind: "app", title: "Pipeline stages (LLM on Bedrock)" },
  { x: 330, y: 544, w: 940, h: 128, kind: "data", title: "Stores" },
];

const edges = [
  // inputs
  { p0: A(nodes.rdb, "r"), p1: A(nodes.score, "l", 0.32), color: "teal", label: "corpus" },
  { p0: A(nodes.tax, "r"), p1: A(nodes.score, "l", 0.78), color: "teal", label: "taxonomy" },
  // daily delta: down the clear channel (x320) between the Inputs group (≤310) and the score box (≥350).
  { p0: A(nodes.rdb, "r", 0.85), p1: A(nodes.enrich, "t", 0.15), color: "teal", dash: true, label: "daily delta", lp: { x: 320, y: 280 }, points: [{ x: 320, y: 166 }, { x: 320, y: 312 }] },
  // stage → stage
  { p0: A(nodes.score, "r"), p1: A(nodes.discover, "l"), color: "gray", label: "TOPIC#" },
  { p0: A(nodes.discover, "b"), p1: A(nodes.assign, "t"), color: "gray", label: "draft" },
  { p0: A(nodes.assign, "r"), p1: A(nodes.agg, "l"), color: "gray" },
  { p0: A(nodes.agg, "b"), p1: A(nodes.rollup, "t"), color: "gray", label: "scores" },
  { p0: A(nodes.assign, "r", 0.7), p1: A(nodes.pubh, "l", 0.35), color: "gray", label: "subtopics", points: [{ x: 965, y: 312 }] },
  { p0: A(nodes.rollup, "b", 0.25), p1: A(nodes.spotlight, "t", 0.8), color: "gray", label: "profiles" },
  // writes to stores — TOPIC# routed down the left gutter (x340) so it clears the enrich box that sits between score and ddb.
  { p0: A(nodes.score, "l", 0.92), p1: A(nodes.ddb, "t", 0.12), color: "amber", label: "TOPIC#", lp: { x: 340, y: 470 }, points: [{ x: 340, y: 232 }, { x: 340, y: 556 }] },
  { p0: A(nodes.enrich, "b", 0.5), p1: A(nodes.ddb, "t", 0.45), color: "amber", label: "IMPACT#" },
  { p0: A(nodes.spotlight, "b", 0.3), p1: A(nodes.ddb, "t", 0.78), color: "amber", label: "SPOTLIGHT#" },
  // FACULTY#: out the right of rollup, down the right margin (x1268, clear of pubh), along the clear band (y512), into ddb.
  { p0: A(nodes.rollup, "r", 0.5), p1: A(nodes.ddb, "t", 0.95), color: "amber", label: "FACULTY#", lp: { x: 880, y: 512 }, points: [{ x: 1268, y: 254 }, { x: 1268, y: 512 }, { x: 700, y: 512 }] },
  { p0: A(nodes.spotlight, "b", 0.7), p1: A(nodes.s3a, "t", 0.5), color: "maroon", label: "spotlight.json" },
  { p0: A(nodes.pubh, "b", 0.5), p1: A(nodes.s3h, "t", 0.5), color: "maroon", label: "hierarchy.json" },
];

export const spec = { id: "processing-pipeline", vb: [1300, 700], groups, nodes, edges };

export const meta = {
  nav: "② Processing pipeline",
  kicker: "View 2 · the data flow",
  heading: "Processing pipeline",
  dot: "#0ca678",
  blurb:
    "What happens between corpus and artifact. <b>score_publications</b> runs a two-pass screen " +
    "(cheap Haiku recall → calibrated Sonnet scoring); <b>subtopics</b> are discovered then assigned; " +
    "<b>rollup_by_cwid</b> builds faculty profiles; a separate <b>daily enrichment</b> job adds " +
    "synopsis + impact; <b>spotlight</b> synthesizes a curated lede; and the <b>hierarchy publisher</b> " +
    "composes the canonical artifact. Each box names the model it calls.",
  legend: [
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Input" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Pipeline stage" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Store" },
  ],
  edgeLegend: [
    { color: "teal", label: "reads input" },
    { color: "gray", label: "stage → stage" },
    { color: "amber", label: "writes to a store" },
    { color: "maroon", label: "publish (SPS-facing)" },
    { color: "teal", dash: true, label: "daily delta (out-of-band)" },
  ],
  seeAlso: [
    { id: "aws-topology", label: "③ these stages as Step Functions + Fargate" },
    { id: "publish-contract", label: "④ what each store publishes" },
  ],
  footnote:
    "The <b>daily enrichment</b> job (synopsis + impact) runs on its own cadence straight from ReciterDB, " +
    "independent of the weekly scoring path. Every stage records a <code>STAGE#</code> row with a " +
    "content-addressed <code>input_hash</code> and is skipped on an unchanged re-run.",
  source: "ARCHITECTURE.md · score_publications.py · assign_subtopics.py · rollup_by_cwid.py · pipeline_enrichment/ · spotlight/ · pipeline_hierarchy/",
};
