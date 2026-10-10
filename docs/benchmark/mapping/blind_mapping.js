export const meta = {
  name: 'research-area-benchmark-mapping',
  description: 'Blind double-mapping of 67 research areas to CARE plan items, NIH ICs and NIH RCDC categories, then adjudication',
  phases: [
    { title: 'Map', detail: 'two independent mappers per frame (reference-first vs area-first)' },
    { title: 'Adjudicate', detail: 'third pass resolves pairs the mappers disagree on' },
  ],
}

// Absolute path to this checkout's docs/benchmark/inputs (agents read the CSVs from disk).
const IN = '<repo>/docs/benchmark/inputs'
const AREAS = `${IN}/research_areas.csv`
const FRAMES = [
  { key: 'B', file: `${IN}/care_plan_items.csv`, what: 'items from Weill Cornell Medicine\'s CARE Strategic Plan 2026-2029. Use ONLY rows with in_scope=yes (ignore in_scope=no rows entirely)', refChunks: 1, areaChunks: 1 },
  { key: 'C1', file: `${IN}/nih_institutes_centers.csv`, what: 'the 24 grant-funding NIH Institutes and Centers (map by each IC\'s statutory research mission)', refChunks: 1, areaChunks: 1 },
  { key: 'C2', file: `${IN}/nih_rcdc_categories.csv`, what: 'NIH RCDC (Research, Condition, and Disease Categorization) spending categories, FY2025', refChunks: 3, areaChunks: 3 },
]

const RELATIONS = `Relation types (pick exactly one per pair you list):
- exact: same field, essentially the same scope.
- area_broader: the reference entry falls within the research area (the area is the superset).
- area_narrower: the research area falls within the reference entry (a recognizable sub-field of it).
- overlap: substantial shared scope, neither contains the other.
Only list pairs with a SUBSTANTIVE relationship that a domain expert would recognize as a primary correspondence. Do not list incidental links (e.g. a disease area is not "overlap" with a generic category like "Clinical Research" just because clinical studies exist in it; but it IS area_narrower of a broad disease-family category it belongs to). No relationship is a valid, expected outcome: many entries will have no counterpart and that must be reported faithfully, not papered over.
Keep each rationale under 20 words.`

const PAIRS = {
  type: 'object',
  properties: {
    pairs: { type: 'array', items: { type: 'object', properties: {
      area_id: { type: 'string' }, ref_id: { type: 'string' },
      relation: { type: 'string', enum: ['exact', 'area_broader', 'area_narrower', 'overlap'] },
      rationale: { type: 'string' } }, required: ['area_id', 'ref_id', 'relation', 'rationale'] } },
    considered: { type: 'integer', description: 'number of rows you actually worked through in your assigned slice' },
  },
  required: ['pairs', 'considered'],
}

const sliceNote = (n, i, unit) => n === 1 ? `Work through EVERY ${unit}.` :
  `Work through ONLY ${unit}s in slice ${i + 1} of ${n}: split the data rows (excluding the header) into ${n} contiguous, near-equal blocks in file order and take block ${i + 1}. Report how many rows you covered in "considered".`

const refFirst = (f, n, i) => `You are mapping a biomedical research taxonomy to an external reference list. Read two CSV files:
- Research areas (id,label,description): ${AREAS}
- Reference list: ${f.file} — ${f.what}. Use its "id" column as ref_id.
Method: go REFERENCE ENTRY BY REFERENCE ENTRY. ${sliceNote(n, i, 'reference entry')} For each entry, scan all research areas and list every area with a substantive relation to it.
${RELATIONS}
Return pairs using exact ids from the files.`

const areaFirst = (f, n, i) => `You are judging how a set of biomedical research areas corresponds to an external reference list. Read two CSV files:
- Research areas (id,label,description): ${AREAS}
- Reference list: ${f.file} — ${f.what}. Use its "id" column as ref_id.
Method: go RESEARCH AREA BY RESEARCH AREA, reading each description carefully. ${sliceNote(n, i, 'research area')} For each area, scan the whole reference list and list every reference entry with a substantive relation to it.
${RELATIONS}
Return pairs using exact ids from the files.`

