#!/usr/bin/env python3
"""The deterministic client. Plays the game in code; no model is involved.

    play2                      resume the current save where it left off
    play2 --flow demo          start a flow from the current sheet
    play2 --auto-dice          roll for the player (headless testing, and for
                               players who would rather not roll their own)

`bp` is unaffected and still works on the same saves: it remains the reference
for looking a section up by hand.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bp                                       # noqa: E402
import state                                    # noqa: E402
from engine import Machine                      # noqa: E402
from ui import term                             # noqa: E402


def new_game(args) -> dict:
    """A bare game, for trying the engine out. Real setup - e001, r202, r225 -
    is plan 05's `new_game` flow, which walks the seven fixed steps."""
    path = state.path_for(args.new)
    if path.exists() and not args.force:
        raise state.Refuse(f"{path.relative_to(ROOT)} already exists - pass "
                           f"--force to start it over")
    if args.wits is None:
        raise state.Refuse("wit & wiles is missing: it is 1d6, a 1 counting as 2 "
                           "(r202). Roll it and pass --wits.")
    prince = state.new_char("Cal Arath", "player")
    prince.update(cs=8, end=9, wits=args.wits)   # r202
    g = {
        "version": state.VERSION,
        "name": state.slug(args.new),
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "day": 1,
        "food": args.food,
        "gold": args.gold,
        "hex": args.hex,
        "party": [prince],
        "foes": [],
        "encounters": {},
        "notes": [],
        "log": [],
    }
    state.note(g, f"game started: Cal Arath cs 8 end 9 w&w {args.wits}, "
                  f"{args.gold} gold, {args.food} food")
    state.write_game(g)
    state.set_current(g["name"])
    return g


def main() -> int:
    p = argparse.ArgumentParser(prog="play2", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--game", help="which save (default: $BP_GAME, then current)")
    p.add_argument("--flow", help="start this flow, discarding any partial day")
    p.add_argument("--auto-dice", action="store_true",
                   help="roll for the player instead of asking")
    p.add_argument("--quiet", action="store_true",
                   help="hide the referee's half - prose only")
    p.add_argument("--new", metavar="NAME", help="create a bare save first")
    p.add_argument("--wits", type=int, help="with --new: wit & wiles (r202)")
    p.add_argument("--gold", type=int, default=0, help="with --new")
    p.add_argument("--food", type=int, default=0, help="with --new")
    p.add_argument("--hex", default=None, help="with --new: starting hex")
    p.add_argument("--force", action="store_true", help="with --new: overwrite")
    args = p.parse_args()

    try:
        if args.new:
            g = new_game(args)
            args.game = args.game or g["name"]
        g = state.load_game(args)
        book = bp.Book()
        m = Machine(g, book)
        if args.flow or not m.eng.get("cursor"):
            flow = args.flow or "demo"
            m.start(flow)
        return term.run(m, auto_dice=args.auto_dice, quiet=args.quiet)
    except state.Refuse as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
