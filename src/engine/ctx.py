"""The handler-facing API. A rule handler touches nothing else.

This is the enforcement point for decision D2: handlers may not roll dice, read
the clock, or reach outside `ctx`. Every source of randomness in the game is a
player answer, which is what makes a day's journal replayable (D1).

Asking suspends and is yielded:      die = yield ctx.die("1d6", why="r215b")
Emitting does not and is called:     ctx.say("The road forks.")
Verbs are returned:                  return ctx.goto("r330")
"""

import bp
import procedures
import state

from .types import (Ask, EndDay, EndEvent, EndGame, EnterCombat, EscapeHex,
                    Event, Goto, HideHere, Invoke, Retry)

Refuse = procedures.Refuse


class Ctx:
    def __init__(self, g: dict, book, out: list, sid: str | None = None,
                 mod: int = 0, params: dict | None = None):
        self.g = g
        self.book = book
        self._out = out        # the machine owns this list and drains it
        self.sid = sid         # the section this handler is running as
        self.mod = mod         # a die modifier the calling section supplied
        self.params = params or {}
        self.frame = None      # set by the machine; only ctx.offer touches it

    def offer(self, row, used, chosen) -> None:
        """Mark this section as somewhere a Retry can return to.

        r314, r315 and r317-r320 send the player back to the previous option
        list when the attempt fails. The die has already been rolled, so the row
        is fixed and only the column is back in play - which is what this
        records.
        """
        if self.frame is None:
            raise Refuse("ctx.offer needs a frame; it is only callable from a "
                         "running handler")
        self.frame.retry_to = {"row": row, "used": list(used), "chosen": chosen}

    def param(self, name: str, default=None):
        """What the calling section supplied - a bribe amount, a hire rate.
        Parameters travel with the reference because the r3xx sections are
        written with the amount left blank (r321-r324, r331, r332)."""
        return self.params.get(name, default)

    def need(self, name: str):
        v = self.params.get(name)
        if v is None:
            raise Refuse(f"{self.sid or 'this section'} needs a {name!r} from the "
                         f"section that referred to it, and none was supplied")
        return v

    # --- asking -----------------------------------------------------------

    def die(self, spec: str = "1d6", why: str | None = None,
            prompt: str | None = None) -> Ask:
        n, sides = parse_die(spec)
        low, high = n, n * sides
        return Ask("die", prompt or f"roll {spec}", {"die": spec, "n": n,
                   "sides": sides, "min": low, "max": high}, why)

    def choose(self, *options: str, why: str | None = None,
               prompt: str = "which?") -> Ask:
        opts = [o for o in options]
        if len(opts) < 2:
            raise Refuse(f"a choice needs at least two options, got {opts!r}")
        return Ask("choice", prompt, {"options": opts}, why)

    def number(self, prompt: str, low: int = 0, high: int | None = None,
               why: str | None = None) -> Ask:
        return Ask("number", prompt, {"min": low, "max": high}, why)

    def confirm(self, prompt: str, why: str | None = None) -> Ask:
        return Ask("confirm", prompt, {}, why)

    def pick_char(self, among: list[dict] | None = None,
                  prompt: str = "who?", why: str | None = None) -> Ask:
        who = among if among is not None else self.men()
        return Ask("pick_char", prompt, {"names": [c["name"] for c in who]}, why)

    def ask_hex(self, prompt: str = "which hex?", why: str | None = None) -> Ask:
        return Ask("hex", prompt, {}, why)

    def invoke(self, target: str, **params) -> Invoke:
        """Run a section or flow as a sub-flow and get its Outcome back.

        The parameter is `target`, not `sid`, so that a caller can pass a `sid`
        of its own through to the flow being invoked - which is exactly what
        running a travel event does: `ctx.invoke("encounter", sid=ref)`.
        """
        return Invoke(target, params)

    # --- emitting ---------------------------------------------------------

    def say(self, text: str, cite: str | None = None) -> None:
        """Prose. Heard."""
        self._out.append(Event("prose", text, voice=True, cite=cite))

    def note(self, text: str, cite: str | None = None) -> None:
        """The referee's half: ids, cites, procedure. Shown, never spoken."""
        self._out.append(Event("rule", text, voice=False, cite=cite))

    def warn(self, text: str) -> None:
        self._out.append(Event("warn", text, voice=False))

    def rolled(self, text: str, cite: str | None = None) -> None:
        self._out.append(Event("roll", text, voice=False, cite=cite))

    def read(self, sid: str) -> None:
        """Read a section out. The only way the book's words reach the player -
        handlers never carry game text of their own."""
        try:
            sec, errata = bp.fetch(self.book, sid)
        except LookupError as e:
            raise Refuse(str(e)) from None
        if errata:
            self.note(errata, cite=sid)
        text = bp.to_prose(sec, self.book.table(sec["id"]))
        self._out.append(Event("prose", text, voice=True, cite=sec["id"]))

    # --- reading state ----------------------------------------------------

    def day(self) -> int:
        return self.g["day"]

    def hex(self) -> str | None:
        return self.g.get("hex")

    def gold(self) -> int:
        return self.g["gold"]

    def food(self) -> int:
        return self.g["food"]

    def wits(self) -> int:
        w = state.wits(self.g)
        if w is None:
            raise Refuse("the party has no player character, so there is no "
                         "wit & wiles to check against (r202)")
        return w

    def party(self) -> list[dict]:
        return state.living(self.g)

    def men(self) -> list[dict]:
        return state.men(self.g)

    def mounts(self) -> list[dict]:
        return state.mounts(self.g)

    def party_size(self) -> int:
        """r303, r319, r320: living men, not mounts."""
        return len(state.men(self.g))

    def player(self) -> dict | None:
        return state.player(self.g)

    def all_mounted(self) -> bool:
        """r204a: the party moves on foot unless there is a mount for every man."""
        return bool(self.men()) and len(self.mounts()) >= len(self.men())

    def all_flying(self) -> bool:
        winged = [m for m in self.mounts() if m.get("winged")]
        return bool(self.men()) and len(winged) >= len(self.men())

    def has_guide(self) -> bool:
        return any(c.get("guide") for c in self.men())

    def find(self, name: str) -> dict:
        return state.find(self.party(), name)

    # --- mutating ---------------------------------------------------------
    #
    # Each writes the sheet, files a log line, and emits a state Event. Nothing
    # parses those Events back: the UI reports what the sheet says, not what a
    # sentence said, so rewording one cannot turn a report into a lie.

    def gain(self, n: int, why: str = "") -> None:
        if n < 0:
            raise Refuse(f"gain takes a positive number, got {n}")
        self.g["gold"] += n
        self._changed(f"gold +{n} ({why})" if why else f"gold +{n}",
                      f"{n} gold gained. Purse: {self.g['gold']}.")

    def spend(self, n: int, why: str = "") -> None:
        if n < 0:
            raise Refuse(f"spend takes a positive number, got {n}")
        if n > self.g["gold"]:
            raise Refuse(f"that costs {n} gold and the party has "
                         f"{self.g['gold']} - short by {n - self.g['gold']}")
        self.g["gold"] -= n
        self._changed(f"gold -{n} ({why})" if why else f"gold -{n}",
                      f"{n} gold spent. Purse: {self.g['gold']}.")

    def food_change(self, n: int, why: str = "") -> None:
        if self.g["food"] + n < 0:
            raise Refuse(f"that needs {-n} food units and the party has "
                         f"{self.g['food']}")
        self.g["food"] += n
        self._changed(f"food {n:+d} ({why})" if why else f"food {n:+d}",
                      f"Food stores: {self.g['food']}.")

    def wound(self, who, n: int = 1, why: str = "") -> dict:
        ch = who if isinstance(who, dict) else self.find(who)
        ch["wounds"] += n
        cond = state.condition(ch)
        self._changed(f"{ch['name']} takes {n} wound{'s' if n != 1 else ''}"
                      f"{' (' + why + ')' if why else ''}",
                      f"{ch['name']}: {ch['wounds']} wounds ({cond}).")
        return ch

    def heal(self, who, n: int = 1, why: str = "") -> dict:
        ch = who if isinstance(who, dict) else self.find(who)
        ch["wounds"] = max(0, ch["wounds"] - n)
        self._changed(f"{ch['name']} heals {n}"
                      f"{' (' + why + ')' if why else ''}",
                      f"{ch['name']}: {ch['wounds']} wounds.")
        return ch

    def add_follower(self, ch: dict) -> dict:
        self.g["party"].append(ch)
        self._changed(f"{ch['name']} joins the party",
                      f"{ch['name']} joins the party.")
        return ch

    def drop(self, who, why: str = "") -> dict:
        ch = who if isinstance(who, dict) else self.find(who)
        self.g["party"].remove(ch)
        self._changed(f"{ch['name']} leaves the party"
                      f"{' (' + why + ')' if why else ''}",
                      f"{ch['name']} is no longer with the party.")
        return ch

    def move_to(self, hid: str, why: str = "") -> None:
        self.g["hex"] = hid
        self._changed(f"moved to {hid}{' (' + why + ')' if why else ''}",
                      f"The party is now in {hid}.")

    def _changed(self, log_line: str, shown: str) -> None:
        state.note(self.g, log_line)
        self._out.append(Event("state", shown, voice=False))

    # --- verbs ------------------------------------------------------------

    def goto(self, sid: str, mod: int = 0, **params) -> Goto:
        return Goto(sid, mod, params)

    def retry(self, why: str = "") -> Retry:
        return Retry(why)

    def end_event(self, time_cost: str = "rest_of_day") -> EndEvent:
        return EndEvent(time_cost)

    def end_day(self, why: str = "") -> EndDay:
        return EndDay(why)

    def escape(self) -> EscapeHex:
        return EscapeHex()

    def hide(self) -> HideHere:
        return HideHere()

    def combat(self, **spec) -> EnterCombat:
        return EnterCombat(spec)

    def end_game(self, result: str, why: str = "") -> EndGame:
        return EndGame(result, why)

    def refuse(self, why: str) -> None:
        raise Refuse(why)


def parse_die(spec: str) -> tuple[int, int]:
    """'2d6' -> (2, 6). Handlers say what to roll; nothing here rolls it."""
    s = str(spec).strip().lower().replace(" ", "")
    if "d" not in s:
        raise Refuse(f"{spec!r} is not a die specification, e.g. '1d6' or '2d6'")
    n, _, sides = s.partition("d")
    try:
        n = int(n or 1)
        sides = int(sides)
    except ValueError:
        raise Refuse(f"{spec!r} is not a die specification, e.g. '1d6'") from None
    if n < 1 or sides < 2:
        raise Refuse(f"{spec!r} is not a rollable die")
    return n, sides
