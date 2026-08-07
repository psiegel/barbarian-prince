"""Combat (r220), victim selection (r343) and the two ways out (r218).

The arithmetic is not reimplemented here. `combat.resolve_strike` does r220c,
`state.wounds_for` does the combat table, `combat.rout_check` does r220f - what
is here is the round structure, and the questions it puts to the player.

Because every die comes back through `ctx`, `--auto-dice` turns this same
generator into the auto-resolver. There is no second code path.
"""

import combat
import creatures
import procedures
import state

from ..sections import section
from ..types import EnterCombat, EscapeHex, HideHere

Refuse = procedures.Refuse

MAX_ROUNDS = combat.MAX_ROUNDS
MAX_PICKS = 200        # r343 and r218 both loop on a die; neither may spin


# --- getting the enemy onto the sheet -------------------------------------


def living_foes(ctx) -> list[dict]:
    return [f for f in ctx.g.get("foes", []) if combat.in_fight(f)]


def add_foes(ctx, name: str, cs: int, end: int, wealth: int, count: int) -> None:
    foes = ctx.g.setdefault("foes", [])
    for i in range(count):
        foes.append({"name": f"{name} {i + 1}" if count > 1 else name,
                     "cs": cs, "end": end, "wealth": wealth, "wounds": 0})


def ensure_foes(ctx, spec: dict):
    """Whoever the party is fighting, on the sheet before a blow is struck.

    Three sources, in order: a fight already in progress, the band sizes
    data/creatures.json holds for the event, or the player reading the numbers
    off the section they have just heard.
    """
    if living_foes(ctx):
        return

    event = spec.get("event") or ctx.param("event")
    specs = creatures.specs(creatures.base_id(event)) if event else []

    if specs:
        for band in specs:
            rec = state.encounter_get(ctx.g, creatures.base_id(event),
                                      band["group"])
            if rec and not state.encounter_stale(ctx.g, rec):
                n = rec["n"]
                ctx.note(f"{n} {rec['noun']}, as rolled earlier today",
                         cite=event)
            else:
                total = yield ctx.die(f"{band.get('dice', 1)}d6",
                                      why=f"{event} band size",
                                      prompt=f"How many {band.get('noun', 'of them')}?")
                n = creatures.size_from(band, total)
                noun, _ = creatures.wording(band, n)[1], None
                state.encounter_set(ctx.g, creatures.base_id(event),
                                    band["group"], n,
                                    band.get("noun", band["group"]),
                                    creatures.describe(band))
                ctx.note(f"{n} of them ({creatures.describe(band)})", cite=event)
            for line in band.get("foes", []):
                c = creatures.head_count(line.get("count", "n"), n)
                if c > 0:
                    add_foes(ctx, line["name"], line["cs"], line["end"],
                             line.get("wealth", 0), c)
        return

    # No spec: the numbers are printed in the section the player just heard.
    ctx.note("the enemy's numbers are printed in the section - read them off it",
             cite="r220")
    count = yield ctx.number("How many are there?", low=1, high=99, why="r220")
    cs = yield ctx.number("Their combat skill?", low=0, high=12, why="r220")
    end = yield ctx.number("Their endurance?", low=2, high=20, why="r220")
    wealth = yield ctx.number("Their wealth code?", low=0, high=99, why="r225")
    add_foes(ctx, ctx.param("noun") or "Enemy", cs, end, wealth, count)


# --- one side's half of a round -------------------------------------------


def ours(ctx) -> list[dict]:
    return [c for c in ctx.g["party"] if combat.in_fight(c)]


def strike_phase(ctx, side: str, pairs=None):
    """All of one side's strikes, each rolled by the player, applied as they
    land. Returns those killed."""
    strikers = [c for c in (ours(ctx) if side == "us" else living_foes(ctx))
                if combat.can_strike(c)]
    targets = living_foes(ctx) if side == "us" else ours(ctx)
    pool = combat.target_pool(targets)
    if not pool or not strikers:
        return []

    matched = pairs if (pairs and side == "us") else combat.match_up(strikers, pool)
    killed = []
    for striker, target in matched:
        if not combat.in_fight(striker) or not combat.can_strike(striker):
            continue
        if not combat.in_fight(target):
            # He fell earlier this round; the strike goes to someone still up
            # rather than being spent on a corpse.
            left = combat.target_pool(targets)
            if not left:
                break
            target = left[0]
        roll = yield ctx.die("2d6", why="r220c",
                             prompt=f"{striker['name']} strikes at {target['name']}")
        s = combat.resolve_strike(striker, target, roll)
        target["wounds"] += s["wounds"]
        ctx.rolled(combat.strike_line(striker, target, s), cite="r220c")
        if s["wounds"] and state.dead(target):
            killed.append(target)
    return killed


