"""Travel (r204), getting lost (r205), and the r207 table.

The ordering is not obvious and the sections disagree about which terrain is
read when, so it is worth stating once at the top:

    the LOST check uses the terrain you are LEAVING          (r205)
    the EVENT check uses the terrain you are ENTERING        (r204b)

with one printed exception - after crossing a river, the second lost check uses
the terrain of the hex being entered (r205d). Both readings are the booklet's
own; `data/procedures.json` records the conflict under "lost_terrain_conflict".

`procedures.plan_move` prints this sequence for a person to follow. What is here
carries it out, off the same thresholds, so the two cannot drift apart.
"""

import procedures
import state

from ..sections import section
from ..types import EndEvent

Refuse = procedures.Refuse

FOOT, MOUNTED, AIRBORNE = "foot", "mounted", "airborne"


# --- the shape of the day's movement --------------------------------------


def speeds(ctx) -> list[tuple[str, str, int]]:
    """r204a: (label, mode, hexes) for each way this party could travel."""
    if ctx.all_flying():
        return [("fly, up to 3 hexes", AIRBORNE, 3),
                ("ride 2 hexes", MOUNTED, 2),
                ("ride 1 hex", MOUNTED, 1)]
    if ctx.all_mounted():
        return [("ride 2 hexes", MOUNTED, 2), ("ride 1 hex", MOUNTED, 1)]
    return [("on foot, 1 hex", FOOT, 1)]


def guides(ctx) -> list[dict]:
    return [c for c in ctx.men() if c.get("guide")]


def pick_guide(ctx):
    """r205a: one guide leads for the day, and it is that one who may desert."""
    have = guides(ctx)
    if not have:
        return None
    if len(have) == 1:
        return have[0]
    who = yield ctx.pick_char(among=have, why="r205a",
                              prompt="Which guide leads today?")
    return next(c for c in have if c["name"] == who)


def where_next(ctx, here: str):
    """Ask for the next hex, offered as the six neighbours with their terrain.

    A destination and a route would be friendlier and would hide the decision
    the game is made of - and getting lost invalidates a route anyway.
    """
    options, targets = [], {}
    for heading, there in procedures.neighbours(*procedures.parse_hex(here)).items():
        if not procedures.on_map(ctx.book, there):
            continue
        try:
            terrain = procedures.hex_terrain(ctx.book, there)
        except Refuse:
            terrain = "terrain unclear"
        label = f"{heading} {there} ({terrain})"
        options.append(label)
        targets[label] = there
    if not options:
        raise Refuse(f"there is nowhere to go from {here} - every neighbour is "
                     f"off the map")
    picked = yield ctx.choose(*options, why="r204", prompt="Travel where?")
    return targets[picked]


# --- the checks -----------------------------------------------------------


def lost_check(ctx, key: str, why: str, cite: str, guide):
    """2d6 against the Lost column. Returns True if the party is lost."""
    row = ctx.book.travel["terrain"][key]
    total = yield ctx.die("2d6", why=cite, prompt=f"Lost check, {why}")
    lost, thr, net = procedures.lost_verdict(ctx.book, key, total,
                                             guide=bool(guide))
    if lost is None:
        ctx.note(f"{row['name']}: you cannot get lost here", cite=cite)
        return False
    shown = f"{total} - 1 for the guide = {net}" if guide else f"{net}"
    ctx.note(f"{row['name']}: lost on {thr}+, you have {shown} -> "
             f"{'LOST' if lost else 'not lost'}", cite=cite)
    if lost and guide:
        yield from guide_deserts(ctx, guide)
    return lost


def guide_deserts(ctx, guide):
    """r205a: the guide who failed may desert in mortification. Only one goes,
    and the party is lost either way."""
    die = yield ctx.die("1d6", why="r205a",
                        prompt=f"Does {guide['name']} stay after failing?")
    if die >= 4:
        ctx.say(f"{guide['name']} cannot face you after that, and is gone.")
        ctx.drop(guide, why="deserted after failing as guide (r205a)")
    else:
        ctx.note(f"{die}: {guide['name']} stays", cite="r205a")


