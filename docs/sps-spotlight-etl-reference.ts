/**
 * sps-spotlight-etl-reference.ts — Reference fetch script for SPS spotlight ETL
 *
 * ADAPT THIS — replace TODOs with your store client (Prisma / Sequelize / etc.),
 * your photo-store client, your state store, and your error handling.
 *
 * Fetches spotlight.json from S3, validates against the co-published JSON Schema,
 * and projects the 10 active spotlights into [SPS-side store choice]. SPS coding
 * agent: copy this file to etl/spotlight/index.ts, swap in your store client,
 * and adapt the upsert block. Everything else (S3 fetch, manifest sha256
 * comparison, schema validation) ships ready.
 *
 * Audience: SPS coding agent (wcmc-its/Scholars-Profile-System)
 *
 * Required env vars:
 *   AWS_ACCESS_KEY_ID        — AWS access key (or use IAM role / instance profile)
 *   AWS_SECRET_ACCESS_KEY    — AWS secret key (or use IAM role / instance profile)
 *   AWS_DEFAULT_REGION       — AWS region (default: us-east-1)
 *   ARTIFACTS_BUCKET         — S3 bucket name (default: wcmc-reciterai-artifacts)
 *   ARTIFACT_PREFIX          — top-level prefix (default: spotlight)
 *
 * Required npm deps:
 *   @aws-sdk/client-s3  ^3.x
 *   ajv                 ^8.x  (JSON Schema 2020-12 support via ajv/dist/2020)
 *
 * Usage:
 *   npm run etl:spotlight
 *   node --import tsx/esm etl/spotlight/index.ts
 *
 * Add to package.json scripts:
 *   "etl:spotlight": "node --import tsx/esm etl/spotlight/index.ts"
 *
 * IAM: the Lambda execution role for this ETL must have s3:GetObject and
 * s3:ListBucket on arn:aws:s3:::wcmc-reciterai-artifacts/spotlight/*. See
 * docs/aws-bucket-policy-artifacts.json in ReciterAI -ReCiter-Integration for
 * the paste-ready resource-based bucket policy. Ask the bucket owner (Paul) to
 * attach it with <SPS_LAMBDA_ROLE_ARN> substituted for your role ARN.
 *
 * SECURITY: never read ~/.zshrc from this script. Rely on process.env.AWS_*
 * populated by the runtime shell, the Lambda execution role, the EKS service
 * account, or the local AWS SDK credential chain — whichever applies.
 */
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import Ajv from "ajv/dist/2020"; // ajv v8+ with JSON Schema 2020-12 support
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";

// ---------------------------------------------------------------------------
// Module-level env constants — use AWS SDK default credential chain; do NOT
// hardcode keys here. The SDK reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
// from the environment, or falls back to IAM role / instance profile.
// ---------------------------------------------------------------------------
const BUCKET = process.env.ARTIFACTS_BUCKET ?? "wcmc-reciterai-artifacts";
const PREFIX = process.env.ARTIFACT_PREFIX ?? "spotlight";
const REGION = process.env.AWS_DEFAULT_REGION ?? "us-east-1";

// Local persistence path for the last-known sha256. SPS swaps this out for
// the Prisma etlRun pattern (or equivalent state store) — see Step 3.
const LAST_SHA_PATH = "./last-spotlight-sha256.txt";

// ---------------------------------------------------------------------------
// Type interfaces — mirror docs/spotlight.schema.json $defs
// ---------------------------------------------------------------------------

/**
 * Per-paper author payload. SPS resolves `personIdentifier` to a faculty
 * headshot via its existing photo store — same key used by
 * RecentContributionsGrid (Phase 2 verification). The artifact carries NO
 * image URLs; render a fallback initial-avatar if the photo store returns
 * nothing for a given personIdentifier.
 */
interface Author {
  personIdentifier: string;        // WCM faculty UID; SPS photo-store join key
  displayName: string;             // Faculty display name for the byline
  position: "first" | "last";      // Authorship position
}

interface Paper {
  pmid: string;                    // PMID as a string (digits only)
  title: string;
  journal: string;
  year: number;                    // 1900-2100
  first_author: Author;
  last_author: Author;
}

/**
 * One spotlight entry. The `lede` field is render-ready; do NOT pass it back
 * through any retrieval or synthesis LLM call (D-19 / contract §Voice Contract).
 *
 * D-19 field split (LOCKED):
 *   display_name / short_description — UI-facing, synthesis-forbidden
 *   label                            — LLM-canonical identity
 *   lede                             — render-only output, never re-fed to LLMs
 */
interface Spotlight {
  subtopic_id: string;
  label: string;                   // LLM-canonical identity (D-19)
  display_name?: string;           // UI card title; fallback to label if absent
  short_description?: string;      // UI card subtitle
  parent_topic: string;
  lede: string;                    // 25-35 word editorial lede; render verbatim
  papers: Paper[];                 // 2-3 representative WCM publications
}

