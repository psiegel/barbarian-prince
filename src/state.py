"""Game state: the numbers that would otherwise live on a paper character sheet.

`show` and `travel` answer "what do the rules say". `start`, `day` and `move`
answer "what happens next, in what order". The commands here answer "what is
true in this game right now" - the day, the food, the gold, who is in the party
and what shape they are in - so that none of it has to be carried in memory or
reconstructed from the transcript, where it goes wrong silently.

Nothing here decides anything. Every derived number cites the rule it comes from
(starvation r216b, wounds r221, loads r206a, wages r210, lodging r217), and
anything the rules settle with a die is printed as an instruction to roll rather
than rolled. Anything that would make a number wrong - spending gold you do not
have, feeding a party you cannot feed - is refused with the shortfall named,
because a silently negative food store is a game quietly played wrong.

Saves live in saves/ as plain JSON, one file per game, and are not tracked.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

import procedures

ROOT = Path(__file__).resolve().parent.parent
SAVES = ROOT / "saves"
CURRENT = SAVES / "current"

VERSION = 1
GAME_DAYS = 70          # e001: ten weeks. Day 71 is a loss.
DAYS_PER_WEEK = 7

KINDS = ("player", "follower", "mount")

# r206b: a man on foot carries 10 loads, any mount 30.
CAPACITY = {"player": 10, "follower": 10, "mount": 30}


class Refuse(procedures.Refuse):
    """A state change that must fail loudly rather than store a wrong number."""


# --- the save file --------------------------------------------------------


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not s:
        raise Refuse(f"{name!r} has no usable characters for a file name")
    return s


def path_for(name: str) -> Path:
    return SAVES / f"{slug(name)}.json"


def current_name() -> str | None:
    if not CURRENT.exists():
        return None
    return CURRENT.read_text().strip() or None


def set_current(name: str) -> None:
    SAVES.mkdir(exist_ok=True)
    CURRENT.write_text(slug(name) + "\n")


def game_name(args) -> str:
    """Which save to act on: --game, then $BP_GAME, then the current pointer."""
    name = getattr(args, "game", None) or os.environ.get("BP_GAME") or current_name()
    if not name:
        raise Refuse(
            "no game is being tracked. Start one with:\n"
            "  bp game new <name> --wits <w&w> --gold <starting gold>\n"
            "(the numbers come from bp start - r202 for wit & wiles, r225 for gold)"
        )
    return name


def load_game(args, required: bool = True) -> dict | None:
    """The active save, or None when nothing is being tracked and that is allowed."""
    try:
        name = game_name(args)
    except Refuse:
        if required:
            raise
        return None
    path = path_for(name)
    if not path.exists():
        if not required:
            return None
        known = ", ".join(sorted(p.stem for p in SAVES.glob("*.json"))) or "none"
        raise Refuse(f"no save at {path.relative_to(ROOT)}. Saves on disk: {known}")
    g = json.loads(path.read_text())
    if g.get("version") != VERSION:
        raise Refuse(f"{path.name} is a version {g.get('version')} save; this bp "
                     f"reads version {VERSION}")
    return g


def write_game(g: dict) -> None:
    """Write via a temp file so an interrupted save cannot truncate the real one."""
    SAVES.mkdir(exist_ok=True)
    path = path_for(g["name"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(g, indent=2) + "\n")
    tmp.replace(path)


def note(g: dict, what: str) -> None:
    g.setdefault("log", []).append({"day": g["day"], "what": what})
    del g["log"][:-300]


# --- encounters: the band sizes that were read out loud -------------------
#
# Several sections leave the size of a band to a die (creatures.py rewrites the
# sentence with the number already in it). The number has to survive between
# commands, or `bp show` says eight goblins and `bp foe add` records three - so
# it is filed here, against the day and hex it was rolled for. A different day or
# a different hex is a different band, and is rolled again.


def encounters(g: dict) -> dict:
    return g.get("encounters") or {}


def encounter_get(g: dict, sid: str, group: str) -> dict | None:
    return encounters(g).get(sid, {}).get(group)


def encounter_stale(g: dict, rec: dict) -> bool:
    """A band counted yesterday, or in another hex, is not this band."""
    return rec.get("day") != g["day"] or rec.get("hex") != g.get("hex")


def encounter_set(g: dict, sid: str, group: str, n: int, noun: str,
                  how: str, foes: dict[str, int] | None = None) -> dict:
    rec = {"n": n, "noun": noun, "how": how, "day": g["day"], "hex": g.get("hex")}
    if foes:
        # What the band comes to on the sheet, so foe add can cross-check it.
        rec["foes"] = foes
    g.setdefault("encounters", {}).setdefault(sid, {})[group] = rec
    note(g, f"{sid}: {n} {noun} ({how})")
    return rec


def encounter_clear(g: dict, sid: str | None = None) -> int:
    """Forget counts once the encounter is done. Returns how many went."""
    book = g.get("encounters") or {}
    if sid is None:
        gone = sum(len(v) for v in book.values())
        g["encounters"] = {}
    else:
        gone = len(book.get(sid, {}))
        book.pop(sid, None)
    return gone


def count_warning(g: dict, name: str, count: int) -> str | None:
    """Is the sheet about to disagree with a number already read out?

    Matched on the enemy names the count was recorded with, not on the prose, so
    the one Hobgoblin leading a band of eight goblins is checked against one and
    not against eight.
    """
    want = name.strip().lower().rstrip("s")
    for sid, groups in encounters(g).items():
        for group, rec in groups.items():
            for foe, expect in (rec.get("foes") or {}).items():
                if foe.lower().rstrip("s") != want or expect == count:
                    continue
                return (f"{rec['n']} {rec.get('noun', group)} were read out for "
                        f"{sid} (day {rec['day']}, hex {rec.get('hex')}), which is "
                        f"{expect} x {foe} - and this would put {count} on the sheet. "
                        f"Nothing was changed. Use --count {expect} to match what "
                        f"the player was told, or `bp encounter set {sid} {group} "
                        f"<n>` first if the count itself is wrong - but do not let "
                        f"the sheet and the story disagree.")
    return None


# --- characters -----------------------------------------------------------


def new_char(name: str, kind: str = "follower") -> dict:
    return {
        "name": name.strip(),
        "kind": kind,
        "cs": 0,
        "end": 0,
        "wits": None,
        "wounds": 0,
        "starve": 0,
        "pay": 0,
        "guide": False,
        "winged": False,
        "note": "",
    }


def find(chars: list[dict], want: str, where: str = "the party") -> dict:
    """Match a character by name, refusing rather than picking one of two."""
    w = " ".join(want.lower().split())
    exact = [c for c in chars if c["name"].lower() == w]
    partial = [c for c in chars if w in c["name"].lower()]
    hits = exact or partial
    if not hits:
        names = ", ".join(c["name"] for c in chars) or "nobody"
        raise Refuse(f"no character called {want!r}. In {where}: {names}")
    if len(hits) > 1:
        raise Refuse(f"{want!r} matches {', '.join(c['name'] for c in hits)} - "
                     f"say which")
    return hits[0]


def capacity(ch: dict) -> int:
    """r206b, halved once per day of starvation (r216b), fractions down."""
    cap = CAPACITY[ch["kind"]]
    for _ in range(ch["starve"]):
        cap //= 2
    return cap


# The rules below read a character, and an enemy in the current fight is enough
# of one: same name, combat skill, endurance and wounds. Enemies carry no kind
# and never starve, so both are defaulted rather than required, and r220 then has
# one implementation for both sides of a fight instead of two that can disagree.


def is_mount(ch: dict) -> bool:
    return ch.get("kind") == "mount"


def starving(ch: dict) -> int:
    return ch.get("starve", 0)


def effective_cs(ch: dict) -> int:
    """r216b takes one off combat skill per day of starvation; r221b zeroes it."""
    if is_mount(ch):
        return 0
    if dead(ch) or unconscious(ch):
        return 0
    return max(0, ch["cs"] - starving(ch))


def dead(ch: dict) -> bool:
    if is_mount(ch):
        # A mount whose capacity has been starved to zero dies (r216).
        return starving(ch) > 0 and capacity(ch) == 0
    return ch["end"] > 0 and ch["wounds"] >= ch["end"]


def unconscious(ch: dict) -> bool:
    return (not is_mount(ch) and ch["end"] > 0
            and not dead(ch) and ch["wounds"] >= ch["end"] - 1)


def serious(ch: dict) -> bool:
    """r221a: wounds equalling or exceeding half endurance."""
    return (not is_mount(ch) and ch["end"] > 0
            and not dead(ch) and ch["wounds"] * 2 >= ch["end"])


def condition(ch: dict) -> str:
    if dead(ch):
        return ("DEAD - starved to no carrying capacity (r216)" if is_mount(ch)
                else "DEAD (r221c)")
    if unconscious(ch):
        return "unconscious and helpless (r221b)"
    if serious(ch):
        return "seriously wounded (r221a)"
    if is_mount(ch) and starving(ch):
        return "starving"
    return "ok"


def strike_notes(ch: dict) -> list[str]:
    """The r220c modifiers a wounded character carries into every strike."""
    out = []
    if is_mount(ch) or dead(ch) or unconscious(ch):
        return out
    if ch["wounds"] >= 1:
        out.append("-1 to his own strikes (has wounds)")
    if serious(ch):
        out.append("-1 more to his own strikes, and +2 to strikes against him")
    return out


# --- r220c: the strike, and the Combat Table ------------------------------

# Strike total -> wounds inflicted. Any total that is not listed missed, which
# is why this is a lookup and not a range: 9 misses although 8 and 10 both land.
COMBAT_TABLE = {
    1: (-1, 3, 5, 8, 11),
    2: (10, 12, 13, 17),
    3: (14,),
    5: (16, 18, 19),
    6: (20,),
}

WOUNDS_BY_TOTAL = {total: n for n, totals in COMBAT_TABLE.items() for total in totals}


def wounds_for(total: int) -> int:
    """r220c: the wounds a strike total inflicts, or 0 if the strike missed."""
    return WOUNDS_BY_TOTAL.get(total, 0)


def combat_table_prose() -> str:
    """The Combat Table as a sentence, for printing next to a fight."""
    return "; ".join(
        ",".join(str(t) for t in totals) + f" {plural(n, 'wound')}"
        for n, totals in COMBAT_TABLE.items()
    )


def strike_mod(ch: dict) -> int:
    """r220c: what the striker's own wounds cost him."""
    if is_mount(ch) or dead(ch) or unconscious(ch):
        return 0
    mod = 0
    if ch["wounds"] >= 1:
        mod -= 1
    if serious(ch):
        mod -= 1
    return mod


