# Deterministic engine — overview

**Goal:** a text-based program that plays *Barbarian Prince* end to end in code.
It knows the daily loop, resolves the cross-reference graph, keeps the sheet, and
asks the player only for the decisions the game actually gives them. The LLM is
optional, sits outside the rules, and can be switched off without breaking the
game.

These plans are written to be resumable. Each one states what it depends on, what
it changes, and how you know it is done. Work them in order; the status board
below is the single place that records progress.

---

## The inversion

Today `bp` is a tool and the model is the program: the model decides which
section to fetch, when the day ends, whether the fight is over. `src/prompts/system.md`
is 900 tokens of instructions not to invent, because inventing is the failure mode
of putting a model in the write path.

The new arrangement:

```
                 engine  (the program: rules, state, control flow)
                   |
     +-------------+-------------+
     |                           |
    ui  (terminal)          narrate  (optional LLM, read-only)
```

The engine emits an event stream and asks questions. The terminal UI renders the
stream and collects answers. The narrator, if enabled, subscribes to the same
stream and produces prose. **The narrator has no write access to game state and no
ability to change control flow.** That is the whole safety argument: a model that
can only describe what already happened can be dull, but it cannot corrupt a game.

## What is already deterministic

Do not rebuild these. The engine imports them.

| Capability | Where |
|---|---|
| 81 machine-readable tables (22 options, 28 table, 31 inline) | `data/tables.json`, `src/tables.py` |
| Map: terrain, adjacency, rivers, roads, features | `data/map.json`, `procedures.hex_terrain`, `neighbours`, `hexside` |
| North-of-Tragoth derivation | `procedures.north_of_tragoth` |
| Travel table thresholds and event refs | `data/travel.json`, `procedures.check_lost`, `check_event` |
| Strike math, combat table, wound tiers | `state.strike_mod`, `target_mod`, `wounds_for`, `combat.strike` |
| Food, wages, lodging cost, transport capacity | `state.food_needed`, `wages`, `lodging_cost`, `capacity` |
| Band sizes and roster rewrites | `data/creatures.json`, `src/creatures.py` |
| Treasure grid (r226) | `procedures.treasure_rows` |
| Errata and known-absent sections | `data/errata.json` |
| Sheet model and save format | `src/state.py` |

## What is missing

1. **A control-flow engine.** Nothing today can suspend at a die roll, persist,
   and resume. `procedures.py` prints checklists for a model to follow.
2. **The 171 untabled sections.** 103 of them contain a die roll whose outcome is
   English prose. They are deterministic but bespoke. See plan 07.
3. **A deferred-effect queue.** ~20 sections schedule consequences for tomorrow,
   tonight, or "until you leave the desert". Nothing in the save holds them.
4. **A re-entrant encounter stack.** Half the r3xx graph can fail back to "return
   to the previous section and select another option".
5. **Party composition detail.** Mounts, guides, single-room classes, hire terms,
   and departure conditions are only partly modelled.

## Load-bearing decisions

These five shape everything. Change them only deliberately.

### D1 — The day is the unit of durability

The save holds a complete game state as of **the start of the current day**, plus
a **journal** of every player input taken since. Resuming replays the journal
silently. Committing a day writes a fresh state and clears the journal.

This buys three things at once: mid-encounter resume, free undo (drop the last
input and replay), and a test harness that is just a journal plus an expected
final state.

### D2 — Rule handlers are generator functions

Because of D1, handlers never need to be serializable — only the journal does.
So a section is written as ordinary Python that reads like the rulebook:

```python
def r317(ctx):
    """Hide: wit & wiles check, else back to the caller's options."""
    die = yield ctx.die("1d6", "hiding")
    if ctx.wits() >= die:
        return ctx.hide()
    return ctx.retry("couldn't think of a hiding place fast enough")
```

`yield` always means *ask the player and wait*. Emitting narration or state
changes is a plain call (`ctx.say`, `ctx.wound`) and does not suspend.

**Determinism rule:** a handler may not call `random`, read the clock, or read
anything outside `ctx`. All randomness enters as player input. Replay depends on
this; plan 09 tests it.

### D3 — The engine yields events; it never prints

Handlers produce `Event` objects. The UI decides what to show, what to speak, and
what to suppress. This generalises the current stdout/stderr contract: instead of
two streams, an event carries `voice=True|False`. Section ids, rule cites, and
table rows are `voice=False`; prose is `voice=True`. The old invariant — "would a
person say this out loud?" — survives as a field.

### D4 — The engine is a library, not a subprocess

`narrator.py` shells out to `bp` for everything. The engine imports `state`,
`procedures`, `combat`, `creatures`, `tables` directly. `bp.py` stays as-is: it
remains the reference CLI for looking things up by hand, and it is how you check
the engine against the book. Neither program calls the other.

### D5 — Refuse rather than guess

