"""The driver: runs rule handlers, suspends at player decisions, and survives
being killed between them.

Decision D1 - the day is the unit of durability. The save holds a complete sheet
as of dawn (`engine.day_start`) plus a `journal` of every player answer since.
Resuming restores dawn and replays the journal with all Events suppressed, which
lands back on the same question with the same state. Committing a day writes a
fresh day_start and clears the journal.

Undo falls out of the same mechanism: drop the last answer and replay.
"""

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import procedures
import state

from . import sections
from .ctx import Ctx
from .types import (Ask, EndDay, EndEvent, Event, Goto, Invoke, Outcome,
                    Retry, validate)

Refuse = procedures.Refuse

RULES_DIR = Path(__file__).resolve().parent / "rules"
MAX_STEPS = 10_000       # a handler chain that never asks is a loop, not a game


@dataclass
class Turn:
    events: list = field(default_factory=list)
    ask: Ask | None = None
    done: bool = False
    result: Outcome | None = None


class Frame:
    """One running handler. `retry_to` is what a Retry unwinds to - the option
    list a failed hide returns the player to. Plan 02 fills it in."""

    __slots__ = ("sid", "gen", "ctx", "mod", "params", "retry_to", "spent")

    def __init__(self, sid, gen, ctx, mod, params):
        self.sid = sid
        self.gen = gen
        self.ctx = ctx
        self.mod = mod
        self.params = params
        self.retry_to = None
        # A frame whose handler has returned but which is kept on the stack so
        # a Retry can come back to it. Its generator is finished; only its sid,
        # params and retry_to are still wanted.
        self.spent = False

    def __repr__(self):
        return f"<Frame {self.sid}>"


