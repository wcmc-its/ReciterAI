/**
 * View 5 — Functional overview (what ReciterAI actually does, in plain terms).
 * Not the scripts or the AWS plumbing (those are views 2 & 3) — the *capabilities*,
 * each framed by the question it answers: for every WCM paper and every scholar,
 * what is it about, where does it sit in the research landscape, and what's worth
 * surfacing. Reads the corpus, understands each publication on three axes, rolls
 * the labelled papers up into faculty profiles, curates a spotlight, and publishes.
 * Source: README.md, ARCHITECTURE.md (two-axis classification),
 * docs/topic-subtopic-assignment.md, docs/tools-a2-architecture.md, spotlight/.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- left: what it reads -----
  corpus: { x: 44, y: 188, w: 212, h: 82, kind: "ext", title: "Publications",
            sub: ["WCM corpus · every faculty paper", "PubMed → ReCiter → ReciterDB"], chip: { tone: "nightly", text: "nightly" } },
  taxonomy: { x: 44, y: 300, w: 212, h: 78, kind: "ext", title: "Domain map",
              sub: ["67 domains · human-approved", "the fixed frame of reference"], chip: { tone: "annual", text: "frozen" } },

  // ----- middle-left: understand each publication (the three axes + enrichment) -----
  classify: { x: 342, y: 134, w: 206, h: 84, kind: "app", title: "Classify by domain",
              sub: ["“What field is this in?”", "Axis 1 · ~67 topics"] },
  theme: { x: 342, y: 232, w: 206, h: 84, kind: "app", title: "Assign a theme",
           sub: ["“Which theme within it?”", "Axis 1.5 · subtopics"] },
  methods: { x: 342, y: 330, w: 206, h: 84, kind: "app", title: "Identify methods",
             sub: ["“What tools & methods?”", "Axis 2 · method families"] },
  summarize: { x: 342, y: 428, w: 206, h: 84, kind: "app", title: "Summarize & rate",
               sub: ["one-line plain synopsis", "impact score · 0–100"] },

  // ----- middle-right: roll up to people, then curate -----
  profile: { x: 652, y: 224, w: 196, h: 96, kind: "app", title: "Build faculty profile",
             sub: ["“What does this scholar", "work on?” — ranked themes"] },
  spotlight: { x: 652, y: 352, w: 196, h: 96, kind: "app", title: "Curate a spotlight",
               sub: ["pick the strongest theme,", "write a short lede"] },

  // ----- right: publish + who sees it -----
  publish: { x: 930, y: 224, w: 212, h: 96, kind: "data", title: "Publish artifacts",
             sub: ["hierarchy + per-paper labels", "faculty profiles + spotlights"] },
  consumer: { x: 930, y: 352, w: 212, h: 110, kind: "net", title: "Public faculty profiles",
              sub: ["Scholars Profile System", "~9,000 scholars · public site"] },
};

const groups = [
  { x: 24, y: 150, w: 252, h: 252, kind: "ext", title: "Source" },
  { x: 320, y: 96, w: 250, h: 432, kind: "app", title: "Understand every publication" },
  { x: 632, y: 188, w: 236, h: 300, kind: "app", title: "Profile & highlight" },
  { x: 910, y: 188, w: 252, h: 300, kind: "net", title: "Publish & who sees it" },
];

const edges = [
  // reads
  { p0: A(nodes.corpus, "r", 0.5), p1: A(nodes.classify, "l", 0.5), color: "teal", label: "each paper" },
  { p0: A(nodes.corpus, "r", 0.7), p1: A(nodes.methods, "l", 0.3), color: "teal" },
  { p0: A(nodes.taxonomy, "r", 0.5), p1: A(nodes.classify, "l", 0.85), color: "teal", dash: true, label: "domain map" },
  // inside "understand": a topic must be picked before its theme
  { p0: A(nodes.classify, "b", 0.5), p1: A(nodes.theme, "t", 0.5), color: "gray", label: "within the field" },
  // labelled, rated papers roll up to the person (theme + summarize stand in for the whole band)
  { p0: A(nodes.theme, "r", 0.5), p1: A(nodes.profile, "l", 0.3), color: "gray", label: "labelled papers" },
  { p0: A(nodes.summarize, "r", 0.5), p1: A(nodes.profile, "l", 0.85), color: "gray" },
  // profile → spotlight → publish → public
  { p0: A(nodes.profile, "b", 0.5), p1: A(nodes.spotlight, "t", 0.5), color: "gray", label: "strongest theme" },
  { p0: A(nodes.profile, "r", 0.5), p1: A(nodes.publish, "l", 0.3), color: "amber", label: "profiles" },
  { p0: A(nodes.spotlight, "r", 0.5), p1: A(nodes.publish, "l", 0.85), color: "amber", label: "spotlights" },
  { p0: A(nodes.publish, "b", 0.5), p1: A(nodes.consumer, "t", 0.5), color: "maroon", label: "to the public site" },
];

export const spec = { id: "functional-overview", vb: [1300, 600], groups, nodes, edges };

export const meta = {
  nav: "⑤ Functional overview",
  kicker: "View 5 · what it actually does",
  heading: "Functional overview — how ReciterAI works",
  dot: "#1098ad",
  blurb:
    "The plain-English picture: ReciterAI reads <b>every WCM publication</b> and, against a frozen " +
    "<b>research-domain map</b>, <b>understands each paper</b> on three axes — its domain (Axis 1), its " +
    "theme within that domain (Axis 1.5), and the tools &amp; methods it used (Axis 2) — and writes a " +
    "one-line synopsis plus an impact score. The labelled papers <b>roll up into a profile</b> for each " +
    "scholar; a <b>spotlight</b> picks the strongest theme and writes a short lede; and everything is " +
    "<b>published</b> to the public faculty profiles. Each box names the question it answers.",
  legend: [
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Source (corpus + taxonomy)" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "What ReciterAI does" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Published artifacts" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "Public profiles (SPS)" },
  ],
  edgeLegend: [
    { color: "teal", label: "reads the corpus" },
    { color: "gray", label: "labels & rolls up" },
    { color: "amber", label: "publishes" },
    { color: "maroon", label: "shown to the public" },
    { color: "teal", dash: true, label: "the fixed taxonomy" },
  ],
  seeAlso: [
    { id: "processing-pipeline", label: "② the same flow as scripts + models" },
    { id: "publish-contract", label: "④ exactly what gets published" },
    { id: "tech-stack", label: "⑥ the technology behind it" },
  ],
  footnote:
    "Three orthogonal axes describe each paper: <b>Axis 1</b> (~67 domain topics, inductive + human-approved), " +
    "<b>Axis 1.5</b> (subtopics within each domain), and <b>Axis 2</b> (a methods &amp; tools taxonomy, built " +
    "separately). The domain map is generated once and <b>frozen</b> — papers are scored against it, never " +
    "the other way around. ReciterAI is upstream of the Scholars Profile System and ships independently.",
  source: "README.md · ARCHITECTURE.md · docs/topic-subtopic-assignment.md · docs/tools-a2-architecture.md · spotlight/",
};
