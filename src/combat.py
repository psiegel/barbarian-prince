"""Auto-resolved combat: the whole fight, rolled out by code (r220).

Everything else in `bp` hands a die back to the player. This is the one place
that does not, and it exists because a nine-goblin fight is twenty minutes of
arithmetic that nobody enjoys and that goes wrong quietly. The player asks for
it explicitly - it is never the default - and in exchange for the speed they
give up every decision inside the fight.

What the code decides, so that nothing here is a judgement call:

  targets     round-robin (r220b): the strikers are matched against the enemies
              in order and wrap around. The only preference is that the helpless
              are left for last - an unconscious character cannot strike back
              (r221b), and no strike is spent on one who has already fallen.
  escape      never. r220e is a real option and a good one, but a party that
              might flee is a party making decisions, and that is not this
              command. Fight it by hand instead.
  routs       r220f is optional, so it is a flag decided once before the fight
              and applied every round. Ask the player; do not assume.

Where the rules stop being arithmetic, so does this command: the Prince falling
unconscious needs the r221b loyalty die and a referee, and the treasure of the
dead is still rolled through `bp foe clear`. Both stop the fight and say so
rather than being guessed at.

Wounds are written to the save as each round ends, so an interrupted fight
leaves the sheet true as far as it got.

`bp fight quick` is the same dice with both sides given on the command line and
no save touched at either end - for the fight you want to see resolved without
first writing everyone down.
"""

import argparse
import random
import sys

import state
from state import Refuse

# A fight where neither side can reach the other would otherwise spin forever.
# No honest fight comes near this, so hitting it means something is wrong.
MAX_ROUNDS = 100

# r220f: those too formidable to frighten. They fight to the death.
ROUT_IMMUNE = 9


def can_strike(ch: dict) -> bool:
    """r221b: the unconscious no longer strike, though they can still be struck."""
    return not state.is_mount(ch) and not state.dead(ch) and not state.unconscious(ch)


def in_fight(ch: dict) -> bool:
    """Still a target. Mounts are not combatants; the helpless still are."""
    return not state.is_mount(ch) and not state.dead(ch)


def rout_immune(f: dict) -> bool:
    return f["cs"] >= ROUT_IMMUNE or f["end"] >= ROUT_IMMUNE


# --- one strike -----------------------------------------------------------


def resolve_strike(striker: dict, target: dict, roll: int,
                   dice: list[int] | None = None) -> dict:
    """Resolve one strike (r220c) from a 2d6 total, without applying it.

    The total is passed in rather than rolled so that the same arithmetic serves
    both a fight the engine runs - where the player rolls and types the number -
    and `bp fight auto`, which rolls its own. `dice` is only for display, and is
    absent when all that came back was a total.
    """
    skill = state.effective_cs(striker) - state.effective_cs(target)
    hurt = state.strike_mod(striker)
    exposed = state.target_mod(target)
    total = skill + roll + hurt + exposed
    return {"skill": skill, "roll": roll, "dice": dice, "hurt": hurt,
            "exposed": exposed, "total": total,
            "wounds": state.wounds_for(total)}


def strike(striker: dict, target: dict, rng: random.Random) -> dict:
    dice = [rng.randint(1, 6), rng.randint(1, 6)]
    return resolve_strike(striker, target, sum(dice), dice)


def arithmetic(s: dict) -> str:
    """The strike, shown, so a player who wants to check it can."""
    shown = (f"2d6 {s['roll']} [{'+'.join(str(d) for d in s['dice'])}]"
             if s.get("dice") else f"2d6 {s['roll']}")
    parts = [shown, f"skill {s['skill']:+d}"]
    if s["hurt"]:
        parts.append(f"{s['hurt']:+d} own wounds")
    if s["exposed"]:
        parts.append(f"{s['exposed']:+d} target hurt")
    return f"{', '.join(parts)} = {s['total']}"


def strike_line(striker: dict, target: dict, s: dict) -> str:
    """One line of the log, as the player reads it."""
    if not s["wounds"]:
        return (f"{striker['name']} strikes at {target['name']} and misses.  "
                f"[{arithmetic(s)}]")
    hit = (f"{striker['name']} strikes {target['name']} for "
           f"{state.plural(s['wounds'], 'wound')}")
    if state.dead(target):
        hit += f" - {target['name']} is dead"
    elif state.unconscious(target):
        hit += f" - {target['name']} is unconscious and helpless"
    elif state.serious(target):
        hit += f" - {target['name']} is seriously wounded"
    return f"{hit}.  [{arithmetic(s)}]"


