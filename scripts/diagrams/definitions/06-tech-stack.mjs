/**
 * View 6 — Technical stack (the technology layers, not the data flow).
 * Six layers from the application code down to the data plane, config, and dev
 * tooling, with what each technology is used for. A clean "stack" view: the
 * banding carries the meaning; a single runtime spine (Fargate invokes Bedrock
 * above it and reads/writes DynamoDB below it) shows how the layers connect.
 * Source: requirements.txt, Dockerfile, infra/ecs_task_definition.json,
 * infra/eventbridge.json, utils/bedrock_client.py, utils/s3_client.py,
 * utils/dynamodb_helpers.py, config/.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- layer 1: language & libraries -----
  py: { x: 60, y: 88, w: 170, h: 60, kind: "app", title: "Python 3.12", sub: ["python:3.12-slim image"] },
  boto: { x: 262, y: 88, w: 170, h: 60, kind: "ext", title: "boto3", sub: ["AWS SDK"] },
  oai: { x: 464, y: 88, w: 170, h: 60, kind: "ext", title: "openai ≥ 2.0", sub: ["OpenAI SDK"] },
  sa: { x: 666, y: 88, w: 170, h: 60, kind: "ext", title: "SQLAlchemy + PyMySQL", sub: ["ReciterDB reads"] },
  js: { x: 868, y: 88, w: 170, h: 60, kind: "ext", title: "jsonschema", sub: ["contract validation"] },
  yaml: { x: 1070, y: 88, w: 170, h: 60, kind: "ext", title: "PyYAML · tqdm", sub: ["config · progress"] },

  // ----- layer 2: AI / inference -----
  bed: { x: 60, y: 202, w: 210, h: 60, kind: "aws", title: "AWS Bedrock", sub: ["managed LLM inference"] },
  son: { x: 302, y: 202, w: 210, h: 60, kind: "aws", title: "Claude Sonnet 4.6", sub: ["dense scoring · synopsis"] },
  hai: { x: 544, y: 202, w: 210, h: 60, kind: "aws", title: "Claude Haiku 4.5", sub: ["screening · assignment"] },
  opu: { x: 786, y: 202, w: 210, h: 60, kind: "aws", title: "Claude Opus 4.7", sub: ["spotlight lede"] },
  oai2: { x: 1028, y: 202, w: 210, h: 60, kind: "ext", title: "OpenAI gpt-5.1", sub: ["content-filter fallback"] },

  // ----- layer 3: compute & orchestration -----
  far: { x: 60, y: 316, w: 210, h: 60, kind: "app", title: "ECS Fargate", sub: ["daily enrichment task"] },
  eb: { x: 302, y: 316, w: 210, h: 60, kind: "aws", title: "EventBridge", sub: ["5 cron rules"] },
  sfn: { x: 544, y: 316, w: 210, h: 60, kind: "aws", title: "Step Functions", sub: ["weekly hot path"] },
  lam: { x: 786, y: 316, w: 210, h: 60, kind: "aws", title: "AWS Lambda ×3", sub: ["spotlight · drift · onboarding"] },
  dok: { x: 1028, y: 316, w: 210, h: 60, kind: "app", title: "Docker", sub: ["python:3.12-slim image"] },

  // ----- layer 4: data & storage -----
  ddb: { x: 60, y: 430, w: 270, h: 60, kind: "data", title: "DynamoDB", sub: ["single table · 3 GSIs"] },
  s3h: { x: 363, y: 430, w: 270, h: 60, kind: "data", title: "S3 · hierarchy", sub: ["wcmc-reciterai-hierarchy"] },
  s3a: { x: 666, y: 430, w: 270, h: 60, kind: "data", title: "S3 · artifacts", sub: ["wcmc-reciterai-artifacts"] },
  rdb: { x: 969, y: 430, w: 270, h: 60, kind: "ext", title: "ReciterDB", sub: ["MariaDB · read-only source"] },

  // ----- layer 5: config, secrets & IaC -----
  sm: { x: 60, y: 544, w: 360, h: 60, kind: "aws", title: "Secrets Manager", sub: ["DB · Bedrock token · Teams"] },
  cfg: { x: 470, y: 544, w: 360, h: 60, kind: "ext", title: "config/*.json · *.yaml", sub: ["thresholds + JSON Schema · llm_prices"] },
  iac: { x: 880, y: 544, w: 360, h: 60, kind: "ext", title: "Single-file JSON IaC", sub: ["eventbridge · ECS task · IAM (D-10)"] },

  // ----- layer 6: dev, CI & delivery -----
  pyt: { x: 60, y: 658, w: 360, h: 60, kind: "ext", title: "pytest", sub: ["aws / mariadb markers · mock CI"] },
  gha: { x: 470, y: 658, w: 360, h: 60, kind: "ext", title: "GitHub Actions", sub: ["axis2-producer-gate"] },
  tea: { x: 880, y: 658, w: 360, h: 60, kind: "ext", title: "MS Teams", sub: ["alert webhook"] },
};

const groups = [
  { x: 40, y: 64, w: 1220, h: 100, kind: "app", title: "Language & libraries" },
  { x: 40, y: 178, w: 1220, h: 100, kind: "aws", title: "AI / inference" },
  { x: 40, y: 292, w: 1220, h: 100, kind: "app", title: "Compute & orchestration" },
  { x: 40, y: 406, w: 1220, h: 100, kind: "data", title: "Data & storage" },
  { x: 40, y: 520, w: 1220, h: 100, kind: "aws", title: "Config, secrets & IaC" },
  { x: 40, y: 634, w: 1220, h: 100, kind: "ext", title: "Dev, CI & delivery" },
];

// A single runtime spine through the left column: the Fargate task is triggered by
// cron, invokes the models above it, and reads/writes the data plane below it.
const edges = [
  { p0: A(nodes.eb, "l", 0.5), p1: A(nodes.far, "r", 0.5), color: "green", label: "cron" },
  { p0: A(nodes.far, "t", 0.5), p1: A(nodes.bed, "b", 0.5), color: "violet", label: "invokes" },
  { p0: A(nodes.far, "b", 0.5), p1: A(nodes.ddb, "t", 0.389), color: "amber", label: "r/w" },
];

export const spec = { id: "tech-stack", vb: [1300, 760], groups, nodes, edges };

export const meta = {
  nav: "⑥ Tech stack",
  kicker: "View 6 · the technology layers",
  heading: "Technical stack",
  dot: "#7048e8",
  blurb:
    "Every technology, grouped by layer. <b>Pure Python 3.12</b> (boto3, the OpenAI SDK, SQLAlchemy, " +
    "jsonschema) calls <b>AWS Bedrock</b> for Claude inference, with an OpenAI <code>gpt-5.1</code> " +
    "fallback. It runs as a <b>Docker</b> image on <b>ECS Fargate</b> and three <b>Lambdas</b>, " +
    "orchestrated by <b>Step Functions</b> + <b>EventBridge</b> cron; persists to <b>DynamoDB</b> and " +
    "<b>S3</b> while reading the corpus from <b>ReciterDB</b>; and is configured by Secrets Manager + " +
    "checked-in JSON/YAML. There is <b>no runtime API</b> — it publishes artifacts and exits.",
  legend: [
    { fill: "#e3faf3", stroke: "#0ca678", label: "App code / compute" },
    { fill: "#f0ebff", stroke: "#7048e8", label: "AWS managed service" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Data store" },
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "Library / external / config" },
  ],
  edgeLegend: [
    { color: "green", label: "scheduled trigger (cron)" },
    { color: "violet", label: "invokes Bedrock" },
    { color: "amber", label: "reads / writes data" },
  ],
  seeAlso: [
    { id: "aws-topology", label: "③ these services wired up at runtime" },
    { id: "functional-overview", label: "⑤ what the stack is used for" },
  ],
  footnote:
    "Infrastructure is <b>single-file JSON</b> (D-10) — <code>infra/eventbridge.json</code>, the ECS task " +
    "definition, and IAM policies — applied with shell scripts until the CDK threshold fires. <b>Bedrock auth " +
    "differs by compute:</b> Lambdas use their IAM role; the Fargate task uses a long-lived " +
    "<code>AWS_BEARER_TOKEN_BEDROCK</code>. CI (GitHub Actions) runs only the mock unit tests; the " +
    "<code>aws</code> / <code>mariadb</code> suites are opt-in.",
  source: "requirements.txt · Dockerfile · infra/ecs_task_definition.json · infra/eventbridge.json · utils/bedrock_client.py · config/",
};
