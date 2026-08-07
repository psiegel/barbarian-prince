"""The daily loop: r203, the evening (r215-r217) and the clock.

One action for the whole party, then the events it raised, then feeding and
paying and housing them, then the date. `narrator.run_dusk` walked this
correctly against the `bp` CLI and its four rules are kept:

  - Ask the player for judgment, never for a lookup. Hunt or not, buy or eat
    stores - theirs. Which side of the Tragoth, whether the hex can be hunted -
    the map's.
  - A step already done today is not done again.
  - A step that cannot proceed puts the choice to the player rather than
    stopping the day dead.
  - The walk speaks for itself: what the hunt brought in and what the meal cost
    are said as they happen, off the sheet rather than off a sentence.
"""

import re

import procedures
import state

from ..sections import section
from ..types import EndDay, EndGame, EnterCombat, EscapeHex, HideHere
from . import travel

Refuse = procedures.Refuse

# r215c/e002: the two starting towns the royal guard searches hardest.
E002_BONUS = ("0101", "1501")
SETTLEMENT = procedures.LODGING_FEATURES        # town, castle, temple

# Plan 06 registers effects against these. Named here so the day flow does not
# have to change when the queue arrives.
HOOKS: dict[str, list] = {"on_dawn": [], "on_dusk": [], "on_night": [],
                          "on_enter_hex": [], "on_settlement": []}


def fire(ctx, hook: str):
    for fn in HOOKS.get(hook, []):
        yield from fn(ctx)


def flags(ctx) -> dict:
    return ctx.g.setdefault("day_flags", {})


def done(ctx, step: str) -> bool:
    return step in flags(ctx).setdefault("done", [])


def mark(ctx, step: str) -> None:
    flags(ctx).setdefault("done", []).append(step)


def features(ctx) -> list[str]:
    entry = procedures.hex_entry(ctx.book, ctx.hex() or "")
    return list((entry or {}).get("features") or [])


def in_settlement(ctx) -> bool:
    return any(f in SETTLEMENT for f in features(ctx))


# --- dawn -----------------------------------------------------------------


def dawn(ctx):
    """A new day. The flags are cleared here and nowhere else."""
    ctx.g["day_flags"] = {"actions_remaining": 1, "done": []}
    yield from fire(ctx, "on_dawn")
    ctx.note(state.time_line(ctx.g), cite="r203")
    where = ctx.hex() or "nowhere recorded"
    feats = features(ctx)
    ctx.note(f"hex {where}" + (f", {', '.join(feats)}" if feats else ""),
             cite="r203")
    starving = [c["name"] for c in ctx.men() if c.get("starve")]
    if starving:
        ctx.note("going hungry: " + ", ".join(starving) + " (r216b)", cite="r216b")


# --- the day's action -----------------------------------------------------


def actions_here(ctx) -> list[tuple[str, str, str]]:
    """(label, key, cite) for every action r203 allows in this hex."""
    feats = features(ctx)
    out = [("Travel to a new hex", "travel", "r204"),
           ("Rest here", "rest", "r222")]
    if "ruins" in feats:
        out.append(("Search the ruins", "r208", "r208"))
    if any(f in SETTLEMENT for f in feats):
        out.append(("Seek news and information", "r209", "r209"))
        out.append(("Seek an audience with the local lord", "r211", "r211"))
    if any(f in ("town", "castle") for f in feats):
        out.append(("Seek to hire followers", "r210", "r210"))
    if "temple" in feats:
        out.append(("Submit an offering", "r212", "r212"))
    out.append(("Search for a cache", "cache", "r214"))
    return out


def choose_action(ctx):
    """r203: one action for the whole party. Followers do not act on their own."""
    choices = actions_here(ctx)
    picked = yield ctx.choose(*[c[0] for c in choices], why="r203",
                              prompt="What does the party do today?")
    label, key, cite = next(c for c in choices if c[0] == picked)
    flags(ctx)["action"] = key

    if key == "travel":
        return (yield ctx.invoke("travel"))
    if key == "rest":
        return (yield from rest_action(ctx))
    if key == "cache":
        raise Refuse("searching for a cache is r214, which plan 08 owns. "
                     "Nothing has been spent - pick another action.")
    raise Refuse(f"{label} is {cite}, which plan 07 owns. Its table is in the "
                 f"booklet - resolve it by hand with `python3 src/bp.py show "
                 f"{cite}`, then pick another action here.")


