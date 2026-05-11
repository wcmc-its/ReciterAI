/**
 * sps-etl-reference.ts — Reference fetch script for SPS hierarchy ETL
 *
 * ADAPT THIS — replace TODOs with your Prisma client, your state store,
 * your error handling.
 *
 * Fetches hierarchy.json from S3, validates against the co-published JSON Schema,
 * and projects subtopics into the MySQL Subtopic table. SPS coding agent: copy
 * this file to etl/hierarchy/index.ts, swap in your Prisma client, and adapt
 * the upsert block to your schema. Everything else (S3 fetch, manifest sha256
 * comparison, schema validation) ships ready.
 *
 * Audience: SPS coding agent (wcmc-its/Scholars-Profile-System)
 *
 * Required env vars:
 *   AWS_ACCESS_KEY_ID        — AWS access key (or use IAM role / instance profile)
 *   AWS_SECRET_ACCESS_KEY    — AWS secret key (or use IAM role / instance profile)
 *   AWS_DEFAULT_REGION       — AWS region (default: us-east-1)
 *   HIERARCHY_BUCKET         — S3 bucket name (default: wcmc-reciterai-hierarchy)
 *
 * Required npm deps:
 *   @aws-sdk/client-s3  ^3.x
 *   ajv                 ^8.x  (JSON Schema 2020-12 support via ajv/dist/2020)
 *
 * Usage:
 *   npm run etl:hierarchy
 *   node --import tsx/esm etl/hierarchy/index.ts
 *
 * Add to package.json scripts:
 *   "etl:hierarchy": "node --import tsx/esm etl/hierarchy/index.ts"
 *
 * IAM: the Lambda execution role for this ETL must have s3:GetObject and
 * s3:ListBucket on arn:aws:s3:::wcmc-reciterai-hierarchy/*. See
 * docs/aws-bucket-policy.json in ReciterAI -ReCiter-Integration for the
 * paste-ready resource-based bucket policy. Ask the bucket owner (Paul) to
 * attach it with <SPS_LAMBDA_ROLE_ARN> substituted for your role ARN.
 */
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import Ajv from "ajv/dist/2020"; // ajv v8+ with JSON Schema 2020-12 support

// ---------------------------------------------------------------------------
// Module-level env constants — use AWS SDK default credential chain; do NOT
// hardcode keys here. The SDK reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
// from the environment, or falls back to IAM role / instance profile.
// ---------------------------------------------------------------------------
const BUCKET = process.env.HIERARCHY_BUCKET ?? "wcmc-reciterai-hierarchy";
const REGION = process.env.AWS_DEFAULT_REGION ?? "us-east-1";

// ---------------------------------------------------------------------------
// Type interfaces — mirror hierarchy-schema.md (Phase 4 canonical source)
// ---------------------------------------------------------------------------

/**
 * A single subtopic within a parent topic.
 *
 * D-19 field split (LOCKED):
 *   display_name / short_description — UI-facing, synthesis-forbidden (see warning below)
 *   label / description             — LLM synthesis-canonical, verbatim-injected into prompts
 *
 * D-06 ID instability: subtopic IDs are stable per-recompute but unstable ACROSS
 * recomputes. Never persist them in a table that outlives a recompute cycle. The
 * ETL upsert keyed on `id` is fine — each ETL run replaces the prior value.
 */
interface SubtopicDef {
  id: string;
  label: string;
  description: string;
  display_name: string;       // D-19: UI card title (Title Case, max 6 words)
  short_description: string;  // D-19: UI card subtitle (noun-phrase, ≤140 chars)
  activity_count: number;     // integer — display only, not a retrieval threshold
  total_weight: number;       // sum of articleScores — use for retrieval thresholds
}

interface TopicEntry {
  subtopics: SubtopicDef[];
}

interface ExcludedTopicEntry {
  id: string;
  reason: string;        // e.g. "below cold-start floor"
  activity_count: number;
}

interface SeeAlsoEntry {
  from: string;    // source subtopic id (or topic id for topic-level links)
  to: string;      // target subtopic id
  reason: string;  // one-sentence rationale
}

interface HierarchyJson {
  version: "subtopic_v1";
  generated_at: string;         // ISO 8601 UTC
  taxonomy_version: string;     // e.g. "taxonomy_v2"
  excluded_topics: ExcludedTopicEntry[];
  topics: Record<string, TopicEntry>;
  see_also: SeeAlsoEntry[];
}

/** Six-field manifest — insertion order is canonical; do not reorder. */
interface HierarchyManifest {
  schema_version: string;    // semver of hierarchy.schema.json, e.g. "1.0.0"
  taxonomy_version: string;  // e.g. "taxonomy_v2"
  version: string;           // publish version: "v" + ISO-date, e.g. "v2026-05-06"
  generated_at: string;      // ISO 8601 UTC publish moment
  sha256: string;            // hex sha256 of hierarchy.json bytes before upload
  artifact_bytes: number;    // byte length of hierarchy.json
}

// ---------------------------------------------------------------------------
// S3 helpers
// ---------------------------------------------------------------------------

async function fetchText(s3: S3Client, key: string): Promise<string> {
  const resp = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  return resp.Body!.transformToString("utf-8");
}