def rout_attempt(ctx, killed: list[dict]):
    """r220f: one die per enemy killed this round, and a 6 sends the rest away.

    The dice are the player's, so this asks rather than rolling - which is why
    it exists at all instead of calling combat.rout_check.
    """
    survivors, liable = combat.rout_liable(ctx.g)
    if not survivors or not liable:
        return combat.rout_from(ctx.g, [], _log(ctx))
    dice = []
    for i in range(len(killed)):
        dice.append((yield ctx.die(
            "1d6", why="r220f",
            prompt=f"Rout check {i + 1} of {len(killed)}")))
    return combat.rout_from(ctx.g, dice, _log(ctx))


def prince_down(ctx):
    """r221b: the Prince is helpless, and his followers decide what to do.

    A 4 or more and they carry him along; a 3 or less and they take him for dead
    and leave with everything he owns. The fight does not stop either way -
    whoever is left standing goes on fighting over him.
    """
    prince = ctx.player()
    ctx.say("The Prince is down, unconscious and helpless.")
    followers = [c for c in ctx.men()
                 if c is not prince and not state.dead(c)]
    if not followers:
        ctx.note("no followers, so there is no attitude to roll for", cite="r221b")
        return

    die = yield ctx.die("1d6", why="r221b",
                        prompt="The followers' attitude to their fallen Prince")
    if die >= 4:
        ctx.say("They close ranks around him and carry him along.")
        # Nothing is flagged: "being carried" is exactly "unconscious with
        # followers left", so it cannot go stale, and plan 08 reads the 20-load
        # cost (r206a) off the same condition.
        ctx.note(f"{die}: they carry him. While he is down he counts as 20 "
                 f"loads to transport (r206a)", cite="r221b")
        return

    ctx.say("They take him for dead, strip him of everything he carries, "
            "and are gone.")
    for f in followers:
        ctx.drop(f, why="deserted the fallen Prince (r221b)")
    if ctx.gold():
        ctx.spend(ctx.gold(), why="taken by the deserting followers (r221b)")
    ctx.warn("his possessions go with them too - plan 08 models those")


def watch_the_prince(ctx, fallen: dict):
    """Fire r221b once, the first time the Prince goes down."""
    prince = ctx.player()
    if fallen["rolled"] or not prince or state.dead(prince):
        return
    if state.unconscious(prince):
        fallen["rolled"] = True
        yield from prince_down(ctx)


def choose_pairs(ctx):
    """r220b. Only worth asking when there is a decision in it."""
    strikers = [c for c in ours(ctx) if combat.can_strike(c)]
    pool = combat.target_pool(living_foes(ctx))
    if len(strikers) < 2 and len(pool) < 2:
        return None
    pairs = []
    for c in strikers:
        who = yield ctx.pick_char(among=pool, why="r220b",
                                  prompt=f"Who does {c['name']} face?")
        pairs.append((c, next(f for f in pool if f["name"] == who)))
    return pairs


# --- the fight ------------------------------------------------------------


def finish(ctx, done: dict, routed: list[dict]):
    """Say how it ended, bank the survivors' wealth, and hand back a verb."""
    ctx.say(done["result"])
    state.note(ctx.g, f"combat: {done['why']} after "
                      f"{state.plural(done['rounds'], 'round')}")
    if done["why"] == "prince-dead":
        return ctx.end_game("loss", "the Barbarian Prince is dead (r221c)")
    if done["why"] == "won":
        return (yield from collect_treasure(ctx, routed))
    if done["why"] == "routed":
        ctx.g["foes"] = []
        return ctx.end_event(time_cost="rest_of_day")
    if done["why"] in ("wiped-out", "stalemate", "too-long"):
        raise Refuse(done["result"] + " The engine will not rule this one - "
                     "settle it by hand and correct the sheet.")
    return ctx.end_event()


