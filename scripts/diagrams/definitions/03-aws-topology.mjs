/**
 * View 3 — AWS runtime topology (how it's deployed and scheduled).
 * The five EventBridge cron rules, their compute targets, and the AWS data /
 * model plane + external endpoints those targets reach. Single-file IaC (D-10).
 * Source: infra/eventbridge.json, infra/ecs_task_definition.json,
 * utils/bedrock_client.py, utils/s3_client.py, utils/dynamodb_helpers.py.
 */
import { A } from "../lib.mjs";

const nodes = {
  // ----- left: triggers + source -----
  eb: { x: 44, y: 124, w: 252, h: 184, kind: "aws", title: "EventBridge",
        sub: ["hot · cron(0 12 ? * MON *)", "enrichment · cron(0 11 * * ? *)",
              "onboarding · cron(0 13 * * ? *)", "drift · cron(0 14 * * ? *)", "spotlight · cron(0 13 1 * ? *)"] },
  rdb: { x: 44, y: 364, w: 252, h: 72, kind: "ext", title: "ReciterDB",
         sub: ["MariaDB · in-VPC read"] },

  // ----- compute targets -----
  sfn: { x: 360, y: 96, w: 288, h: 84, kind: "aws", title: "Step Functions · hot-path",
         sub: ["score → assign → top_topic → rollup", "weekly delta"] },
  ecs: { x: 360, y: 208, w: 288, h: 84, kind: "app", title: "ECS Fargate · enrichment",
         sub: ["run_daily_enrichment", "synopsis + impact"] },
  lam1: { x: 360, y: 320, w: 288, h: 64, kind: "aws", title: "Lambda · spotlight-orchestrator",
          sub: ["dirty-gate → backfill_spotlight"] },
  lam2: { x: 360, y: 400, w: 288, h: 64, kind: "aws", title: "Lambda · drift-evaluator",
          sub: ["events → DRIFT# + alerts"] },
  lam3: { x: 360, y: 480, w: 288, h: 64, kind: "aws", title: "Lambda · onboarding-detector",
          sub: ["gap scan → GitHub issues"] },

  // ----- AWS data / model plane -----
  bedrock: { x: 720, y: 96, w: 240, h: 84, kind: "aws", title: "AWS Bedrock",
             sub: ["Sonnet 4.6 · Haiku 4.5", "Opus 4.7"] },
  ddb: { x: 720, y: 208, w: 240, h: 80, kind: "data", title: "DynamoDB · reciterai",
         sub: ["single table · 3 GSIs"] },
  s3: { x: 720, y: 320, w: 240, h: 80, kind: "data", title: "S3",
        sub: ["reciterai-hierarchy", "reciterai-artifacts"] },
  sm: { x: 720, y: 432, w: 240, h: 84, kind: "aws", title: "Secrets Manager",
        sub: ["DB · Bedrock token · OpenAI", "Teams webhook"] },

  // ----- external / egress + consumer -----
  openai: { x: 1040, y: 96, w: 236, h: 76, kind: "ext", title: "OpenAI",
            sub: ["gpt-5.1 · content-filter fallback"] },
  teams: { x: 1040, y: 208, w: 236, h: 80, kind: "ext", title: "MS Teams",
           sub: ["alerts via webhook"] },
  sps: { x: 1040, y: 360, w: 236, h: 120, kind: "net", title: "Scholars Profile System",
         sub: ["pulls S3 + DynamoDB", "on its own ETL schedule"] },
};

const groups = [
  { x: 28, y: 104, w: 284, h: 212, kind: "aws", title: "Scheduler" },
  { x: 28, y: 344, w: 284, h: 112, kind: "ext", title: "Source" },
  { x: 344, y: 80, w: 320, h: 480, kind: "app", title: "Compute targets" },
  { x: 704, y: 80, w: 272, h: 452, kind: "aws", title: "AWS data / model plane" },
  { x: 1024, y: 80, w: 272, h: 452, kind: "net", title: "External + consumer" },
];