# --- one side's half of a round -------------------------------------------


def target_pool(targets: list[dict]) -> list[dict]:
    """Who is worth facing: those still able to strike back, if any are.

    An unconscious enemy is helpless and harmless (r221b), so leaving him until
    last is the obvious choice rather than a tactic - the alternative is trading
    strikes with a body while someone who can still hit you does.
    """
    standing = [t for t in targets if in_fight(t)]
    return [t for t in standing if can_strike(t)] or standing


def match_up(strikers: list[dict], targets: list[dict]) -> list[tuple[dict, dict]]:
    """r220b: each striker against one enemy, round-robin when they run out."""
    return [(s, targets[i % len(targets)]) for i, s in enumerate(strikers)]


def strike_phase(strikers: list[dict], targets: list[dict],
                 rng: random.Random, log: list[str]) -> list[dict]:
    """All of one side's strikes, results applied. Returns those killed."""
    pool = target_pool(targets)
    if not pool:
        return []
    killed = []
    for striker, target in match_up([s for s in strikers if can_strike(s)], pool):
        if not in_fight(target):
            # He fell earlier this round, so the strike goes to someone still up
            # rather than being spent on a corpse.
            left = target_pool(targets)
            if not left:
                break
            target = left[0]
        s = strike(striker, target, rng)
        target["wounds"] += s["wounds"]
        log.append("  " + strike_line(striker, target, s))
        if s["wounds"] and state.dead(target):
            killed.append(target)
    return killed


def rout_liable(g: dict) -> tuple[list[dict], list[dict]]:
    """Who is left, and which of them can be frightened at all (r220f)."""
    survivors = [f for f in g["foes"] if in_fight(f)]
    return survivors, [f for f in survivors if not rout_immune(f)]


def rout_from(g: dict, dice: list[int], log: list[str]) -> list[dict]:
    """r220f from dice already rolled - one per enemy killed this round.

    Split from `rout_check` so the engine can ask the player for those dice
    while `bp fight auto` goes on rolling its own, off one implementation.
    """
    survivors, liable = rout_liable(g)
    if not survivors:
        return []
    if not liable:
        who = ("he is" if len(survivors) == 1 else "they are")
        fights = ("he fights" if len(survivors) == 1 else "they fight")
        log.append(f"  no rout check - {who} combat skill or endurance "
                   f"{ROUT_IMMUNE}+, so {fights} to the death (r220f).")
        return []
    rolled = ", ".join(str(d) for d in dice)
    if 6 not in dice:
        log.append(f"  rout check, one die per kill: {rolled} - no 6, they stand "
                   f"their ground (r220f).")
        return []
    for f in liable:
        g["foes"].remove(f)
    flee = ("breaks and flees, carrying his wealth away with him"
            if len(liable) == 1 else
            "break and flee, carrying their wealth away with them")
    log.append(f"  rout check, one die per kill: {rolled} - a 6! "
               f"{state.plural(len(liable), 'enemy', 'enemies')} {flee} (r220f).")
    return liable


def rout_check(g: dict, killed: list[dict], rng: random.Random,
               log: list[str]) -> list[dict]:
    return rout_from(g, [rng.randint(1, 6) for _ in killed], log)


# --- the fight ------------------------------------------------------------


def outcome(g: dict, rounds: int, routed: list[dict],
            by_rout: bool = False, halt_on_prince_down: bool = True) -> dict | None:
    """The reason to stop, if there is one, checked after every strike phase.

    `by_rout` says a rout just emptied the field, which is not the same ending as
    killing the last of them: a fight can rout part of a band and then finish the
    rest, and the wealth of the two groups is settled differently (r220f).
    """
    prince = state.player(g)
    ours = [c for c in g["party"] if in_fight(c)]
    theirs = [f for f in g.get("foes", []) if in_fight(f)]

    if prince and state.dead(prince):
        return {"why": "prince-dead", "rounds": rounds, "routed": routed,
                "result": "The Barbarian Prince is dead. The game is lost (r221c)."}
    if not theirs:
        if by_rout:
            return {"why": "routed", "rounds": rounds, "routed": routed,
                    "result": "The enemy broke and ran - the survivors are gone, and "
                              "so is the wealth they carried (r220f)."}
        if routed:
            won = "Everyone who stayed to fight is dead. The field is yours."
        elif len(g.get("foes", [])) == 1:
            won = "He is dead. The fight is yours."
        else:
            won = "Every one of them is dead. The fight is yours."
        return {"why": "won", "rounds": rounds, "routed": routed, "result": won}
    if not ours:
        return {"why": "wiped-out", "rounds": rounds, "routed": routed,
                "result": "Your whole party has fallen. The enemy holds the field."}
    if halt_on_prince_down and prince and state.unconscious(prince):
        # `bp fight auto` stops here for a ruling. The engine does not: r221b
        # rolls for the followers' attitude and, if they stay, they fight on
        # over him - which is how a game survives the Prince going down.
        return {"why": "prince-down", "rounds": rounds, "routed": routed,
                "result": "The Prince is unconscious and helpless, combat skill 0 "
                          "(r221b) - the fight stops here for a ruling."}
    if not any(can_strike(c) for c in ours) and not any(can_strike(f) for f in theirs):
        return {"why": "stalemate", "rounds": rounds, "routed": routed,
                "result": "Nobody left standing on either side can strike - the "
                          "fight cannot go on."}
    return None