// ---------------------------------------------------------------------------
// Main ETL flow
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  // Step 1: initialize S3 client using the AWS SDK default credential chain.
  // The SDK automatically reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or
  // falls back to IAM role / instance profile / ECS task role. No creds here.
  const s3 = new S3Client({ region: REGION });

  // Step 2: Fetch the latest manifest to determine whether the hierarchy has
  // changed since the last ETL run.
  const manifestText = await fetchText(s3, "latest/manifest.json");
  const manifest: HierarchyManifest = JSON.parse(manifestText);
  console.log(
    `Hierarchy manifest: version=${manifest.version}, ` +
    `schema=${manifest.schema_version}, sha256=${manifest.sha256.slice(0, 12)}…`
  );

  // Step 3: Short-circuit if hierarchy is unchanged since last ETL run.
  //
  // TODO: compare manifest.sha256 against your last_known_sha256 from MySQL
  //       etl_run table (or similar). Skip fetch + upsert if sha256 is unchanged.
  //
  //       const lastRun = await prisma.etlRun.findFirst({
  //         where: { source: "hierarchy" },
  //         orderBy: { createdAt: "desc" },
  //       });
  //       if (lastRun?.metadata?.sha256 === manifest.sha256) {
  //         console.log("Hierarchy unchanged — skip");
  //         return;
  //       }
  //
  // After a successful ETL run, persist manifest.sha256 AND manifest.version
  // so the next run can short-circuit:
  //       await prisma.etlRun.create({
  //         data: { source: "hierarchy", metadata: { sha256: manifest.sha256, version: manifest.version } },
  //       });

  // Step 4: Fetch schema for this SPECIFIC version — NOT latest/ — to guard
  // against schema drift during the 30-day breaking-change deprecation window.
  // Always fetch schema and hierarchy from the SAME manifest.version prefix.
  const schemaText = await fetchText(s3, `${manifest.version}/hierarchy.schema.json`);
  const schema = JSON.parse(schemaText);

  // Step 5: Fetch hierarchy.json from the same version-specific prefix.
  const hierarchyText = await fetchText(s3, `${manifest.version}/hierarchy.json`);
  const hierarchy: HierarchyJson = JSON.parse(hierarchyText);

  // Step 6: Validate hierarchy against schema (fail-fast — do NOT write to
  // MySQL if the schema is invalid). This mirrors the producer-side validation
  // gate in backfill_all.py --publish (D-16 consumer mirror).
  const ajv = new Ajv({ strict: false });
  const validate = ajv.compile(schema);
  if (!validate(hierarchy)) {
    console.error("Schema validation failed:", validate.errors);
    process.exit(1);
  }
  console.log("Schema validation passed.");

  // Step 7: Project hierarchy to MySQL Subtopic table.
  //
  // SECURITY (D-19): NEVER pass display_name or short_description into an LLM
  // prompt. They are UI-only strings; synthesis-canonical text lives in `label`
  // and `description`. See .planning/phases/04-subtopic-system/hierarchy-schema.md.
  //
  // Canonical Subtopic shape per docs/hierarchy-contract.md:
  //   id, label, display_name, short_description, parent_topic_id,
  //   activity_count, total_weight, description, source, refreshed_at
  //
  // D-06 ID instability: subtopic IDs change across recomputes. The upsert
  // pattern below is correct — do NOT assume IDs are stable beyond one ETL cycle.
  // Do NOT store subtopic IDs as foreign keys in other tables that outlive a run.
  let upserted = 0;
  for (const [topicId, topicEntry] of Object.entries(hierarchy.topics)) {
    for (const subtopic of topicEntry.subtopics) {
      // TODO: replace with actual prisma.subtopic.upsert(...)
      //
      //   await prisma.subtopic.upsert({
      //     where: { id: subtopic.id },
      //     update: {
      //       label:             subtopic.label,
      //       description:       subtopic.description,
      //       display_name:      subtopic.display_name || subtopic.label,
      //       short_description: subtopic.short_description,
      //       parent_topic_id:   topicId,
      //       activity_count:    subtopic.activity_count,
      //       total_weight:      subtopic.total_weight,
      //       source:            "hierarchy_etl",
      //       refreshed_at:      new Date(),
      //     },
      //     create: {
      //       id:                subtopic.id,
      //       label:             subtopic.label,
      //       description:       subtopic.description,
      //       display_name:      subtopic.display_name || subtopic.label,
      //       short_description: subtopic.short_description,
      //       parent_topic_id:   topicId,
      //       activity_count:    subtopic.activity_count,
      //       total_weight:      subtopic.total_weight,
      //       source:            "hierarchy_etl",
      //       refreshed_at:      new Date(),
      //     },
      //   });
      //
      // Note: use `subtopic.display_name || subtopic.label` for the UI title
      // (D-19 fallback — legacy artifacts before D-19 backfill have display_name === label
      // or display_name === ""; this guards against empty card titles).
      console.log(
        `  [DRY-RUN] UPSERT subtopic ${subtopic.id} → topic ${topicId} | ` +
        `"${subtopic.display_name || subtopic.label}"`
      );
      upserted++;
    }
  }

  console.log(
    `Hierarchy ETL complete: ${upserted} subtopics projected from ${manifest.version}`
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
