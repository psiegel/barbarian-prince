"""The deterministic front end: print what happened, ask what the rules ask,
read a line, hand it back.

No model, no network, no game logic. `Event.voice` decides how a line is shown -
prose plainly, the referee's half dimmed - and it is the same flag a speech layer
would read (plan 10).
"""

import random
import shutil
import sys
import textwrap

import state

from . import parse

META = {"/sheet", "/undo", "/log", "/quit", "/exit", "/help", "?"}


def width() -> int:
    return min(88, max(50, shutil.get_terminal_size((80, 24)).columns - 2))


def tty() -> bool:
    return sys.stdout.isatty()


def dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if tty() else s


def bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if tty() else s


def render(events, quiet: bool = False) -> None:
    w = width()
    for ev in events:
        if not ev.text:
            continue
        if ev.voice:
            for para in ev.text.split("\n\n"):
                print(textwrap.fill(para.strip(), w))
                print()
        elif not quiet:
            cite = f" [{ev.cite}]" if ev.cite else ""
            marker = "!!" if ev.kind == "warn" else "·"
            body = textwrap.fill(ev.text + cite, w - 3,
                                 initial_indent="", subsequent_indent="   ")
            print(dim(f" {marker} {body}"))
    sys.stdout.flush()


def roll(ask) -> int:
    n = ask.spec.get("n", 1)
    sides = ask.spec.get("sides", 6)
    return sum(random.randint(1, sides) for _ in range(n))


def prompt_line(ask) -> str:
    hint = parse.expected(ask)
    why = f"  ({ask.why})" if ask.why else ""
    head = f"{ask.prompt}{why}"
    return f"{bold(head)}\n {dim(hint)}\n> " if hint else f"{bold(head)}\n> "


def obtain(machine, ask, auto_dice: bool):
    """Get one valid answer, handling meta commands on the way."""
    while True:
        if auto_dice and ask.kind == "die":
            v = roll(ask)
            print(f"{ask.prompt}: {bold(str(v))} {dim('(auto)')}\n")
            return v
        try:
            raw = input(prompt_line(ask))
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        cmd = raw.strip().lower()
        if cmd in META:
            handled = meta(machine, cmd)
            if handled == "quit":
                return None
            if handled == "undone":
                return "__undone__"
            continue
        res = parse.parse(ask, raw)
        if res.ok:
            print()
            return res.value
        print(dim(f" {res.error}\n"))


def meta(machine, cmd: str) -> str:
    if cmd in ("/quit", "/exit"):
        return "quit"
    if cmd == "/sheet":
        print(state.sheet(machine.g))
        print()
        return "ok"
    if cmd == "/log":
        for entry in machine.g.get("log", [])[-20:]:
            print(dim(f"  day {entry['day']}: {entry['what']}"))
        print()
        return "ok"
    if cmd == "/undo":
        turn = machine.undo()
        if turn is None:
            print(dim("  nothing to undo - this is dawn\n"))
            return "ok"
        print(dim("  undone\n"))
        render(turn.events)
        return "undone"
    print(dim("  /sheet  /log  /undo  /quit\n"))
    return "ok"


def run(machine, auto_dice: bool = False, quiet: bool = False) -> int:
    """Drive a machine to a terminal state or until the player leaves."""
    turn = machine.resume()
    while True:
        render(turn.events, quiet)
        if turn.done:
            outcome = type(turn.result).__name__ if turn.result else "nothing"
            print(dim(f" · the flow ended: {outcome}"))
            return 0
        value = obtain(machine, turn.ask, auto_dice)
        if value is None:
            machine.persist()
            print(dim(" · saved. The day resumes where you left it."))
            return 0
        if value == "__undone__":
            # meta() already rendered the rewound segment.
            turn = machine.current()
            turn.events = []
            continue
        turn = machine.answer(value)