def target_mod(ch: dict) -> int:
    """r220c: +2 to strikes at a target already half-way to death."""
    return 2 if serious(ch) else 0


# --- derived party numbers ------------------------------------------------


def living(g: dict) -> list[dict]:
    return [c for c in g["party"] if not dead(c)]


def men(g: dict) -> list[dict]:
    return [c for c in living(g) if c["kind"] != "mount"]


def mounts(g: dict) -> list[dict]:
    return [c for c in living(g) if c["kind"] == "mount"]


def player(g: dict) -> dict | None:
    return next((c for c in g["party"] if c["kind"] == "player"), None)


def wits(g: dict) -> int | None:
    p = player(g)
    return p["wits"] if p else None


def food_needed(g: dict, fodder: bool | None, water: bool = True) -> tuple[int, str]:
    """r215a: one unit per man per day, two per mount that cannot forage,
    everything doubled where there is no water."""
    n = len(men(g))
    bits = [f"{n} for the men"]
    if mounts(g):
        if fodder is None:
            raise Refuse(
                f"there are {len(mounts(g))} mounts in the party and I do not know "
                f"whether they can forage here. Mounts eat nothing in farmland, "
                f"countryside, forest or hills, and two food units a day anywhere "
                f"else (r215f). Pass --hex <id> and it is read off the travel table, "
                f"or say --fodder / --no-fodder.")
        if not fodder:
            n += 2 * len(mounts(g))
            bits.append(f"{2 * len(mounts(g))} for {len(mounts(g))} mounts that "
                        f"cannot forage (r215f)")
        else:
            bits.append("the mounts forage for themselves (r215f)")
    if not water:
        n *= 2
        bits.append("doubled for carrying water (r215a)")
    return n, ", ".join(bits)


def wages(g: dict) -> int:
    return sum(c["pay"] for c in living(g))


