# Architecture diagrams — the reproducible method

A portable playbook for producing the *same* polished, version-controlled
architecture diagrams in **any** repository. This is the "how to do it again
somewhere else" companion to [`README.md`](README.md) (which is the "how to use
it here" reference). Nothing in the method is specific to ReciterAI or to the
Scholars Profile System it was first built for.

The output is a set of self-contained SVGs + PNGs + a single `index.html`
gallery, all generated from small plain-data files by a dependency-free Node
renderer. No Mermaid, no Graphviz, no draw.io, no layout engine, no npm install.

---

## Why this approach

| Property | Why it matters |
|---|---|
| **Diagrams are code** (plain-data `.mjs` specs) | reviewed in PRs, diffable, regenerated deterministically, never drift from a binary nobody can edit |
| **Dependency-free renderer** | runs anywhere Node runs; no toolchain to install, nothing to break on a fresh machine or in CI |
| **Coordinates are literal** | what you type is where it lands — no auto-layout fighting you; predictable, total control |
| **Geometry is validated** | the build fails on overlaps / out-of-bounds, so a broken diagram can gate CI |
| **One house style** | every diagram shares one palette + renderer, so a whole repo's diagrams look like a set |
| **SVG is the primary artifact** | crisp at any zoom, renders inline on GitHub, ⌘P → PDF for slides; PNG is a best-effort raster for tools that won't show SVG |

When *not* to use it: a quick throwaway sketch (use Mermaid in a markdown
fence) or a diagram with 50+ nodes where hand-placing coordinates stops paying
off (reach for a real layout engine). The sweet spot is a **small set of
high-value, long-lived diagrams** (≈3–6 views, ≤25 nodes each) that you want to
look deliberate and keep accurate.

---

## What you copy (the toolkit)

Three files are **project-agnostic** — copy them into the new repo:

```
scripts/diagrams/
  lib.mjs              # the renderer: palettes, anchors, renderSVG(), validate(). Pure functions.
  build.mjs            # validate → SVG → PNG → index.html. (Has one HTML shell to rebrand — see step 3.)
  check-crossings.mjs  # dev linter: flags edges that tunnel THROUGH a foreign node box.
```

One file is **project-specific** — write your own per repo, modelled on the example:

```
  verify-facts.mjs     # asserts the constants baked into the diagrams (model ids, cron
                       # strings, bucket/table names) still match THIS repo's real sources.
                       # build.mjs auto-runs it IF present, so build.mjs stays copyable.
```

Everything else (`definitions/*.mjs`, `README.md`, this file) is content you
write per project.

`lib.mjs` has zero DOM and zero Node API usage, so the exact same code could even
run in a browser. It's worth keeping **in sync** across the repos that use it — but
"in sync" not "frozen": this copy carries accessibility, provenance, and
edge-legend improvements the original SPS copy doesn't yet have. When you improve
the renderer, port the change to the other copies rather than letting them drift
silently.

---

## The 8-step process

### 1. Map the system before drawing anything

Diagrams are only as good as the facts in them. Build an evidence-backed map
*first*, every claim tied to a file path. Good sources, in order of trust:

1. The code itself (config files, infra-as-code, entrypoints, client wrappers).
2. Authoritative in-repo docs (`README.md`, `ARCHITECTURE.md`, ADRs, contracts).
3. A subagent sweep for breadth — but **treat its report as leads, not truth**.

> **Provenance discipline.** Before you bake a load-bearing fact into a
> committed diagram (a model ID, a cron string, a bucket/table name, a port),
> verify it against the actual source — not a summary. A diagram is read cold by
> people who will trust it; a wrong constant in a diagram is worse than none.
> Cite the real sources in each diagram's `meta.source`.

### 2. Choose the views (don't over-draw)

A few orthogonal views beat one cluttered megagram. Two proven templates:

**For a request-serving app** (the original SPS set):
1. **System context** — what feeds it, who it serves (the one-glance picture).
2. **App & deployment topology** — the runtime boxes and how they connect.
3. **Network topology** — VPC / subnets / security groups (if reviewers need it).
4. **Internals** — a C4-component zoom inside the main container.
5. *(optional)* a **decision** view spotlighting one open architectural fork.

