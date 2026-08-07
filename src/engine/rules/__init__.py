"""Rule handlers, one module per area. Importing this package registers them.

Handlers are generator functions over `ctx` and nothing else (decision D2): no
`random`, no clock, no environment. All randomness enters as a player answer, so
a day's journal replays to the same sheet every time. `tests/test_determinism.py`
enforces it.

No module here may contain the book's text. Read a section with `ctx.read(sid)`;
never carry a sentence of it in a string or a docstring - the repository ships no
game content and `tests/test_no_game_text.py` (plan 09) checks that it stays true.
"""

from . import combat, demo, graph, travel  # noqa: F401

__all__ = ["combat", "demo", "graph", "travel"]
