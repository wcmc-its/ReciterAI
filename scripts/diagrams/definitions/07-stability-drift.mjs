/**
 * View 7 — Stability & drift (the run-to-run dynamics the structural views omit).
 * The "jiggling": re-clustering relabels subtopics, the upstream author set shifts,
 * and daily grounding moves — all of which would churn ids, ledes, and rankings on
 * every run. This view shows the three sources of jitter, the machinery that damps
 * each, the durable state that machinery reads/writes, and the watchdogs that
 * measure residual drift and alert.
 * Source: pipeline_hierarchy/subtopic_id_store.py, subtopic_reconcile.py,
 * subtopic_lifecycle.py, relabel_skip.py, spotlight/lede_skip.py,
 * spotlight/rotation_selector.py, pipeline_drift/evaluator.py, pipeline_onboarding/.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- sources of jitter -----
  relabel: { x: 44, y: 118, w: 226, h: 88, kind: "ext", title: "Re-cluster relabel",
             sub: ["subtopics get fresh labels", "each rebuild — id would move"] },
  authorchurn: { x: 44, y: 230, w: 226, h: 88, kind: "ext", title: "Upstream author churn",
                 sub: ["ReCiter accepted-PMID set", "shifts run-to-run"] },
  grounding: { x: 44, y: 342, w: 226, h: 88, kind: "ext", title: "Daily grounding churn",
               sub: ["synopsis + impact change daily", "would churn a good lede"] },

  // ----- stabilizers · identity -----
  durableid: { x: 330, y: 118, w: 226, h: 88, kind: "app", title: "Durable subtopic IDs",
               sub: ["match-or-mint · keeps id", "across rebuilds (#191)"] },
  reconcile: { x: 330, y: 230, w: 226, h: 88, kind: "app", title: "Reconcile ladder",
               sub: ["overlap → centroid → LLM", "verdicts cached by input-hash"] },
  lifecycle: { x: 330, y: 342, w: 226, h: 88, kind: "app", title: "Lifecycle",
               sub: ["mint floor · gc / retire", "split / merge lineage"] },

  // ----- stabilizers · compute & output -----
  skipcache: { x: 616, y: 118, w: 226, h: 88, kind: "app", title: "STAGE# skip-cache",
               sub: ["content-addressed input_hash", "unchanged ⇒ stage skips"] },
  ledeskip: { x: 616, y: 230, w: 226, h: 88, kind: "app", title: "Lede / relabel skip",
              sub: ["dual gate: overlap verdict", "+ grounding-PMID equality"] },
  rotation: { x: 616, y: 342, w: 226, h: 88, kind: "app", title: "Rotation selector",
              sub: ["exp. decay vs last-shown", "one subtopic per parent"] },

  // ----- watchdogs -----
  drift: { x: 902, y: 118, w: 226, h: 88, kind: "aws", title: "Drift evaluator",
           sub: ["daily DRIFT# rows", "uncovered_rate · stage-fails"] },
  onboard: { x: 902, y: 230, w: 226, h: 88, kind: "aws", title: "Onboarding detector",
             sub: ["gap scan + R9 churn", "baseline-gated"] },
  feedback: { x: 902, y: 342, w: 226, h: 88, kind: "app", title: "Feedback sweep",
              sub: ["FINDING# records", "equivalence checks"] },

  // ----- alert sinks -----
  teams: { x: 1180, y: 170, w: 160, h: 84, kind: "ext", title: "MS Teams", sub: ["drift alerts"] },
  github: { x: 1180, y: 300, w: 160, h: 84, kind: "ext", title: "GitHub issues", sub: ["onboarding gaps"] },

  // ----- durable state (DynamoDB single table) -----
  stagest: { x: 44, y: 584, w: 290, h: 80, kind: "data", title: "STAGE#", sub: ["run audit + skip-cache"] },
  idstore: { x: 377, y: 584, w: 290, h: 80, kind: "data", title: "Durable-ID store", sub: ["SUBTOPIC# id ↔ membership"] },
  sphist: { x: 710, y: 584, w: 290, h: 80, kind: "data", title: "SPOTLIGHT_HISTORY#", sub: ["last-shown per subtopic_id"] },
  driftrows: { x: 1043, y: 584, w: 290, h: 80, kind: "data", title: "DRIFT# · FINDING#", sub: ["drift + feedback rows"] },
};

const groups = [
  { x: 24, y: 84, w: 266, h: 380, kind: "ext", title: "Sources of run-to-run jitter" },
  { x: 310, y: 84, w: 266, h: 380, kind: "app", title: "Stabilizers · identity" },
  { x: 596, y: 84, w: 266, h: 380, kind: "app", title: "Stabilizers · compute & output" },
  { x: 882, y: 84, w: 266, h: 380, kind: "aws", title: "Watchdogs" },
  { x: 1162, y: 150, w: 200, h: 250, kind: "ext", title: "Alert sinks" },
  { x: 24, y: 548, w: 1338, h: 160, kind: "data", title: "Durable state · DynamoDB (single table reciterai)" },
];

const edges = [
  // jitter → the stabilizer that absorbs it
  { p0: A(nodes.relabel, "r", 0.5), p1: A(nodes.durableid, "l", 0.5), color: "teal", label: "relabel" },
  { p0: A(nodes.authorchurn, "r", 0.5), p1: A(nodes.reconcile, "l", 0.5), color: "teal", label: "membership shift" },
  // grounding reaches past identity into the output gate — routed along the clear channel below the boxes
  { p0: A(nodes.grounding, "r", 0.6), p1: A(nodes.ledeskip, "l", 0.85), color: "teal", dash: true, label: "daily grounding", lp: { x: 450, y: 474 }, points: [{ x: 300, y: 474 }, { x: 600, y: 474 }] },
  // identity ladder + the verdict it hands to the output gate
  { p0: A(nodes.durableid, "b", 0.5), p1: A(nodes.reconcile, "t", 0.5), color: "gray" },
  { p0: A(nodes.reconcile, "b", 0.5), p1: A(nodes.lifecycle, "t", 0.5), color: "gray" },
  { p0: A(nodes.reconcile, "r", 0.5), p1: A(nodes.ledeskip, "l", 0.4), color: "gray", label: "verdict reused" },
  // stabilizers persist to durable state (bottom-row boxes sit next to the band)
  { p0: A(nodes.lifecycle, "b", 0.5), p1: A(nodes.idstore, "t", 0.5), color: "amber", label: "durable ids" },
  { p0: A(nodes.rotation, "b", 0.5), p1: A(nodes.sphist, "t", 0.5), color: "amber", dash: true, label: "last-shown" },
  // watchdogs observe the run records, then emit
  { p0: A(nodes.skipcache, "r", 0.5), p1: A(nodes.drift, "l", 0.5), color: "gray", dash: true, label: "STAGE# rows" },
  { p0: A(nodes.drift, "r", 0.3), p1: A(nodes.teams, "l", 0.4), color: "gray", label: "alert" },
  { p0: A(nodes.onboard, "r", 0.5), p1: A(nodes.github, "l", 0.4), color: "gray", label: "issues" },
  { p0: A(nodes.drift, "r", 0.85), p1: A(nodes.driftrows, "t", 0.85), color: "amber", label: "DRIFT#", lp: { x: 1150, y: 400 }, points: [{ x: 1150, y: 193 }, { x: 1150, y: 560 }] },
  { p0: A(nodes.feedback, "b", 0.4), p1: A(nodes.driftrows, "t", 0.2), color: "amber", label: "FINDING#" },
];

export const spec = { id: "stability-drift", vb: [1380, 740], groups, nodes, edges };

export const meta = {
  nav: "⑦ Stability & drift",
  kicker: "View 7 · run-to-run dynamics",
  heading: "Stability & drift — keeping it from jiggling",
  dot: "#e8590c",
  blurb:
    "The dynamics the other six views freeze out. Three things <b>jiggle</b> every run: re-clustering " +
    "<b>relabels</b> subtopics, the upstream <b>author set churns</b>, and <b>daily grounding</b> (synopsis " +
    "+ impact) moves — each of which would otherwise churn subtopic ids, spotlight ledes, and rankings. A " +
    "band of <b>stabilizers</b> damps it: <b>durable ids</b> (match-or-mint keeps an id across rebuilds), the " +
    "<b>reconcile ladder</b> (overlap → centroid → LLM, cached by input-hash), the <b>STAGE# skip-cache</b>, " +
    "the <b>lede/relabel skip</b> gates, and the <b>rotation selector</b>. <b>Watchdogs</b> then measure the " +
    "residual drift and alert.",
  legend: [
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Jitter source / alert sink" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Stabilizer" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "Watchdog (daily Lambda)" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Durable state (DynamoDB)" },
  ],
  edgeLegend: [
    { color: "teal", label: "run-to-run jitter" },
    { color: "teal", dash: true, label: "daily grounding churn" },
    { color: "gray", label: "verdict / control" },
    { color: "amber", label: "persists / emits a record" },
    { color: "gray", dash: true, label: "observes runs / alerts" },
  ],
  seeAlso: [
    { id: "processing-pipeline", label: "② the stages these wrap" },
    { id: "publish-contract", label: "④ STAGE# and the record types" },
  ],
  footnote:
    "Honest mapping: <b>relabel</b> jitter is damped by the identity stabilizers; <b>daily grounding</b> by the " +
    "lede-skip gate (grounding-PMID equality); <b>author churn</b> is re-absorbed by membership reconcile and " +
    "independently flagged by the onboarding watchdog's R9 churn check. The durable-ID store is " +
    "<b>pipeline-internal</b> recognition memory — SPS still consumes slug ids; the slug→durable repoint is the " +
    "remaining brick-D step (#191). Every stabilizer is idempotent: an unchanged input <code>input_hash</code> " +
    "skips the stage entirely.",
  source: "pipeline_hierarchy/subtopic_id_store.py · subtopic_reconcile.py · subtopic_lifecycle.py · spotlight/lede_skip.py · spotlight/rotation_selector.py · pipeline_drift/evaluator.py · pipeline_onboarding/",
};