**For a batch / pipeline / data service** (the ReciterAI set):
1. **System context** — sources → service → published outputs → consumer.
2. **Processing pipeline** — the stages, the model/tool at each, what each r/w.
3. **Runtime topology** — schedulers → compute targets → data/model plane.
4. **Publish / integration contract** — the exact hand-off surface to consumers.

Pick the 3–5 that answer real questions your audience asks. Each view = one
file in `definitions/`, named `NN-slug.mjs` so they order in the gallery.

### 3. Drop in the toolkit and rebrand the shell

Copy the three toolkit files. Then make the edits `build.mjs` needs — its
`buildHtml()` has a hard-coded hero (the `docTitle`/`docDesc`, the meta chips) and
OG tags. Rebrand those for the new project; the description + OG tags flow from
`docDesc`. (Everything else in `build.mjs` is generic.) Write a short `README.md`
for per-repo usage, and — when you're ready for tier 3 of verification — a
`verify-facts.mjs` that knows this repo's source paths.

**You get for free** (no per-diagram work): each SVG is emitted with `role="img"`
+ `<title>`/`<desc>` (accessible name from the view's heading/blurb) and a
`<title>` on every node/group (which also gives native hover tooltips); the
gallery `<head>` gets a `meta description` + Open Graph tags so it unfurls in
Slack/Teams; and the footer stamps the git commit SHA + date (deterministic — no
wall-clock churn) so readers know how fresh the picture is.

### 4. Write each diagram as data

A diagram is a `spec` of `groups` (labelled container bands), `nodes` (boxes),
and `edges` (wires). Coordinates live in a `vb: [width, height]` viewBox.

```js
import { A } from "../lib.mjs";

const nodes = {
  api: { x: 360, y: 120, w: 264, h: 80, kind: "app",
         title: "service.py", sub: ["what it does", "second line"],
         chip: { tone: "nightly", text: "nightly" } },   // optional status pill
  db:  { x: 360, y: 360, w: 264, h: 72, kind: "data", title: "DynamoDB" },
};
const groups = [{ x: 330, y: 96, w: 320, h: 360, kind: "app", title: "Service", fo: 0.06 }];
const edges  = [{ p0: A(nodes.api, "b"), p1: A(nodes.db, "t"), color: "amber", label: "write" }];

export const spec = { id: "my-view", vb: [1300, 560], groups, nodes, edges };
export const meta = {
  nav: "① My view", kicker: "View 1 · …", heading: "My view", dot: "#0ca678",
  blurb: "One paragraph a newcomer can read.",
  legend: [{ fill: "#e3faf3", stroke: "#0ca678", label: "Compute" }],
  source: "path/to/real/source.py · ARCHITECTURE.md",   // provenance, shown under the diagram
};
```

Key primitives (full list in `README.md` and `lib.mjs`):
- **`kind`** sets the colour: `ext` (grey/external), `app` (green/compute),
  `data` (amber/store), `aws`/`net`/`edge` (managed/network/CDN), `good`/`open`
  (green-check / red-open for decision states).
- **`A(node, side, f)`** anchors an edge to a side (`t/b/l/r`), `f`∈[0,1] slides
  along it. Edges auto-curve; `points:[{x,y},…]` hand-routes around obstacles;
  `dash:true` for dashed; `lp:{x,y}` to place a stubborn label.
- **`chip`** = a right-aligned status pill (cadence, state). **`badge`** = a top
  accent stripe (e.g. owning stack/module).

**Layout tips that save iterations:** lay out on a grid (column x-positions and
row y-positions you reuse); leave **clear channels** between groups to route
long edges through; keep ≤25 nodes per view; give every box a 1-line `sub` so it
explains itself.

### 5. Build

```
node scripts/diagrams/build.mjs        # → docs/architecture/*.svg, *.png, index.html
```

PNGs rasterize via `rsvg-convert` if installed, else the `sharp` npm package;
if neither is present, SVG + HTML still build and only PNG is skipped. (On
macOS: `brew install librsvg` gives you `rsvg-convert`.)

### 6. Verify in four tiers — this is the part people skip

`validate()` (run automatically by the build) only catches **node overlaps and
out-of-bounds**. It says nothing about edges, facts, or readability. So:

1. **Build-time `validate()`** — overlaps / bounds. Free, gates CI.
2. **Crossing lint** (`node scripts/diagrams/check-crossings.mjs`) — flags edges
   whose drawn path tunnels *through* a foreign node box. This is the failure
   class `validate()` is blind to, and it's the most common ugly-diagram bug.
3. **Fact check** (`verify-facts.mjs`, also auto-run by the build) — asserts the
   constants baked into the diagram text (model versions, cron strings, bucket /
   table names) still match their real sources, and **fails loud if it can't find
   a source constant** (a check that passes because it couldn't look is worse than
   no check). This is what makes the diagram *un*-driftable. See "assert, don't
   assume" below.
4. **Look at the rendered PNG.** Always. No automated check catches a label sitting
   on a line, a curve bulging into a group title, or a wire that's *technically*
   clear but visually confusing. Open `index.html` or read the PNG and fix by eye.

> Treat "geometry clean" as **necessary, not sufficient**. The diagram isn't done
> until you've seen it rendered.

**Assert, don't assume.** Any constant you hand-type into a diagram (a model ID, a
cron, a bucket) will eventually drift from the code. `verify-facts.mjs` closes that
gap: it reads the *actual* sources and fails the build on a mismatch. Where the
sources are JSON (e.g. `infra/*.json`), read them directly; where they're another
language (Python constants, say), parse the constant out with a tight regex and
**fail if the constant can't be located** so a refactor that moves it trips the
check instead of silently passing. (The first build of this kit shipped with the
hero meta listing "Sonnet + Haiku" while the diagrams showed Opus — exactly the
drift this tier now prevents.)

