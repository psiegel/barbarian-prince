"""How many of them there are: the die rolls that size a band of enemies.

Two dozen event sections name their enemies and their stats but leave the size of
the band to a die - "You sight a band of Goblins in the distance. Roll two dice
for the number in the band". Read out as printed, that stops the sentence dead to
ask for a roll, and then leaves the number in nobody's hands but the narrator's,
which is where eight goblins quietly becomes three goblins two messages later.

So the count is rolled once, written into the save file against the day and hex it
was rolled for, and substituted into the prose wherever the section is read -
`show`, `speak`, `options`, and the follow-on section `resolve` prints. Every later
read of the same section on the same day in the same hex gets the same number back
out, and the `bp foe add` line is printed with that number already in it, so the
sheet cannot end up disagreeing with what was said aloud. A different day or a
different hex is a different band, so the count is rolled again.

Only creature counts. Wounds suffered, days lost, hexes blown off course and gold
demanded in a bribe are consequences the player rolls for themselves, and they are
read exactly as printed - see the _readme in data/creatures.json.

Nothing is substituted when no game is being tracked. There would be nowhere to
record the number, and an unrecorded roll is the whole problem this file exists to
prevent, so the booklet's own wording is read instead.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

import play
import state

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "creatures.json"

_CACHE: dict | None = None


def load() -> dict:
    """The rewrites. Missing file is not an error - bp simply reads as printed."""
    global _CACHE
    if _CACHE is None:
        if not DATA.exists():
            _CACHE = {}
        else:
            raw = json.loads(DATA.read_text())
            _CACHE = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _CACHE


def specs(sid: str) -> list[dict]:
    return load().get(sid.strip().lower(), [])


def base_id(sid: str) -> str:
    """'e001#caravan' -> 'e001'. Counts are filed under the whole section."""
    return sid.split("#")[0].strip().lower()


# --- the roll -------------------------------------------------------------


def describe(spec: dict) -> str:
    out = f"{spec.get('dice', 1)}d6"
    div = spec.get("div")
    if div == 2:
        out += " halved, rounding up"
    elif div:
        out += f" divided by {div}, rounding up"
    if spec.get("add"):
        out += f" {spec['add']:+d}"
    if spec.get("min") is not None:
        out += f", minimum {spec['min']}"
    return out


def lowest(spec: dict) -> int:
    """The smallest band this spec can produce - does it need singular wording?"""
    n = spec.get("dice", 1)
    if spec.get("div"):
        n = -(-n // spec["div"])
    n += spec.get("add", 0)
    return max(n, spec.get("min", n))


def size(spec: dict) -> tuple[int, str]:
    """Roll one band size. Returns the count and how it was arrived at."""
    dice = [random.randint(1, 6) for _ in range(spec.get("dice", 1))]
    n = sum(dice)
    if spec.get("div"):
        n = -(-n // spec["div"])          # round up, without importing math
    n += spec.get("add", 0)
    if spec.get("min") is not None:
        n = max(n, spec["min"])
    return n, f"{describe(spec)} [{' + '.join(map(str, dice))}]"


def head_count(expr, n: int) -> int:
    """A foe line's count: a fixed number, or 'n' / 'n-1' against the band size."""
    if isinstance(expr, int):
        return expr
    m = re.fullmatch(r"n\s*([+-]\s*\d+)?", str(expr).strip())
    if not m:
        raise ValueError(f"count {expr!r} in data/creatures.json is not a number, "
                         f"'n', or 'n-1'")
    return n + int((m.group(1) or "0").replace(" ", ""))


def wording(spec: dict, n: int) -> tuple[str, str]:
    """The rewrite and the noun to use for a band of n.

    "There are 1 in the patrol" is the sort of thing that makes a narrator stop
    trusting the tool, so any count that can come out as one carries a singular
    form alongside the plural.
    """
    if n == 1:
        return (spec.get("one") or spec["replace"],
                spec.get("noun_one") or spec.get("noun", spec["group"]))
    return spec["replace"], spec.get("noun", spec["group"])


def roster(spec: dict, n: int) -> dict[str, int]:
    """Who a band of n comes to on the sheet: enemy name -> how many.

    Recorded alongside the count so `bp foe add` can check any of them, not just
    the rank and file - e055's six orcs are five warriors and one chieftain, and
    neither line's number is six.
    """
    out = {}
    for f in spec.get("foes", []):
        c = head_count(f.get("count", "n"), n)
        if c > 0:                         # a patrol of one has no rank and file
            out[f["name"]] = c
    return out


def foe_lines(spec: dict, n: int) -> list[str]:
    out = []
    for f in spec.get("foes", []):
        c = head_count(f.get("count", "n"), n)
        if c <= 0:
            continue
        # Quoted, because a demi-Troll and an Orc Chieftain have spaces in them
        # and the line is printed to be pasted straight back in.
        name = f'"{f["name"]}"' if " " in f["name"] else f["name"]
        line = f"bp foe add {name} --cs {f['cs']} --end {f['end']}"
        if f.get("wealth"):
            line += f" --wealth {f['wealth']}"
        if c > 1:
            line += f" --count {c}"
        out.append(line)
    return out