def event_check(ctx, key: str, why: str, cite: str):
    """2d6 against the Event column; on an event, 1d6 for which one, then run it.

    Returns the event's Outcome, or None if no event occurred.
    """
    row = ctx.book.travel["terrain"][key]
    total = yield ctx.die("2d6", why=cite, prompt=f"Travel event check, {why}")
    happens, thr = procedures.event_verdict(ctx.book, key, total)
    ctx.note(f"{row['name']}: event on {thr}+, you have {total} -> "
             f"{'EVENT' if happens else 'no event'}", cite=cite)
    if not happens:
        return None
    die = yield ctx.die("1d6", why="r207", prompt="Which event?")
    ref = row["event_refs"][die - 1]
    ctx.note(f"{row['name']} {die} -> {ref}", cite="r207")
    return (yield ctx.invoke("encounter", sid=ref))


# --- one hex --------------------------------------------------------------


def move_one_hex(ctx, here: str, there: str, mode: str, guide, trip: dict):
    """The ordered checks for a single hex of travel.

    Returns (moved, spent) - whether the party is now in `there`, and whether
    the day's movement is over. `trip` carries what the next hex, or the end of
    the day, needs to know.
    """
    side = procedures.hexside(ctx.book, here, there)

    if mode == AIRBORNE:
        return (yield from fly(ctx, here, there, guide, trip))
    if side["road"]:
        return (yield from by_road(ctx, here, there, guide))
    if side["river"]:
        return (yield from over_river(ctx, here, there, guide))
    return (yield from overland(ctx, here, there, guide))


def overland(ctx, here, there, guide):
    """The ordinary case: lost on the terrain left, event on the terrain
    entered."""
    frm = procedures.hex_terrain(ctx.book, here)
    to = procedures.hex_terrain(ctx.book, there)

    lost = yield from lost_check(ctx, frm, "leaving the hex", "r205", guide)
    if not lost:
        ctx.move_to(there, why="travel (r204)")
    else:
        ctx.say("You cannot find your way out, and the day goes with it.")
        ctx.note("stuck in the hex you tried to leave; no alternate action "
                 "(r204). The event below is still checked, as if you had "
                 "entered (r205)", cite="r205")

    out = yield from event_check(ctx, to, "in the hex you entered", "r204b")
    return (not lost), lost or spends_day(out)


def by_road(ctx, here, there, guide):
    """r204c: no lost check ever, the road's own event row first."""
    ctx.note("following the road, so there is no lost check", cite="r205b")
    ctx.move_to(there, why="travel by road (r204c)")

    out = yield from event_check(ctx, "on road", "on the road", "r204c")
    if out is None:
        to = procedures.hex_terrain(ctx.book, there)
        out = yield from event_check(ctx, to,
                                     "in the terrain entered, no road event "
                                     "having occurred", "r204c")
        return True, spends_day(out)

    # A road event happened, so the terrain line need not be consulted - but
    # the player may if they want to (r204c).
    if not spends_day(out):
        also = yield ctx.confirm("Check the terrain line as well?", why="r204c")
        if also:
            to = procedures.hex_terrain(ctx.book, there)
            out = yield from event_check(ctx, to, "in the terrain entered",
                                         "r204c")
    return True, spends_day(out)


def over_river(ctx, here, there, guide):
    """r204e/r205d: the crossing has its own lost check and its own event row,
    and only then does the far hex get looked at."""
    lost = yield from lost_check(ctx, "cross river", "finding a way over the "
                                 "river", "r205d", guide)
    if lost:
        ctx.say("There is no crossing to be found today.")
        ctx.note("you stay where you started and there is no travel event at "
                 "all", cite="r205d")
        return False, True

    out = yield from event_check(ctx, "cross river", "crossing the river",
                                 "r204e")
    if spends_day(out):
        return False, True

    # r205d names the terrain ENTERED for this one, against the general rule.
    to = procedures.hex_terrain(ctx.book, there)
    lost = yield from lost_check(ctx, to, "entering the far hex", "r205d", guide)
    if lost:
        ctx.say("You are over the water, but lose the far bank in the dark.")
        ctx.note("you count as across the river and end where you started; "
                 "tomorrow you may try any hex on the far side", cite="r205d")
        ctx.g.setdefault("day_flags", {})["across_river"] = there
    else:
        ctx.move_to(there, why="crossed the river (r204e)")

    out = yield from event_check(ctx, to, "in the hex you entered", "r204b")
    return (not lost), True