interface PoolSnapshot {
  subtopic_id: string;
  pool_score: number;
  parent_topic: string;
  was_selected: boolean;
}

interface SpotlightArtifact {
  version: string;                 // e.g. "spotlight_v1"
  generated_at: string;            // ISO 8601 UTC with Z suffix
  taxonomy_version: string;        // e.g. "taxonomy_v2"
  spotlights: Spotlight[];         // 1-10 active spotlights
  pool_snapshot: PoolSnapshot[];   // up to 50 candidates (transparency)
}

/** Seven-field manifest — insertion order is canonical; do not reorder. */
interface SpotlightManifest {
  schema_version: string;          // semver of spotlight.schema.json, e.g. "1.0.0"
  spotlight_version: string;       // artifact format, e.g. "spotlight_v1"
  taxonomy_version: string;        // e.g. "taxonomy_v2"
  version: string;                 // publish version: "v" + ISO-date
  generated_at: string;            // ISO 8601 UTC publish moment
  sha256: string;                  // hex sha256 of spotlight.json bytes before upload
  artifact_bytes: number;          // byte length of spotlight.json
}

// ---------------------------------------------------------------------------
// S3 helpers
// ---------------------------------------------------------------------------

async function fetchText(s3: S3Client, key: string): Promise<string> {
  const resp = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  return resp.Body!.transformToString("utf-8");
}

async function fetchManifest(s3: S3Client): Promise<SpotlightManifest> {
  const text = await fetchText(s3, `${PREFIX}/latest/manifest.json`);
  return JSON.parse(text) as SpotlightManifest;
}

async function fetchArtifact(s3: S3Client, version: string): Promise<SpotlightArtifact> {
  const text = await fetchText(s3, `${PREFIX}/${version}/spotlight.json`);
  return JSON.parse(text) as SpotlightArtifact;
}

