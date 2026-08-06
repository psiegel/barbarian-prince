"""Reading a table cell as a reference: where it sends you, and with what.

An option cell is terse and the extraction is imperfect. `bribe (5) r322` is a
target plus a parameter; `hider318` lost its space to pdftotext; `**audience`
carries no target at all because the footnote holds the rule. This turns all of
them into the same small record, and says so plainly when it cannot.

Amounts travel with the reference because the r3xx sections are written with the
sum left blank - r322 is "pay the amount indicated", and the amount is indicated
by whoever sent you there.
"""

import re

import state

Refuse = state.Refuse

# A section id. No word boundary in front: pdftotext eats the space in front of
# some cells ("hider318", "passr325") and the id is still an id. No space between
# the letter and the digits either - every reference in the book is glued, and
# allowing a gap would read "bribe 100 gold" as a jump to e100.
ID_RE = re.compile(r"([re])(\d{3})(?!\d)")
AMOUNT_RE = re.compile(r"\((\d+)\)")
# Footnote markers sit in front of the verb and mean "there is a note below".
MARKER_RE = re.compile(r"^[*†+‡]+")


class Ref:
    """Where a cell sends you. `sid` is None when the cell names no section."""

    __slots__ = ("sid", "amount", "verb", "marker", "raw")

    def __init__(self, sid, amount, verb, marker, raw):
        self.sid = sid
        self.amount = amount
        self.verb = verb
        self.marker = marker
        self.raw = raw

    def __repr__(self):
        bits = [repr(self.sid)]
        if self.amount is not None:
            bits.append(f"amount={self.amount}")
        if self.marker:
            bits.append(f"marker={self.marker!r}")
        return f"Ref({', '.join(bits)})"

    def __eq__(self, other):
        return (isinstance(other, Ref) and self.sid == other.sid
                and self.amount == other.amount)


def parse_ref(cell: str) -> Ref:
    """Read one option cell. Raises Refuse when it names two sections - that is
    an extraction fault, and guessing which one was meant is how a player ends up
    in the wrong fight."""
    raw = " ".join(str(cell or "").split())
    m = MARKER_RE.match(raw)
    marker = m.group(0) if m else ""
    body = raw[len(marker):].strip()

    hits = ID_RE.findall(body)
    if len(hits) > 1:
        found = ", ".join(f"{a}{b}" for a, b in hits)
        raise Refuse(f"the cell {raw!r} names more than one section ({found}). "
                     f"Fix the extraction or add an errata entry; do not guess.")

    sid = f"{hits[0][0]}{hits[0][1]}" if hits else None
    amount = AMOUNT_RE.search(body)
    verb = ID_RE.sub("", body)
    verb = AMOUNT_RE.sub("", verb)
    verb = " ".join(verb.replace("-", " ").split()).strip().lower()

    return Ref(sid, int(amount.group(1)) if amount else None, verb, marker, raw)


def resolve_ref(book, cell: str) -> Ref:
    """parse_ref, then apply errata. `hide r119` is a typo for r319 and the
    errata layer is the only place that knows it."""
    ref = parse_ref(cell)
    if ref.sid:
        ref.sid, _ = book.resolve(ref.sid)
    return ref


def option_cells(tables: dict):
    """Every cell of every options table: (sid, row, column, text)."""
    for sid, tab in sorted(tables.items()):
        if tab.get("kind") != "options":
            continue
        for row, cols in tab.get("rows", {}).items():
            for col, text in cols.items():
                yield sid, row, col, text


def sweep(book) -> dict:
    """Parse every option cell. Returns what parsed, what named no section, and
    what failed - the plan 02 gate."""
    ok, targetless, failed = [], [], []
    for sid, row, col, text in option_cells(book.tables):
        try:
            ref = resolve_ref(book, text)
        except Refuse as e:
            failed.append((sid, row, col, text, str(e)))
            continue
        (ok if ref.sid else targetless).append((sid, row, col, text, ref))
    return {"ok": ok, "targetless": targetless, "failed": failed}