def fly(ctx, here, there, guide, trip):
    """r204d/r205c: the airborne row for both checks.

    The terrain of the hex actually landed in is *not* checked here. r204d puts
    that on the last hex of the day, and the last hex is not known until the
    flight stops - the player may break it off early. `travel_action` does it
    once the loop ends, off `trip["aloft_event"]`.
    """
    lost = yield from lost_check(ctx, "airborne", "flying out of the hex",
                                 "r205c", guide)
    landed = there
    if lost:
        drift = yield ctx.die("1d6", why="r205c", prompt="Do you drift?")
        if drift >= 4:
            way = yield ctx.die("1d6", why="r205c",
                                prompt="Which way? (1-N, 2-NE, 3-SE, 4-S, "
                                       "5-SW, 6-NW)")
            heading = procedures.DRIFT[way]
            blown = procedures.neighbours(
                *procedures.parse_hex(there)).get(heading)
            if procedures.on_map(ctx.book, blown):
                ctx.note(f"blown one hex {heading} before landing", cite="r205c")
                landed = blown
            else:
                ctx.note(f"{heading} would be off the map; you come down where "
                         f"you were headed", cite="r205c")
        else:
            ctx.note(f"{drift}: no drift", cite="r205c")

    ctx.move_to(landed, why="airborne travel (r204d)")
    out = yield from event_check(ctx, "airborne", "while flying", "r204d")
    trip["aloft_event"] = out is not None
    return True, lost or spends_day(out)


def spends_day(out) -> bool:
    """r204f: an event normally consumes the rest of the day. Talk and a fight
    that killed everyone do not."""
    if out is None:
        return False
    cost = getattr(out, "time_cost", "rest_of_day")
    return cost not in ("minutes", "none")


# --- the day's action -----------------------------------------------------


@section("travel")
def travel_action(ctx):
    """r204, as a daily action: pick a speed, then a hex at a time."""
    here = ctx.hex()
    if not here:
        raise Refuse("the party's hex is not on the sheet. Set it with "
                     "`bp game set --hex <id>`.")

    ways = speeds(ctx)
    if len(ways) == 1:
        label, mode, allowed = ways[0]
        ctx.note(f"{label} - the party has no other speed", cite="r204a")
    else:
        picked = yield ctx.choose(*[w[0] for w in ways], why="r204a",
                                  prompt="How do you travel?")
        label, mode, allowed = next(w for w in ways if w[0] == picked)

    guide = yield from pick_guide(ctx)
    if guide:
        ctx.note(f"{guide['name']} guides today: -1 on the lost checks",
                 cite="r205a")

    trip = {"aloft_event": False, "flew": False}
    for step in range(1, allowed + 1):
        there = yield from where_next(ctx, ctx.hex())
        moved, spent = yield from move_one_hex(
            ctx, ctx.hex(), there, mode, guide, trip)
        trip["flew"] = trip["flew"] or mode == AIRBORNE
        if spent:
            ctx.note("the day's movement is over", cite="r204f")
            break
        if step < allowed:
            more = yield ctx.confirm(f"Travel on? ({allowed - step} more "
                                     f"hex{'es' if allowed - step > 1 else ''} "
                                     f"available)", why="r204a")
            if not more:
                break

    # r204d: the flight is over, so the hex it ended in gets a terrain check -
    # unless something already happened aloft on that last leg. Where the party
    # comes down may not be where it aimed, so this reads the hex it is in.
    if trip["flew"] and not trip["aloft_event"]:
        to = procedures.hex_terrain(ctx.book, ctx.hex())
        yield from event_check(ctx, to, "in the hex you land in", "r204d")

    ctx.g.setdefault("day_flags", {})["action"] = "travel"
    return EndEvent(time_cost="rest_of_day")