def fight(g: dict, rng: random.Random, rout: bool = False, first: str = "us",
          surprise: str | None = None, save: bool = True) -> tuple[list[str], dict]:
    """Run the fight to its end. Returns (the log, the outcome).

    `save` off is `bp fight quick`: the game is a scratch dict built from the
    command line and has no file behind it, so there is nothing to write to.
    """
    log: list[str] = []
    routed: list[dict] = []

    def ours() -> list[dict]:
        return [c for c in g["party"] if not state.is_mount(c)]

    def theirs() -> list[dict]:
        return g["foes"]

    def phase(side: str) -> dict | None:
        """One side strikes; returns an outcome if that ended the fight."""
        nonlocal routed
        fled = []
        if side == "us":
            killed = strike_phase(ours(), theirs(), rng, log)
            if rout and killed:
                fled = rout_check(g, killed, rng, log)
                routed += fled
        else:
            strike_phase(theirs(), ours(), rng, log)
        cleared = bool(fled) and not any(in_fight(f) for f in theirs())
        return outcome(g, rnd, routed, by_rout=cleared)

    order = ["us", "them"] if first == "us" else ["them", "us"]
    rnd = 0

    if surprise:
        # r220d: one free strike before the rounds begin, then that side leads.
        who = "you have" if surprise == "us" else "they have"
        log.append(f"Surprise - {who} one free strike before the fight begins (r220d).")
        done = phase(surprise)
        if done:
            return log, done
        order = ["us", "them"] if surprise == "us" else ["them", "us"]

    for rnd in range(1, MAX_ROUNDS + 1):
        lead = "you strike first" if order[0] == "us" else "they strike first"
        log.append(f"\nRound {rnd} - {lead} (r220a).")
        for side in order:
            done = phase(side)
            if done:
                return log, done
        if save:
            state.write_game(g)

    return log, {"why": "too-long", "rounds": MAX_ROUNDS, "routed": routed,
                 "result": f"Still going after {MAX_ROUNDS} rounds. Something is "
                           f"wrong - the sheet is saved as it stands; finish this "
                           f"one by hand."}


# --- the command ----------------------------------------------------------


def cmd_fight_auto(book, args) -> int:
    try:
        g = state.load_game(args)
        foes = g.get("foes", [])
        if not foes:
            raise Refuse("no fight in progress. bp foe add <name> --cs <n> --end <n> "
                         "[--count <n>] for what the party is facing, then run this "
                         "again.")
        if not any(in_fight(f) for f in foes):
            raise Refuse("every enemy in this fight is already dead. bp foe clear to "
                         "collect what they carried (r225).")
        prince = state.player(g)
        if prince and state.dead(prince):
            raise Refuse("the Prince is dead and the game is over (r221c).")
        if prince and state.unconscious(prince):
            raise Refuse("the Prince is unconscious and helpless (r221b) - he cannot "
                         "strike, so there is no fight to roll. Settle the loyalty "
                         "die and what becomes of him first.")
        if not any(can_strike(c) for c in g["party"] if not state.is_mount(c)):
            raise Refuse("nobody in the party is able to strike. There is no fight "
                         "to roll out here.")

        rng = random.Random(args.seed)
        log, done = fight(g, rng, rout=args.rout, first=args.first,
                          surprise=args.surprise)
        when = (state.plural(done["rounds"], "round") if done["rounds"]
                else "the free surprise strike")
        state.note(g, f"auto combat: {done['why']} after {when}")
        state.write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1

    print("\n".join(log))
    print(f"\n{done['result']}")
    report(g, done)
    return 0