def rest_action(ctx):
    """r222: resting still draws an encounter, and only a quiet day heals.

    The check is made as if travelling into the hex the party already occupies,
    so it uses the same event machinery travel does.
    """
    here = ctx.hex()
    terrain = procedures.hex_terrain(ctx.book, here)
    ctx.say("The party stays put, resting.")
    out = yield from travel.event_check(
        ctx, terrain, "as if entering the hex you are resting in", "r222")

    if flags(ctx).get("fought") or flags(ctx).get("escaped"):
        ctx.note("a fight or an escape, so nobody heals tonight", cite="r222")
        return out

    healed = []
    for c in ctx.men():
        if c["wounds"] > 0:
            ctx.heal(c, 1, why="a day's rest (r222)")
            healed.append(c["name"])
    if healed:
        ctx.say("A quiet day: " + ", ".join(healed) +
                (" heals a wound." if len(healed) == 1 else " each heal a wound."))
    else:
        ctx.note("nobody is wounded, so the rest heals nothing", cite="r222")
    flags(ctx)["rested"] = True
    return out


# --- dusk -----------------------------------------------------------------


def dusk(ctx):
    """r215-r217, in the order the sections interlock."""
    yield from fire(ctx, "on_dusk")
    out = yield from e002_check(ctx)
    if isinstance(out, EndGame):
        return out
    yield from hunt_step(ctx)
    yield from meal_step(ctx)
    yield from wages_step(ctx)
    yield from lodging_step(ctx)
    yield from fire(ctx, "on_night")
    return None


def e002_check(ctx):
    """The standing dusk check north of the Tragoth (e001, e002).

    Which side of the river the party is on is the map's answer, not the
    player's, so it is never asked for.
    """
    if done(ctx, "e002"):
        return None
    here = ctx.hex()
    north = procedures.north_of_tragoth(procedures.hexes_of(ctx.book), here)
    mark(ctx, "e002")
    if not north:
        ctx.note(f"{here} is south of the Tragoth, so there is no e002 check",
                 cite="e001")
        return None

    bonus = 1 if here in E002_BONUS else 0
    ctx.note(f"north of the Tragoth in {here}: 1d6 - 3"
             + (f" + 1 for {here}" if bonus else ""), cite="e002")
    die = yield ctx.die("1d6", why="e002",
                        prompt="The royal guard, hunting for you")
    score = die - 3 + bonus
    ctx.note(f"{die} - 3{f' + {bonus}' if bonus else ''} = {score}", cite="e002")
    if score < 1:
        ctx.note("no event", cite="e002")
        return None
    ctx.say("Riders are on the road behind you.")
    return (yield ctx.invoke("encounter", sid="e002"))


