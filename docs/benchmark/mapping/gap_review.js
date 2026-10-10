export const meta = {
  name: 'research-area-benchmark-gap-review',
  description: 'Review entries both blind mappers left unmatched, for missed relations',
  phases: [{ title: 'Gap review' }],
}
// Absolute path to this checkout's docs/benchmark.
const IN = '<repo>/docs/benchmark'
const SCHEMA = { type: 'object', properties: { decisions: { type: 'array', items: { type: 'object', properties: {
  area_id: { type: 'string' }, ref_id: { type: 'string' },
  relation: { type: 'string', enum: ['exact', 'area_broader', 'area_narrower', 'overlap'] },
  rationale: { type: 'string' } }, required: ['area_id', 'ref_id', 'relation', 'rationale'] } },
  confirmed_unmatched: { type: 'array', items: { type: 'string' }, description: 'ids you checked and found genuinely without counterpart' } },
  required: ['decisions', 'confirmed_unmatched'] }
const RELATIONS = `Relation types:
- exact: same field, essentially the same scope.
- area_broader: the reference entry falls within the research area (area is the superset), e.g. a specific disease inside a broader disease-family area.
- area_narrower: the research area falls within the reference entry.
- overlap: substantial shared scope, neither contains the other.
Only substantive, primary correspondences a domain expert would recognize. Incidental links do not count. Confirming that an entry has NO counterpart is a valid and expected outcome; do not force matches.`
const frames = [
  { k: 'C1', ref: 'inputs/nih_institutes_centers.csv', fwd: 'outputs/frame_C1_forward.csv', rev: 'outputs/frame_C1_reverse.csv', what: 'grant-funding NIH Institutes and Centers, judged by each IC\'s research mission' },
  { k: 'C2', ref: 'inputs/nih_rcdc_categories.csv', fwd: 'outputs/frame_C2_forward.csv', rev: 'outputs/frame_C2_reverse.csv', what: 'NIH RCDC spending categories' },
]
const out = await parallel(frames.map(f => () => agent(
`Two independent annotators mapped biomedical research areas to a reference list (${f.what}). Some entries ended up with NO match from either annotator. Your job: check each unmatched entry for a substantive relation they both missed.
Files:
- Research areas (id,label,description): ${IN}/inputs/research_areas.csv
- Reference list (id,name,...): ${IN}/${f.ref}
- Per-area results: ${IN}/${f.fwd}  (rows with best=none are unmatched AREAS)
- Per-reference results: ${IN}/${f.rev} (rows with best=none are unmatched REFERENCE ENTRIES)
For every unmatched area, scan the full reference list. For every unmatched reference entry, scan all research areas. Return each missed relation you find (area_id, ref_id, relation, rationale under 20 words), and list in confirmed_unmatched every id (area id or ref id) that you checked and found genuinely without counterpart.
${RELATIONS}`, { label: `${f.k}:gap-review`, phase: 'Gap review', schema: SCHEMA }).then(r => r && { frame: f.k, ...r })))
return out.filter(Boolean)