# --- a fight with no save behind it ---------------------------------------
#
# Everything above works on a game read off disk. This does not: both sides are
# typed out, the dice are rolled, and nothing is read from or written to a save.
# It is the same r220 code either way - the only difference is where the two
# rosters come from, so a quick fight cannot drift from a recorded one.


def numbers(spec: list[str], fields: list[str], what: str) -> tuple[str, list[int]]:
    """'Cal Arath 8 9' -> ('Cal Arath', [8, 9]). The first two are never optional."""
    name, *rest = spec
    said = " ".join(spec)
    if len(rest) < 2:
        raise Refuse(f"{what}, {said!r}: a name, a combat skill and an endurance "
                     f"are the least of it.")
    if len(rest) > len(fields):
        raise Refuse(f"{what}, {said!r}: {len(rest)} numbers, and there are only "
                     f"{len(fields)} to give - {', '.join(fields)}.")
    for n in rest:
        if not n.lstrip("-").isdigit():
            raise Refuse(f"{what}, {said!r}: {n!r} is not a number. The name comes "
                         f"first, then {', '.join(fields)}.")
    vals = [int(n) for n in rest]
    if not name.strip():
        raise Refuse(f"{what}, {said!r}: no name.")
    if vals[1] < 1:
        # end 0 is how the sheet marks a character whose endurance is unknown, and
        # state.dead() can never be true for one - the fight would not end.
        raise Refuse(f"{name} has endurance {vals[1]}. A combatant with no "
                     f"endurance can never be killed and the fight would not end.")
    if vals[0] < 0:
        raise Refuse(f"{name} has combat skill {vals[0]}.")
    return name.strip(), vals


US_FIELDS = ["combat skill", "endurance", "wounds already taken"]
THEM_FIELDS = ["combat skill", "endurance", "wealth code", "how many of them"]


def quick_game(args) -> dict:
    """Build a scratch game from --us/--them. Never touches saves/."""
    if not args.us:
        raise Refuse("nobody is fighting. --us NAME CS END [WOUNDS], once per "
                     "character on your side.")
    if not args.them:
        raise Refuse("nothing to fight. --them NAME CS END [WEALTH] [COUNT], once "
                     "per kind of enemy.")

    party = []
    for i, spec in enumerate(args.us):
        name, vals = numbers(spec, US_FIELDS, "your side")
        # The Prince is the reason two of the outcomes exist (r221b, r221c), so
        # the first one named is him unless the player says otherwise.
        kind = "follower" if (i or args.no_prince) else "player"
        ch = state.new_char(name, kind)
        ch.update(cs=vals[0], end=vals[1], wounds=vals[2] if len(vals) > 2 else 0)
        party.append(ch)

    foes = []
    for spec in args.them:
        name, vals = numbers(spec, THEM_FIELDS, "the enemy")
        wealth = vals[2] if len(vals) > 2 else 0
        count = vals[3] if len(vals) > 3 else 1
        if count < 1:
            raise Refuse(f"{name}: {count} of them is not a band.")
        for i in range(count):
            foes.append({"name": name if count == 1 else f"{name} {i + 1}",
                         "cs": vals[0], "end": vals[1], "wealth": wealth,
                         "wounds": 0})

    clash = {f["name"].lower() for f in foes}
    if len(clash) != len(foes):
        raise Refuse("two enemies share a name - the log would be unreadable. "
                     "Give them distinct names, or one name with a count.")
    return {"party": party, "foes": foes}


def roster_line(chars: list[dict]) -> str:
    return ", ".join(
        f"{c['name']} cs {c['cs']} end {c['end']}"
        + (f" ({c['wounds']} wounded)" if c.get("wounds") else "")
        + (f" wealth {c['wealth']}" if c.get("wealth") else "")
        for c in chars)


def cmd_fight_quick(book, args) -> int:
    try:
        g = quick_game(args)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1

    # What was understood from the command line, so a mistyped number is caught
    # by reading rather than by wondering why the fight went the way it did.
    # Referee's, like every other roster: the player hears the fight, not this.
    print(f"quick fight - no save is read or written.\n"
          f"  your side: {roster_line(g['party'])}\n"
          f"  the enemy: {roster_line(g['foes'])}", file=sys.stderr)

    log, done = fight(g, random.Random(args.seed), rout=args.rout,
                      first=args.first, surprise=args.surprise, save=False)
    print("\n".join(log))
    print(f"\n{done['result']}")
    report(g, done, saved=False)
    print("\nNone of this reached a character sheet - both sides were typed, not "
          "read from a save. bp party wound <name> +<n> to record what it cost.")
    return 0


