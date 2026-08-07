# 04 — Travel (r204, r205, the r207 table)

**Goal:** move the party across the map with every check in the right order —
getting lost, river crossings, roads, airborne movement, and travel events — and
hand any event that fires to the engine as a normal section.

**Depends on:** 01, 02 (travel events land in the graph).

**Done when:** a two-hex mounted move runs both hexes' checks, a road move never
rolls for lost, a river crossing runs its own lost-and-event pair before the
destination's, and a travel event stops the day.

---

## The r207 table is `data/travel.json`

`r207` is **not** in `sections.json` — the booklet prints it on a separate sheet.
`data/travel.json` is that table, and it is complete: ten rows, each with a lost
threshold, an event threshold, and six event references.

```
farmland      lost 10+  event 8+       swamp        lost 5+   event 10+
countryside   lost  9+  event 9+       desert       lost 6+   event 10+
forest        lost  8+  event 9+       cross river  lost 8+   event 10+
hills         lost  8+  event 10+      on road      never     event 9+
mountains     lost  7+  event 9+       airborne     lost 12+  event 10+
```

`procedures.check_lost` and `check_event` already read it, apply the guide
modifier, and return a verdict. `procedures.plan_move` already emits the ordered
checklist for one hex — **that ordering is the spec for this plan**; the work is
executing it rather than printing it.

## Order of operations for one hex

The order is not obvious and the sections disagree about which terrain is
consulted when. Getting this wrong is silent, so encode it once:

1. **Lost** uses the terrain you are **leaving** (r205).
2. **Events** use the terrain you are **entering** (r204b).

```
leave hex A for hex B
├─ by road?          no lost check ever (r204c/r205b); use the "on road" event row
├─ by raft?          no lost check (r205b, r213)
├─ airborne?         use the "airborne" row for lost (r205c)
└─ otherwise         "lost" threshold of A's terrain, −1 with a guide (r205a)

if lost:
   ├─ guide present → 1d6, 4+ and the guide deserts (r205a)
   ├─ party stays in A, the day's movement is over, no alternate action (r204)
   └─ still roll a travel event for B, as if entered (r205)
        └─ except when lost crossing a river — then no event (r205d)

if a river lies on the A/B edge and you are not flying (r204e/r205d):
   ├─ lost check on the "cross river" row first
   │    └─ if lost: you did not cross; day over
   ├─ event check on the "cross river" row
   └─ then continue into B: lost check for B's terrain, then B's event
        └─ if lost after crossing: you are across the river but end in A.
           Tomorrow you may enter any hex on the far side. (Track this.)

event check: 2d6 ≥ threshold → 1d6 → the row's six event refs → run that section
```

Road and airborne have a second-event rule that is easy to miss:

- **Road (r204c):** consult the road row first. If **no** road event occurs, then
  consult the terrain row for the hex entered. If a road event does occur, the
  terrain check is optional (offer it; do not force it).
- **Airborne (r204d):** every hex uses the airborne row. On the **last** hex of the
  day, if no airborne event occurred, consult the terrain row for the landing hex.
  Airborne events can change where you land, so read the terrain of where you
  actually are.
- **Airborne and lost (r205c):** on a lost result, `1d6`; on 4+, roll `1d6` again
  for a drift direction and move one hex that way before landing.
  `procedures.DRIFT` already has the direction map.

## Speeds (r204a)

| Party | Hexes per day |
|---|---|
| anyone on foot | 1 |
| all mounted | 1 or 2, player's choice |
| all winged | 3 airborne, or 1–2 short-hopping as if mounted |

Ask the speed once at the start of the move, not per hex. The party's composition
decides which options are offered — `ctx.all_mounted()` and `ctx.all_flying()`
from plan 01.

## Events end the day (r204f)

A travel event normally consumes the rest of the day. Three exceptions:

- Events resolved purely by combat **where every enemy was killed** — travel may
  continue with any speed remaining.
- Events that were only talk or negotiation — a few minutes; travel may continue.
- Events that explicitly grant extra movement.

This means the travel flow cannot simply run the event and stop. It needs the
event's outcome back:

- [x] `EndEvent(time_cost=...)` — already landed in plan 01.
- [x] The graph's terminals set it: `Pass()` and the talk sections are
      `"minutes"`; a won fight is `"minutes"`; escape and hide are
      `"rest_of_day"`. `travel.spends_day()` reads it.
- [x] After each hex, if movement remains and the day is not spent, offer to
      continue.

## Work items

- [x] `src/engine/rules/travel.py` — `travel_action(ctx)`, with `overland`,
      `by_road`, `over_river` and `fly` behind `move_one_hex`.
- [x] Port `plan_move`'s ordering. The thresholds stayed in `procedures.py`:
      `lost_verdict` and `event_verdict` were split out as the numbers, and
      `check_lost`/`check_event` now print what those decide, so the CLI and the
      engine cannot drift. A test drives every row at every total through both.
