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

- [ ] `pick_victim(ctx)` in `rules/combat.py`, exported for events to call.
- [ ] Guard the loop: it can in principle run forever. Cap iterations and
      `Refuse` rather than spin.

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

- [ ] `escape_hex(ctx)` — the re-roll loop, with a cap and a `Refuse` if every
      direction is blocked.
- [ ] Set a day flag so plan 04's travel does not fire an entry event, and plan 05
      knows the day's action is spent.

### Foes and treasure

- Enemies come from `creatures.py` band rolls, already keyed by section, and go
  onto the sheet through the existing `foes` list.
- On victory, collect the dead's wealth codes and drive the r226 grid
  (`procedures.treasure_rows`) — one `1d6` per dead enemy. Routed enemies
  contribute nothing.
- On the player's death, the game ends. That check belongs to plan 05's clock but
  must be raised here — `EndGame("Cal Arath is dead")`.

## Work items

- [ ] `src/engine/rules/combat.py`: `battle()`, `choose_matchups()`,
      `strike_phase()`, `pick_victim()`, `escape_hex()`, `hide_here()`.
- [ ] Rewire `EnterCombat` from plan 02 to it.
- [ ] Treasure collection on victory, feeding plan 08's item rules.
- [ ] Retire the duplicated round loop in `src/combat.py`; keep the maths.
- [ ] `EndGame` outcome verb, raised on the player's death.

## Tests

Combat is the easiest part of the game to test hard, because it is pure
arithmetic over a journal.

- **Golden fights:** a fixed journal of dice → an exact final sheet. Include the
  r220c worked example from the section itself as one case.
- **Modifier coverage:** each of the three special modifiers and each combat-table
  tier (1, 2, 3, 5, 6 wounds) fires at least once.
- **Initiative matrix:** all four `Fight` variants produce the right strike order,
  and surprise grants exactly one bonus phase before the first round.
- **Rout immunity:** an enemy with skill or endurance 9+ never routs.
- **Termination:** 10,000 random fights all end — no infinite rounds, no
  negative endurance, no striking by the dead.
- **Escape:** a hex with rivers on five edges escapes through the sixth; a corner
  hex never escapes off-map; a flying party crosses a river.
- **Parity:** the refactored resolver and the old `combat.fight()` agree on the
  same seeded dice, before the old one is deleted.

## Open questions

- **Match-up automation.** Auto-pairing is right for speed and wrong for tactics —
  choosing who faces the chieftain matters. Recommended: auto-pair with a
  one-key override, and always ask when the party outnumbers the enemy (r220b
  explicitly gives the player that choice).
- **Wounds on mounts.** r220 talks about characters; mounts in the party are not
  combatants. Confirm mounts are excluded from `target_pool` — check what
  `combat.py` does today.
- **Poison wounds** (r222) cannot be healed normally. The sheet has no flag for
  them. Add `poison` to the wound model here or in plan 08, but pick one.
