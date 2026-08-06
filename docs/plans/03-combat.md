# 03 — Combat (r220, r343, r218)

**Goal:** fight a battle interactively, round by round, with the player choosing
match-ups and rolling strikes — and keep the existing auto-resolver as a mode of
the same code, not a second implementation.

**Depends on:** 01, 02.

**Done when:** an encounter reached through the graph fights to a conclusion in
either mode, wounds land on the sheet as they happen, and the survivors' wealth
codes come back for the treasure rolls.

---

## What already exists

`src/combat.py` has the arithmetic right and it is reusable:

| Function | Keep |
|---|---|
| `combat.strike(striker, target, rng)` | the r220c calculation |
| `state.strike_mod`, `state.target_mod` | the three special modifiers |
| `state.wounds_for(total)` | the combat table tiers |
| `combat.rout_check` | r220f, including the skill/endurance 9 immunity |
| `combat.match_up`, `target_pool` | r220b pairing |
| `state.effective_cs`, `dead`, `unconscious`, `serious` | condition rules |

What it does not have is a player in the loop: `combat.fight()` rolls everything
itself through an injected `rng` and returns a log.

## Design

### One resolver, two dice sources

Refactor so the round structure is a generator and the dice come from `ctx`:

```python
def battle(ctx, spec):
    if spec.surprise:                      # r220d free bonus strike
        yield from strike_phase(ctx, side=spec.surprise, bonus=True)
    first = spec.surprise or spec.initiative
    while both_sides_alive(ctx):
        pairs = yield from choose_matchups(ctx)      # r220b
        for side in order(first):
            yield from strike_phase(ctx, side, pairs)
            if not both_sides_alive(ctx): break
        if ctx.rout_enabled and killed_this_round:
            yield from rout_check(ctx)               # r220f
```

`--auto-dice` (plan 01) already answers every `Ask` by rolling, so **auto-resolve
is not a separate code path** — it is the same generator with the dice source
switched. Delete `combat.fight()`'s duplicate round loop once this passes the same
tests; keep `strike()` and `rout_check()`.

### Player asks per round

Keep these few and skip the ones with only one answer:

1. **Match-ups** (r220b) — skip when both sides have one character, or offer
   "same as last round" as the default. Auto-pair by default; ask only when the
   player asks to.
2. **Strike or flee** (r220e) — once per round, all or none. Only offer when
   escape is legal for this encounter.
3. **The 2d6 per strike** — one `Ask` per strike unless auto-dice.
4. **Rout attempts** (r220f) — ask once at the start whether to attempt routs at
   all, then roll automatically after each kill. Warn once that routed enemies
   leave with their treasure.

### r343 Victim selection

Implement once, here; called from the graph and from a dozen event sections.
It is a loop over party members rolling `1d6` until a `6` selects the target,
cycling if necessary. Solo party: the player is the target with no roll.

- [x] `pick_victim(ctx)` in `rules/combat.py`, exported for events to call.
- [x] Guard the loop: capped at `MAX_PICKS` (200), then `Refuse`.

### r218 Escape and hide

The two terminal outcomes of half the graph, and both have real map consequences.

- **Escape (r218a):** `1d6` for direction (1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW),
  re-rolling for a river crossing or an off-map result — unless the whole party
  flies, which crosses rivers freely. The party is in the new hex for the rest of
  the day, **no new travel event fires for entering it**, and the day still ends
  with the meal.
- **Hide (r218b):** stay in the hex, day continues per the section.

Both already have the map primitives they need: `procedures.neighbours`,
`procedures.hexside` (for the river edge), `procedures.fmt_hex` (returns `None`
off-map).

- [x] `escape_hex(ctx)` — the re-roll loop, with a cap and a `Refuse` if every
      direction is blocked.
- [x] Set a day flag so plan 04's travel does not fire an entry event, and plan 05
      knows the day's action is spent. `g["day_flags"]["no_entry_event"]` holds
      the hex entered; the outcome's `time_cost` is `rest_of_day`.

### Foes and treasure

- Enemies come from `creatures.py` band rolls, already keyed by section, and go
  onto the sheet through the existing `foes` list.
- On victory, collect the dead's wealth codes and drive the r226 grid
  (`procedures.treasure_rows`) — one `1d6` per dead enemy. Routed enemies
  contribute nothing.
