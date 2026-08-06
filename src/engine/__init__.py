"""The deterministic Barbarian Prince engine.

The rules are code; the player supplies every die and every decision; the model,
if there is one, only describes what already happened. See docs/plans/ for the
architecture and the order the pieces land in.
"""

from . import rules  # noqa: F401  - importing registers the handlers
from .ctx import Ctx
from .machine import Machine, Turn, rules_fingerprint
from .sections import REGISTRY, registered, section
from .types import (Ask, EndDay, EndEvent, EndGame, EnterCombat, EscapeHex,
                    Event, Goto, HideHere, Invoke, Outcome, Retry)

__all__ = [
    "Ask", "Ctx", "EndDay", "EndEvent", "EndGame", "EnterCombat", "EscapeHex",
    "Event", "Goto", "HideHere", "Invoke", "Machine", "Outcome", "REGISTRY",
    "Retry", "Turn", "registered", "rules_fingerprint", "section",
]
