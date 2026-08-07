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

- [x] `day_flags["actions_remaining"]`, normally 1, that events can increment.
- [x] The action loop drains it; travel and rest each spend one.
- [ ] A "skip N days" outcome — **deferred**. Nothing raises one until plan 07
      finds the events that do, and the counter is in place for it.

Offer only the actions the hex allows. `procedures.LODGING_FEATURES` and the hex
features in `map.json` already answer this; do not re-derive it.

**Rest (r222)** is not "do nothing": it checks for an encounter as if travelling
into the hex you occupy, and heals one wound per character only if no event
involving combat or an escape occurred. It also improves hunting (r215b: extra
characters may hunt on a rest day, each adding +1).

- [x] `rest_action(ctx)` — reuses `travel.event_check` with the current hex's
      terrain, then heals if the day stayed quiet. "Quiet" is
      `day_flags["fought"]`/`["escaped"]`, set by plan 03's combat and escape.
- [ ] Poison wounds do not heal (r222) — **still plan 08**, which owns the wound
      model. Recorded there.

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

- [x] `check_end(ctx)` after the clock advances, all three conditions.
- [x] "In the Northlands" is `procedures.north_of_tragoth`, not a row number.
- [x] `EndGame(outcome, why)`; the terminal prints the outcome.

## Setup

`procedures.setup_steps` and `narrator.run_setup` already walk the seven fixed
steps of r202/r225/e001 with no model involved. Port them to the engine as the
`new_game` flow: read e001#premise, roll wit & wiles (r202, a 1 counts as 2),
roll starting gold (r225 at wealth code 2), read e001#caravan, roll `1d6` for the
starting hex, read e001#dawn, then day 1.

The `readings` anchors in `procedures.json` handle the mid-section pauses and
already refuse rather than print the wrong passage if an anchor stops matching.

## Work items

- [x] `src/engine/rules/day.py` — `day_flow`, `dawn`, `choose_action`, `dusk`
      and its six steps, `check_end`.
- [x] `rest_action`, and r208-r212 refusing by name with the section to look up.
- [x] Port `run_dusk`'s sequence, keeping its four rules.
- [x] Port `run_setup` — as the first step of `day_flow` rather than a separate
      flow; see "As built".
- [x] Named hook points, with `day.HOOKS` and `day.fire(ctx, hook)`.
- [x] Commit-at-dusk — in the **machine**, not the rules; see "As built".

## Tests

`tests/test_day.py`, 68 tests. Suite total 287, about 65 seconds.

- [x] Days roll over, the journal clears at the boundary, and a day in progress
      resumes in a fresh process.
- [x] **Food arithmetic:** a unit a man, mounts foraging free or eating two,
      the waterless-desert doubling, an oasis having water, and the same numbers
      asserted straight against `state.food_needed`.
- [x] **Desertion** at exactly 4 and at 3, against two wit & wiles values, with
      the Prince never rolled for and each follower rolled separately.
- [x] **Idempotence:** steps are marked done; declining a hunt is *not* marked,
      so the offer comes round if the meal needs it; flags clear at dawn.
- [x] **Short of food:** the choice goes to the player - share out (no r216b
      penalties) or all go without - rather than the day stopping.
- [x] **End conditions:** day 71, 500 in the Northlands, 499 in the Northlands,
      500 south of the river, and a dead Prince.
- [x] **Setup:** a 1 yields 2, gold off the r226 grid for all six rolls, the
      caravan hex, the 0801/0901 typo corrected, and setup not run twice.
- [x] Beyond the plan: the actions offered per hex feature, an unimplemented
      action naming its section, and the hooks firing.

## Open questions, decided

- **Dusk is not skippable.** The rules require eating every day and the engine
  asks accordingly. `--brisk` was **not** built: standing preferences are a real
  piece of design (what does "always lodge" mean when the purse is short?), and
  `--auto-dice` already removes most of the tedium for a test run. Worth doing
  once the game has been played enough to know what the preferences should be.
- **Bonus actions.** The counter exists; nothing increments it until plan 07
  finds the events that do.

---

## As built

Landed. 287 tests. `./play2 --new mygame --flow day` sets a game up from r202
and r225 and plays it, day after day, with no model in it.

### The day boundary is the machine's, not the rules'

`day_flow` runs exactly one day and returns `EndDay`. The machine sees that,
commits (the finished day becomes the new `day_start`, the journal empties) and
starts the flow again — `Machine._roll_over`. So D1's durability boundary lives
in one place, the rules never call `commit`, and a save is never left mid-turn.

The first proof of this was a three-day run where dawn of day 2 arrived on its
own with an empty journal behind it.

### Setup is the day flow's first step

The plan had `new_game` as a separate flow, which would have needed the cursor
to change from `new_game` to `day` — something `ctx` cannot do and should not
be able to. Instead `day_flow` checks `needs_setup(ctx)` (the Prince has no wit
& wiles yet) and walks r202, r225 and e001 before dawn on day 1. One flow, one
cursor, and a blank sheet plus `--flow day` is the whole of starting a game.

`play2 --new` no longer wants `--wits`: it writes a blank sheet and the r202
roll fills it in.

### A save edited outside the engine now says so

Mid-day the sheet on disk is a *derived* value — `resume` rebuilds it from dawn
plus the journal — so `bp gold +50` in the middle of a day is discarded. That is
correct, and it was silent. It cost a confusing minute during this plan's own
integration run, which is exactly what it would cost a player.

`engine.sheet_hash` is written on every persist and checked on load; a mismatch
warns once, at the top of the next turn, and says what to do instead. Between
days the journal is empty, the sheet *is* dawn, and `bp` remains safe — which is
the invariant plan 01 set up and this now enforces rather than assumes.

### Dusk, ported

`narrator.run_dusk`'s four rules are kept and the sequence is unchanged: e002
first (it is an encounter and can end elsewhere), then the hunt before the meal,
the meal, wages, lodging. Two details worth keeping visible:

- **Declining the hunt is not "done".** The meal below may need the food, so the
  offer has to be able to come round again. Only a hunt that happened is marked.
- **Which side of the Tragoth is never asked.** It is the map's answer, through
  `north_of_tragoth`. The player is asked whether to hunt and whether to buy a
  meal — judgment — and nothing else.

Short of food, r216a's two options go to the player: share out what there is (no
r216b penalties, but the desertion rolls still happen) or everyone goes without,
the Prince included. The day does not stop either way.

### Deferred, and where it went

- **r208–r212**, the five settlement actions, refuse by name with the section to
  look up. The loop is complete without them, as the plan said.
- **r214 caches** refuse to plan 08.
- **Poison wounds** stay in plan 08 with the rest of the wound model.
- **"Skip N days"** has its counter and no caller yet.
- **`--brisk`** deliberately not built; see the decided question above.