- On the player's death, the game ends. That check belongs to plan 05's clock but
  must be raised here — `EndGame("Cal Arath is dead")`.

## Work items

- [x] `src/engine/rules/combat.py`: `battle()`, `choose_pairs()`,
      `strike_phase()`, `rout_attempt()`, `pick_victim()`, `escape_hex()`,
      `hide_here()`.
- [x] Rewire `EnterCombat` from plan 02 to it — through the new `encounter`
      flow rather than inside the machine; see "As built".
- [x] Treasure collection on victory, feeding plan 08's item rules.
- [x] Split the *arithmetic* in `src/combat.py` so there is one implementation:
      `resolve_strike(striker, target, roll)` and `rout_from(g, dice, log)` take
      numbers, and the old `strike`/`rout_check` are those two with an `rng` on
      top. **The round loop stays** — `bp fight auto` still uses it and works.
      Deleting it belongs with plan 10, when `narrator.py` retires.
- [x] `EndGame` outcome verb, raised on the player's death.
- [x] Not on this list: `Machine.answer` now validates against the `Ask`.

## Tests

Combat is the easiest part of the game to test hard, because it is pure
arithmetic over a journal.

`tests/test_combat.py`, 39 tests. Suite total 151, about 35 seconds.

- [x] **Golden fights:** the r220c worked example from the section itself, both
      halves — the Dwarf's strike and the Prince's reply.
- [x] **Modifier coverage:** the three special modifiers, every combat-table tier
      (1, 2, 3, 5, 6 wounds), and the totals that are not on the table at all.
- [x] **Initiative matrix:** all four variants strike in the right order, and
      surprise grants exactly one bonus phase before the first round.
- [x] **Rout immunity:** skill *or* endurance 9+ is immune, and a fight against
      only immune enemies never asks the rout question.
- [x] **Termination:** 10,000 random fights all end — no infinite rounds, no
      negative wounds or gold. 0.7s per thousand, so the full ten thousand sits
      in the fast suite.
- [x] **Escape:** the map edge is re-rolled, a river is re-rolled on foot, a
      flying party crosses it, and every-direction-blocked refuses.
- [x] **Parity:** `strike` and `resolve_strike` agree over 50 seeds. Full-fight
      parity against `combat.fight()` was dropped deliberately — the two round
      loops are now genuinely different code, so the meaningful assertion is
      that they share the arithmetic, which this makes exact.
- [x] Beyond the plan: every wealth code in `creatures.json` has an r226 line;
      `size_from` matches what `size` rolls; and a bad value cannot reach the
      arithmetic (`TestAnswerValidation`).

## Open questions, decided

- **Match-up automation.** Asked **once** at the start of a fight — "choose who
  faces whom each round?" — and only when the party has more than one member.
  Answering no auto-pairs every round. Per-round prompting was the alternative
  and it makes a six-round fight unbearable.
- **Wounds on mounts.** Confirmed excluded. `combat.in_fight` already rejects
  mounts, so they are neither targets nor strikers, and `target_pool` inherits
  that.
- **Poison wounds.** Deliberately **not** added here. The plan said pick one
  place; plan 08 owns the wound model, and adding a half-enforced flag now would
  mean two places to change. Recorded in 08.

---

## As built

Landed. 151 tests. `./play2 --flow encounter --sid e003` plays an option table,
the graph, a fight and the treasure roll with no model anywhere in it.

### The composition layer, not the machine

`EnterCombat`, `EscapeHex` and `HideHere` stay *terminal outcomes of the graph*.
Making the machine carry them out was the obvious move and it is wrong: plan 02's
tests say what each of the 44 sections resolves to, and they can only say that if
resolving one does not fight a battle. It would also have exploded the path
explorer by a factor of 2d6 per strike.

So the hand-off lives one level up, in a new `encounter` flow:

```python
out = yield ctx.invoke(sid)
while isinstance(out, (EnterCombat, EscapeHex, HideHere)):
    out = yield ctx.invoke({"_combat", "_escape", "_hide"}[...])
```

It loops because a fight can end in an escape. Plan 05's day flow calls
`encounter` instead of invoking a section directly.

### `rout_check` rolled its own dice

It took an `rng`, so reusing it from a handler would have broken decision D2
silently — the rout would have been the one roll in the game the player did not
make. Split the same way as the strike: `rout_from(g, dice, log)` takes numbers,
`rout_check(g, killed, rng, log)` is that with dice on top, and the engine's
`rout_attempt` asks the player for one die per kill.