def hunt_step(ctx):
    """r215b, before the meal and only where the rules allow it."""
    if done(ctx, "hunt"):
        return
    here = ctx.hex()
    allowed, why = procedures.hunting_here(ctx.book, here)
    if not allowed:
        ctx.note(f"no hunting here: {why}", cite="r215b")
        return

    ctx.note(f"hunting is allowed here: {why}", cite="r215b")
    want = yield ctx.confirm("Send someone out to hunt?", why="r215b")
    if not want:
        # Declining is not "done": the meal below may need the food, and the
        # way out of that is the offer coming round again.
        return

    able = [c for c in ctx.men()
            if not state.dead(c) and not state.unconscious(c)]
    if not able:
        ctx.note("nobody is fit to hunt", cite="r215b")
        return
    name = (able[0]["name"] if len(able) == 1
            else (yield ctx.pick_char(among=able, why="r215b",
                                      prompt="Who hunts?")))
    hunter = next(c for c in able if c["name"] == name)

    extras = []
    if flags(ctx).get("rested"):
        others = [c for c in able if c is not hunter]
        for c in others:
            # r215b: on a rest day each extra hunter adds one, and a guide two.
            join = yield ctx.confirm(f"Does {c['name']} hunt as well?",
                                     why="r215b")
            if join:
                extras.append(c)

    level, bits = hunt_level(hunter, extras)
    ctx.note(f"{hunter['name']}: {level} ({'; '.join(bits)})", cite="r215b")
    roll = yield ctx.die("2d6", why="r215b",
                         prompt=f"{hunter['name']} hunts for tonight's food")
    units = level - roll
    mark(ctx, "hunt")

    if roll == 12:
        # r215b: a 12 hurts the hunter whatever else happened, and if it knocks
        # him out the hunt failed after all.
        ctx.say(f"{hunter['name']} is hurt in the hunt.")
        wounds = yield ctx.die("1d6", why="r215b",
                               prompt=f"How badly is {hunter['name']} hurt?")
        ctx.wound(hunter, wounds, why="hurt hunting (r215b)")
        if state.dead(hunter) or state.unconscious(hunter):
            ctx.say("The hunt failed with him, and brings back nothing.")
            return
    if units > 0:
        ctx.food_change(units, why=f"hunting, {hunter['name']} (r215b)")
        ctx.say(f"{hunter['name']} brings back {state.plural(units, 'food unit')}.")
    else:
        ctx.say(f"{hunter['name']} comes back empty-handed.")


def hunt_level(hunter: dict, extras: list[dict]) -> tuple[int, list[str]]:
    """r215b: combat skill plus half current endurance, fractions down, plus a
    point for a guide and for each extra hunter."""
    cs = state.effective_cs(hunter)
    stamina = (hunter["end"] - hunter["wounds"]) // 2
    level = cs + stamina
    bits = [f"combat skill {cs}",
            f"half of {hunter['end'] - hunter['wounds']} endurance = {stamina}"]
    if hunter.get("guide"):
        level += 1
        bits.append("+1, a guide")
    for c in extras:
        level += 1 + (1 if c.get("guide") else 0)
    if extras:
        bits.append(f"+{sum(1 + (1 if c.get('guide') else 0) for c in extras)} "
                    f"for {state.plural(len(extras), 'extra hunter')}")
    return level, bits


def water_here(ctx) -> bool:
    """r215a: a desert hex with no oasis doubles the food, for water carried."""
    try:
        terrain = procedures.hex_terrain(ctx.book, ctx.hex())
    except Refuse:
        return True
    return terrain != "desert" or "oasis" in features(ctx)


def fodder_here(ctx) -> bool:
    """r215f: mounts graze free in farmland, countryside, forest and hills."""
    terrain = procedures.hex_terrain(ctx.book, ctx.hex())
    return ctx.book.travel["terrain"][terrain]["fodder"].lower().startswith("y")