def battle(ctx, spec: dict):
    """r220a: rounds until one side escapes or is wiped out."""
    yield from ensure_foes(ctx, spec)
    if not living_foes(ctx):
        raise Refuse("there is nobody to fight")

    # The same guards `bp fight auto` applies, checked before a single question
    # is put: a fight that cannot be fought should say so, not ask for dice
    # and then refuse.
    prince = ctx.player()
    if prince and state.dead(prince):
        raise Refuse("the Prince is dead and the game is over (r221c)")
    if not any(combat.can_strike(c) for c in ours(ctx)):
        raise Refuse("nobody in the party is able to strike; there is no fight "
                     "to roll out here")

    rout = False
    if any(not combat.rout_immune(f) for f in living_foes(ctx)):
        rout = yield ctx.confirm(
            "Try to frighten the survivors into running as they fall? "
            "Routed enemies leave with their treasure.", why="r220f")

    pick_pairs = False
    if len(ours(ctx)) > 1:
        pick_pairs = yield ctx.confirm("Choose who faces whom each round?",
                                       why="r220b")

    routed: list[dict] = []
    order = ["us", "them"] if spec.get("initiative", "us") == "us" else ["them", "us"]

    fallen = {"rolled": False}

    def phase_outcome(rnd, cleared=False):
        return combat.outcome(ctx.g, rnd, routed, by_rout=cleared,
                              halt_on_prince_down=False)

    if spec.get("surprise"):
        side = spec["surprise"]
        who = "Your party has" if side == "us" else "They have"
        ctx.say(f"{who} one free strike before the fight begins.")
        killed = yield from strike_phase(ctx, side)
        if side == "us" and rout and killed:
            routed += yield from rout_attempt(ctx, killed)
        yield from watch_the_prince(ctx, fallen)
        done = phase_outcome(0)
        if done:
            return (yield from finish(ctx, done, routed))
        order = ["us", "them"] if side == "us" else ["them", "us"]

    for rnd in range(1, MAX_ROUNDS + 1):
        can_act = any(combat.can_strike(c) for c in ours(ctx))
        if can_act and not spec.get("no_escape"):
            what = yield ctx.choose("fight", "flee", why="r220e",
                                    prompt=f"Round {rnd}")
            if what == "flee":
                die = yield ctx.die("1d6", why="r220e")
                if die >= 4:
                    ctx.say("You break off and get clear.")
                    return (yield from escape_hex(ctx))
                ctx.note(f"{die}: too slow, the fight goes on", cite="r220e")

        pairs = (yield from choose_pairs(ctx)) if pick_pairs else None
        lead = "you strike first" if order[0] == "us" else "they strike first"
        ctx.note(f"round {rnd} - {lead}", cite="r220a")

        for side in order:
            killed = yield from strike_phase(ctx, side, pairs)
            cleared = False
            if side == "us" and rout and killed:
                fled = yield from rout_attempt(ctx, killed)
                routed += fled
                cleared = bool(fled) and not living_foes(ctx)
            yield from watch_the_prince(ctx, fallen)
            done = phase_outcome(rnd, cleared)
            if done:
                return (yield from finish(ctx, done, routed))

    raise Refuse(f"still fighting after {MAX_ROUNDS} rounds - something is wrong. "
                 f"The sheet stands as it is; finish this one by hand.")


class _log(list):
    """combat.rout_check writes its reasoning into a list of lines. Route those
    into the event stream instead of collecting them and throwing them away."""

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx

    def append(self, line):
        self._ctx.rolled(line.strip(), cite="r220f")


# --- treasure -------------------------------------------------------------


def collect_treasure(ctx, routed: list[dict]):
    """r225/r226: one die per body, against the wealth code it carried."""
    dead = [f for f in ctx.g.get("foes", []) if state.dead(f)]
    ctx.g["foes"] = []
    if not dead:
        return ctx.end_event(time_cost="minutes")

    rows = procedures.treasure_rows(ctx.book)
    gained, items = 0, []
    for f in dead:
        code = str(f.get("wealth") or 0)
        if code == "0":
            continue
        if code not in rows:
            ctx.warn(f"r226 prints no line for wealth code {code} "
                     f"({f['name']}) - settle this one by hand")
            continue
        die = yield ctx.die("1d6", why="r226",
                            prompt=f"Treasure for {f['name']} (wealth {code})")
        cell = rows[code][die - 1]
        gold = _gold_in(cell)
        if gold is None:
            ctx.warn(f"r226 code {code} on {die} reads {cell!r}, which is not a "
                     f"number - settle it by hand")
            continue
        gained += gold
        for letter in _letters_in(cell):
            items.append(letter)
        ctx.note(f"{f['name']}: wealth {code} on {die} -> {cell}", cite="r226")

    if gained:
        ctx.gain(gained, why="taken from the dead (r225)")
    if items:
        ctx.note("special possessions to roll for: " + ", ".join(items) +
                 " - one die each on that letter's line (r225)", cite="r226")
        ctx.warn("possessions are plan 08; note them on paper for now")
    return ctx.end_event(time_cost="minutes")


def _gold_in(cell: str) -> int | None:
    import re
    m = re.match(r"\s*(\d+)", cell)
    return int(m.group(1)) if m else None


