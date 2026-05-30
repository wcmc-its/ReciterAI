/**
 * View 1 — System context (the one-glance landscape).
 * What feeds ReciterAI, the service itself + the model providers it calls, and
 * the two-channel artifacts it publishes for the Scholars Profile System to pull.
 * Source: README.md, ARCHITECTURE.md, utils/s3_client.py, utils/bedrock_client.py.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- left: data sources -----
  rdb: { x: 40, y: 132, w: 290, h: 66, kind: "ext", title: "ReciterDB",
         sub: ["MariaDB · publications, abstracts", "faculty (CWID), keyword-relevance"], chip: { tone: "nightly", text: "nightly" } },
  tax: { x: 40, y: 226, w: 290, h: 66, kind: "ext", title: "taxonomy_v2.json",
         sub: ["frozen 67-topic taxonomy", "human-approved · in-repo"], chip: { tone: "annual", text: "frozen" } },

  // ----- center: the service -----
  pipe: { x: 450, y: 150, w: 320, h: 162, kind: "app", title: "ReciterAI pipeline",
          sub: ["1 · taxonomy (cold · frozen)", "2 · score pubs (Haiku → Sonnet)",
                "3 · subtopics: discover + assign", "4 · faculty rollups (by CWID)",
                "5 · enrichment: synopsis + impact", "6 · spotlight (lede + critic)",
                "7 · publish artifacts"] },
  // ----- center: model providers it invokes -----
  bedrock: { x: 450, y: 400, w: 184, h: 92, kind: "aws", title: "AWS Bedrock",
             sub: ["Claude Sonnet 4.6", "Haiku 4.5 · Opus 4.7"] },
  openai:  { x: 650, y: 400, w: 120, h: 92, kind: "ext", title: "OpenAI",
             sub: ["gpt-5.1", "content-filter", "fallback"] },

  // ----- right: published outputs (AWS) -----
  s3h: { x: 880, y: 140, w: 300, h: 74, kind: "data", title: "S3 · reciterai-hierarchy",
         sub: ["hierarchy.json + JSON Schema", "versioned + latest/ · manifest sha256"] },
  s3a: { x: 880, y: 234, w: 300, h: 74, kind: "data", title: "S3 · reciterai-artifacts",
         sub: ["spotlight.json", "versioned + latest/"] },
  ddb: { x: 880, y: 328, w: 300, h: 74, kind: "data", title: "DynamoDB · reciterai",
         sub: ["scores · faculty rollups", "impact · spotlight records"] },

  // ----- far right: the consumer -----
  sps: { x: 1232, y: 170, w: 216, h: 184, kind: "net", title: "Scholars Profile System",
         sub: ["pulls on a schedule via ETLs:", "· hierarchy + spotlight (S3)",
               "· scores, rollups, impact (DDB)", "→ Prisma / MySQL", "→ ~9,000 public profiles"] },
};

const groups = [
  { x: 20, y: 96, w: 330, h: 220, kind: "ext", title: "Data sources" },
  { x: 430, y: 110, w: 360, h: 220, kind: "edge", title: "ReciterAI service" },
  { x: 430, y: 362, w: 360, h: 150, kind: "aws", title: "Model providers" },
  { x: 860, y: 104, w: 340, h: 320, kind: "data", title: "Published outputs (AWS)" },
  { x: 1216, y: 104, w: 248, h: 320, kind: "net", title: "Consumer (downstream)" },
];

const edges = [
  { p0: A(nodes.rdb, "r"), p1: A(nodes.pipe, "l", 0.3), color: "teal", label: "read corpus" },
  { p0: A(nodes.tax, "r"), p1: A(nodes.pipe, "l", 0.72), color: "teal", label: "taxonomy" },
  { p0: A(nodes.pipe, "b", 0.4), p1: A(nodes.bedrock, "t", 0.5), color: "violet", label: "invoke" },
  { p0: A(nodes.pipe, "b", 0.82), p1: A(nodes.openai, "t", 0.5), color: "gray", dash: true, label: "fallback" },
  { p0: A(nodes.pipe, "r", 0.22), p1: A(nodes.s3h, "l", 0.5), color: "amber", label: "publish" },
  { p0: A(nodes.pipe, "r", 0.5), p1: A(nodes.s3a, "l", 0.5), color: "amber" },
  { p0: A(nodes.pipe, "r", 0.8), p1: A(nodes.ddb, "l", 0.5), color: "amber", label: "write" },
  { p0: A(nodes.s3h, "r"), p1: A(nodes.sps, "l", 0.24), color: "maroon", label: "latest/ + sha256" },
  { p0: A(nodes.s3a, "r"), p1: A(nodes.sps, "l", 0.52), color: "maroon" },
  { p0: A(nodes.ddb, "r"), p1: A(nodes.sps, "l", 0.82), color: "maroon", label: "scheduled ETL" },
];

export const spec = { id: "system-context", vb: [1480, 560], groups, nodes, edges };

export const meta = {
  nav: "① System context",
  kicker: "View 1 · the one-glance landscape",
  heading: "System context",
  dot: "#7d1c1c",
  blurb:
    "The picture for newcomers: ReciterAI reads the WCM publication corpus from <b>ReciterDB</b> " +
    "and a frozen <b>taxonomy</b>, runs a seven-stage LLM pipeline against <b>AWS Bedrock</b> " +
    "(with an OpenAI fallback), and publishes to <b>two S3 buckets + DynamoDB</b>. The Scholars " +
    "Profile System <b>pulls</b> from those — ReciterAI is upstream and ships independently.",
  legend: [
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Source / external" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "ReciterAI compute" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "AWS managed (Bedrock)" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Published store (S3 / DynamoDB)" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "Consumer" },
  ],
  edgeLegend: [
    { color: "teal", label: "reads input" },
    { color: "violet", label: "invokes model" },
    { color: "amber", label: "writes output" },
    { color: "maroon", label: "consumed by SPS" },
    { color: "gray", dash: true, label: "fallback / out-of-band" },
  ],
  seeAlso: [
    { id: "processing-pipeline", label: "② the seven stages in detail" },
    { id: "aws-topology", label: "③ how it's scheduled & deployed" },
    { id: "publish-contract", label: "④ the SPS hand-off" },
  ],
  footnote:
    "ReciterDB's publication data originates from <b>PubMed</b> via the ReCiter disambiguation " +
    "engine (upstream of this diagram). The taxonomy is generated once by <code>generate_taxonomy.py</code> " +
    "and <b>frozen</b> after human review — no runtime recompute. ReciterAI never calls the Scholars " +
    "Profile System; the hand-off is one-way through S3 + DynamoDB.",
  source: "README.md · ARCHITECTURE.md · utils/s3_client.py · utils/bedrock_client.py · utils/dynamodb_helpers.py",
};