def meal_step(ctx):
    """r215: the evening meal, and r216 for whoever goes without."""
    if done(ctx, "eat"):
        return
    water = water_here(ctx)
    need, why = state.food_needed(ctx.g, fodder_here(ctx), water=water)
    ctx.note(f"the meal needs {state.plural(need, 'food unit')}: {why}",
             cite="r215a")

    if in_settlement(ctx):
        cost = len(ctx.men()) + len(ctx.mounts())      # r215d: a gold a head
        if ctx.gold() >= cost:
            buy = yield ctx.confirm(f"Buy tonight's meals for {cost} gold?",
                                    why="r215d")
            if buy:
                ctx.spend(cost, why="meals bought (r215d)")
                ctx.say("The party eats bought food.")
                mark(ctx, "eat")
                return
        else:
            ctx.note(f"meals would cost {cost} gold and the party has "
                     f"{ctx.gold()}", cite="r215d")

    if ctx.food() >= need:
        ctx.food_change(-need, why="the evening meal (r215)")
        ctx.say(f"The party eats. {state.plural(ctx.food(), 'food unit')} left.")
        mark(ctx, "eat")
        return

    # r216a: with too little for everyone, it is shared out or withheld from
    # all - including the Prince. Either way the followers may leave.
    ctx.say(f"There is not enough to go round: {ctx.food()} units for a meal "
            f"that needs {need}.")
    pick = yield ctx.choose("share out what there is",
                            "everyone goes without", why="r216a",
                            prompt="What do you do?")
    mark(ctx, "eat")
    if pick.startswith("share"):
        if ctx.food():
            ctx.food_change(-ctx.food(), why="shared out short (r216a)")
        ctx.note("shared out, so no starvation penalties - but they may still "
                 "leave", cite="r216a")
    else:
        for c in ctx.men():
            c["starve"] = c.get("starve", 0) + 1
        state.note(ctx.g, "went without food: " +
                   ", ".join(c["name"] for c in ctx.men()))
        ctx.note("from tomorrow, a point off combat skill and half the loads "
                 "each (r216b)", cite="r216b")
    yield from desertion_checks(ctx, "hunger", "r216a")


def desertion_checks(ctx, why: str, cite: str):
    """r216a and r217 share a shape: 2d6 less wit & wiles per follower, and 4 or
    more and he goes."""
    followers = [c for c in ctx.men() if c["kind"] != "player"]
    if not followers:
        return
    wits = ctx.wits()
    for c in list(followers):
        roll = yield ctx.die("2d6", why=cite,
                             prompt=f"Does {c['name']} stay? ({why})")
        score = roll - wits
        if score >= 4:
            ctx.say(f"{c['name']} has had enough, and is gone.")
            ctx.drop(c, why=f"deserted over {why} ({cite})")
        else:
            ctx.note(f"{c['name']}: {roll} - {wits} = {score}, he stays",
                     cite=cite)


def wages_step(ctx):
    """r215/r333: henchmen are paid at the evening meal, or they leave."""
    if False:                # a generator, like every other dusk step: it will
        yield                # ask which to dismiss once plan 08 tracks upkeep
    if done(ctx, "pay"):
        return
    mark(ctx, "pay")
    owed = [c for c in ctx.men()
            if c.get("pay") and (c.get("pay_starts") or 0) <= ctx.day()]
    if not owed:
        return
    due = sum(c["pay"] for c in owed)
    if ctx.gold() >= due:
        ctx.spend(due, why=f"wages for {len(owed)} (r215)")
        ctx.say(f"Wages paid: {due} gold.")
        return
    ctx.say(f"Wages come to {due} gold and there is {ctx.gold()}.")
    for c in list(owed):
        ctx.drop(c, why="not paid (r333)")
    ctx.note("unpaid henchmen do not stay", cite="r333")


def lodging_step(ctx):
    """r217: rooms and stables, or a night rough and the rolls that go with it."""
    if done(ctx, "lodge") or not in_settlement(ctx):
        return
    mark(ctx, "lodge")
    cost, detail = state.lodging_cost(ctx.g)
    if ctx.gold() >= cost:
        take = yield ctx.confirm(f"Buy lodging for {cost} gold? ({detail})",
                                 why="r217")
        if take:
            ctx.spend(cost, why=f"lodging: {detail} (r217)")
            ctx.say("Rooms for the night, and stables for the animals.")
            return
    else:
        ctx.note(f"lodging is {cost} gold ({detail}) and the party has "
                 f"{ctx.gold()}", cite="r217")

    ctx.say("The party sleeps rough.")
    yield from desertion_checks(ctx, "a penurious leader", "r217")
    for m in list(ctx.mounts()):
        roll = yield ctx.die("1d6", why="r217",
                             prompt=f"Is {m['name']} still there at dawn?")
        if roll >= 4:
            ctx.say(f"{m['name']} is gone - thieves in the night.")
            ctx.drop(m, why="stolen for want of a stable (r217)")


# --- the clock ------------------------------------------------------------


