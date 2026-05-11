# SPS Integration Brief — `display_name` + `short_description` Subtopic Card Fields

**Audience:** Coding agent working in the Scholars Profile System (Cornell's VIVO
replacement, separate codebase).

**Authoritative schema:** `hierarchy-schema.md` rule **D-19** in this directory.

**Status:** Upstream pipeline change shipped 2026-05-06. Existing `hierarchy.json`
artifacts may or may not have the new fields populated yet — your code MUST tolerate
both states. Annual recomputes will produce them naturally going forward.

---

## Background

SPS consumes a `hierarchy.json` artifact produced by the ReCiter AI pipeline. Each
topic contains a `subtopics[]` array of `SubtopicDef` records. The existing UI renders
the `label` field, which produces clunky strings like *"Tumor microenvironment
immunity"* or *"Genomics molecular profiling"* — slug-style concatenations without
proper Title Case or punctuation.

The upstream pipeline now emits two new UI-facing fields on every `SubtopicDef` so
SPS can render polished card titles and subtitles without changing the
synthesis-canonical fields the LLM pipeline depends on.

---

## What changed upstream (data model)

`SubtopicDef` (snake_case in JSON) gained two fields:

```ts
interface SubtopicDef {
  id: string;
  label: string;              // unchanged — synthesis-prompt field
  description: string;        // unchanged — synthesis-prompt field
  display_name: string;       // NEW — Title Case UI title, max 6 words, ampersands/hyphens OK
  short_description: string;  // NEW — noun-phrase subtitle, <= 140 chars
  activity_count: number;
  total_weight: number;
}
```

Examples of well-formed values:

- `display_name: "Tumor Microenvironment & Immunity"`,
  `short_description: "Immune cell composition, signaling, and stromal interactions in solid tumors."`
- `display_name: "Cellular Senescence & Molecular Aging"`,
  `short_description: "Molecular pathways of aging including senescence, epigenetic clocks, NAD+ metabolism, telomere biology, and senolytic interventions."`

---

## What you need to change in SPS

### 1. TypeScript types

Find the type that mirrors `SubtopicDef` (likely named `Subtopic`, `SubtopicDef`, or
similar — search for `total_weight` or `activity_count`). Add the two new fields.
Decide one of:

- **Keep snake_case** at the type boundary if the rest of the file does.
- **Map to camelCase** (`displayName`, `shortDescription`) at the JSON-load boundary
  if SPS already converts other fields. Be consistent with existing convention — do
  not introduce a hybrid.

Both fields are **always present in the payload as strings** (never `undefined`),
but **may be empty strings** for older/legacy hierarchy files. Type them as `string`,
not `string | undefined`.

### 2. Card title

Wherever a subtopic card or list item renders the title today (search for usages of
`subtopic.label`):

```tsx
// before
<h3>{subtopic.label}</h3>

// after
<h3>{subtopic.displayName || subtopic.label}</h3>
```

The fallback to `label` is required — it is how legacy data and any future emergency
regen still produce a readable card. Do not throw, do not show a placeholder, do not
show "Untitled".

### 3. Card subtitle (new)

Render `shortDescription` as a secondary line under the title, **only when
non-empty**:

```tsx
{subtopic.shortDescription && (
  <p className="subtopic-subtitle">{subtopic.shortDescription}</p>
)}
```

Style guidance: smaller text size than the title, muted color, single line with
ellipsis OR allow two-line wrap (no more) — match the visual hierarchy of any
existing description/subtitle pattern in the app. If the user has a design system
token for "card subtitle" or "muted body text", use it.

### 4. Search / filter / sort behavior

If SPS supports filtering or searching subtopics by name, **include `display_name`
in the searchable fields** alongside (or replacing) `label`. Keep `label` searchable
too — users may have memorized older labels.

If subtopics sort alphabetically anywhere, sort on `displayName || label`.

### 5. Anywhere `label` is currently shown to a human, switch to `displayName || label`

Sweep the codebase for `subtopic.label` (or whatever your accessor is). For each
usage decide:

- **Visible to a user** (card, list, breadcrumb, tooltip, page title, modal header)
  → switch to `displayName || label`.
- **Internal use** (analytics event names, URL slugs, log lines, internal IDs,
  retrieval keys) → leave as `label` or `id` — do NOT switch.

### 6. Do NOT pass `display_name` or `short_description` to any LLM

If SPS makes any LLM call that includes subtopic context (synthesis, RAG prompts,
etc.), it must use the existing `label` and `description` fields verbatim — those
are the canonical synthesis-prompt fields per D-15. The new UI fields are explicitly
forbidden in synthesis prompts (per D-19) because they are stylized for human
display, not LLM context. If you find an LLM call site, leave the field names alone.

---

## Tolerance contract (must hold)

| Condition                                                            | UI behavior                                |
| -------------------------------------------------------------------- | ------------------------------------------ |
| `display_name` populated, `short_description` populated              | Render new title + subtitle                |
| `display_name` populated, `short_description` empty                  | Render new title, omit subtitle            |
| `display_name` empty, `short_description` empty (legacy data)        | Render `label` as title, omit subtitle     |
| Both fields missing entirely (older artifact predating the field)    | Same as above — never throw                |

Test all four conditions before declaring done.

---

## Acceptance criteria

1. Subtopic cards render `display_name` when present, `label` as fallback,
   **never an empty title**.
2. `short_description` renders as a subtitle when present, omitted when empty —
   no empty `<p>` shells.
3. Search / filter / sort include the new field.
4. No LLM call site has been modified to pass `display_name` or `short_description`.
5. Loading a hierarchy artifact missing the new fields entirely does not crash and
   degrades gracefully.
6. Visual review on at least 5 subtopic cards confirms the new strings read like
   journal section headings, not slugs.

---

## Things NOT to do

- Do not modify the upstream pipeline or `hierarchy.json` shape — that's a different
  repo (`ReciterAI -ReCiter-Integration`).
- Do not synthesize a `display_name` client-side from `label` (e.g., title-casing
  it). The fallback is the raw `label`; the upstream pipeline owns the rewrite.
- Do not cache or persist `display_name` / `short_description` in a database that
  outlives a hierarchy regeneration. Subtopic IDs are unstable across recomputes
  (D-06); the new fields are equally non-canonical.
- Do not introduce a third "name" concept. There are exactly two: `label`
  (synthesis-canonical) and `display_name` (UI-canonical).
- Do not add a feature flag — the fallback to `label` makes this safe to ship
  immediately, even before all fields are populated upstream.

---

## Verification

Before opening a PR:

1. Render a card whose subtopic has both new fields populated. Title and subtitle
   should look like a journal heading + tagline.
2. Render a card whose subtopic has only `label` (simulate by editing fixture data
   to clear `display_name`). Card should still render with the legacy label, no
   subtitle, no errors.
3. Run search / filter for a string that appears only in a `display_name`. It
   should match.
4. Grep the codebase for any LLM call site that includes subtopic data. Confirm
   `label` / `description` are still the fields being sent.