def _letters_in(cell: str) -> list[str]:
    import re
    return re.findall(r"\+([ABC])\b", cell)


# --- r343 and r218 --------------------------------------------------------


def pick_victim(ctx, among: list[dict] | None = None):
    """r343: one die per character in turn, a 6 selects, cycling if need be."""
    pool = among if among is not None else [c for c in ctx.men()
                                            if not state.dead(c)]
    if not pool:
        raise Refuse("there is nobody left to be the victim (r343)")
    if len(pool) == 1:
        ctx.note(f"{pool[0]['name']} is the only one there, so the target "
                 f"is not rolled for", cite="r343")
        return pool[0]
    for i in range(MAX_PICKS):
        who = pool[i % len(pool)]
        die = yield ctx.die("1d6", why="r343",
                            prompt=f"Is it {who['name']}?")
        if die == 6:
            ctx.note(f"6: {who['name']} is the target", cite="r343")
            return who
    raise Refuse(f"r343 went {MAX_PICKS} rolls without a 6. Pick the target by "
                 f"hand.")


def escape_hex(ctx):
    """r218a: away to a random adjacent hex - not across a river, not off the
    map, and not into the same refusal twice."""
    here = ctx.hex()
    if not here:
        raise Refuse("the party's hex is not on the sheet, so there is nowhere "
                     "to escape to (r218a). Set it with `bp game set --hex`.")
    flying = ctx.all_flying()
    tried = set()
    for _ in range(MAX_PICKS):
        die = yield ctx.die("1d6", why="r218a",
                            prompt="Which way? (1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW)")
        heading = procedures.DRIFT[die]
        there = procedures.neighbours(*procedures.parse_hex(here)).get(heading)
        tried.add(die)
        if not procedures.on_map(ctx.book, there):
            # `neighbours` formats an id for every direction, including ones
            # past the edge of the board - 0101's northern neighbour comes back
            # as "0100". Only the map says which of those are real.
            ctx.note(f"{heading} is off the map - roll again", cite="r218a")
        elif not flying and procedures.hexside(ctx.book, here, there).get("river"):
            ctx.note(f"{heading} crosses a river - roll again", cite="r218a")
        else:
            ctx.move_to(there, why="escaped (r218a)")
            ctx.g.setdefault("day_flags", {})["no_entry_event"] = there
            ctx.note("no travel event for entering it, and the day still ends "
                     "with the meal", cite="r218a")
            return ctx.end_event(time_cost="rest_of_day")
        if len(tried) == 6:
            raise Refuse(
                f"every direction out of {here} is a river or the map edge, so "
                f"r218a has nowhere to send the party. Rule this one by hand.")
    raise Refuse("r218a could not find a way out")


def hide_here(ctx):
    """r218b: stay put, out of sight."""
    ctx.note("the party stays in this hex, hidden", cite="r218b")
    return ctx.end_event(time_cost="rest_of_day")


@section("r343")
def r343(ctx):
    who = yield from pick_victim(ctx)
    ctx.say(f"{who['name']} is the one.")
    ctx.g.setdefault("day_flags", {})["victim"] = who["name"]
    return ctx.end_event()


# --- carrying an encounter all the way through ----------------------------
#
# The graph stops at EnterCombat, EscapeHex and HideHere on purpose: those are
# what a *section* resolves to, and keeping them as outcomes is what lets plan
# 02's tests say what each section does without fighting a battle to find out.
#
# Something above has to carry them out. This is that something, and plan 05's
# day flow will call it in place of invoking a section directly.


@section("_combat")
def _combat(ctx):
    return (yield from battle(ctx, ctx.param("spec") or {}))


@section("_escape")
def _escape(ctx):
    return (yield from escape_hex(ctx))


@section("_hide")
def _hide(ctx):
    return hide_here(ctx)
    yield          # noqa: unreachable - keeps this a generator


@section("encounter")
def encounter(ctx):
    """Resolve one section to something the day can act on.

    A fight can end in an escape, and an escape is itself an outcome, so this
    loops rather than assuming one hand-off.
    """
    out = yield ctx.invoke(ctx.need("sid"), **ctx.param("params", {}))
    for _ in range(10):
        if isinstance(out, EnterCombat):
            out = yield ctx.invoke("_combat", spec=out.spec)
        elif isinstance(out, EscapeHex):
            out = yield ctx.invoke("_escape")
        elif isinstance(out, HideHere):
            out = yield ctx.invoke("_hide")
        else:
            return out
    raise Refuse("this encounter handed off ten times without settling - "
                 "that is a loop between combat and escape")