def rules_fingerprint() -> str:
    """A hash of the rule sources. A journal recorded against different rules
    cannot be trusted to replay to the same place, so resume says so."""
    h = hashlib.sha256()
    if RULES_DIR.is_dir():
        for p in sorted(RULES_DIR.rglob("*.py")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def sheet_hash(g: dict) -> str:
    """A fingerprint of the sheet, so an edit made outside the engine shows up."""
    return hashlib.sha256(
        json.dumps(snapshot(g), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def snapshot(g: dict) -> dict:
    """The sheet, without the engine's own bookkeeping."""
    return copy.deepcopy({k: v for k, v in g.items() if k != "engine"})


def restore(g: dict, snap: dict) -> None:
    """Put the sheet back to a snapshot, in place, keeping the engine block."""
    eng = g.get("engine")
    g.clear()
    g.update(copy.deepcopy(snap))
    if eng is not None:
        g["engine"] = eng


class Machine:
    def __init__(self, g: dict, book, autosave: bool = True):
        self.g = g
        self.book = book
        # The path explorer in tests/test_graph.py runs tens of thousands of
        # short games; writing a save file for each would make an exhaustive
        # sweep too slow to keep in the fast suite.
        self.autosave = autosave
        self.out: list[Event] = []
        self.stack: list[Frame] = []
        self.ask: Ask | None = None
        self.result: Outcome | None = None
        self.replaying = False
        # Every section entered, in order. The graph has real cycles in it
        # (r337 -> r342 -> r337), so a caller that walks paths needs to see a
        # repeat coming rather than follow it forever.
        self.trace: list[str] = []
        eng = g.setdefault("engine", {})
        eng.setdefault("journal", [])
        eng.setdefault("cursor", None)
        eng.setdefault("fingerprint", rules_fingerprint())
        # An empty journal means nothing has happened since dawn, so whatever
        # the sheet says now *is* dawn. Refreshing here means using `bp` on a
        # save between days cannot leave day_start stale.
        if "day_start" not in eng or not eng["journal"]:
            eng["day_start"] = snapshot(g)
            eng.pop("sheet_hash", None)
        self.edited_outside = self._edited_outside()

    def _edited_outside(self) -> bool:
        """Did something change this save since the engine last wrote it?

        Mid-day, the sheet on disk is a *derived* value: `resume` rebuilds it
        from dawn plus the journal, so an edit made with `bp` in the middle of a
        day is silently thrown away. That is the right behaviour - the journal
        is the truth - but it must not be quiet, or a player who ran
        `bp gold +50` watches it evaporate with no idea why.
        """
        was = self.eng.get("sheet_hash")
        return bool(was) and was != sheet_hash(self.g)

    # --- lifecycle --------------------------------------------------------

    @property
    def eng(self) -> dict:
        return self.g["engine"]

    def start(self, flow: str, **params) -> Turn:
        """Begin a flow from the current sheet, discarding any partial day."""
        self.eng["cursor"] = {"flow": flow, "params": params}
        self.eng["journal"] = []
        self.eng["day_start"] = snapshot(self.g)
        turn = self.resume()
        self.persist()
        return turn

    def resume(self) -> Turn:
        """Replay the day's journal and stop at the first unanswered question."""
        cur = self.eng.get("cursor")
        if not cur:
            raise Refuse("this save has no flow in progress. Start one with "
                         "`play2 --flow <name>`.")
        journal = self.eng["journal"]
        fp = rules_fingerprint()
        stale = bool(journal) and self.eng.get("fingerprint") != fp

        restore(self.g, self.eng["day_start"])
        self.out = []
        self.stack = []
        self.result = None
        self.ask = None
        self.trace = []

        self._push(cur["flow"], params=cur.get("params") or {})
        self.replaying = True
        try:
            # Each segment clears the outbox first, so what survives the loop is
            # the events since the last answered question - the context for the
            # question the player is about to face, and nothing they have already
            # seen.
            ask = self._segment(None)
            for i, entry in enumerate(journal):
                if ask is None:
                    raise Refuse(
                        f"the day's journal has {len(journal)} answers but the "
                        f"rules ran out of questions after {i}. The partial day "
                        f"cannot be replayed; `undo` back to a good point or "
                        f"start the day over.")
                if entry.get("kind") != ask.kind:
                    raise Refuse(
                        f"journal entry {i} answers a {entry.get('kind')!r} "
                        f"question but the rules now ask a {ask.kind!r} one. "
                        f"The rules changed under this save.")
                ask = self._segment(entry["value"])
        finally:
            self.replaying = False

        self.ask = ask
        if self.edited_outside:
            self.edited_outside = False
            self.out.insert(0, Event(
                "warn",
                "This save was changed outside the engine since the day began. "
                "A day in progress is rebuilt from dawn plus its answers, so "
                "that edit has been discarded. Finish or undo the day first, "
                "then use `bp`.", voice=False))
        if stale:
            self.out.insert(0, Event(
                "warn",
                "The rule code changed since this day was recorded. It replayed "
                "cleanly, but if anything looks wrong, undo to dawn and play the "
                "day again.", voice=False))
        self.eng["fingerprint"] = fp
        return self._turn()

    def answer(self, value) -> Turn:
        if self.ask is None:
            raise Refuse("nothing is waiting on an answer")
        try:
            validate(self.ask, value)
        except ValueError as e:
            raise Refuse(str(e)) from None
        self.eng["journal"].append({"kind": self.ask.kind, "value": value})
        # In place: the running handlers hold a reference to this list.
        self.out.clear()
        self.ask = self._drive(value)
        self._roll_over()
        self.persist()
        return self._turn()

    def _roll_over(self) -> None:
        """A flow that ends the day commits and begins the next one.

        D1 puts the durability boundary at dawn, and this is where it is: the
        finished day becomes the new day_start, the journal is emptied, and the
        same flow starts again. Nothing above the machine has to know that a day
        ended, and a save is never left mid-boundary.
        """
        for _ in range(2):          # one roll-over per answer is enough
            if self.ask is not None or not isinstance(self.result, EndDay):
                return
            cur = self.eng.get("cursor")
            if not cur:
                return
            self.commit()
            self.result = None
            self.trace = []
            self.stack = []
            self._push(cur["flow"], params=cur.get("params") or {})
            self.ask = self._drive(None)

    def undo(self) -> Turn | None:
        """Drop the last answer and replay. Returns None at dawn."""
        if not self.eng["journal"]:
            return None
        self.eng["journal"].pop()
        turn = self.resume()
        self.persist()
        return turn

    def commit(self) -> None:
        """The day is over: this sheet becomes the new dawn (D1)."""
        self.eng["day_start"] = snapshot(self.g)
        self.eng["journal"] = []
        self.persist()

    def persist(self) -> None:
        self.eng["sheet_hash"] = sheet_hash(self.g)
        if self.autosave:
            state.write_game(self.g)

    def current(self) -> Turn:
        """Where things stand, without advancing anything."""
        return self._turn()

    def _turn(self) -> Turn:
        return Turn(list(self.out), self.ask, self.ask is None, self.result)

    # --- the driver -------------------------------------------------------

    def _push(self, sid: str, params: dict | None = None, mod: int = 0) -> None:
        params = dict(params or {})
        fn = sections.handler(sid)
        ctx = Ctx(self.g, self.book, self.out, sid=sid, mod=mod, params=params)
        gen = fn(ctx)
        frame = Frame(sid, gen, ctx, mod, params)
        ctx.frame = frame          # so ctx.offer can mark it retry-able
        self.trace.append(sid)
        if not hasattr(gen, "send"):
            raise RuntimeError(
                f"the handler for {sid} ({fn.__name__}) is not a generator. A "
                f"rule handler must `yield` its questions, even if it asks none - "
                f"add `if False: yield` or make it ask something.")
        self.stack.append(frame)

    def _segment(self, send):
        """One drive during replay, discarding whatever the last one emitted."""
        self.out.clear()
        return self._drive(send)

    def _drive(self, send):
        """Run until a question, or until the stack empties."""
        steps = 0
        while self.stack:
            steps += 1
            if steps > MAX_STEPS:
                raise Refuse(
                    f"the rules ran {MAX_STEPS} steps without asking anything - "
                    f"that is a loop. Stack: "
                    f"{' -> '.join(f.sid for f in self.stack)}")
            frame = self.stack[-1]
            try:
                y = frame.gen.send(send)
            except StopIteration as stop:
                send = self._returned(frame, stop.value)
                if not self.stack:
                    return None
                continue
            send = None
            if isinstance(y, Ask):
                return y
            if isinstance(y, Invoke):
                self._push(y.sid, y.params)
                continue
            raise RuntimeError(
                f"{frame.sid} yielded {y!r}. A handler may only yield an Ask "
                f"(a question) or an Invoke (a sub-flow); everything else is "
                f"either called (ctx.say) or returned (ctx.goto).")
        return None

    def _returned(self, frame: Frame, out):
        """Apply a handler's return value. Returns what to send into the frame
        underneath, if any."""
        if out is None:
            out = EndEvent()
        if not isinstance(out, Outcome):
            raise RuntimeError(
                f"{frame.sid} returned {out!r}. A handler must return one of the "
                f"ctx verbs - goto, retry, end_event, end_day, escape, hide, "
                f"combat, end_game.")

        if isinstance(out, Goto):
            # A tail call: this handler is finished and the next takes its
            # place. A frame that offered options is kept anyway, marked spent,
            # so a failed hide four sections later can come back to it.
            frame.spent = True
            if frame.retry_to is None:
                self.stack.pop()
            self._push(out.sid, out.params, mod=out.mod)
            return None

        if isinstance(out, Retry):
            self._retry(out)
            return None

        # Terminal for this frame: hand the outcome back to its invoker, popping
        # through any spent frames on the way - their handlers have returned and
        # cannot be sent anything.
        self.stack.pop()
        while self.stack and self.stack[-1].spent:
            self.stack.pop()
        if not self.stack:
            self.result = out
            return None
        return out

    def _retry(self, out: Retry) -> None:
        """r314/r315/r317-r320: back to the caller's options, minus the one just
        tried. The failing handler's generator is finished, so the frame it
        returns to is re-entered rather than resumed - which is why the option
        frame records enough to start again (the row, and what has been used)."""
        self.stack.pop()
        while self.stack and self.stack[-1].retry_to is None:
            self.stack.pop()
        if not self.stack:
            raise Refuse(
                f"a section asked to return to the previous option list "
                f"({out.why or 'the attempt failed'}), but nothing on the way "
                f"here offered one. Rule this one by hand.")
        frame = self.stack.pop()
        offered = dict(frame.retry_to)
        used = list(offered.get("used") or [])
        chosen = offered.get("chosen")
        if chosen and chosen not in used:
            used.append(chosen)
        params = dict(frame.params)
        params["_retry"] = {"row": offered["row"], "used": used}
        self._push(frame.sid, params, mod=frame.mod)