def check_end(ctx):
    """r221c, e001: the three ways a game stops."""
    prince = ctx.player()
    if prince is None or state.dead(prince):
        return ctx.end_game("loss", "the Barbarian Prince is dead (r221c)")
    if ctx.day() > state.GAME_DAYS:
        return ctx.end_game(
            "loss", f"day {ctx.day()}: the ten weeks are up (e001)")
    if ctx.gold() >= 500:
        try:
            home = procedures.north_of_tragoth(procedures.hexes_of(ctx.book),
                                               ctx.hex())
        except Refuse:
            home = False
        if home:
            return ctx.end_game(
                "win", f"{ctx.gold()} gold, and home in the Northlands on day "
                       f"{ctx.day()} (e001)")
    return None


# --- setting a game up ----------------------------------------------------


def needs_setup(ctx) -> bool:
    p = ctx.player()
    return p is not None and p.get("wits") is None


def run_setup(ctx):
    """The seven fixed steps of e001, r202 and r225.

    There is no judgment anywhere in it - read a passage, roll a die, write it
    down - so it is walked rather than offered. The reading anchors live in
    data/procedures.json and refuse rather than print the wrong passage.
    """
    setup = ctx.book.procedures["setup"]
    ctx.read("e001#premise")

    ctx.read("r202")
    die = yield ctx.die("1d6", why="r202", prompt="Roll for wit & wiles")
    wits = 2 if die == 1 else die           # r202: a 1 counts as 2
    prince = ctx.player()
    prince["wits"] = wits
    prince.update(cs=8, end=9)              # r202, fixed
    ctx.note(f"wit & wiles {die}"
             + (" - a 1 counts as 2" if die == 1 else ""), cite="r202")
    ctx.say(f"Cal Arath: combat skill 8, endurance 9, wit and wiles {wits}.")

    rows = procedures.treasure_rows(ctx.book)
    die = yield ctx.die("1d6", why="r225",
                        prompt="Roll for starting gold (wealth code 2)")
    gold = int(rows["2"][die - 1])
    if gold:
        ctx.gain(gold, why="starting gold, wealth code 2 (r225)")
    ctx.say(f"You got away with {gold} gold." if gold
            else "You got away with nothing but Bonebiter.")

    ctx.read("e001#caravan")
    die = yield ctx.die("1d6", why="e001",
                        prompt="Roll for where the caravan set you down")
    where = setup["hexes"][str(die)]
    m = re.search(r"\b(\d{4})\b", where)
    if not m:
        raise Refuse(f"the caravan table's entry {die} ({where!r}) names no hex")
    hid = m.group(1)
    # The entry carries its own errata where the booklet has one - destination
    # 3 prints 0801 and means 0901. That belongs to the referee, not the voice.
    place, _, aside = where.partition(" - ")
    ctx.move_to(hid, why=f"the caravan set you down ({place})")
    ctx.say(f"The caravan leaves you at {place.split('(')[0].strip()}.")
    if aside:
        ctx.note(f"{hid}: {aside.rstrip(')')}", cite="e001")

    ctx.read("e001#dawn")
    state.note(ctx.g, f"game started: Cal Arath cs 8 end 9 w&w {wits}, "
                      f"{gold} gold, hex {m.group(1)}")


@section("day")
def day_flow(ctx):
    """One day, dawn to dawn. The machine commits at the EndDay and starts the
    next one, which is where D1's durability boundary lives."""
    if needs_setup(ctx):
        yield from run_setup(ctx)
    yield from dawn(ctx)

    while flags(ctx).get("actions_remaining", 0) > 0:
        flags(ctx)["actions_remaining"] -= 1
        out = yield from choose_action(ctx)
        if isinstance(out, EndGame):
            return out

    out = yield from dusk(ctx)
    if isinstance(out, EndGame):
        return out

    ctx.g["day"] += 1
    state.note(ctx.g, f"day {ctx.day() - 1} -> {ctx.day()}")
    end = check_end(ctx)
    if end is not None:
        return end
    return EndDay(f"day {ctx.day() - 1} closed out")
