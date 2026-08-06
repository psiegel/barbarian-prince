# 05 — The daily loop (r203, r215–r217, r222)

**Goal:** the loop that makes this a game rather than a rules library. Pick an
action, resolve it and its events, feed the party, pay them, house them, advance
the clock, check for the end.

**Depends on:** 01, 04. Plan 06 plugs into the hook points defined here.

**Done when:** you can play from setup to day 71 or to 500 gold in the Northlands
without the engine asking you anything the rules do not ask, and without a model
being involved at any point.

---

## The shape of a day

```
DAWN
  ├─ fire scheduled effects for "dawn"        (plan 06 hook)
  ├─ apply standing conditions                (starvation r216b, desert r120, ...)
  └─ report the day, the date, the hex
ACTION  (r203 — exactly one, all followers do the same)
  ├─ Rest in this hex                    r222   any hex
  ├─ Travel                              r204   any hex        → plan 04
  ├─ Search for a cache                  r214   any hex        → plan 08
  ├─ Seek news & information             r209   town/castle/temple
  ├─ Seek to hire followers              r210   town/castle
  ├─ Seek audience with the local lord    r211   town/castle/temple
  ├─ Submit an offering                  r212   temple
  └─ Search a ruins                      r208   ruins
  └─ then: any events the action raised, resolved to completion
DUSK
  ├─ fire scheduled effects for "dusk"        (plan 06 hook)
  ├─ e002 check, if north of the Tragoth
  ├─ food (r215): hunt, purchase, or stores; mounts fodder or stable
  ├─ starvation (r216) for anyone unfed
  ├─ wages — every henchman with pay_per_day, at the evening meal
  ├─ lodging (r217) in a town, castle or temple
  ├─ fire scheduled effects for "night"       (plan 06 hook)
  └─ commit: write the new day_start, clear the journal
CLOCK
  └─ advance; check end conditions
```

## Actions

`r203` is emphatic that there is one action per day for the whole party, that some
events grant a bonus action, and that some require skipping days. So:

- [ ] The day carries an `actions_remaining` counter, normally 1, that events can
      increment.
- [ ] `EndDay` and travel events set it to 0.
- [ ] A "skip N days" outcome advances the clock without offering actions, but
      still runs dusk for each day — the party still eats.

Offer only the actions the hex allows. `procedures.LODGING_FEATURES` and the hex
features in `map.json` already answer this; do not re-derive it.

**Rest (r222)** is not "do nothing": it checks for an encounter as if travelling
into the hex you occupy, and heals one wound per character only if no event
involving combat or an escape occurred. It also improves hunting (r215b: extra
characters may hunt on a rest day, each adding +1).

- [ ] `rest_action(ctx)` — the entry-event check reuses plan 04's event check with
      the current hex's terrain, then heals if the day stayed quiet.
- [ ] Poison wounds do not heal (r222). Needs the wound model from plan 03.

The four settlement actions (r208–r212) each have their own table and lead into
events. They are small; treat them as five more entries in plan 07's queue and
stub them with `Refuse` until then. **The loop is complete without them** — travel
and rest are enough to play.

## Dusk

`narrator.run_dusk` already walks this correctly and its rules are worth keeping
verbatim. Port them; do not rediscover them:

- **Ask the player for judgment, never for a lookup.** Hunt or not, buy or eat
  stores — theirs. Which side of the Tragoth, whether the hex can be hunted —
  the map's, via `procedures.north_of_tragoth` and `hunting_here`.
- **A step already done today is not done again.** `already_today()` reads the
  log. Under the new engine the journal makes this exact rather than heuristic —
  the day's steps are recorded, so check those instead of parsing the log.
- **A step that refuses stops the walk.** The day stays open; the player decides.

What changes: the walk no longer shells out to `bp`, and `tell()`/`spoken()` go
away because `Event.voice` (D3) carries the same distinction.

Order within dusk matters and the sections interlock:

1. **e002** first — it is an encounter and can end elsewhere.
2. **Hunting (r215b)** before the meal. In farmland, a `1d6` after the hunt for a
   possible pursuit event (e017 on a 5; e050 on a 6, at +2). A `12` on the hunt
   dice wounds the hunter regardless of success.
