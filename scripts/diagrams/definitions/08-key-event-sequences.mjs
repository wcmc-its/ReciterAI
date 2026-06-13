/**
 * View 8 — Key event sequences (the temporal register the structural views omit).
 * Five canonical recurring events, each drawn as a numbered left→right trace:
 * trigger (+ cadence) → ordered steps → the record/artifact it produces. Each
 * lane's edges share one colour so the five traces read apart at a glance.
 * Constants are real: score_floor 0.3, SELECTION_SIZE 10, DECAY_TAU_WEEKS 12,
 * spotlight_clone_overlap_min 0.4, spotlight_scholar_penalty_lambda 0.08,
 * drift_uncovered_rate_alert 0.05.
 * Source: infra/eventbridge.json, score_publications.py, pipeline_enrichment/,
 * spotlight/rotation_selector.py, spotlight/critic.py, pipeline_hierarchy/,
 * pipeline_drift/evaluator.py, config/thresholds.json.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ===== Lane 1 — new publication scored (weekly) =====
  t1: { x: 40, y: 92, w: 176, h: 64, kind: "aws", title: "EventBridge", sub: ["hot path · weekly"] },
  s1a: { x: 250, y: 92, w: 212, h: 64, kind: "aws", title: "① Haiku screen", sub: ["all topics · keep ≥0.3"] },
  s1b: { x: 500, y: 92, w: 212, h: 64, kind: "aws", title: "② Sonnet score", sub: ["dense 0–1 + rationale"] },
  s1c: { x: 750, y: 92, w: 212, h: 64, kind: "app", title: "③ Assign + rollup", sub: ["subtopic → CWID profile"] },
  s1d: { x: 1000, y: 92, w: 212, h: 64, kind: "data", title: "④ TOPIC# · FACULTY#", sub: ["scores + faculty rollup"] },

  // ===== Lane 2 — daily enrichment + OpenAI fallback (daily) =====
  t2: { x: 40, y: 218, w: 176, h: 64, kind: "aws", title: "EventBridge", sub: ["enrichment · daily"] },
  s2a: { x: 250, y: 218, w: 212, h: 64, kind: "app", title: "① Read delta", sub: ["new PMIDs from ReciterDB"] },
  s2b: { x: 500, y: 218, w: 212, h: 64, kind: "aws", title: "② Sonnet synopsis", sub: ["+ impact 0–100", "→ gpt-5.1 if content-filtered"] },
  s2c: { x: 750, y: 218, w: 212, h: 64, kind: "data", title: "③ IMPACT#", sub: ["synopsis + impact rows"] },

  // ===== Lane 3 — monthly spotlight publish (monthly) =====
  t3: { x: 40, y: 344, w: 176, h: 64, kind: "aws", title: "EventBridge", sub: ["spotlight · monthly"] },
  s3a: { x: 250, y: 344, w: 212, h: 64, kind: "app", title: "① Dirty-gate → pool", sub: ["≥5 new pubs per subtopic"] },
  s3b: { x: 500, y: 344, w: 212, h: 64, kind: "aws", title: "② Opus lede + critic", sub: ["Haiku critic loop"] },
  s3c: { x: 750, y: 344, w: 212, h: 64, kind: "app", title: "③ Clone/coverage gate", sub: ["overlap ≥0.4 · λ 0.08"] },
  s3d: { x: 1000, y: 344, w: 212, h: 64, kind: "data", title: "④ Publish + rotate", sub: ["spotlight.json · SPOTLIGHT#", "home page rotates 10 (τ=12wk)"] },

  // ===== Lane 4 — hierarchy rebuild, the stability path (on-demand) =====
  t4: { x: 40, y: 470, w: 176, h: 64, kind: "ext", title: "Operator", sub: ["--publish · on-demand"] },
  s4a: { x: 250, y: 470, w: 212, h: 64, kind: "app", title: "① Re-cluster", sub: ["per-topic subtopics"] },
  s4b: { x: 500, y: 470, w: 212, h: 64, kind: "aws", title: "② Reconcile", sub: ["overlap → centroid → LLM"] },
  s4c: { x: 750, y: 470, w: 212, h: 64, kind: "app", title: "③ Match-or-mint id", sub: ["STAGE# skip if unchanged"] },
  s4d: { x: 1000, y: 470, w: 212, h: 64, kind: "data", title: "④ Publish hierarchy", sub: ["+ schema + sha256 → SPS"] },

  // ===== Lane 5 — drift watchdog fires (daily) =====
  t5: { x: 40, y: 596, w: 176, h: 64, kind: "aws", title: "EventBridge", sub: ["drift · daily"] },
  s5a: { x: 250, y: 596, w: 212, h: 64, kind: "aws", title: "① Scan window", sub: ["new PMIDs · STAGE# fails"] },
  s5b: { x: 500, y: 596, w: 212, h: 64, kind: "app", title: "② Threshold check", sub: ["uncovered_rate ≥0.05"] },
  s5c: { x: 750, y: 596, w: 212, h: 64, kind: "data", title: "③ DRIFT# row", sub: ["persist evaluation"] },
  s5d: { x: 1000, y: 596, w: 212, h: 64, kind: "ext", title: "④ Teams alert", sub: ["on breach"] },
};

const groups = [
  { x: 24, y: 70, w: 1204, h: 108, kind: "ext", title: "New publication scored" },
  { x: 24, y: 196, w: 1204, h: 108, kind: "ext", title: "Daily enrichment + OpenAI fallback" },
  { x: 24, y: 322, w: 1204, h: 108, kind: "ext", title: "Monthly spotlight publish" },
  { x: 24, y: 448, w: 1204, h: 108, kind: "ext", title: "Hierarchy rebuild — stability path" },
  { x: 24, y: 574, w: 1204, h: 108, kind: "ext", title: "Drift watchdog fires" },
];

const edges = [
  // Lane 1 — teal
  { p0: A(nodes.t1, "r"), p1: A(nodes.s1a, "l"), color: "teal" },
  { p0: A(nodes.s1a, "r"), p1: A(nodes.s1b, "l"), color: "teal" },
  { p0: A(nodes.s1b, "r"), p1: A(nodes.s1c, "l"), color: "teal" },
  { p0: A(nodes.s1c, "r"), p1: A(nodes.s1d, "l"), color: "teal" },
  // Lane 2 — amber
  { p0: A(nodes.t2, "r"), p1: A(nodes.s2a, "l"), color: "amber" },
  { p0: A(nodes.s2a, "r"), p1: A(nodes.s2b, "l"), color: "amber" },
  { p0: A(nodes.s2b, "r"), p1: A(nodes.s2c, "l"), color: "amber" },
  // Lane 3 — maroon
  { p0: A(nodes.t3, "r"), p1: A(nodes.s3a, "l"), color: "maroon" },
  { p0: A(nodes.s3a, "r"), p1: A(nodes.s3b, "l"), color: "maroon" },
  { p0: A(nodes.s3b, "r"), p1: A(nodes.s3c, "l"), color: "maroon" },
  { p0: A(nodes.s3c, "r"), p1: A(nodes.s3d, "l"), color: "maroon" },
  // Lane 4 — indigo
  { p0: A(nodes.t4, "r"), p1: A(nodes.s4a, "l"), color: "indigo" },
  { p0: A(nodes.s4a, "r"), p1: A(nodes.s4b, "l"), color: "indigo" },
  { p0: A(nodes.s4b, "r"), p1: A(nodes.s4c, "l"), color: "indigo" },
  { p0: A(nodes.s4c, "r"), p1: A(nodes.s4d, "l"), color: "indigo" },
  // Lane 5 — violet
  { p0: A(nodes.t5, "r"), p1: A(nodes.s5a, "l"), color: "violet" },
  { p0: A(nodes.s5a, "r"), p1: A(nodes.s5b, "l"), color: "violet" },
  { p0: A(nodes.s5b, "r"), p1: A(nodes.s5c, "l"), color: "violet" },
  { p0: A(nodes.s5c, "r"), p1: A(nodes.s5d, "l"), color: "violet" },
];

export const spec = { id: "key-event-sequences", vb: [1252, 712], groups, nodes, edges };

export const meta = {
  nav: "⑧ Key events",
  kicker: "View 8 · worked event sequences",
  heading: "Key event sequences",
  dot: "#2b8a3e",
  blurb:
    "The temporal register the other views leave out: <b>what happens, step by step</b>, when a specific " +
    "thing occurs. Five canonical recurring events, each a numbered trace from its trigger to the record it " +
    "writes — a paper getting <b>scored</b> (weekly), the <b>daily enrichment</b> tick (with its OpenAI " +
    "content-filter fallback), the <b>monthly spotlight</b> publish, an operator <b>hierarchy rebuild</b> " +
    "(the durable-id stability path), and the daily <b>drift watchdog</b>. Numbers are real thresholds, not " +
    "illustrations; each step is a component you can find in views ② – ⑦.",
  legend: [
    { fill: "#e3faf3", stroke: "#0ca678", label: "Pipeline step (compute)" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "LLM / managed call (Bedrock · EventBridge)" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Record / artifact produced" },
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Trigger / external (operator · OpenAI · Teams)" },
  ],
  edgeLegend: [
    { color: "teal", label: "new paper scored · weekly" },
    { color: "amber", label: "daily enrichment · daily" },
    { color: "maroon", label: "monthly spotlight · monthly" },
    { color: "indigo", label: "hierarchy rebuild · on-demand" },
    { color: "violet", label: "drift watchdog · daily" },
  ],
  seeAlso: [
    { id: "aws-topology", label: "③ the schedulers that fire these" },
    { id: "stability-drift", label: "⑦ the durability machinery behind rebuild" },
  ],
  footnote:
    "These are the recurring events, not exhaustive. Real constants shown: screen <code>score_floor</code> 0.3, " +
    "spotlight dirty-gate ≥5 new pubs/subtopic, <code>spotlight_clone_overlap_min</code> 0.4 + scholar penalty " +
    "<code>λ=0.08</code>, the home-page rotation <code>SELECTION_SIZE</code> 10 at decay <code>τ=12wk</code>, and " +
    "<code>drift_uncovered_rate_alert</code> 0.05. The OpenAI <code>gpt-5.1</code> step in lane 2 fires <b>only</b> " +
    "when Bedrock content-filters the synopsis/impact call; the <code>STAGE#</code> skip in lane 4 short-circuits " +
    "any stage whose <code>input_hash</code> is unchanged. <b>Worked examples with real values:</b> " +
    "<code>docs/architecture/event-walkthroughs.md</code>.",
  source: "infra/eventbridge.json · score_publications.py · spotlight/rotation_selector.py · spotlight/critic.py · pipeline_hierarchy/ · pipeline_drift/evaluator.py · config/thresholds.json",
};
