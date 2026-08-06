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

- [ ] Extend the `Outcome` verbs with a `time_cost`: `"rest_of_day"` (default),
      `"minutes"`, `"none"`. `EndEvent(time_cost=...)`.
- [ ] The graph's terminals set it: `Pass()` and the talk sections are
      `"minutes"`; a won fight with no survivors is `"minutes"`; escape and hide
      are `"rest_of_day"`.
- [ ] After each hex, if movement remains and the day is not spent, offer to
      continue.

## Work items

- [ ] `src/engine/rules/travel.py` — `travel_action(ctx)`: ask destination and
      speed, then `move_one_hex(ctx, a, b)` per hex following the order above.
- [ ] Port `procedures.plan_move`'s ordering; keep `check_lost`/`check_event` as
      the threshold authority so there is one implementation.
- [ ] Guide desertion on a lost result — only one guide deserts, and only the one
      chosen for the day. The party model needs a `guide` flag (it has one) and
      the flow needs to ask which guide when there is more than one.
- [ ] River-crossing state: a "crossed but lost" party ends in A with a note that
      the far side is reachable tomorrow. Store it as a day flag on the save.
- [ ] Road and airborne second-event rules.
- [ ] Airborne drift.
- [ ] Refuse on the hexes the map cannot answer for: `edge_conflicts` in
      `map.json`, the terrain-straddling hexes, and `1720`. `bp hex` already
      refuses on these — reuse that judgement, do not re-derive it.

## Tests

- **Ordering:** a golden journal per shape — plain move, road move, river
  crossing, airborne move, lost with a guide, lost without.
- **Terrain sourcing:** assert lost reads the *from* hex and events the *to* hex.
  This is the single most likely bug; test it directly rather than through a
  scenario.
- **Thresholds:** every row of `travel.json` triggers at its stated number and not
  at one less.
- **Guide:** −1 applied; desertion roll only on a lost result; only one guide
  leaves.
- **Speed gating:** a party with one man on foot is never offered two hexes.
- **Refusal:** moving into `1720` refuses rather than guessing.
- **Map invariants** (regression, from CLAUDE.md): `bp hex 0101` is north of the
  Tragoth, `0102` is south. Assert through `procedures.north_of_tragoth` so a
  re-transcribed map is caught here too.

## Open questions

- **Does the player pick the route, or the destination?** Picking a destination
  and pathfinding is friendlier but hides the hex-by-hex decisions the game is
  made of, and getting lost invalidates a route anyway. Recommended: ask for the
  next hex, each hex, offering the six neighbours with their terrain.
- **Raft travel (r213)** only arises from special events. Defer to plan 07, but
  leave the "no lost check" branch in place for it now — `travel.json` already
  carries the eleven-row raft table.