# --- recording ------------------------------------------------------------

NO_GAME = ("[{sid}] the band's size is left as the booklet prints it: no game is "
           "being tracked, so there is nowhere to record the number. Start one "
           "with `bp game new`, or take the roll yourself.")

DRIFT = ("[{sid}] data/creatures.json expects the phrase {find!r}, which is not in "
         "the section text any more, so the count was left as printed. The file "
         "needs fixing.")


def recall(g: dict, sid: str, spec: dict) -> tuple[dict, bool]:
    """This encounter's count: the one already recorded, or a fresh roll."""
    rec = state.encounter_get(g, sid, spec["group"])
    if rec and not state.encounter_stale(g, rec):
        return rec, False
    n, how = size(spec)
    rec = state.encounter_set(g, sid, spec["group"], n, noun=wording(spec, n)[1],
                              how=how, foes=roster(spec, n))
    return rec, True


def report(sid: str, spec: dict, rec: dict, fresh: bool) -> list[str]:
    n, noun = rec["n"], rec.get("noun", spec["group"])
    where = f"day {rec['day']}" + (f", hex {rec['hex']}" if rec.get("hex") else "")
    if fresh:
        out = [f"[{sid}] {n} {noun} - {rec.get('how', 'rolled')}, "
               f"recorded for {where}."]
    else:
        out = [f"[{sid}] {n} {noun} - recorded earlier for {where}, "
               f"so it reads the same as it did then."]
    if spec.get("reading"):
        out.append(f"  reading: {spec['reading']}")
    out += [f"  -> {line}" for line in foe_lines(spec, n)]
    if spec.get("then"):
        out.append(f"  -> {spec['then'].format(n=n)}")
    out.append(f"  the player's own number wins: bp encounter set {sid} "
               f"{spec['group']} <n>")
    return out


def apply_text(sid: str, text: str, raw: bool = False,
               partial: bool = False) -> tuple[str, list[str]]:
    """Substitute recorded band sizes into a passage. Returns (text, notes).

    `partial` says the text is a slice - a part, or the prose above a table - so a
    phrase that is simply somewhere else in the section is nothing to complain
    about. On a whole section body a missing phrase is data drift, and is said out
    loud rather than silently reading the un-rewritten sentence.
    """
    entries = specs(base_id(sid))
    if raw or not entries:
        return text, []
    try:
        g = state.load_game(argparse.Namespace(game=None), required=False)
    except state.Refuse as e:
        return text, [f"[{sid}] band sizes not substituted: {e}"]
    if g is None:
        return text, [NO_GAME.format(sid=base_id(sid))]

    notes: list[str] = []
    dirty = False
    for spec in entries:
        m = play.anchor(text, spec["find"])
        if not m:
            if not partial:
                notes.append(DRIFT.format(sid=base_id(sid), find=spec["find"]))
            continue
        rec, fresh = recall(g, base_id(sid), spec)
        dirty = dirty or fresh
        said = wording(spec, rec["n"])[0].format(n=rec["n"])
        text = text[:m.start()] + said + text[m.end():]
        notes += report(base_id(sid), spec, rec, fresh)
    if dirty:
        state.write_game(g)
    return text, notes


def apply(sec: dict, raw: bool = False) -> tuple[dict, list[str]]:
    """The same, on a section dict. The original is left alone."""
    body, notes = apply_text(sec["id"], sec["body"], raw=raw,
                             partial="#" in sec["id"])
    if body == sec["body"]:
        return sec, notes
    return {**sec, "body": body}, notes


def show_notes(notes: list[str], stream=sys.stdout) -> None:
    if notes:
        print("", file=stream)
        for n in notes:
            print(n, file=stream)


# --- commands -------------------------------------------------------------


def cmd_encounter(book, args) -> int:
    """What band sizes are on the sheet, and whether they are still in play."""
    try:
        g = state.load_game(args)
    except state.Refuse as e:
        print(e, file=sys.stderr)
        return 1
    rows = [(sid, group, rec)
            for sid, groups in sorted(state.encounters(g).items())
            for group, rec in sorted(groups.items())]
    if not rows:
        print("no creature counts recorded. They are rolled and filed the first "
              "time a section that needs one is read:\n  bp speak e052")
        return 0
    for sid, group, rec in rows:
        stale = " (stale - a new day or hex, so it will be rolled again)" \
            if state.encounter_stale(g, rec) else ""
        where = f"day {rec['day']}" + (f", hex {rec['hex']}" if rec.get("hex") else "")
        print(f"{sid} {group}: {rec['n']} {rec.get('noun', '')}  [{where}]{stale}")
        if rec.get("how"):
            print(f"  {rec['how']}")
        if rec.get("foes"):
            print("  on the sheet: " +
                  ", ".join(f"{c} x {name}" for name, c in rec["foes"].items()))
    print("\nThese are what was read out loud. bp encounter set <section> <group> "
          "<n> to correct one, bp encounter clear when the encounter is over.")
    return 0


