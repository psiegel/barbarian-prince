"""Test scaffolding.

Every command writes to whatever save is current, so a bare test command edits
the player's live game. Everything here works on a uniquely named save, sets
BP_GAME for the duration, never touches saves/current, and deletes the file
afterwards.
"""

import contextlib
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bp                                       # noqa: E402
import state                                    # noqa: E402
from engine import Machine                      # noqa: E402

_BOOK = None


def book():
    """One Book for the whole suite - loading the data is the slow part."""
    global _BOOK
    if _BOOK is None:
        _BOOK = bp.Book()
    return _BOOK


def blank(name: str, **over) -> dict:
    prince = state.new_char("Cal Arath", "player")
    prince.update(cs=8, end=9, wits=4)
    g = {
        "version": state.VERSION,
        "name": state.slug(name),
        "created": "test",
        "day": 1,
        "food": 3,
        "gold": 10,
        "hex": "1301",
        "party": [prince],
        "foes": [],
        "encounters": {},
        "notes": [],
        "log": [],
    }
    g.update(over)
    return g


@contextlib.contextmanager
def temp_game(name: str | None = None, **over):
    """A save that exists only for the duration of one test."""
    name = state.slug(name or f"enginetest-{os.getpid()}-{id(over)}")
    was = os.environ.get("BP_GAME")
    os.environ["BP_GAME"] = name
    path = state.path_for(name)
    try:
        g = blank(name, **over)
        state.write_game(g)
        yield g
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        with contextlib.suppress(FileNotFoundError):
            path.with_suffix(".json.tmp").unlink()
        if was is None:
            os.environ.pop("BP_GAME", None)
        else:
            os.environ["BP_GAME"] = was


def reload(name: str) -> dict:
    """Read the save back off disk, as a fresh process would."""
    return state.load_game(types.SimpleNamespace(game=state.slug(name)))


def machine(g: dict) -> Machine:
    return Machine(g, book())


def play(m: Machine, answers, flow: str | None = None):
    """Drive a machine through a list of answers. Returns the last Turn."""
    turn = m.start(flow) if flow else m.resume()
    for a in answers:
        if turn.done:
            raise AssertionError(f"the flow ended before answering {a!r}")
        turn = m.answer(a)
    return turn


def sheet_of(g: dict) -> dict:
    """The comparable part of a save: everything the engine does not own, minus
    the fields that are timestamps rather than game state."""
    out = {k: v for k, v in g.items() if k not in ("engine", "created")}
    return out
