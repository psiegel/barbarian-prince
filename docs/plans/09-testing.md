# 09 — Testing

**Goal:** the thing a deterministic engine buys that the current design cannot
have — the game plays itself, ten thousand times, and tells you where it breaks.

**Depends on:** 01. Then grows with every plan; do not defer it to the end.

**Done when:** a single command plays a thousand random games to a terminal state
and reports every refusal, every unhandled section, and every invariant breach —
and the run is fast enough to sit in a pre-commit hook.

---

## Why this is now possible

Model behaviour is not a unit test — the current repo says so, and it is right.
`./play --referee --quiet` and reading what the model does is the only way to
check `system.md`. Once the rules are code with all randomness entering through
the journal (D1, D2), the game becomes an ordinary deterministic program and the
whole apparatus of testing applies.

## The four layers

### 1. Golden journals

The unit test of this project. A journal is a list of player inputs; a test is a
journal plus an expected sheet.

```
tests/golden/e003-fight-row4.journal
tests/golden/e003-fight-row4.expected.json
```

- [ ] `python3 -m tests.golden --record <name>` plays interactively and writes both
      files, so recording a case is playing it once.
- [ ] `--verify` replays every case and diffs the sheet.
- [ ] A failing diff prints the two sheets side by side, not a Python traceback.

Write these from the section text, not from the handler (plan 07 says the same
thing and it is the rule most likely to be quietly broken).

### 2. Exhaustive sweeps

For anything with a small input space, enumerate rather than sample.

- [ ] Every section, driven at every die value at every ask, terminates in a verb
      or a `Refuse`. This is the single most valuable test in the suite: it finds
      unhandled branches, infinite loops, and typo references in one pass.
- [ ] The whole r226 grid.
- [ ] Every travel.json row at its threshold and one below.
- [ ] Every combat-table total from −5 to 25.

### 3. Random play

- [ ] `python3 -m tests.play --games 1000 --seed 1` — a bot answering every `Ask`
      by rolling dice and picking choices at random, playing to a win, a loss, or
      day 71.
- [ ] Report: refusals by section, unhandled sections, exceptions, games that
      failed to terminate, and the distribution of outcomes.
- [ ] A refusal count per section is the plan 07 to-do list, generated.
- [ ] A second bot that plays *plausibly* (travels toward the Northlands, hunts
      when it can, avoids fights when wounded) catches different bugs — the random
      bot rarely survives to day 40.

### 4. Invariants

Checked after every single `Ask` during random play, not just at the end. Cheap
and they localise a bug to the step that caused it.

- gold ≥ 0, food ≥ 0
- no character has wounds > endurance and is still acting
- no dead character strikes, eats, is paid, or is housed
- the day never moves backwards; the clock never skips without an explicit
  "advance N days"
- party size ≥ 1 while the game is running; the player is present or the game has
  ended
- loads ≤ capacity, or an overload prompt is pending
- every condition field set has an effect queued that can clear it (plan 06)
- the journal replays to the current state — assert it every N steps, since a
  replay divergence is the one bug that would silently corrupt saves

## Regression tests to carry over

The existing suite is good and must keep passing.

- [ ] **The leak test.** Render every section through `bp.to_prose()` and assert
      nothing matches `TABLE_LINE_RE`, `ROW_LINE_RE`, `DIE_HEADER_RE` or
      `INLINE_OUTCOMES_RE`. The new UI is a new way to break it: an `Event` with
      `voice=True` carrying a table row is the same defect wearing new clothes.
      **Extend the assertion to every `voice=True` event the engine emits.**
- [ ] **Map invariants.** `bp hex 0101` north of the Tragoth, `0102` south. A
      re-transcribed `map-data.csv` that moves an N edge into rows 01–02 moves the
      river.
- [ ] **Errata coverage.** Every typo id resolves; every non-existent id refuses.
- [ ] `src/audit.py` — see what it already checks and fold it in rather than
      duplicating it.

## Determinism guard

D2 says handlers may not call `random`, read the clock, or read outside `ctx`.
Enforce it rather than trusting it:

- [ ] A test that imports every module under `engine/rules/` and asserts none
      references `random`, `time`, `datetime`, or `os.environ` at module or
      function scope. A simple AST walk is enough and it will catch the mistake the
      day it is made.
- [ ] Replay the same journal twice in one process and assert byte-identical
      sheets — catches accidental dict-ordering and set-iteration dependencies.

## Test hygiene

**`BP_GAME` for everything.** A bare test command edits the live save. The harness
should refuse to run without it set, and should create and delete its own save per
test rather than reusing one. Never leave `saves/current` pointing at a save the
test then deletes.

## Work items

- [ ] `tests/` package with a plain `unittest` runner — no new dependencies; the
      repo is standard-library-only and should stay that way.
- [ ] Golden journal record/verify.
- [ ] The sweep, the bots, the invariant checker, the determinism guard.
- [ ] `make test` or `./check` running the fast subset in under ten seconds, with
      the thousand-game run behind a flag.
- [ ] Wire the fast subset into a pre-commit hook.

## Open question

**How much does the random bot need to know?** A bot that answers uniformly at
random will refuse constantly on choices that are illegal in context (buying food
with no gold). Recommended: the bot picks uniformly among the options the engine
*offers*, which is the right test anyway — if the engine offers an illegal option,
that is the bug.
