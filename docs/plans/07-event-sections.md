# 07 — The event sections

**Goal:** every section the engine can be sent to resolves in code. This is the
long tail, and it is the bulk of the remaining work.

**Depends on:** 02–06. Nearly every section reaches into one of them.

**Done when:** the coverage report shows no unhandled section reachable from
travel, rest, or the encounter graph — and a thousand headless games finish
without a `Refuse` that is not a genuine rules gap.

---

## The shape of the problem

```
252 sections
 81  have a machine-readable table            -> generic handler, already works
171  do not
      103  contain a die roll with a prose outcome
       68  are prose only (descriptions, rules text, possessions)
```

Of the 171, **137 match at least one recurring shape**:

```
127  branch to another section (mostly into the r300-r343 graph)
 20  schedule a deferred effect            -> plan 06
 15  a wit & wiles check against 1d6       -> plan 02's Check primitive
 13  roll for a band's size                -> creatures.py already does this
 10  award a wealth-code treasure          -> plan 08
  8  roll once per character
```

So most sections are a short composition of primitives that already exist. The
work is volume, not difficulty — with a tail of genuinely bespoke ones (`e079`
weather, `e092` flood, `e120` exhaustion, the `e180`–`e194` possessions).

## Method

### 1. The generic handler covers the 81

`engine/sections.py` already falls back to: read the section, resolve its table,
follow the reference. Do not write handlers for these. Verify instead: drive every
tabled section at every die value and assert it terminates.

### 2. Triage the 171

- [ ] `src/engine/coverage.py` — for each section: has a table? has a handler?
      reachable from where? what shapes does its text match? Output a checklist,
      sorted by inbound reference count so the most-visited sections come first.
- [ ] Commit the checklist as `docs/plans/07-coverage.md` and tick it off. That
      file is the resume point for this plan.

### 3. Write handlers in batches by shape, not by number

Batching by shape means each batch shares a helper and a test pattern:

| Batch | Sections | Helper |
|---|---|---|
| A — band + follow/escape | e052, e055, and the sightings | `sight_band()`: size roll, escape r218 or follow r219, discovery check, destination roll |
| B — wit & wiles trap or trick | e031 and the r3xx-adjacent events | `wits_check()` from plan 02 |
| C — per-character save | e079, e133, and the plagues | `each_character(die, threshold, effect)` |
| D — weather and terrain conditions | e078, e079, e092, e096, e120 | plan 06 effects, one bespoke handler each |
| E — settlement actions | r208–r212 and their event tables | mostly tabled; wire the action verbs from plan 05 |
| F — possessions | e180–e194 | plan 08 |
| G — the rest | whatever survives triage | individually |

Start with A and E: A because band sightings are the most common travel outcome,
E because it unlocks four of the eight daily actions.

### 4. Use the model where it belongs — at build time

Drafting 171 handlers from section text is exactly what a strong model is good
at, and it is safe here because the output is **code you review, diff, and test**,
not a runtime decision. This is the inversion argued for in the overview: the same
extraction work, done once and checked in, instead of re-derived every turn by a
small local model under a prompt telling it not to hallucinate.

Workflow per batch:

1. Dump the batch's section bodies.
2. Draft handlers against the `ctx` API and the shape helpers.
3. Review every one against the printed text — **this step is not optional and not
   delegable.** A wrong handler is a wrong ruling that costs the player a game,
   and unlike a hallucination it will be wrong the same way forever.
4. Write the golden-journal test alongside, from the section text, not from the
   handler. A test derived from the handler proves nothing.
5. Tick the coverage checklist with the reviewer's initials and the date.

## Conventions for handlers

- One module per hundred: `rules/events/e000.py`, `e100.py`, `r200.py`, `r300.py`.
- Docstring = the section title and a one-line statement of the mechanic. Never
  the section's prose — that lives in `sections.json`, which is git-ignored.
- Read the book's text with `ctx.read(sid)`; never re-type it.
- Cite the rule on every roll: `ctx.die("1d6", why="r215b")`. The cite is
  `voice=False`, so it shows without being spoken.
- Anything the section leaves genuinely ambiguous: `Refuse` with the section id
  and what is unclear. A refusal in the log is a to-do with a citation.

## Known hard cases

Flag these early; they will not fit the batches.

- **e079 Heavy Rains** — a three-day weather state machine with per-character and
  per-mount rolls, conditional on whether you travel.
- **e092 Flood / e096 Mounts Die** — "each day until" conditions.
- **e120 Exhaustion** — suppresses healing and halves capacity with a location
  predicate.
- **e053/e160** — table rows wrapped across lines with the die number alone on its
  own line. The extractor handles them; any handler reading the raw body must not.
- **e189** — genuinely absent from the source PDF. Reaching it must refuse and say
  so, not fall through.
- **e167–e179, r223–r224, r229, r282–r299** — do not exist. Assert the errata
  layer refuses on them.
- **The four typo references** — `r119`→`r319`, `e341`→`r341`, `e250`→`e150`,
  `e226`→`r226`. `bp` already corrects them; the engine must route through the
  same errata layer rather than resolving ids directly. The layer also carries
  `hex_typos` (e001's caravan destination 3 is `0901`, not `0801`),
  `source_fixes` (an OCR slip in e067) and `orphan_footnote_markers` (e100's
  asterisk with no footnote) — all four categories must be honoured.

## Work items

- [ ] `coverage.py` and the generated checklist.
- [ ] Shape helpers: `sight_band`, `each_character`, `wits_check`, `wealth_roll`,
      `follow` (r219).
- [ ] Batches A–G, in that order, ticking the checklist.
- [ ] A `--strict` mode that turns any fall-through to the generic handler into an
      error, so coverage cannot silently regress.

## Tests

- **Termination sweep:** every section, every die value at every ask, terminates
  in a verb or a `Refuse`. No infinite loops, no unhandled exceptions.
- **Per-handler golden journals**, written from the section text.
- **Reference integrity:** every `refs` entry in `sections.json` resolves through
  the errata layer to a section that exists or a documented absence.
- **The leak test still passes.** Prose must never contain a table row — it is
  already the repo's sharpest test and the new UI is a new way to break it.
- **Coverage does not regress:** the checklist count is asserted in CI.