def lodging_cost(g: dict, singles: list[dict] | None = None) -> tuple[int, str]:
    """r217: a room a night, the Prince and any spellcaster alone, others two to
    a room, one gold a mount for stables."""
    alone = {id(c) for c in (singles or [])}
    p = player(g)
    if p and not dead(p):
        alone.add(id(p))
    sharers = [c for c in men(g) if id(c) not in alone]
    single_rooms = len([c for c in men(g) if id(c) in alone])
    shared_rooms = -(-len(sharers) // 2)
    stables = len(mounts(g))
    bits = [plural(single_rooms, "single room")]
    if sharers:
        two_up = ", two to a room" if len(sharers) > 1 else ""
        bits.append(f"{plural(shared_rooms, 'shared room')} for "
                    f"{plural(len(sharers), 'follower')}{two_up}")
    if stables:
        bits.append(plural(stables, "stable"))
    return single_rooms + shared_rooms + stables, ", ".join(bits)


def loads(g: dict) -> tuple[int, int, str]:
    """r206a: a food unit is one load, every 100 gold or part of it is one load."""
    food = g["food"]
    gold = -(-g["gold"] // 100)
    carried = food + gold
    cap = sum(capacity(c) for c in living(g))
    return carried, cap, f"{food} food + {gold} for {g['gold']} gold"


# --- formatting -----------------------------------------------------------


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def week_of(day: int) -> str:
    w, d = divmod(day - 1, DAYS_PER_WEEK)
    return f"week {w + 1}, day {d + 1}"


def time_line(g: dict) -> str:
    day = g["day"]
    left = GAME_DAYS - day
    if day > GAME_DAYS:
        tail = f"PAST THE LIMIT - the game was lost on day {GAME_DAYS + 1} (e001)"
    elif left == 0:
        tail = "the last day - the Prince must be home in the Northlands tonight"
    else:
        tail = f"{left} day{'s' if left != 1 else ''} left of the ten weeks"
    return f"day {day} of {GAME_DAYS}   {week_of(day)}   {tail}"


def char_row(ch: dict) -> list[str]:
    eff = effective_cs(ch)
    cs = "-" if ch["kind"] == "mount" else (
        f"{ch['cs']}" if eff == ch["cs"] else f"{ch['cs']} -> {eff}")
    end = "-" if ch["kind"] == "mount" else str(ch["end"])
    wounds = "-" if ch["kind"] == "mount" else str(ch["wounds"])
    starve = f"{ch['starve']}d" if ch["starve"] else "-"
    pay = f"{ch['pay']}/day" if ch["pay"] else "-"
    tags = []
    if ch["guide"]:
        tags.append("guide")
    if ch["winged"]:
        tags.append("winged")
    if ch["wits"] is not None:
        tags.append(f"w&w {ch['wits']}")
    if ch["note"]:
        tags.append(ch["note"])
    return [ch["name"], ch["kind"], cs, end, wounds, starve, pay,
            condition(ch), ", ".join(tags)]


HEAD = ["name", "kind", "combat skill", "end", "wounds", "starving", "pay",
        "condition", ""]


def table(rows: list[list[str]], head: list[str]) -> str:
    widths = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    out = []
    for r in [head] + rows:
        out.append("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    rule = ("-" * w if h else " " * w for h, w in zip(head, widths))
    out.insert(1, "  " + "  ".join(rule).rstrip())
    return "\n".join(out)


def sheet(g: dict, brief: bool = False) -> str:
    out = [f"{g['name']}   {time_line(g)}"]
    where = f"hex {g['hex']}" if g.get("hex") else "hex not recorded"
    out.append(f"{where}   food {g['food']} units   gold {g['gold']}")
    if brief:
        return "\n".join(out)

    alive = living(g)
    if not alive:
        out.append("\nparty: nobody left alive")
    else:
        out.append("")
        out.append(table([char_row(c) for c in alive], HEAD))

    gone = [c for c in g["party"] if dead(c)]
    if gone:
        out.append("\ndead: " + ", ".join(c["name"] for c in gone) +
                   "   (bp party drop <name> to clear them off the sheet)")

    hurt = [c for c in alive if strike_notes(c)]
    if hurt:
        out.append("\ncombat modifiers in play (r220c):")
        for c in hurt:
            for n in strike_notes(c):
                out.append(f"  {c['name']}: {n}")

    starving = [c for c in alive if c["starve"]]
    if starving:
        out.append("\nstarvation (r216b): one off combat skill and half the load "
                   "capacity per day gone without food. A normal meal undoes one "
                   "day, a double meal two.")
        for c in starving:
            out.append(f"  {c['name']}: {plural(c['starve'], 'day')}, carries "
                       f"{capacity(c)} loads")

    carried, cap, detail = loads(g)
    over = "  OVER CAPACITY - something must be dropped or cached (r206, r214)" \
        if carried > cap else ""
    out.append(f"\nloads: {carried} of {cap} carried ({detail}){over}")

    upkeep = [f"{len(men(g))} food for the men"]
    if mounts(g):
        upkeep.append(f"0 or {2 * len(mounts(g))} for the mounts, depending on fodder")
    if wages(g):
        upkeep.append(f"{wages(g)} gold in wages")
    out.append("daily upkeep: " + ", ".join(upkeep) + "  (r215, r210)")

    live = [(sid, group, rec)
            for sid, groups in sorted(encounters(g).items())
            for group, rec in sorted(groups.items())
            if not encounter_stale(g, rec)]
    if live:
        out.append("\nband sizes read out today (bp encounter):")
        for sid, group, rec in live:
            out.append(f"  {sid} {group}: {rec['n']} {rec.get('noun', '')}")

    if g.get("foes"):
        out.append(f"\nCOMBAT IN PROGRESS - "
                   f"{plural(len(g['foes']), 'enemy', 'enemies')} on the sheet. bp foe")
    if g.get("notes"):
        out.append("\nnotes:")
        for n in g["notes"]:
            out.append(f"  - {n}")
    return "\n".join(out)


def summary_line(args) -> str | None:
    """One line for other commands to print, or None if nothing is tracked."""
    try:
        g = load_game(args, required=False)
    except Refuse:
        return None
    if not g:
        return None
    return (f"[{g['name']}] {time_line(g)} | food {g['food']} | gold {g['gold']} | "
            f"party {len(men(g))}" +
            (f" + {len(mounts(g))} mounts" if mounts(g) else ""))


# --- amounts --------------------------------------------------------------


def apply_amount(cur: int, raw: str, what: str, floor: int = 0) -> tuple[int, str]:
    """'+5' adds, '-3' spends, '12' or '=12' sets. Refuses to go below zero."""
    raw = raw.strip().replace(" ", "")
    if not re.fullmatch(r"[+\-=]?\d+", raw):
        raise Refuse(f"{raw!r} is not an amount. Use +5 to gain, -3 to spend, "
                     f"or 12 to set the total.")
    if raw[0] in "+-":
        new = cur + int(raw)
        delta = f" ({raw})"
    else:
        new = int(raw.lstrip("="))
        delta = f" (was {cur})"
    if new < floor:
        raise Refuse(f"that would leave {what} at {new}. You have {cur}, and "
                     f"{raw} takes {abs(int(raw))}. Nothing was changed - check "
                     f"the number, or spend what you actually have.")
    return new, f"{what}: {cur} -> {new}{delta}"


# --- commands: game -------------------------------------------------------


def cmd_game(book, args) -> int:
    try:
        g = load_game(args)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(sheet(g, brief=args.brief))
    return 0


def cmd_game_new(book, args) -> int:
    try:
        name = args.name or "game"
        path = path_for(name)
        if path.exists() and not args.force:
            raise Refuse(f"{path.relative_to(ROOT)} already exists. Use a different "
                         f"name, `bp game use {slug(name)}` to switch to it, or "
                         f"--force to start it over.")
        if args.wits is None:
            raise Refuse("wit & wiles is missing. It is 1d6, a 1 counting as 2 "
                         "(r202) - roll it with the player first, then pass --wits.")
        prince = new_char(args.prince, "player")
        prince.update(cs=args.cs, end=args.end, wits=args.wits)
        g = {
            "version": VERSION,
            "name": slug(name),
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "day": args.day,
            "food": args.food,
            "gold": args.gold,
            "hex": args.hex,
            "party": [prince],
            "foes": [],
            "encounters": {},
            "notes": [],
            "log": [],
        }
        note(g, f"game started: {prince['name']} cs {args.cs} end {args.end} "
                f"w&w {args.wits}, {args.gold} gold, {args.food} food")
        write_game(g)
        set_current(g["name"])
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(sheet(g))
    print(f"\nsaved to saves/{g['name']}.json and made current. These numbers are "
          f"tracked from here on - read them back with `bp game` rather than "
          f"remembering them.")
    return 0


def cmd_game_list(book, args) -> int:
    cur = current_name()
    saves = sorted(SAVES.glob("*.json"))
    if not saves:
        print("no games recorded. bp game new <name> --wits <w&w> --gold <gold>")
        return 0
    for p in saves:
        try:
            g = json.loads(p.read_text())
            line = f"{p.stem:<16} {time_line(g)}   food {g['food']}, gold {g['gold']}"
        except (json.JSONDecodeError, KeyError):
            line = f"{p.stem:<16} (unreadable)"
        print(("* " if p.stem == cur else "  ") + line)
    return 0


def cmd_game_use(book, args) -> int:
    path = path_for(args.name)
    if not path.exists():
        print(f"no save called {slug(args.name)!r}. bp game list", file=sys.stderr)
        return 1
    set_current(args.name)
    print(f"current game is now {slug(args.name)}")
    return 0


def cmd_game_set(book, args) -> int:
    try:
        g = load_game(args)
        changed = []
        if args.hex is not None:
            g["hex"] = args.hex
            changed.append(f"hex {args.hex}")
        if args.note:
            g.setdefault("notes", []).append(args.note)
            changed.append(f"note {args.note!r}")
        if args.clear_notes:
            g["notes"] = []
            changed.append("notes cleared")
        if not changed:
            raise Refuse("nothing to set. Use --hex, --note or --clear-notes.")
        note(g, "; ".join(changed))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print("; ".join(changed))
    print(sheet(g, brief=True))
    return 0


def cmd_game_log(book, args) -> int:
    try:
        g = load_game(args)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    entries = g.get("log", [])[-args.limit:]
    if not entries:
        print("nothing logged yet")
        return 0
    for e in entries:
        print(f"  day {e['day']:>3}  {e['what']}")
    return 0


# --- commands: time, food, gold -------------------------------------------


def cmd_time(book, args) -> int:
    try:
        g = load_game(args)
        if args.amount:
            old = g["day"]
            g["day"], line = apply_amount(g["day"], args.amount, "day", floor=1)
            note(g, line)
            write_game(g)
            print(line)
            if g["day"] > old:
                print("\nThe end-of-day checks belong to the day that just ended - "
                      "e002 north of the Tragoth, the meal, then lodging (r215-r217). "
                      "`bp day` lists them in order.")
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(time_line(g))
    if g["day"] > GAME_DAYS:
        print("\nThe ten weeks are up. Unless the Prince is home in the Northlands "
              "with 500 gold, the game is lost (e001).")
    elif GAME_DAYS - g["day"] <= 7:
        print("\nLess than a week left - the run home has to start now (e001).")
    return 0


def cmd_food(book, args) -> int:
    try:
        g = load_game(args)
        if args.amount:
            g["food"], line = apply_amount(g["food"], args.amount, "food")
            note(g, line)
            write_game(g)
            print(line)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    need = len(men(g))
    if need:
        print(f"{plural(g['food'], 'food unit')}; the men eat {need} a day, so "
              f"there is enough for {plural(g['food'] // need, 'more day')} of "
              f"meals (r215a)")
    else:
        print(plural(g['food'], 'food unit'))
    if mounts(g):
        print(f"plus {2 * len(mounts(g))} a day for the mounts wherever they "
              f"cannot forage (r215f)")
    return 0


def cmd_gold(book, args) -> int:
    try:
        g = load_game(args)
        if args.amount:
            g["gold"], line = apply_amount(g["gold"], args.amount, "gold")
            note(g, line)
            write_game(g)
            print(line)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"gold {g['gold']}   ({wages(g)} a day in wages)" if wages(g)
          else f"gold {g['gold']}")
    short = 500 - g["gold"]
    if short > 0:
        print(f"{short} short of the 500 the Prince must bring home (e001)")
    else:
        print("that is the 500 gold - get home to the Northlands (e001)")
    return 0


# --- commands: party ------------------------------------------------------


def set_fields(ch: dict, args) -> list[str]:
    changed = []
    for field in ("cs", "end", "wits", "wounds", "starve", "pay"):
        val = getattr(args, field, None)
        if val is not None:
            ch[field] = val
            changed.append(f"{field} {val}")
    for flag in ("guide", "winged"):
        val = getattr(args, flag, None)
        if val is not None:
            ch[flag] = val
            changed.append(f"{flag} {'yes' if val else 'no'}")
    if getattr(args, "note", None) is not None:
        ch["note"] = args.note
        changed.append(f"note {args.note!r}")
    return changed


def cmd_party(book, args) -> int:
    try:
        g = load_game(args)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    alive = living(g)
    if not alive:
        print("nobody left alive in the party")
        return 0
    print(table([char_row(c) for c in alive], HEAD))
    guides = [c["name"] for c in alive if c["guide"]]
    print(f"\nguides: {', '.join(guides) if guides else 'none'} "
          f"({'-1 on every lost check (r205a)' if guides else 'no -1 on lost checks'})")
    if wages(g):
        print(f"wages: {wages(g)} gold a day (r210)")
    return 0


def cmd_party_add(book, args) -> int:
    try:
        g = load_game(args)
        if any(c["name"].lower() == args.name.strip().lower() for c in g["party"]):
            raise Refuse(f"{args.name!r} is already on the sheet. Use `bp party set` "
                         f"to change them, or give this one a distinct name.")
        ch = new_char(args.name, args.kind)
        set_fields(ch, args)
        if ch["kind"] != "mount" and (ch["cs"] == 0 or ch["end"] == 0):
            raise Refuse(f"a {ch['kind']} needs a combat skill and an endurance "
                         f"(r201) - pass --cs and --end. The section that produced "
                         f"them prints both; r210 does for hired followers.")
        g["party"].append(ch)
        note(g, f"joined: {ch['name']} ({ch['kind']}) cs {ch['cs']} end {ch['end']}"
                + (f", {ch['pay']} gold/day" if ch["pay"] else " at no cost"))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{ch['name']} joins the party.")
    print(table([char_row(c) for c in living(g)], HEAD))
    if ch["pay"]:
        print(f"\nwages are now {wages(g)} gold a day, paid daily (r210).")
    if ch["kind"] != "mount":
        print(f"they eat one food unit a day like everyone else (r215a).")
    return 0


def cmd_party_set(book, args) -> int:
    try:
        g = load_game(args)
        ch = find(g["party"], args.name)
        changed = set_fields(ch, args)
        if not changed:
            raise Refuse("nothing to change. Fields: --cs --end --wits --wounds "
                         "--starve --pay --guide/--no-guide --winged/--no-winged "
                         "--note")
        note(g, f"{ch['name']}: " + ", ".join(changed))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{ch['name']}: " + ", ".join(changed))
    print(table([char_row(ch)], HEAD))
    for n in strike_notes(ch):
        print(f"  {n} (r220c)")
    if dead(ch):
        print(f"\n{ch['name']} is dead (r221c)." +
              ("  The Prince has fallen - the game is lost (r221c)."
               if ch["kind"] == "player" else ""))
    return 0


def cmd_party_wound(book, args) -> int:
    try:
        g = load_game(args)
        ch = find(g["party"], args.name)
        if ch["kind"] == "mount":
            raise Refuse("mounts are not tracked for wounds here - if a mount is "
                         "killed by an event, bp party drop it.")
        ch["wounds"], line = apply_amount(ch["wounds"], args.amount,
                                          f"{ch['name']} wounds")
        note(g, line)
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(line)
    print(f"{ch['name']}: {condition(ch)}, combat skill now {effective_cs(ch)}")
    for n in strike_notes(ch):
        print(f"  {n} (r220c)")
    if dead(ch):
        if ch["kind"] == "player":
            print("\nThe Barbarian Prince is dead. The game is lost (r221c).")
        else:
            print(f"\n{ch['name']} is dead (r221c). bp party drop {ch['name']!r} "
                  f"when you have taken anything they carried.")
    elif unconscious(ch) and ch["kind"] == "player":
        print("\nThe Prince is unconscious: roll 1d6 for his followers. 4 or more "
              "and they carry him (20 loads, r206b); 3 or less and they all desert, "
              "taking his money and possessions (r221b).")
    return 0


def cmd_party_heal(book, args) -> int:
    try:
        g = load_game(args)
        targets = [find(g["party"], n) for n in args.names] if args.names else \
            [c for c in living(g) if c["kind"] != "mount"]
        healed = []
        for ch in targets:
            if ch["wounds"] <= 0:
                continue
            ch["wounds"] = max(0, ch["wounds"] - args.amount)
            healed.append(f"{ch['name']} -> {ch['wounds']}")
        if not healed:
            print("nobody had a wound to heal")
            return 0
        note(g, "healed: " + ", ".join(healed))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print("healed " + ", ".join(healed))
    print("\nA day's rest heals one wound each, but only if no combat or escape "
          "happened that day, and the hex still gets a travel-event check (r222). "
          "Poison wounds never heal this way.")
    return 0


def cmd_party_drop(book, args) -> int:
    try:
        g = load_game(args)
        ch = find(g["party"], args.name)
        if ch["kind"] == "player":
            raise Refuse("the Prince cannot leave his own party. If he died, the "
                         "game is over (r221c).")
        g["party"].remove(ch)
        note(g, f"left the party: {ch['name']} ({args.why or 'dismissed'})")
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{ch['name']} is off the sheet ({args.why or 'dismissed'}).")
    if ch["pay"]:
        print(f"wages are now {wages(g)} gold a day.")
    return 0


# --- commands: eat, lodge -------------------------------------------------


def fodder_here(book, g, args) -> bool | None:
    """Whether mounts can forage: from the flag, else from the hex's terrain."""
    if args.fodder is not None:
        return args.fodder
    hid = args.hex or g.get("hex")
    if not hid:
        return None
    try:
        key = procedures.terrain_key(book, hid)
    except procedures.Refuse as e:
        raise Refuse(f"{e}\nSo I cannot tell whether the mounts can forage - pass "
                     f"--fodder or --no-fodder.")
    return book.travel["terrain"][key]["fodder"].strip().lower().startswith("y")


def cmd_eat(book, args) -> int:
    """r215: the evening meal, and r216 for whoever goes without."""
    try:
        g = load_game(args)
        skipped = [find(g["party"], n) for n in args.skip]
        doubles = [find(g["party"], n) for n in args.double]
        # Identity, not equality: two followers rolled off the same line of r210
        # are equal dicts, and `in` would drop the wrong one from the meal.
        eaters = [c for c in living(g) if not any(c is s for s in skipped)]
        if not eaters:
            raise Refuse("nobody is eating - that is not a meal, it is starvation. "
                         "bp starve instead.")

        fodder = fodder_here(book, g, args)
        eating_men = [c for c in eaters if c["kind"] != "mount"]
        eating_mounts = [c for c in eaters if c["kind"] == "mount"]
        units = len(eating_men) + sum(1 for c in doubles if c["kind"] != "mount")
        detail = [plural(len(eating_men), "man", "men") +
                  (f", {len(doubles)} of them eating double" if doubles else "")]
        if eating_mounts:
            if fodder is None:
                raise Refuse(
                    f"{len(eating_mounts)} mounts are eating and I do not know "
                    f"whether they can forage here (r215f). Pass --hex <id>, or "
                    f"--fodder / --no-fodder.")
            if not fodder:
                units += 2 * len(eating_mounts)
                detail.append(f"{2 * len(eating_mounts)} for "
                              f"{plural(len(eating_mounts), 'mount')} with no fodder")
            else:
                detail.append(f"{plural(len(eating_mounts), 'mount')} foraging free")
        if not args.water:
            units *= 2
            detail.append("doubled - no water here (r215a)")

        if args.buy:
            cost = len(eating_men) + len(eating_mounts)   # r215d: 1 gold each
            if cost > g["gold"]:
                raise Refuse(f"a purchased meal is {cost} gold ({len(eating_men)} "
                             f"characters, {len(eating_mounts)} at the stables) and "
                             f"you have {g['gold']} (r215d). Eat stores, or feed "
                             f"fewer and starve the rest.")
            g["gold"] -= cost
            paid = f"bought for {cost} gold (r215d)"
        elif args.free:
            paid = "hunted or foraged - no stores used (r215b)"
        else:
            if units > g["food"]:
                raise Refuse(
                    f"the meal needs {plural(units, 'food unit')} "
                    f"({'; '.join(detail)}) and "
                    f"you have {g['food']}. Nothing has been eaten. Either hunt "
                    f"(r215b, in farmland/countryside/forest/hills/swamp), buy a "
                    f"meal with --buy in a town, castle or temple, or feed some and "
                    f"starve the rest with --skip <name> (r216).")
            g["food"] -= units
            paid = f"{plural(units, 'food unit')} eaten ({'; '.join(detail)})"

        fed, starved = [], []
        for ch in eaters:
            if ch["kind"] == "mount":
                # r216: a mount recovers from all starvation on one normal meal.
                if ch["starve"]:
                    fed.append(f"{ch['name']} (mount) recovers fully from "
                               f"{plural(ch['starve'], 'day')} starving")
                ch["starve"] = 0
            else:
                back = 2 if any(ch is d for d in doubles) else 1
                if ch["starve"]:
                    ch["starve"] = max(0, ch["starve"] - back)
                    fed.append(f"{ch['name']} works off {plural(back, 'day')} of "
                               f"starvation -> {ch['starve']} left")
        for ch in skipped:
            ch["starve"] += 1
            starved.append(ch)

        note(g, f"meal: {paid}" + (f"; skipped {', '.join(c['name'] for c in skipped)}"
                                   if skipped else ""))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1

    print(f"The party eats. {paid}.")
    print(f"food {g['food']}, gold {g['gold']}")
    for line in fed:
        print(f"  {line}")
    if doubles:
        print("  (a double meal undoes two days of starvation; a triple does no "
              "more than a double - r216b)")
    if starved:
        print()
        print(starve_report(g, starved))
    print()
    if wages(g):
        print(f"Wages are paid at the evening meal too - {wages(g)} gold tonight, "
              f"or the hirelings leave (r333): bp pay")
    print("Then lodging, if this is a town, castle or temple (r217): bp lodge")
    return 0


def starve_report(g: dict, who: list[dict]) -> str:
    out = [f"Going without food tonight: {', '.join(c['name'] for c in who)} (r216)."]
    followers = [c for c in who if c["kind"] == "follower"]
    if followers:
        w = wits(g)
        out.append(f"  Roll 2d6 for each follower and subtract wit & wiles"
                   f"{f' ({w})' if w is not None else ''}: 4 or more and he deserts "
                   f"(r216a). bp party drop <name> --why deserted for any who go.")
        if not any(c["kind"] == "player" for c in who):
            out.append("  Note r216a: you cannot withhold food from followers while "
                       "eating yourself, if there is food or money for it.")
    for c in who:
        if c["kind"] == "mount":
            cap = capacity(c)
            out.append(f"  {c['name']} (mount) carries {cap} loads now" +
                       (" - starved to nothing, the mount dies (r216)" if cap == 0
                        else ""))
        else:
            out.append(f"  {c['name']}: from tomorrow combat skill "
                       f"{max(0, c['cs'] - c['starve'])} and {capacity(c)} loads "
                       f"(r216b)")
    return "\n".join(out)


def cmd_starve(book, args) -> int:
    """Nobody ate: record the day gone without, and print what it costs."""
    try:
        g = load_game(args)
        who = [find(g["party"], n) for n in args.names] if args.names else living(g)
        for ch in who:
            ch["starve"] += 1
        note(g, "went without food: " + ", ".join(c["name"] for c in who))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(starve_report(g, who))
    return 0


def cmd_pay(book, args) -> int:
    """r333: hirelings stay as long as they are paid every day at the evening meal."""
    try:
        g = load_game(args)
        unpaid = [find(g["party"], n) for n in args.skip]
        owed = [c for c in living(g) if c["pay"]
                and not any(c is u for u in unpaid)]
        due = sum(c["pay"] for c in owed)
        if not owed and not unpaid:
            print("nobody in the party is on wages")
            return 0
        breakdown = ", ".join("{} {}".format(c["name"], c["pay"]) for c in owed)
        if due > g["gold"]:
            raise Refuse(
                f"wages come to {due} gold ({breakdown}) and you have {g['gold']} "
                f"(r333). Pay who you can - --skip <name> for the rest - but an "
                f"unpaid hireling leaves at the evening meal.")
        g["gold"] -= due
        note(g, f"wages: {due} gold to {len(owed)} follower(s)" +
                (f"; unpaid: {', '.join(c['name'] for c in unpaid)}" if unpaid else ""))
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    if owed:
        print(f"Wages paid: {due} gold - {breakdown} (r333).")
        print(f"gold {g['gold']}")
    for c in unpaid:
        print(f"{c['name']} goes unpaid. A hireling stays only as long as he is paid "
              f"each evening (r333) - bp party drop {c['name']!r} --why unpaid, "
              f"unless the section that hired him says otherwise.")
    return 0


def cmd_lodge(book, args) -> int:
    """r217: rooms and stables for a night in a town, castle or temple."""
    try:
        g = load_game(args)
        singles = [find(g["party"], n) for n in args.single]
        cost, detail = lodging_cost(g, singles)
        if args.rough:
            print(f"No rooms bought. Roll 2d6 for each character and subtract wit "
                  f"& wiles ({wits(g)}): 4 or more and he deserts, refusing to serve "
                  f"such a penurious leader (r217).")
            if mounts(g):
                print(f"Roll 1d6 for each of the {len(mounts(g))} mounts too: 4 or "
                      f"more and thieves take it in the night, permanently (r217).")
            return 0
        if cost > g["gold"]:
            raise Refuse(f"lodging is {cost} gold ({detail}) and you have "
                         f"{g['gold']} (r217). Pay what you can by dismissing "
                         f"followers first, or sleep rough with --rough and take "
                         f"the desertion rolls.")
        g["gold"] -= cost
        note(g, f"lodging: {cost} gold ({detail})")
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Lodging for the night: {cost} gold - {detail} (r217).")
    print(f"gold {g['gold']}")
    print("\nThe Prince and any priest, monk, magician, wizard or witch each need "
          "a room to themselves; pass --single <name> for any spellcaster who "
          "joined (r217).")
    return 0


# --- commands: foes -------------------------------------------------------


def foe_row(f: dict) -> list[str]:
    if dead(f):
        state = "DEAD"
    elif unconscious(f):
        state = "unconscious and helpless, cs 0 (r221b)"
    elif serious(f):
        state = "seriously wounded: +2 to strikes at him (r220c)"
    else:
        state = "ok"
    return [f["name"], str(f["cs"]), str(f["end"]), str(f["wounds"]),
            str(f["wealth"]) if f["wealth"] else "-", state]


FOE_HEAD = ["enemy", "cs", "end", "wounds", "wealth", "condition"]


def cmd_foe(book, args) -> int:
    try:
        g = load_game(args)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    foes = g.get("foes", [])
    if not foes:
        print("no combat in progress. bp foe add <name> --cs <n> --end <n> "
              "[--wealth <code>] [--count <n>]")
        return 0
    print(table([foe_row(f) for f in foes], FOE_HEAD))
    alive = [f for f in foes if not dead(f)]
    print(f"\n{len(alive)} of {len(foes)} still standing.")
    print("Strike: striker's combat skill minus target's, plus 2d6, plus the r220c "
          f"modifiers. Results {combat_table_prose()}. Anything else misses.")
    print("bp foe wound <name> +2 to apply one. bp foe clear when the fight ends.")
    print("bp fight auto to have the whole fight rolled out round by round.")
    return 0


def cmd_foe_add(book, args) -> int:
    try:
        g = load_game(args)
        clash = count_warning(g, args.name, args.count)
        if clash:
            raise Refuse(clash)
        added = []
        for i in range(args.count):
            name = args.name if args.count == 1 else f"{args.name} {i + 1}"
            if any(f["name"].lower() == name.lower() for f in g.get("foes", [])):
                raise Refuse(f"there is already an enemy called {name!r} in this "
                             f"fight - give them distinct names or use --count.")
            g.setdefault("foes", []).append({
                "name": name, "cs": args.cs, "end": args.end,
                "wealth": args.wealth or 0, "wounds": 0,
            })
            added.append(name)
        note(g, f"combat: faced {', '.join(added)} (cs {args.cs}, end {args.end})")
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"facing {', '.join(added)}")
    print(table([foe_row(f) for f in g["foes"]], FOE_HEAD))
    print("\nMatch each of your characters against one enemy, then strike (r220a, "
          "r220b). Your own combat modifiers: bp party")
    return 0


def cmd_foe_wound(book, args) -> int:
    try:
        g = load_game(args)
        f = find(g.get("foes", []), args.name, "this fight")
        f["wounds"], line = apply_amount(f["wounds"], args.amount,
                                         f"{f['name']} wounds")
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(line)
    print(table([foe_row(f)], FOE_HEAD))
    if dead(f):
        print(f"\n{f['name']} is dead (r221c). Roll 1d6: on a 6 the rest rout and "
              f"flee, and you get none of their wealth (r220f). Enemies with combat "
              f"skill or endurance 9+ never rout.")
        left = [x for x in g["foes"] if not dead(x)]
        if not left:
            print("That was the last of them - the fight is over. bp foe clear")
    return 0


def cmd_foe_clear(book, args) -> int:
    try:
        g = load_game(args)
        foes = g.get("foes", [])
        if not foes:
            print("no combat in progress")
            return 0
        killed = [f for f in foes if dead(f)]
        loot = [f for f in killed if f["wealth"]]
        g["foes"] = []
        # The fight is over, so the band that was counted for it is over too.
        counts = encounter_clear(g)
        note(g, f"combat ended: {len(killed)} of {len(foes)} killed")
        write_game(g)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"combat cleared - {len(killed)} of {len(foes)} enemies killed.")
    if counts:
        print(f"({plural(counts, 'creature count')} forgotten too - the next band "
              f"of the same kind is rolled fresh.)")
    if loot and not args.no_loot:
        print("\nBefore they are forgotten, the dead carry wealth (r225):")
        for f in loot:
            print(f"  {f['name']}: wealth code {f['wealth']} -> roll 1d6, "
                  f"bp treasure {f['wealth']} <die>")
        print("Then bp gold +<amount>. If the enemy routed rather than died, you "
              "get none of it (r220f).")
    return 0