`procedures.Refuse` already exists and is the right instinct. When the engine
cannot resolve something — an ambiguous hex, a missing section, an errata gap, a
rule the handler does not cover — it raises `Refuse`, the UI explains, and the
player rules on it. A refusal is a bug report with a section id attached. Never
paper over one with a plausible default.

## Repository conventions these plans keep

- **No game text in git.** `sections.json`, `tables.json`, `travel.json`,
  `map.json` are generated and git-ignored. Hand-maintained data (`errata.json`,
  `procedures.json`, `creatures.json`, `map-fixes.json`) is in git because it
  encodes *structure, order and corrections*, not the book's prose. New data files
  introduced by these plans follow the same rule — see Open question Q1.
- **`BP_GAME` for every test.** A bare command edits the live save. Clean up
  afterwards and never leave `saves/current` pointing at a deleted save.
- **Deterministic regeneration.** Nothing in `data/` that is generated gets
  hand-edited.

## Layout

```
src/
  bp.py, state.py, procedures.py, combat.py, creatures.py, tables.py   unchanged libraries
  narrator.py                                                          legacy client, untouched until plan 10
  engine/
    __init__.py
    types.py        Event, Ask, Outcome, Refuse
    ctx.py          the handler-facing API
    machine.py      driver, journal, replay, save/commit
    sections.py     handler registry and dispatch
    rules/
      graph.py      r300-r343              plan 02
      combat.py     r220, r343, r218       plan 03
      travel.py     r204, r205, r207       plan 04
      day.py        r203, r215-r217, r222  plan 05
      effects.py    deferred queue         plan 06
      events/       the e-sections         plan 07
  ui/
    term.py         the deterministic terminal front end
    parse.py        input interpretation (no LLM)
  narrate/          plan 10
play2               entry point until it replaces ./play
```

## Status board

Update this table as the single source of truth. Do not track status inside the
individual plans.

| # | Plan | Depends on | Status |
|---|---|---|---|
| 01 | [Engine core](01-engine-core.md) — protocol, journal, replay, terminal UI | — | ☐ not started |
| 02 | [Encounter graph](02-encounter-graph.md) — r300–r343 | 01 | ☐ not started |
| 03 | [Combat](03-combat.md) — r220, r343, r218 | 01, 02 | ☐ not started |
| 04 | [Travel](04-travel.md) — r204, r205, r207 | 01, 02 | ☐ not started |
| 05 | [Daily loop](05-daily-loop.md) — r203, r215–r217, r222 | 01, 04 | ☐ not started |
| 06 | [Deferred effects](06-deferred-effects.md) — the pending queue | 05 | ☐ not started |
| 07 | [Event sections](07-event-sections.md) — the 171 | 02–06 | ☐ not started |
| 08 | [Economy and items](08-economy-and-items.md) — r206, r214, r225/r226, e180–e194 | 05 | ☐ not started |
| 09 | [Testing](09-testing.md) — headless play, invariants, coverage | 01 (grows with each) | ☐ not started |
| 10 | [Narration](10-narration.md) — the optional LLM layer | 05 | ☐ not started |

A playable game exists after **05**. Plans 06–08 fill in the long tail; 09 runs
throughout; 10 is the reward.

## Glossary

- **Ask** — a suspension point. The engine needs a die, a choice, a number, or a
  confirmation from the player.
- **Event** — something the engine did, addressed to the UI. Never a question.
- **Outcome** — a handler's return value: the control-flow verb (`Goto`,
  `EnterCombat`, `Retry`, `EndEvent`, `EndDay`, …).
- **Journal** — the ordered list of player inputs since the start of the day.
- **Frame** — one entry on the encounter stack; what `Retry` pops back to.
- **Commit** — write a new day-start state and clear the journal.

## Open questions

**Q1 — Where does hand-encoded rule structure live?** Plan 02 needs the branch
logic of r300–r343 in some machine-readable form. Three options:

- (a) A `data/graph.json` in git, treated like `procedures.json` — cites sections,
  encodes structure only. *Recommended:* the precedent exists and the file is
  useless without the git-ignored `sections.json` beside it.
- (b) The same file, git-ignored, hand-authored — safest on copyright, but the
  repo no longer bootstraps for anyone else.
- (c) Structure inline in Python handlers — code is already in git, and the
  parameters (bribe amounts, hire terms) genuinely vary per caller.

The plans assume (a) with (c) for anything that needs real logic. Revisit before
starting plan 02.

**Q2 — Does the player still roll their own dice?** The current design is
emphatic that they do. The engine supports both (`ctx.die` can accept a typed
number or roll one), but the *default* changes the feel a lot. Recommended:
player rolls by default, `--auto-dice` for headless testing and for players who
would rather not. Plan 09 needs auto-dice regardless.

**Q3 — "Number of characters in your party" (r303, r319, r320).** Living men, or
men plus mounts? Reading favours men. Flag it in the handler and let the player
override.

**Q4 — Does `./play` survive?** Plan 10 assumes the new UI becomes the default and
`narrator.py` is retired or reduced to the narration layer. Nothing before plan 10
touches it, so the decision can wait.
