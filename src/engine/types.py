"""The three things a rule handler deals in: Events, Asks, and Outcomes.

An Event is something that happened, addressed to the UI. An Ask is a
suspension point - the engine needs something only the player can supply. An
Outcome is a handler's return value, the control-flow verb that tells the
machine what to run next.

Handlers never print and never decide what happens after they return. See
docs/plans/00-overview.md, decisions D2 and D3.
"""

from dataclasses import dataclass, field

# Which channel an Event belongs on. `voice` carries the distinction the
# stdout/stderr contract used to: would a person say this sentence out loud?
# A table, a rule cite, a section id or a command is a no.
KINDS = ("prose", "rule", "roll", "state", "table", "warn", "banner")


@dataclass(frozen=True)
class Event:
    kind: str
    text: str
    voice: bool = False
    cite: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}; expected one of "
                             f"{', '.join(KINDS)}")


@dataclass(frozen=True)
class Ask:
    """A question for the player. `spec` is everything ui/parse.py needs to
    interpret an answer without a model."""

    kind: str          # die | choice | number | confirm | pick_char | hex | text
    prompt: str
    spec: dict = field(default_factory=dict)
    why: str | None = None      # rule cite; shown, never spoken


@dataclass(frozen=True)
class Invoke:
    """Yielded, not returned: run another section as a sub-flow and send its
    Outcome back. This is how a day flow runs an event and learns what it cost.

    A handler that is *finished* with control returns Goto instead; that is a
    tail call and the caller does not resume.
    """

    sid: str
    params: dict = field(default_factory=dict)


# --- control-flow verbs ---------------------------------------------------


class Outcome:
    """Base class. A handler returns exactly one of these."""


@dataclass(frozen=True)
class Goto(Outcome):
    """Tail-call another section. `mod` adjusts its next die roll (r330 is the
    only section that consumes one today); `params` carries what the calling
    section supplies, such as a bribe amount."""

    sid: str
    mod: int = 0
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Retry(Outcome):
    """Return to the caller's option list and choose again, minus the option
    just attempted (r314, r315, r317-r320)."""

    why: str = ""


@dataclass(frozen=True)
class EndEvent(Outcome):
    """This event is over. `time_cost` is r204f: an event normally consumes the
    rest of the day, but pure talk takes minutes and a fight where everything
    died leaves the party free to travel on."""

    time_cost: str = "rest_of_day"      # rest_of_day | minutes | none


@dataclass(frozen=True)
class EndDay(Outcome):
    why: str = ""


@dataclass(frozen=True)
class EscapeHex(Outcome):
    """r218a: move randomly to an adjacent hex for the rest of the day."""


@dataclass(frozen=True)
class HideHere(Outcome):
    """r218b."""


@dataclass(frozen=True)
class EnterCombat(Outcome):
    spec: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EndGame(Outcome):
    result: str          # win | loss
    why: str = ""


TERMINAL = (EndEvent, EndDay, EscapeHex, HideHere, EnterCombat, EndGame)