const ADJ = {
  type: 'object',
  properties: { decisions: { type: 'array', items: { type: 'object', properties: {
    area_id: { type: 'string' }, ref_id: { type: 'string' },
    relation: { type: 'string', enum: ['exact', 'area_broader', 'area_narrower', 'overlap', 'none'] },
    rationale: { type: 'string' } }, required: ['area_id', 'ref_id', 'relation', 'rationale'] } } },
  required: ['decisions'],
}

const key = p => `${p.area_id}|${p.ref_id}`
const dedupe = ps => { const m = new Map(); for (const p of ps) if (!m.has(key(p))) m.set(key(p), p); return [...m.values()] }

async function runFrame(f) {
  const jobs = [
    ...Array.from({ length: f.refChunks }, (_, i) => ({ m: 'ref_first', p: refFirst(f, f.refChunks, i), i })),
    ...Array.from({ length: f.areaChunks }, (_, i) => ({ m: 'area_first', p: areaFirst(f, f.areaChunks, i), i })),
  ]
  const res = await parallel(jobs.map(j => () =>
    agent(j.p, { label: `${f.key}:${j.m}:${j.i + 1}`, phase: 'Map', schema: PAIRS }).then(r => r && { ...r, m: j.m, i: j.i })))
  const got = res.filter(Boolean)
  if (got.length < jobs.length) log(`${f.key}: ${jobs.length - got.length} mapper slice(s) FAILED — results incomplete`)
  const m1 = dedupe(got.filter(r => r.m === 'ref_first').flatMap(r => r.pairs))
  const m2 = dedupe(got.filter(r => r.m === 'area_first').flatMap(r => r.pairs))
  const considered = got.map(r => ({ m: r.m, slice: r.i + 1, considered: r.considered }))
  const a = new Map(m1.map(p => [key(p), p])), b = new Map(m2.map(p => [key(p), p]))
  const disagree = [...new Set([...a.keys(), ...b.keys()])]
    .filter(k => (a.get(k)?.relation ?? 'none') !== (b.get(k)?.relation ?? 'none'))
    .map(k => { const [area_id, ref_id] = k.split('|'); return { area_id, ref_id,
      mapper1: a.get(k) ? `${a.get(k).relation}: ${a.get(k).rationale}` : 'none',
      mapper2: b.get(k) ? `${b.get(k).relation}: ${b.get(k).rationale}` : 'none' } })
  log(`${f.key}: mapper1 ${m1.length} pairs, mapper2 ${m2.length} pairs, ${disagree.length} disagreements`)
  const CH = 60
  const chunks = Array.from({ length: Math.ceil(disagree.length / CH) }, (_, i) => disagree.slice(i * CH, (i + 1) * CH))
  const adj = await parallel(chunks.map((c, i) => () => agent(
    `Two independent annotators mapped biomedical research areas to a reference list and disagreed on the pairs below. Decide each pair yourself.
Research areas (id,label,description): ${AREAS}
Reference list: ${f.file} — ${f.what}.
${RELATIONS}
Use "none" when there is no substantive relationship. Do not default to either annotator; neither is privileged. Return exactly one decision per pair below, same ids.
PAIRS (JSON):
${JSON.stringify(c)}`,
    { label: `${f.key}:adjudicate:${i + 1}`, phase: 'Adjudicate', schema: ADJ })))
  const decisions = adj.filter(Boolean).flatMap(r => r.decisions)
  const missing = disagree.length - new Set(decisions.map(key)).size
  if (missing) log(`${f.key}: ${missing} disagreement(s) got no adjudication decision`)
  return { frame: f.key, m1, m2, considered, disagree, decisions }
}

const out = await parallel(FRAMES.map(f => () => runFrame(f)))
return out.filter(Boolean)