def report(g: dict, done: dict, saved: bool = True) -> None:
    """The state of both sides once the dice stop, and what to do next."""
    ours = [c for c in g["party"] if not state.is_mount(c)]
    print("\nyour party:")
    for c in ours:
        print(f"  {c['name']}: {state.plural(c['wounds'], 'wound')} of {c['end']} "
              f"endurance - {state.condition(c)}")

    foes = g.get("foes", [])
    killed = [f for f in foes if state.dead(f)]
    left = [f for f in foes if in_fight(f)]
    if left:
        print("\nstill facing you:")
        for f in left:
            print(f"  {f['name']}: {state.plural(f['wounds'], 'wound')} of {f['end']} "
                  f"endurance - {state.condition(f)}")
    print(f"\n{state.plural(len(killed), 'enemy', 'enemies')} killed"
          + (f", {len(done['routed'])} routed away" if done["routed"] else "")
          + (f", {len(left)} still standing" if left else "") + ".")

    if done["why"] == "prince-dead":
        return
    if done["why"] == "prince-down":
        followers = [c for c in ours if c["kind"] == "follower" and not state.dead(c)]
        if followers:
            print(f"\nRoll 1d6 for the followers still with him "
                  f"({', '.join(c['name'] for c in followers)}): 4 or more and they "
                  f"carry him out at 20 loads (r206b); 3 or less and they desert, "
                  f"taking his money and possessions with them (r221b). Then rule on "
                  f"the fight - the enemy is still standing.")
        else:
            print("\nHe is alone and helpless with the enemy still standing. No "
                  "loyalty die applies - there is nobody left to carry him or to "
                  "desert him (r221b). Rule on what becomes of him.")
        return
    if done["why"] == "wiped-out":
        print("\nNobody is left to strike back. Rule on what the enemy does with "
              "the fallen.")
        return
    if not (killed or left):
        return
    if saved:
        print("\nbp foe clear when you are done here - it prints the wealth codes "
              "of the dead so the treasure roll is not forgotten (r225).")
        return
    # Nothing will be cleared later, so the wealth codes are printed here or not
    # at all - a quick fight has no `bp foe clear` to remember them for it.
    loot = [f for f in killed if f["wealth"]]
    if loot:
        print("\nThe dead carried wealth (r225):")
        for f in loot:
            print(f"  {f['name']}: wealth code {f['wealth']} -> roll 1d6, "
                  f"bp treasure {f['wealth']} <die>")


def register(sub) -> None:
    """Add `bp fight` to bp's subparser. Called from bp.main()."""
    s = sub.add_parser("fight", help="resolve a whole combat by code (r220)")
    fsub = s.add_subparsers(dest="fightcmd", required=True)

    def r220_flags(p):
        """The three decisions that are the player's, not the code's."""
        p.add_argument("--rout", action="store_true",
                       help="try to rout the enemy after each round in which you "
                            "kill one: 1d6 per kill, a 6 and the survivors flee "
                            "with their wealth (r220f). Ask the player before the "
                            "fight; it applies every round.")
        p.add_argument("--first", choices=["us", "them"], default="us",
                       help="which side strikes first each round - the event says "
                            "which (r220a, default: us)")
        p.add_argument("--surprise", choices=["us", "them"],
                       help="that side gets one free strike, then strikes first "
                            "each round (r220d)")
        p.add_argument("--seed", type=int, help="fix the dice, to replay a fight")

    a = fsub.add_parser("auto", help="roll out every round until one side wins")
    a.add_argument("--game", help="act on a save other than the current one")
    r220_flags(a)
    a.set_defaults(fn=cmd_fight_auto)

    q = fsub.add_parser("quick", help="the same fight with both sides given here, "
                                      "and no save touched")
    q.add_argument("--us", action="append", nargs="+", metavar="NAME CS END [WOUNDS]",
                   help="one character on your side; repeat for each. The first "
                        "named is the Prince (r221b, r221c).")
    q.add_argument("--them", action="append", nargs="+",
                   metavar="NAME CS END [WEALTH] [COUNT]",
                   help="one kind of enemy; repeat for each. COUNT numbers them "
                        "'Goblin 1', 'Goblin 2', ...")
    q.add_argument("--no-prince", action="store_true",
                   help="nobody on your side is the Prince, so his falling does "
                        "not stop the fight")
    r220_flags(q)
    q.set_defaults(fn=cmd_fight_quick)