`combat.rout_from` writes its reasoning into a list of lines; the engine passes a
list subclass whose `append` emits an Event instead of collecting text.

### Two bugs the tests found

**`fmt_hex` formats coordinates that are not on the board.** `neighbours("0101")`
returns `0100`, `0000` and `0001` — ids for hexes that do not exist. `escape_hex`
checked `if not there`, which never fired, and the run died inside `hexside`.
Escape now checks membership in `map["hexes"]`, and refuses outright if the map
is not loaded rather than guessing which directions are legal.

**`Machine.answer` took anything.** A misaligned test driver passed the string
`"fight"` where a 2d6 total belonged and it surfaced as `int + str` three frames
deep. `types.validate` now checks every answer against its `Ask` before it is
journalled. This is stricter than the terminal — `ui/parse.py` already resolves
`"cal"` to `"Cal Arath"` — so a programmatic caller must pass exact values.

### r221b: the Prince can fall and the fight goes on

This was got wrong first time and is worth stating plainly, because it is the
rule that decides whether a bad fight ends the game or only nearly does.

When the Prince is knocked unconscious the battle **does not stop**. r221b rolls
one die for his followers' attitude: 4 or more and they close around him and
carry him along; 3 or less and they take him for dead, strip him of everything,
and leave. Either way, whoever is still standing keeps fighting over him — a
party can lose its Prince to unconsciousness and still win the battle, which is
the whole point of the rule.

- `combat.outcome` grew `halt_on_prince_down`. `bp fight auto` still stops for a
  ruling (it has no way to ask the attitude die); the engine passes `False` and
  handles r221b itself.
- `watch_the_prince` fires the roll **once**, the first time he goes down,
  checked after every strike phase including the surprise phase.
- Being carried is not flagged. It is exactly "unconscious with followers left",
  so it cannot go stale, and plan 08 reads the 20-load transport cost (r206a)
  off the same condition.
- Desertion drops the followers and zeroes the purse. Possessions go with them
  too; plan 08 owns those.
- Abandoned and helpless, he is finished off by the enemy and the game is lost
  through **r221c**, the ordinary death rule. Nothing had to be invented for it.

Two things follow from the same rule:

- **The pre-flight guards shrank.** Refusing to start a fight because the Prince
  is unconscious was wrong for the same reason — his followers can fight over
  him. What remains is: Prince dead (the game is already over), or nobody at all
  able to strike.
- **The round decision is only offered to a party that can act.** A helpless
  Prince alone cannot choose to press the attack, and cannot flee (r220e needs
  someone on their feet), so the round goes straight to the enemy's strike.

### Foes come from three places

In order: a fight already in progress, `data/creatures.json` keyed by the event
(the band die is asked for, and `creatures.size_from` does the arithmetic), or
the player reading the numbers off the section they have just heard. The third
is the honest fallback for the ~230 sections with no band spec; plan 07 will
narrow it.

`graph.py` now carries the originating event id along with `amount` and `terms`,
so a fight four sections deep still knows which event it came from.

### Worth knowing

- **The wound model.** Endurance is a fixed maximum and never changes; wounds
  count up against it. `serious` is `wounds * 2 >= end` (r221a), `unconscious` is
  `wounds == end - 1` (r221b), `dead` is `wounds >= end` (r221c). "One endurance
  left" and "one wound short of endurance" are the same state; the sheet stores
  the second.
- **Nothing in the game has endurance below 2.** Checked: the lowest the booklet
  prints is 2 (e017's mob, e032's ghosts, e048's magician, r210's hireling), and
  `creatures.json` agrees. `end` of 0 is the sentinel for stats not yet filled
  in — `dead`, `unconscious` and `serious` all guard on `end > 0` — so a
  character created with 0 would be unwoundable and one created with 1 would be
  unconscious before being touched. `do_join` and `ensure_foes` were accepting
  both; the floor is now 2, with tests pinning it at all three levels.
- A fight that ends in a way the rules do not settle — wiped out, Prince
  helpless, stalemate, a hundred rounds — **refuses** rather than ruling (D5).
- Treasure: one `1d6` per body against its wealth code. r226 prints seventeen
  codes, not every number, so a foe with an unlisted code warns rather than
  paying out silently. A `+A`/`+B`/`+C` result is reported and left for plan 08.