const edges = [
  // schedules → targets
  { p0: A(nodes.eb, "r", 0.12), p1: A(nodes.sfn, "l", 0.4), color: "green", label: "weekly" },
  { p0: A(nodes.eb, "r", 0.32), p1: A(nodes.ecs, "l", 0.4), color: "green", label: "daily" },
  { p0: A(nodes.eb, "r", 0.55), p1: A(nodes.lam1, "l", 0.5), color: "green", label: "monthly" },
  { p0: A(nodes.eb, "r", 0.74), p1: A(nodes.lam2, "l", 0.5), color: "green", label: "daily" },
  { p0: A(nodes.eb, "r", 0.92), p1: A(nodes.lam3, "l", 0.5), color: "green", label: "daily" },
  // source reads
  { p0: A(nodes.rdb, "r", 0.25), p1: A(nodes.sfn, "l", 0.75), color: "teal", label: "corpus" },
  { p0: A(nodes.rdb, "r", 0.55), p1: A(nodes.ecs, "l", 0.78), color: "teal", label: "delta" },
  { p0: A(nodes.rdb, "r", 0.9), p1: A(nodes.lam3, "l", 0.5), color: "teal", label: "gap scan" },
  // targets → plane
  { p0: A(nodes.sfn, "r"), p1: A(nodes.bedrock, "l", 0.35), color: "violet", label: "invoke" },
  { p0: A(nodes.sfn, "r", 0.7), p1: A(nodes.ddb, "l", 0.25), color: "amber" },
  { p0: A(nodes.ecs, "r"), p1: A(nodes.bedrock, "l", 0.75), color: "violet" },
  { p0: A(nodes.ecs, "r", 0.7), p1: A(nodes.ddb, "l", 0.6), color: "amber", label: "r/w" },
  // secrets: exit ECS right, drop down the inter-group channel (clear of the Lambda column), into Secrets Mgr.
  { p0: A(nodes.ecs, "r", 0.9), p1: A(nodes.sm, "l", 0.4), color: "violet", dash: true, label: "secrets", lp: { x: 676, y: 410 }, points: [{ x: 676, y: 284 }, { x: 676, y: 474 }] },
  { p0: A(nodes.lam1, "r", 0.5), p1: A(nodes.ddb, "l", 0.85), color: "amber" },
  { p0: A(nodes.lam1, "r", 0.2), p1: A(nodes.s3, "l", 0.4), color: "maroon", label: "publish" },
  { p0: A(nodes.lam2, "r", 0.5), p1: A(nodes.ddb, "l", 0.95), color: "amber", points: [{ x: 700, y: 432 }] },
  // egress (overhead route across the top, clear of the SFN box and group title tabs)
  { p0: A(nodes.ecs, "t", 0.95), p1: A(nodes.openai, "t", 0.5), color: "gray", dash: true, label: "fallback", lp: { x: 900, y: 50 }, points: [{ x: 690, y: 196 }, { x: 690, y: 50 }, { x: 1158, y: 50 }] },
  // alert: up into the clear corridor between DynamoDB (bottom 288) and S3 (top 320), across to Teams.
  { p0: A(nodes.lam2, "r", 0.8), p1: A(nodes.teams, "l", 0.7), color: "gray", dash: true, label: "alert", lp: { x: 858, y: 304 }, points: [{ x: 690, y: 304 }, { x: 1000, y: 304 }] },
  // plane → consumer
  { p0: A(nodes.s3, "r"), p1: A(nodes.sps, "l", 0.3), color: "maroon", label: "consume" },
  { p0: A(nodes.ddb, "r", 0.8), p1: A(nodes.sps, "l", 0.7), color: "maroon", points: [{ x: 1000, y: 360 }] },
];

export const spec = { id: "aws-topology", vb: [1320, 600], groups, nodes, edges };

export const meta = {
  nav: "③ AWS topology",
  kicker: "View 3 · how it's deployed",
  heading: "AWS runtime topology",
  dot: "#7048e8",
  blurb:
    "The deployed shape: <b>five EventBridge cron rules</b> drive a Step Functions hot path, a Fargate " +
    "enrichment task, and three Lambdas. They reach <b>Bedrock</b> (models), <b>DynamoDB</b> (state), " +
    "<b>S3</b> (artifacts) and <b>Secrets Manager</b> (config), read the corpus from <b>ReciterDB</b>, " +
    "fall back to <b>OpenAI</b> on a content filter, and alert <b>Teams</b>. Infrastructure is single-file " +
    "JSON (D-10) until the CDK migration threshold fires.",
  legend: [
    { fill: "#f0ebff", stroke: "#7048e8", label: "AWS managed (EventBridge / SFN / Lambda / Bedrock)" },
    { fill: "#e3faf3", stroke: "#0ca678", label: "Fargate task" },
    { fill: "#fff4d6", stroke: "#f08c00", label: "Data store" },
    { fill: "#f1f3f5", stroke: "#adb5bd", label: "External" },
    { fill: "#e7ecff", stroke: "#4263eb", label: "Consumer" },
  ],
  edgeLegend: [
    { color: "green", label: "scheduled trigger" },
    { color: "teal", label: "reads ReciterDB" },
    { color: "violet", label: "invokes Bedrock · secrets" },
    { color: "amber", label: "DynamoDB r/w" },
    { color: "maroon", label: "publish / consumed by SPS" },
    { color: "gray", dash: true, label: "egress (OpenAI, Teams)" },
  ],
  seeAlso: [
    { id: "processing-pipeline", label: "② the per-stage data flow inside these targets" },
    { id: "publish-contract", label: "④ the S3 + DynamoDB hand-off" },
  ],
  footnote:
    "<b>Bedrock auth differs by compute:</b> Lambdas use the task IAM role; the Fargate enrichment task " +
    "authenticates with a long-lived <code>AWS_BEARER_TOKEN_BEDROCK</code> (no <code>bedrock:InvokeModel</code> " +
    "grant on its role — a missing token fails loud). All schedules are tunable in " +
    "<code>infra/eventbridge.json</code> and applied with <code>scripts/deploy_cron.sh</code>.",
  source: "infra/eventbridge.json · infra/ecs_task_definition.json · utils/bedrock_client.py · utils/s3_client.py · scripts/deploy_cron.sh",
};
