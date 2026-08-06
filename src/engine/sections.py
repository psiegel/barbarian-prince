"""Which handler runs for a section id.

A section with a machine-readable table needs no handler of its own - the
generic one reads it and resolves it. A section whose outcome is written in
prose gets a registered handler. `coverage.py` (plan 07) reports the gap.
"""

import state

from .types import EndEvent

Refuse = state.Refuse

REGISTRY: dict[str, object] = {}


def section(*sids: str):
    """Register a handler for one or more section ids, or for a named flow."""

    def deco(fn):
        for sid in sids:
            key = sid.strip().lower()
            if key in REGISTRY:
                raise RuntimeError(f"two handlers registered for {key}: "
                                   f"{REGISTRY[key].__name__} and {fn.__name__}")
            REGISTRY[key] = fn
        return fn

    return deco


def registered(sid: str) -> bool:
    return sid.strip().lower() in REGISTRY


def handler(sid: str):
    """The handler for a section, or the generic reader."""
    key = sid.strip().lower()
    fn = REGISTRY.get(key)
    if fn is not None:
        return fn
    return _generic


def _generic(ctx):
    """Read the section out and stop.

    Table resolution lands in plan 02 and the prose sections in plan 07. Until
    then this is honest rather than clever: the player hears the section and the
    engine says plainly that it cannot take it further, instead of inventing an
    outcome to paper over the gap (D5).
    """
    ctx.read(ctx.sid)
    table = ctx.book.table(ctx.sid)
    if table is not None:
        ctx.note(f"{ctx.sid} has a {table.get('kind')} table that no handler "
                 f"resolves yet (plan 02).", cite=ctx.sid)
    else:
        ctx.note(f"{ctx.sid} has no handler yet (plan 07).", cite=ctx.sid)
    return EndEvent()
