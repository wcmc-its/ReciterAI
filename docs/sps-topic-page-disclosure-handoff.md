# SPS Handoff — Topic Page Disclosure UX

**Audience:** Coding agent working in the Scholars Profile System (`wcmc-its/Scholars-Profile-System`).

**Authoritative source specs:**
- `docs/topic-cross-listing-display.md`
- `docs/topic-page-inclusion-threshold.md`

**Status:** Adopted 2026-05-15. Both source specs adopted in ReciterAI; producer-side tracking issues: [#68 (`top_topic_id` field)](https://github.com/wcmc-its/ReciterAI/issues/68) and [#69 (`display_threshold`)](https://github.com/wcmc-its/ReciterAI/issues/69). Do not start SPS work until both producer-side issues land.

**Surface affected:** `/topics/<topic_id>` ("Scholars Topic page → Research Articles in this area"). Optionally also faculty-profile topic sections — confirm with stakeholders whether v1 scope.

---

## Background

ReciterAI's per-paper topic scores are multi-label by design: every topic above the 0.3 floor is persisted. The SPS Scholars Topic page is the inclusive read surface — it lists every paper above floor for the displayed topic. Today this surface has two known UX gaps:

1. **Cross-listing is invisible.** A paper appearing under `/topics/pediatrics_neonatology` looks identical whether Pediatrics was its #1 fit or its #5. No affordance for the reader to recognize that the paper is centrally on a different topic.
2. **No tiering for relevance strength.** A paper that just-cleared the 0.3 floor on Peds is displayed the same as a paper that scored 0.95. Noise-floor false positives (e.g. PMID 35353857, an adult-focused COVID epi paper that triggered Peds because the abstract mentions house-bound children) appear indistinguishable from canonical Peds work.

This handoff addresses both gaps with two complementary UI changes that consume two new ReciterAI fields.

---

## What ReciterAI publishes

### Field 1 — `top_topic_id` (per-paper)

Argmax of the paper's topic-score vector among topics that cleared the 0.3 floor. Deterministic tiebreak. Read-time observational field; **not** a designation, **not** a rollup input.

Available on every per-paper activity record SPS already reads.

### Field 2 — `display_threshold` (per-topic)

A second threshold above the existing 0.3 floor, published per topic in the `hierarchy.json` SPS already reads. Default ~0.5; some topics carry tuned values.

The 0.3 `score_floor` is unchanged — same papers persisted, same rollups. Only the display logic on the topic page is tiered.

---

## What SPS implements

### Two tiers of papers on `/topics/<topic_id>`

For the topic `T` shown on the page, define:

- **Strongly relevant**: papers where `score[T] >= display_threshold[T]`. Shown by default.
- **Also relevant**: papers where `score_floor <= score[T] < display_threshold[T]`. Shown only when the user opts in via a wider-net affordance.

Both groups already exist in DDB; this is read-time filtering, not a producer change.

**Recommended UX (Option B from the spec)**: render two grouped sections on the page. The "Strongly relevant" section is always visible. The "Also relevant" section is a collapsible region with a clearly-labeled toggle.

Suggested toggle copy: **"View additional articles that are relevant"** (user-chosen phrasing; verifiable with a quick user check).

Alternatives considered and de-prioritized:
- *"Show more"* — too vague.
- *"View weaker matches"* — negative framing.
- *"Show borderline papers"* — admits the system's limitation in the user's face.

A small text marker on the topic-page header can reinforce the policy: *"Showing strongly relevant articles. Click below to view all relevant matches."* Optional but recommended for honesty.

### Top-topic label on each citation

For each paper in either section, render `top_topic_id` inline **only when** `top_topic_id != T` (i.e. the displayed page isn't this paper's strongest fit). Suggested copy:

> *Top topic: Mental Health & Psychiatry*

Click-through links to `/topics/<top_topic_id>`.

If `top_topic_id == T`, render no inline affordance (or a subtle "Top topic in this paper" marker — minor copy choice, OK to skip).

### Paper detail modal

Each paper entry should open a modal on click (or via a dedicated icon — match SPS's existing patterns). The modal shows:

- Full title.
- Full abstract.
- All above-threshold topic assignments, sorted by score descending, with scores visible. The current page's topic `T` is marked.
- Any other per-paper detail SPS already surfaces (authors, journal, year, links).

**The modal absorbs the "Also in: …" question.** The cross-listing display spec deliberately doesn't show the full multi-label list inline (visual noise); the modal is where the full spread becomes legible. A reader confused by the inline "Top topic: MH" label clicks through and sees the full context (e.g. MH 0.95, Neuro 0.90, Radiology 0.55, Peds 0.41) and self-corrects.

If SPS already has a paper-detail modal, this is adding two fields (all-topics list, top-topic-in-context marker) to it. If no modal exists today, building one is the bulk of the SPS work in this handoff.

### Apply both affordances to both tiers

The top-topic label and modal apply equally to the "Strongly relevant" and "Also relevant" sections. The labeling is especially valuable in the "Also relevant" section, where noise-floor false positives (like PMID 35353857) are visibly tagged with their real top topic, giving the reader context to dismiss them.

---

## What SPS does NOT need to implement

- **Hiding multi-label.** The "Also relevant" section exists precisely to keep lower-confidence matches accessible.
- **Per-PMID overrides.** Not in scope for either spec.
- **Re-querying ReciterAI or recomputing scores.** All filtering and labeling is read-time on existing fields.
- **A faculty-profile equivalent.** v1 is topic page only unless explicitly scoped in.

---

## Rendering rules — summary

| User state | Section visible | For each entry |
|---|---|---|
| Default (page load) | "Strongly relevant" only | Show `top_topic_id` label if ≠ current topic; click → modal with full topic spread |
| After clicking "View additional articles that are relevant" | "Strongly relevant" + "Also relevant" | Same labeling rules apply; the "Also relevant" section is visually distinguished but uses the same affordances |
| Click any citation | Modal opens | Title, abstract, all above-threshold topics with scores, current topic marked |

---

## Open questions for SPS-side resolution

- **Modal pattern**: does SPS have an existing paper-detail modal? Adding fields is small; building from scratch is the bulk of v1 work.
- **Toggle persistence**: should "View additional articles that are relevant" stick across sessions / page navigations, or reset to collapsed on every page load? Spec lean: reset (less surprising default).
- **Score display in modal**: show numeric scores (e.g. "Mental Health & Psychiatry: 0.95") or just rank order? Spec lean: show scores (more honest); revisit if it feels too data-dense.
- **Faculty-profile topic sections**: in v1 or v1.1? Decide before starting; affects whether SPS needs the same logic in two render paths.
- **Copy of toggle and labels**: validate with a 15–30 minute user check before commit. The cross-listing display spec flags the inverted-confusion risk (*"Top topic: MH" reads as "why is this on this page?"*), partially mitigated by the modal but still copy-sensitive.

---

## Cross-references

- `docs/topic-cross-listing-display.md` (source spec for top-topic + modal)
- `docs/topic-page-inclusion-threshold.md` (source spec for two-tier display)
- `Projects/ReciterAI - Planning/topic-precision-audit-DRAFT.md` (parked — context only)
- `docs/sps-integration-handoff.md` (existing handoff pattern this doc follows)
- `docs/sps-spotlight-handoff.md` (existing handoff pattern this doc follows)
- `docs/topic-subtopic-assignment.md` (per-paper score vector reference)