3. **The meal (r215a/d/e).** One unit per man; two per mount that cannot forage;
   **doubled in a waterless desert hex**. `state.food_needed` already computes
   this. Purchase at 1gp in a settlement; hunting is prohibited there.
4. **Fodder (r215f)** — animals graze free in farmland, countryside, forest or
   hills, but not swamp, desert or mountains, and must be stabled at 1gp in a
   settlement.
5. **Starvation (r216)** for anyone unfed: `2d6` minus wit & wiles per follower,
   4+ deserts; survivors take the r216b penalties. The player may not withhold
   food from followers unless going without themselves.
6. **Wages** — 1 or 2 gold per day per henchman, from the terms recorded in plan
   02. Unpaid henchmen leave.
7. **Lodging (r217)** — 1gp per room; priests, monks, magicians, wizards and
   witches need singles, others share two to a room; mounts 1gp stabled.
   Declining costs a `2d6` minus wit & wiles desertion check per character, and a
   `1d6` per mount where 4+ means it is stolen. `state.lodging_cost` already
   knows the room maths and needs the class list.

## The clock and the end

The goal is recorded in `data/procedures.json` already: 500 gold and back in the
Northlands within ten weeks. Day 71 is a loss.

- [ ] `check_end(ctx)` after the clock advances:
      - player dead → loss, immediately, wherever it happens (raised from plan 03)
      - day > 70 → loss
      - gold ≥ 500 and the party is in the Northlands → win
- [ ] "In the Northlands" is rows 01–02 — the same derivation
      `procedures.north_of_tragoth` performs. Reuse it; do not hard-code rows.
- [ ] `EndGame(outcome, why)` verb; the UI prints a final sheet and the log.

## Setup

`procedures.setup_steps` and `narrator.run_setup` already walk the seven fixed
steps of r202/r225/e001 with no model involved. Port them to the engine as the
`new_game` flow: read e001#premise, roll wit & wiles (r202, a 1 counts as 2),
roll starting gold (r225 at wealth code 2), read e001#caravan, roll `1d6` for the
starting hex, read e001#dawn, then day 1.

The `readings` anchors in `procedures.json` handle the mid-section pauses and
already refuse rather than print the wrong passage if an anchor stops matching.

## Work items

- [ ] `src/engine/rules/day.py` — `day_flow(ctx)`, `dawn()`, `choose_action()`,
      `dusk()`, `advance_clock()`, `check_end()`.
- [ ] `rest_action`, and stubs for r208–r212 that `Refuse` with a clear message.
- [ ] Port `run_dusk`'s sequence, keeping its four rules.
- [ ] Port `run_setup` as `new_game`.
- [ ] Named hook points — `on_dawn`, `on_dusk`, `on_night`, `on_enter_hex`,
      `on_settlement` — that plan 06 subscribes to. Define them here even though
      nothing fires yet.
- [ ] Commit-at-dusk: the day boundary is the durability boundary (D1).

## Tests

- **A full week** driven from a journal, asserting the sheet each dawn.
- **Food arithmetic:** man/mount counts, the desert doubling, fodder terrain,
  settlement prohibitions — table-driven against `state.food_needed`.
- **Starvation and lodging desertions** at each `2d6` total against several wit &
  wiles values, including the boundary at exactly 4.
- **Idempotence:** a hunt already recorded today is not offered twice.
- **Refusal:** the day does not close when food or gold is short; it stays open
  and the player chooses.
- **End conditions:** day 71, 500 gold in row 01, 499 gold in row 01, 500 gold in
  row 03, and the prince's death mid-fight.
- **Setup:** a wit & wiles roll of 1 yields 2.

## Open questions

- **Is dusk skippable?** The rules require eating every day. But a player wanting
  to fast-forward through a quiet week will want it. Recommended: no skip; add a
  `--brisk` mode instead that auto-answers dusk with the player's standing
  preferences (hunt if possible, else stores, always lodge) and reports what it
  did. It must still journal every input.
- **Bonus actions.** Which events grant them is discovered in plan 07. The
  counter exists from day one so nothing has to be retrofitted.