# --- wiring ---------------------------------------------------------------


def add_char_flags(s, kinds: bool = False) -> None:
    if kinds:
        s.add_argument("--kind", choices=KINDS, default="follower",
                       help="follower (default), mount, or player")
    s.add_argument("--cs", type=int, help="combat skill (r201)")
    s.add_argument("--end", type=int, help="endurance - wounds needed to kill (r221)")
    s.add_argument("--wits", type=int, help="wit & wiles; the Prince only (r202)")
    s.add_argument("--wounds", type=int, help="wounds taken so far")
    s.add_argument("--starve", type=int, metavar="DAYS",
                   help="days gone without food (r216b)")
    s.add_argument("--pay", type=int, metavar="GOLD",
                   help="wages per day for a hired follower (r210)")
    # Tri-state, like bp move's --river: unset means "leave it as it is", so
    # `bp party set` can change one field without silently clearing the others.
    s.add_argument("--guide", default=None, action=argparse.BooleanOptionalAction,
                   help="counts as a guide: -1 on lost checks (r205a)")
    s.add_argument("--winged", default=None, action=argparse.BooleanOptionalAction,
                   help="a winged mount, or a follower who can fly (r204a)")
    s.add_argument("--note", help="free text kept on the sheet")


def sheet_command(fn):
    """Make a state command print to stderr instead of stdout.

    The sheet is the referee's, never the player's. Every command in this file
    prints the same kind of thing - aligned tables, bare numbers, rule cites,
    and `bp foe add` lines to run next - and none of it is anything a DM says.
    Read aloud, `bp game` becomes "name kind combat skill end wounds starving
    pay condition, dash dash dash", which is how it sounded before this existed.

    So the numbers reach the narrator and the narrator says them: "you have four
    gold and nothing to eat". Wrapping at registration rather than in each of
    the two dozen commands keeps the rule in one place, where it can't drift.
    """
    def wrapped(book, args):
        with contextlib.redirect_stdout(sys.stderr):
            return fn(book, args)
    return wrapped