- [x] Guide desertion — asked for by name when there is more than one, `-1`
      applied, the desertion roll only on a lost result, and only one leaves.
- [x] River-crossing state: `day_flags["across_river"]` holds the far hex.
- [x] Road and airborne second-event rules.
- [x] Airborne drift, including a drift that would leave the map.
- [x] Refuse on what the map cannot answer: the seven straddle hexes and any
      hexside the transcription contradicts itself about, both through
      `procedures`. **`1720` is no longer one of them** — the map data now gives
      it countryside and the town of Lullwyn, so the note in this plan and in
      CLAUDE.md is stale.

## Tests

`tests/test_travel.py`, 49 tests. Suite total 215, about 37 seconds.

- [x] **Ordering:** one per shape — plain, road, river, airborne, lost with a
      guide and without.
- [x] **Terrain sourcing:** asserted head on, with a fixture picked so both
      columns differ. `0104` countryside (lost 9+, event 9+) into `0204` hills
      (lost 8+, event 10+): a lost roll of 8 must *not* be lost, and an event
      roll of 9 must *not* fire. Either mistake flips exactly one of them.
- [x] **Thresholds:** every row triggers at its number and not one below, in
      both columns, and `check_lost`'s prose agrees with `lost_verdict` for
      every row × every total × guide or not.
- [x] **Guide:** all four properties.
- [x] **Speed gating:** on foot is never offered two hexes, and one man on foot
      keeps a part-mounted party walking.
- [x] **Refusal:** a straddle hex refuses; it is still *offered* as "terrain
      unclear" rather than hidden, so the player can see why.
- [x] **Map invariants:** `0101` north of the Tragoth, `0102` south, and `1401`
      south despite being in row 01.

## Open questions, decided

- **Route or destination?** The next hex, each hex, offered as the six
  neighbours labelled with their terrain — `NE 0203 (forest)`. Off-map
  neighbours are dropped; a straddle hex is offered as "terrain unclear" so it
  is visible rather than silently missing.
- **Raft travel (r213)** left to plan 07 as planned. `lost_verdict` already
  returns `None` for a row that cannot get lost, which is the branch it needs.

---

## As built

Landed. 215 tests. `./play2 --flow travel` walks the map with every check in
order and no model in it.

### Two bugs worth recording

**`ctx.invoke(sid, **params)` could not pass a `sid` through.** Running a travel
event is `ctx.invoke("encounter", sid=ref)`, and the positional argument was
also called `sid`, so Python saw two values for one parameter. Renamed to
`target`. A small thing that only shows up when one flow invokes another and
wants to name a section — which is exactly what plan 05 will do all day.

**`state.Refuse` subclasses `procedures.Refuse`, so catching the subclass missed
the parent.** `hex_terrain` and `hexside` both raise the base class, and the
engine aliased `Refuse = state.Refuse` everywhere — including `play2`, which
would have shown a traceback instead of a clean message the first time a player
walked toward a straddle hex. Everything now aliases `procedures.Refuse`, which
catches both. Worth remembering: the raise site and the catch site have to agree
about which end of a hierarchy they name.

### The airborne landing check moved out of the hex loop

r204d puts the terrain check on the *last* hex of the day, and the last hex is
not known while the loop is running — the player may break the flight off early.
It is now done once, after the loop, off `trip["aloft_event"]`, and it reads the
hex the party is actually *in*, which after a drift is not the one it aimed at.

### The lost/event asymmetry, and its exception

Stated at the top of `travel.py` because it is the thing most likely to go
silently wrong:

```
lost  uses the terrain you LEAVE       (r205)
event uses the terrain you ENTER       (r204b)
```

with one printed exception — after crossing a river, the second lost check uses
the terrain being *entered* (r205d). Both readings are the booklet's own;
`data/procedures.json` has recorded the conflict under `lost_terrain_conflict`
since before this plan. The test fixture is chosen so that getting either one
backwards flips a result.

### One implementation of the thresholds

`check_lost` and `check_event` returned prose for the CLI. Rather than parse it
or write a second copy, `lost_verdict` and `event_verdict` were split out as the
numbers and the prose functions now print what they decide. A test drives every
row at every total, with and without a guide, and asserts the two agree — so
`bp travel` and `play2` cannot diverge on a threshold.

### Deferred

- **Raft travel (r213)**, as planned — plan 07.
- **`day_flags["across_river"]`** is recorded but nothing reads it yet. Plan 05
  owns tomorrow, and the rule is that the party may then enter any hex on the
  far side.
- **The road's optional second check** is offered as a question when a road
  event occurred and left the day alive. r204c makes it optional and the engine
  does not force it.