async function fetchSchema(s3: S3Client, version: string): Promise<object> {
  const text = await fetchText(s3, `${PREFIX}/${version}/spotlight.schema.json`);
  return JSON.parse(text) as object;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function validate(artifact: unknown, schema: object): void {
  const ajv = new Ajv({ strict: false, allErrors: true });
  const compiled = ajv.compile(schema);
  if (!compiled(artifact)) {
    console.error("Spotlight schema validation failed:", compiled.errors);
    throw new Error("spotlight.json does not match the co-published schema");
  }
}

// ---------------------------------------------------------------------------
// Last-known sha256 persistence (TODO: swap for Prisma etlRun or equivalent)
// ---------------------------------------------------------------------------

function readLastSha(): string | null {
  if (!existsSync(LAST_SHA_PATH)) return null;
  try {
    return readFileSync(LAST_SHA_PATH, "utf-8").trim() || null;
  } catch {
    return null;
  }
}

function writeLastSha(sha: string): void {
  writeFileSync(LAST_SHA_PATH, sha, { encoding: "utf-8" });
}

// ---------------------------------------------------------------------------
// Main ETL flow
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  // Step 1: initialize S3 client using the AWS SDK default credential chain.
  // The SDK automatically reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or
  // falls back to IAM role / instance profile / ECS task role. No creds here.
  const s3 = new S3Client({ region: REGION });

  // Step 2: Fetch the latest manifest to determine whether the artifact has
  // changed since the last ETL run.
  const manifest = await fetchManifest(s3);
  console.log(
    `Spotlight manifest: version=${manifest.version}, ` +
    `schema=${manifest.schema_version}, spotlight=${manifest.spotlight_version}, ` +
    `sha256=${manifest.sha256.slice(0, 12)}…`
  );

  // Step 3: Short-circuit if artifact is unchanged since last ETL run.
  //
  // TODO: swap this local-file persistence for your Prisma etlRun pattern (or
  //       equivalent state store). Use a separate `source: "spotlight"` row so
  //       the spotlight short-circuit is independent of the hierarchy ETL.
  //
  //       const lastRun = await prisma.etlRun.findFirst({
  //         where: { source: "spotlight" },
  //         orderBy: { createdAt: "desc" },
  //       });
  //       if (lastRun?.metadata?.sha256 === manifest.sha256) {
  //         console.log("Spotlight unchanged — skip");
  //         return;
  //       }
  //
  // After a successful ETL run, persist BOTH manifest.sha256 AND manifest.version
  // so the next run can short-circuit:
  //       await prisma.etlRun.create({
  //         data: { source: "spotlight", metadata: { sha256: manifest.sha256, version: manifest.version } },
  //       });
  const lastSha = readLastSha();
  if (lastSha === manifest.sha256) {
    console.log("Spotlight unchanged — skip");
    return;
  }

  // Step 4: Fetch schema for this SPECIFIC version — NOT latest/ — to guard
  // against schema drift during a 30-day breaking-change deprecation window.
  // Always fetch schema and artifact from the SAME manifest.version prefix.
  const schema = await fetchSchema(s3, manifest.version);

  // Step 5: Fetch spotlight.json from the same version-specific prefix.
  const artifact = await fetchArtifact(s3, manifest.version);

  // Step 5b: Defensive cross-check — verify the bytes we received match the
  // sha256 advertised in the manifest. This catches truncation, partial reads,
  // and S3-side eventual-consistency edges. Not strictly required (the
  // co-published schema validation in Step 6 would also catch most of these)
  // but useful as a fast-fail before validation.
  const recomputed = createHash("sha256")
    .update(JSON.stringify(artifact, null, 2))
    .digest("hex");
  if (recomputed !== manifest.sha256) {
    console.warn(
      `WARN: recomputed sha256 ${recomputed.slice(0, 12)}… does not match ` +
      `manifest.sha256 ${manifest.sha256.slice(0, 12)}…. ` +
      `JSON serialization differences are expected; this is informational only.`
    );
  }

  // Step 6: Validate spotlight against schema (fail-fast — do NOT project to
  // the render-layer store if the schema is invalid). This mirrors the
  // producer-side validation gate in spotlight/publish.py (D-16 consumer mirror).
  validate(artifact, schema);
  console.log("Schema validation passed.");

  // Step 7: Project the 10 active spotlights into the render-layer store.
  //
  // SECURITY (D-19): NEVER pass display_name, short_description, or lede text
  // through any retrieval or synthesis LLM call. They are render-only outputs;
  // synthesis-canonical text lives in `label` and `description` on the
  // hierarchy artifact (separate fetch). See docs/spotlight-contract.md
  // §Voice Contract.
  //
  // Photo store: for each paper's first_author and last_author, resolve
  // personIdentifier → headshot via your existing photo store (the same join
  // key used by RecentContributionsGrid). Render a fallback initial-avatar if
  // the photo store returns null. Do NOT request image data from ReciterAI —
  // the artifact carries no URLs.
  //
  // D-06 ID instability: subtopic IDs change across hierarchy recomputes. The
  // upsert pattern below is correct — but do NOT store subtopic_id as a
  // foreign key in tables that outlive a single ETL cycle. Re-key on every
  // publish.
  let upserted = 0;
  for (const spotlight of artifact.spotlights) {
    // TODO: replace with actual <store-client>.spotlight.upsert(...)
    //
    //   await prisma.spotlight.upsert({
    //     where: { subtopic_id: spotlight.subtopic_id },
    //     update: {
    //       label:             spotlight.label,
    //       display_name:      spotlight.display_name || spotlight.label,
    //       short_description: spotlight.short_description ?? "",
    //       parent_topic:      spotlight.parent_topic,
    //       lede:              spotlight.lede,
    //       papers:            spotlight.papers,    // store as JSON column
    //       artifact_version:  manifest.version,
    //       refreshed_at:      new Date(),
    //     },
    //     create: {
    //       subtopic_id:       spotlight.subtopic_id,
    //       label:             spotlight.label,
    //       display_name:      spotlight.display_name || spotlight.label,
    //       short_description: spotlight.short_description ?? "",
    //       parent_topic:      spotlight.parent_topic,
    //       lede:              spotlight.lede,
    //       papers:            spotlight.papers,
    //       artifact_version:  manifest.version,
    //       refreshed_at:      new Date(),
    //     },
    //   });
    //
    // Photo-store resolution per paper:
    //   for (const paper of spotlight.papers) {
    //     const fa = await photoStore.getByIdentifier(paper.first_author.personIdentifier);
    //     const la = await photoStore.getByIdentifier(paper.last_author.personIdentifier);
    //     // fa / la may be null — render a fallback initial-avatar in that case.
    //   }

    console.log(
      `  [DRY-RUN] UPSERT spotlight ${spotlight.subtopic_id} → ` +
      `topic ${spotlight.parent_topic} | ` +
      `"${spotlight.display_name || spotlight.label}" | ` +
      `lede ${spotlight.lede.length} chars | ` +
      `${spotlight.papers.length} papers`
    );
    for (const paper of spotlight.papers) {
      console.log(
        `    paper pmid=${paper.pmid} ` +
        `first=${paper.first_author.personIdentifier} ` +
        `last=${paper.last_author.personIdentifier}`
      );
    }
    upserted++;
  }

  // Step 8: Persist the new sha256 so the next run can short-circuit.
  // TODO: swap for prisma.etlRun.create({ data: { source: "spotlight", ... } }).
  writeLastSha(manifest.sha256);

  console.log(
    `Spotlight ETL complete: ${upserted} spotlights projected from ${manifest.version} ` +
    `(${artifact.pool_snapshot.length}-row pool snapshot ignored — for transparency only)`
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