def register(sub) -> None:
    """Add the state commands to bp's subparser. Called from bp.main()."""

    def game_flag(p):
        p.add_argument("--game", help="act on a save other than the current one")

    # bp game ...
    s = sub.add_parser("game", help="the character sheet: day, food, gold, party")
    game_flag(s)
    s.add_argument("-b", "--brief", action="store_true", help="one-line summary")
    s.set_defaults(fn=sheet_command(cmd_game))
    gsub = s.add_subparsers(dest="gamecmd")

    n = gsub.add_parser("new", help="start tracking a game (after bp start)")
    n.add_argument("name", nargs="?", default="game")
    n.add_argument("--wits", type=int, required=False,
                   help="wit & wiles, 1d6 with a 1 counting as 2 (r202)")
    n.add_argument("--gold", type=int, default=0, help="starting gold (r225)")
    n.add_argument("--food", type=int, default=0, help="starting food units")
    n.add_argument("--hex", help="the hex the caravan dropped them in (e001)")
    n.add_argument("--day", type=int, default=1)
    n.add_argument("--prince", default="Cal Arath", help="the player's name")
    n.add_argument("--cs", type=int, default=8, help="combat skill (r202: 8)")
    n.add_argument("--end", type=int, default=9, help="endurance (r202: 9)")
    n.add_argument("--force", action="store_true", help="overwrite an existing save")
    n.set_defaults(fn=sheet_command(cmd_game_new))

    l = gsub.add_parser("list", help="saves on disk")
    l.set_defaults(fn=sheet_command(cmd_game_list))

    u = gsub.add_parser("use", help="switch which game is current")
    u.add_argument("name")
    u.set_defaults(fn=sheet_command(cmd_game_use))

    e = gsub.add_parser("set", help="record where the party is, or a note")
    game_flag(e)
    e.add_argument("--hex", help="the hex they are standing in")
    e.add_argument("--note", help="add a note to the sheet")
    e.add_argument("--clear-notes", action="store_true")
    e.set_defaults(fn=sheet_command(cmd_game_set))

    g = gsub.add_parser("log", help="what has been recorded, most recent last")
    game_flag(g)
    g.add_argument("-n", "--limit", type=int, default=25)
    g.set_defaults(fn=sheet_command(cmd_game_log))

    # bp time / food / gold
    s = sub.add_parser("time", help="the day counter: 70 days, ten weeks (e001)")
    game_flag(s)
    s.add_argument("amount", nargs="?", help="+1 to advance a day, or 12 to set it")
    s.set_defaults(fn=sheet_command(cmd_time))

    s = sub.add_parser("food", help="food units in store (r215a)")
    game_flag(s)
    s.add_argument("amount", nargs="?", help="+5 gained, -3 spent, or 12 to set")
    s.set_defaults(fn=sheet_command(cmd_food))

    s = sub.add_parser("gold", help="gold pieces (r225)")
    game_flag(s)
    s.add_argument("amount", nargs="?", help="+40 gained, -3 spent, or 12 to set")
    s.set_defaults(fn=sheet_command(cmd_gold))

    # bp party ...
    s = sub.add_parser("party", help="the characters with you, and their state")
    game_flag(s)
    s.set_defaults(fn=sheet_command(cmd_party))
    psub = s.add_subparsers(dest="partycmd")

    a = psub.add_parser("add", help="a follower or mount joins (r201)")
    game_flag(a)
    a.add_argument("name")
    add_char_flags(a, kinds=True)
    a.set_defaults(fn=sheet_command(cmd_party_add))

    st = psub.add_parser("set", help="correct any field on a character")
    game_flag(st)
    st.add_argument("name")
    add_char_flags(st)
    st.set_defaults(fn=sheet_command(cmd_party_set))

    w = psub.add_parser("wound", help="apply or remove wounds (r221)")
    game_flag(w)
    w.add_argument("name")
    w.add_argument("amount", help="+2 for two wounds, -1 to take one back")
    w.set_defaults(fn=sheet_command(cmd_party_wound))

    h = psub.add_parser("heal", help="a day's rest: one wound each (r222)")
    game_flag(h)
    h.add_argument("names", nargs="*", help="default: everyone")
    h.add_argument("-n", "--amount", type=int, default=1)
    h.set_defaults(fn=sheet_command(cmd_party_heal))

    d = psub.add_parser("drop", help="a follower dies, deserts or is dismissed")
    game_flag(d)
    d.add_argument("name")
    d.add_argument("--why", help="dead, deserted, dismissed, sold...")
    d.set_defaults(fn=sheet_command(cmd_party_drop))

    # bp eat / starve / lodge
    s = sub.add_parser("eat", help="the evening meal, and who goes without (r215)")
    game_flag(s)
    s.add_argument("--skip", nargs="*", default=[], metavar="NAME",
                   help="who is not fed tonight (r216)")
    s.add_argument("--double", nargs="*", default=[], metavar="NAME",
                   help="who eats double to work off starvation (r216b)")
    s.add_argument("--buy", action="store_true",
                   help="purchase meals in a town, castle or village: 1 gold each (r215d)")
    s.add_argument("--free", action="store_true",
                   help="hunted or foraged - no stores consumed (r215b)")
    s.add_argument("--hex", help="where they are, to read fodder off the map")
    s.add_argument("--fodder", default=None, action=argparse.BooleanOptionalAction,
                   help="can the mounts forage here? (r215f)")
    s.add_argument("--no-water", dest="water", action="store_false", default=True,
                   help="desert with no oasis: the requirement doubles (r215a)")
    s.set_defaults(fn=sheet_command(cmd_eat))

    s = sub.add_parser("pay", help="the day's wages to hired followers (r333)")
    game_flag(s)
    s.add_argument("--skip", nargs="*", default=[], metavar="NAME",
                   help="who goes unpaid tonight - they leave (r333)")
    s.set_defaults(fn=sheet_command(cmd_pay))

    s = sub.add_parser("starve", help="record a day gone without food (r216)")
    game_flag(s)
    s.add_argument("names", nargs="*", help="default: the whole party")
    s.set_defaults(fn=sheet_command(cmd_starve))

    s = sub.add_parser("lodge", help="rooms and stables for the night (r217)")
    game_flag(s)
    s.add_argument("--single", nargs="*", default=[], metavar="NAME",
                   help="followers needing a room alone: priests, monks, magicians, "
                        "wizards, witches (r217)")
    s.add_argument("--rough", action="store_true",
                   help="buy no rooms, and take the desertion rolls instead")
    s.set_defaults(fn=sheet_command(cmd_lodge))

    # bp foe ...
    s = sub.add_parser("foe", help="enemies in the current fight (r220), cleared after")
    game_flag(s)
    s.set_defaults(fn=sheet_command(cmd_foe))
    fsub = s.add_subparsers(dest="foecmd")

    a = fsub.add_parser("add", help="an enemy character enters the fight")
    game_flag(a)
    a.add_argument("name")
    a.add_argument("--cs", type=int, required=True, help="combat skill (r201)")
    a.add_argument("--end", type=int, required=True, help="endurance (r201)")
    a.add_argument("--wealth", type=int, default=0,
                   help="wealth code, for treasure if killed (r225, r226)")
    a.add_argument("--count", type=int, default=1,
                   help="that many identical enemies, numbered")
    a.set_defaults(fn=sheet_command(cmd_foe_add))

    w = fsub.add_parser("wound", help="wound an enemy")
    game_flag(w)
    w.add_argument("name")
    w.add_argument("amount", help="+1, +2, ...")
    w.set_defaults(fn=sheet_command(cmd_foe_wound))

    c = fsub.add_parser("clear", help="the fight is over; forget the enemies")
    game_flag(c)
    c.add_argument("--no-loot", action="store_true",
                   help="skip the treasure reminder (they routed, or fled)")
    c.set_defaults(fn=sheet_command(cmd_foe_clear))