### 7. Wire it into the repo

- `.gitignore`: add `docs/architecture/*.png` (PNGs are regenerable; commit the
  SVGs, which render on GitHub and diff as text).
- Link `docs/architecture/index.html` (or a key SVG) from the repo `README`.
- *(optional)* CI gate: run the build and fail on non-zero exit (geometry
  problems), and/or run the crossing linter.

### 8. Keep them honest

Add a line to the relevant `meta.source` whenever the underlying system changes,
and re-run the build. Because the specs are plain data under version control, a
diagram update is a normal reviewable diff — that's the whole point.

---

## Lessons from doing this twice

- **Subagent maps are scaffolding, not sources.** A breadth-first agent sweep is
  the fastest way to learn an unfamiliar system, but every fact that lands in a
  committed diagram got re-verified against the actual file. Two repos in, the
  re-verification caught details a summary had rounded off.
- **The crossing linter pays for itself on the first dense diagram.** The runtime
  topology view (schedulers → many targets → shared data plane) is where edges
  inevitably want to cross boxes. Hand-routing through clear channels + the linter
  is the difference between "looks intentional" and "looks autogenerated."
- **One renderer, many specs.** Resisting the urge to special-case `lib.mjs` per
  diagram is what keeps a repo's diagrams looking like a coherent set — and what
  lets you copy the renderer to the next project untouched.
- **Write the view's `blurb` for a newcomer.** If you can't explain the view in
  one plain paragraph, the view is doing too much — split it.

---

## TL;DR

```
1. Map the system (code first; verify every fact you'll commit).
2. Pick 3–5 orthogonal views.
3. Copy lib.mjs + build.mjs + check-crossings.mjs; rebrand build.mjs's HTML hero.
4. Write each view as a plain-data spec in definitions/NN-slug.mjs.
5. node scripts/diagrams/build.mjs
6. Verify in 4 tiers: validate() → crossing-lint → fact-check → LOOK at the PNG.
7. gitignore the PNGs, commit the SVGs, link index.html from the README.
8. Update meta.source + rebuild whenever the system changes.
```