def cmd_encounter_set(book, args) -> int:
    """The player's own roll, or a correction, replaces what bp rolled."""
    try:
        g = state.load_game(args)
        sid = base_id(args.section)
        found = [s for s in specs(sid) if s["group"] == args.group]
        known = ", ".join(s["group"] for s in specs(sid)) or "none"
        if not found:
            raise state.Refuse(f"{sid} has no creature count called "
                               f"{args.group!r}. It has: {known}")
        spec = found[0]
        if args.n < 0:
            raise state.Refuse("a band cannot have a negative number in it")
        rec = state.encounter_set(g, sid, args.group, args.n,
                                  noun=wording(spec, args.n)[1],
                                  how="set by the player",
                                  foes=roster(spec, args.n))
        state.write_game(g)
    except state.Refuse as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{sid} {args.group}: {rec['n']} {rec.get('noun', '')} "
          f"(day {rec['day']}, hex {rec.get('hex')})")
    for line in foe_lines(spec, rec["n"]):
        print(f"  -> {line}")
    print(f"Re-read the section and it will say {rec['n']}: bp speak {sid}")
    return 0


def cmd_encounter_clear(book, args) -> int:
    try:
        g = state.load_game(args)
        gone = state.encounter_clear(g, base_id(args.section) if args.section else None)
        state.write_game(g)
    except state.Refuse as e:
        print(e, file=sys.stderr)
        return 1
    what = f" for {base_id(args.section)}" if args.section else ""
    print(f"{gone} creature {state.plural(gone, 'count')}{what} cleared. The next "
          f"read of those sections rolls a fresh band.")
    return 0


def cmd_encounter_check(book, args) -> int:
    """Does every rewrite still match the extracted text? A data test, not a move."""
    bad = 0
    for sid, entries in sorted(load().items()):
        sec = book.get(sid)
        if not sec:
            print(f"{sid}: no such section", file=sys.stderr)
            bad += 1
            continue
        for spec in entries:
            m = play.anchor(sec["body"], spec["find"])
            if not m:
                print(f"{sid} {spec['group']}: the phrase {spec['find']!r} is not "
                      f"in the section text", file=sys.stderr)
                bad += 1
                continue
            try:
                sample = spec["replace"].format(n=99)
                spec.get("one", "").format(n=1)
                spec.get("then", "").format(n=99)
            except (KeyError, IndexError) as e:
                print(f"{sid} {spec['group']}: a template is not valid ({e})",
                      file=sys.stderr)
                bad += 1
                continue
            for f in spec.get("foes", []):
                head_count(f.get("count", "n"), 1)
            if lowest(spec) == 1 and not spec.get("one"):
                print(f"{sid} {spec['group']}: {describe(spec)} can roll a 1, and "
                      f"there is no \"one\" wording, so it would read "
                      f"{spec['replace'].format(n=1)!r}", file=sys.stderr)
                bad += 1
                continue
            if args.verbose:
                print(f"{sid} {spec['group']:<10} {describe(spec)}")
                print(f"  ...{sample}")
                if spec.get("one"):
                    print(f"  ...{spec['one'].format(n=1)}")
    total = sum(len(v) for v in load().values())
    print(f"{total - bad} of {total} rewrites match the extracted text."
          if bad else f"all {total} rewrites match the extracted text.")
    return 1 if bad else 0


def register(sub) -> None:
    """Add the encounter commands to bp's subparser. Called from bp.main()."""

    def game_flag(p):
        p.add_argument("--game", help="act on a save other than the current one")

    s = sub.add_parser("encounter",
                       help="the band sizes rolled for this encounter, and read out")
    game_flag(s)
    s.set_defaults(fn=cmd_encounter)
    esub = s.add_subparsers(dest="enccmd")

    e = esub.add_parser("set", help="record the player's own count for a band")
    game_flag(e)
    e.add_argument("section", help="the section it belongs to, e.g. e052")
    e.add_argument("group", help="which count: bp encounter lists them")
    e.add_argument("n", type=int, help="how many there are")
    e.set_defaults(fn=cmd_encounter_set)

    c = esub.add_parser("clear", help="the encounter is over; forget the counts")
    game_flag(c)
    c.add_argument("section", nargs="?", help="just this one (default: all)")
    c.set_defaults(fn=cmd_encounter_clear)

    k = esub.add_parser("check", help="do the rewrites still match the sections?")
    k.add_argument("-v", "--verbose", action="store_true",
                   help="print each rewrite and its dice")
    k.set_defaults(fn=cmd_encounter_check)
