"""The encounter-resolution graph, r300-r343, and the option tables that lead
into it.

Forty-four sections, eight primitives. The branch structure is in
data/graph.json; what is here is the machinery that walks it, plus explicit
handlers for the nine sections that need real logic.

Nothing in this module carries the book's words. A section is read with
`ctx.read`, and what a handler says of its own is a one-line note naming the
rule - these sections are procedure, not prose, and the player hears the
outcome rather than the section.
"""

import json
from pathlib import Path

import state

from ..refs import resolve_ref
from ..sections import section

Refuse = state.Refuse

GRAPH = json.loads(
    (Path(__file__).resolve().parents[3] / "data" / "graph.json").read_text()
)["sections"]

OPS = {
    "ge": (lambda a, b: a >= b, ">="),
    "gt": (lambda a, b: a > b, ">"),
    "lt": (lambda a, b: a < b, "<"),
    "le": (lambda a, b: a <= b, "<="),
}

STATS = {
    "wits": (lambda ctx: ctx.wits(), "wit & wiles"),
    # r303, r319, r320: living men, not mounts.
    "party_size": (lambda ctx: ctx.party_size(), "party size"),
}

# What the calling section supplied and what the next one may still need.
CARRIED = ("amount", "terms", "noun", "event")


def carried(ctx) -> dict:
    return {k: ctx.param(k) for k in CARRIED if ctx.param(k) is not None}


def expand(key: str) -> list[int]:
    """'1,2' -> [1, 2]. Table keys group equivalent rolls."""
    out = []
    for part in str(key).split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def row_for(rows: dict, total: int, cite: str):
    for key, spec in rows.items():
        if total in expand(key):
            return spec
    raise Refuse(f"{cite}: nothing on the table for a roll of {total}")


# --- the primitives -------------------------------------------------------


def run(ctx, d: dict):
    """Execute one outcome descriptor. A generator, because some branches ask."""
    do = d.get("do")

    if do == "fight":
        return enter_combat(ctx, d)

    if do == "escape":
        ctx.note("escaping to an adjacent hex", cite="r218a")
        return ctx.escape()

    if do == "hide":
        ctx.note("hiding in this hex", cite="r218b")
        return ctx.hide()

    if do == "pass":
        ctx.note("the encounter ends without a fight", cite=ctx.sid)
        return ctx.end_event(time_cost="minutes")

    if do == "battle":
        mod = d.get("mod", 0)
        if mod:
            ctx.note(f"forced to battle, {mod:+d} on the r330 roll", cite=ctx.sid)
        else:
            ctx.note("forced to battle", cite=ctx.sid)
        return ctx.goto("r330", mod=mod, **carried(ctx))

    if do == "retry":
        return ctx.retry(f"{ctx.sid} failed")

    if do == "goto":
        params = carried(ctx)
        if d.get("amount") is not None:
            params["amount"] = d["amount"]
        if d.get("terms"):
            params["terms"] = list(params.get("terms") or []) + list(d["terms"])
        return ctx.goto(d["sid"], mod=d.get("mod", 0), **params)

    if do == "join":
        return (yield from do_join(ctx, d.get("join") or {}))

    if do == "choose":
        options = d["options"]
        labels = [o["label"] for o in options]
        picked = yield ctx.choose(*labels, why=ctx.sid, prompt="Which?")
        chosen = next(o for o in options if o["label"] == picked)
        return (yield from run(ctx, chosen))

    raise Refuse(f"{ctx.sid}: data/graph.json asks for {do!r}, which is not one "
                 f"of the outcomes this module knows how to carry out")


def enter_combat(ctx, d: dict):
    """r220 setup. The foes themselves come from the encounter (plan 03); what
    the graph settles is who strikes first and who is surprised."""
    spec = {
        "initiative": d.get("initiative", "us"),
        "surprise": d.get("surprise"),
        "target": d.get("target", "party"),
        "from": ctx.sid,
        # Which event led here: creatures.json keys band sizes by it.
        "event": ctx.param("event"),
    }
    who = "your party" if spec["initiative"] == "us" else "the characters encountered"
    extra = ""
    if spec["surprise"]:
        side = "your party" if spec["surprise"] == "us" else "they"
        extra = f", {side} with surprise (r220d)"
    if spec["target"] == "player":
        extra += ", striking at the Prince personally"
    ctx.note(f"combat: {who} strike first{extra}", cite="r220")
    return ctx.combat(**spec)


def do_check(ctx, spec):
    """1d6 against a party stat. The >= and > forms are different sections and
    are not collapsed."""
    getter, label = STATS[spec["stat"]]
    test, sym = OPS[spec["op"]]
    value = getter(ctx)
    die = yield ctx.die("1d6", why=ctx.sid)
    won = test(value, die)
    ctx.note(f"{label} {value} {sym} {die}: {'pass' if won else 'fail'}",
             cite=ctx.sid)
    return (yield from run(ctx, spec["pass" if won else "fail"]))


def do_bribe(ctx, spec):
    """r321-r324. The sum is set by whoever sent us here, not by this section."""
    amount = ctx.need("amount")
    if ctx.gold() < amount:
        ctx.note(f"{amount} gold is demanded and the party has {ctx.gold()}",
                 cite=ctx.sid)
        paid = False
    else:
        paid = yield ctx.confirm(f"Pay {amount} gold?", why=ctx.sid)
    if paid:
        ctx.spend(amount, why=f"bribe ({ctx.sid})")
        return (yield from run(ctx, spec["pass"]))
    return (yield from run(ctx, spec["fail"]))


def do_inline_table(ctx, spec):
    """A table extract.py already resolved into roll -> section."""
    tab = ctx.book.tables.get(ctx.sid)
    if not tab or tab.get("kind") != "inline":
        raise Refuse(f"{ctx.sid} expects an inline table in data/tables.json "
                     f"and there is none. Re-run src/extract.py.")
    die = yield ctx.die(tab.get("die", "1d6"), why=ctx.sid)
    total = die
    if spec.get("consumes_mod") and ctx.mod:
        total = die + ctx.mod
        ctx.note(f"{die} {ctx.mod:+d} = {total}", cite=ctx.sid)

    values = [v for rolls in tab["rolls"].values() for v in rolls]
    low, high = min(values), max(values)
    if total < low or total > high:
        clamped = max(low, min(high, total))
        ctx.note(f"{total} reads as {clamped} - the table's ends are "
                 f"'{low} or less' and '{high} or more'", cite=ctx.sid)
        total = clamped

    for key, rolls in tab["rolls"].items():
        if total in rolls:
            target = tab["results"][key].get("goto")
            if not target:
                ctx.note("no section on that result; the event ends",
                         cite=ctx.sid)
                return ctx.end_event(time_cost="minutes")
            return ctx.goto(target, **carried(ctx))
    raise Refuse(f"{ctx.sid}: nothing on the table for a roll of {total}")


def do_outcome_table(ctx, spec):
    """A die table whose rows carry more than a target - a surprise, an amount,
    a term of service."""
    if spec.get("consumes_day"):
        ctx.note("this takes the rest of the day (r204f)", cite=ctx.sid)
    die = yield ctx.die(spec.get("die", "1d6"), why=ctx.sid)
    total = die + (ctx.mod if spec.get("consumes_mod") else 0)
    return (yield from run(ctx, row_for(spec["rows"], total, ctx.sid)))


def do_terminal(ctx, spec):
    """r300, r304, r307, r310, r311, r316, r325: no roll, one outcome."""
    return (yield from run(ctx, {"do": spec["shape"], **spec}))


def do_join_shape(ctx, spec):
    return (yield from do_join(ctx, spec.get("join") or {}))


# --- joining the party ----------------------------------------------------


def do_join(ctx, jspec: dict, count: int | None = None):
    """Take characters into the party on stated terms.

    Their combat skill and endurance are printed in the event that led here, so
    they are asked for rather than invented. Plan 03 wires the encounter record
    in and these questions go away.
    """
    terms = sorted(set(list(jspec.get("terms") or []) +
                       list(ctx.param("terms") or [])))
    pay = jspec.get("pay", 0)
    noun = ctx.param("noun") or "follower"

    if count is None:
        count = yield ctx.number(f"How many {noun}s join?", low=1, high=99,
                                 why=ctx.sid)
    cs = yield ctx.number("Their combat skill?", low=0, high=12, why=ctx.sid)
    # Endurance 1 or less is not a character the wound rules can describe:
    # `end` of 0 is the sentinel for "stats not filled in", so r221 would treat
    # such a follower as neither woundable nor killable. The lowest endurance
    # the booklet prints is 2.
    end = yield ctx.number("Their endurance?", low=2, high=20, why=ctx.sid)

    if pay and jspec.get("pay_starts") == "today":
        due = pay * count
        if ctx.gold() < due:
            raise Refuse(
                f"today's wages come to {due} gold ({count} at {pay} a day) and "
                f"the party has {ctx.gold()}. They will not join unpaid - go "
                f"back and hire fewer, or decline.")
        ctx.spend(due, why=f"wages on hiring ({ctx.sid})")

    starts = ctx.day() + 1 if jspec.get("pay_starts") == "tomorrow" else ctx.day()
    for i in range(count):
        ch = state.new_char(f"{noun.title()} {i + 1}" if count > 1
                            else noun.title(), "follower")
        ch.update(cs=cs, end=end, pay=pay)
        ch["pay_starts"] = starts if pay else None
        ch["terms"] = terms
        ctx.add_follower(ch)

    if pay:
        ctx.note(f"{count} at {pay} gold a day, from day {starts}", cite=ctx.sid)
    if terms:
        ctx.note("terms: " + ", ".join(terms), cite=ctx.sid)
    return ctx.end_event(time_cost="minutes")


# --- the nine that need real logic ----------------------------------------


def _escape_requiring(ctx, spec):
    """r312, r313: the whole party goes or none of it does - unless some are
    left behind."""
    req = spec["requires"]
    able = ctx.all_mounted() if req == "all_mounted" else ctx.all_flying()
    if able:
        ctx.note(f"the whole party can go ({req.replace('_', ' ')})", cite=ctx.sid)
        return ctx.escape()

    mounts = ctx.mounts() if req == "all_mounted" else [
        m for m in ctx.mounts() if m.get("winged")]
    short = len(ctx.men()) - len(mounts)
    ctx.note(f"{short} of the party cannot go", cite=ctx.sid)

    pick = yield ctx.choose("abandon them", "nobody escapes",
                            why=ctx.sid, prompt=f"Leave {short} behind?")
    if pick == "nobody escapes":
        return ctx.goto("r330", **carried(ctx))

    left = []
    for _ in range(short):
        choices = [c for c in ctx.men()
                   if c["kind"] != "player" and c not in left]
        if not choices:
            raise Refuse(
                f"{ctx.sid}: {short} must be left behind but only the Prince is "
                f"left to leave, and abandoning him ends the game. Choose "
                f"'nobody escapes' instead.")
        who = yield ctx.pick_char(among=choices, prompt="Who is left behind?",
                                  why=ctx.sid)
        left.append(ctx.find(who))
    for c in left:
        ctx.drop(c, why=f"abandoned ({ctx.sid})")
    return ctx.escape()


def _bribe_to_join(ctx, spec):
    """r331: pay the sum and they join; refuse and their mood is rolled for."""
    amount = ctx.need("amount")
    if ctx.gold() < amount:
        ctx.note(f"{amount} gold is asked and the party has {ctx.gold()}",
                 cite=ctx.sid)
        paid = False
    else:
        paid = yield ctx.confirm(f"Pay {amount} gold?", why=ctx.sid)
    if paid:
        ctx.spend(amount, why=f"to hire ({ctx.sid})")
        return (yield from do_join(ctx, spec["join"]))
    ctx.note("the sum demanded carries to whichever section the die names",
             cite=ctx.sid)
    return (yield from do_inline_table(ctx, spec))


def _r333(ctx, spec):
    """r333: they need work; today's wages are due at once, and you may take
    some rather than all."""
    take = yield ctx.confirm("Hire them?", why=ctx.sid)
    if take:
        return (yield from do_join(ctx, spec["join"]))
    return (yield from do_inline_table(ctx, spec))


def _r337(ctx, spec):
    """r337: convince them, or roll for what they do instead."""
    die = yield ctx.die("1d6", why=ctx.sid)
    if ctx.wits() > die:
        ctx.note(f"wit & wiles {ctx.wits()} > {die}: they join", cite=ctx.sid)
        return (yield from do_join(ctx, spec["join"]))
    ctx.note(f"wit & wiles {ctx.wits()} not > {die}", cite=ctx.sid)
    return (yield from do_inline_table(ctx, spec))


def _r338(ctx, spec):
    """r338: the only three-way check in the graph - beating the die hires them
    cheaper than matching it."""
    die = yield ctx.die("1d6", why=ctx.sid)
    w = ctx.wits()
    if w > die:
        ctx.note(f"wit & wiles {w} > {die}: they hire on cheaply", cite=ctx.sid)
        return (yield from do_join(ctx, spec["join_gt"]))
    if w == die:
        ctx.note(f"wit & wiles {w} = {die}: they hire on", cite=ctx.sid)
        return (yield from do_join(ctx, spec["join_eq"]))
    ctx.note(f"wit & wiles {w} < {die}: they pass by", cite=ctx.sid)
    return (yield from run(ctx, {"do": "pass"}))


def _r339(ctx, spec):
    """r339: they pass by unless you stop to talk, and stopping risks their
    attitude."""
    talk = yield ctx.confirm("Stop to talk?", why=ctx.sid)
    if not talk:
        return (yield from run(ctx, {"do": "pass"}))
    die = yield ctx.die("1d6", why=ctx.sid)
    if ctx.wits() > die:
        ctx.note(f"wit & wiles {ctx.wits()} > {die}: they hire on", cite=ctx.sid)
        return (yield from do_join(ctx, spec["join"]))
    ctx.note(f"wit & wiles {ctx.wits()} not > {die}: rolling their attitude",
             cite=ctx.sid)
    return (yield from do_outcome_table(ctx, spec["decline"]))


def _r340(ctx, spec):
    """r340: let them pass, or try to take them on - and they may turn nasty."""
    pick = yield ctx.choose("let them pass", "convince them", why=ctx.sid,
                            prompt="What do you do?")
    if pick == "let them pass":
        return (yield from run(ctx, {"do": "pass"}))
    die = yield ctx.die("1d6", why=ctx.sid)
    if ctx.wits() >= die:
        ctx.note(f"wit & wiles {ctx.wits()} >= {die}: they join", cite=ctx.sid)
        return (yield from do_join(ctx, spec["join"]))
    ctx.note(f"wit & wiles {ctx.wits()} < {die}: they may turn hostile",
             cite=ctx.sid)
    return (yield from do_outcome_table(ctx, spec["hostile"]))


HANDLERS = {
    "r312": _escape_requiring,
    "r313": _escape_requiring,
    "r331": _bribe_to_join,
    "r332": _bribe_to_join,
    "r333": _r333,
    "r337": _r337,
    "r338": _r338,
    "r339": _r339,
    "r340": _r340,
}

SHAPES = {
    "check": do_check,
    "bribe": do_bribe,
    "inline_table": do_inline_table,
    "outcome_table": do_outcome_table,
    "join": do_join_shape,
    "fight": do_terminal,
    "escape": do_terminal,
    "hide": do_terminal,
    "pass": do_terminal,
}


@section(*GRAPH)
def graph_section(ctx):
    spec = GRAPH[ctx.sid]
    shape = spec["shape"]
    if shape == "handler":
        fn = HANDLERS.get(ctx.sid)
        if fn is None:
            raise Refuse(f"data/graph.json marks {ctx.sid} as needing a handler "
                         f"and src/engine/rules/graph.py has none")
        return (yield from fn(ctx, spec))
    fn = SHAPES.get(shape)
    if fn is None:
        raise Refuse(f"data/graph.json gives {ctx.sid} shape {shape!r}, which is "
                     f"not one this module implements")
    return (yield from fn(ctx, spec))


# --- the option tables that lead into the graph ---------------------------


def encounter_options(ctx):
    """The three-column table most encounters open with: roll the row, choose a
    column, follow the cell.

    A failed hide or talk-past returns here (r314, r315, r317-r320), so the row
    is remembered and the column already tried is not offered again.
    """
    tab = ctx.book.tables.get(ctx.sid)
    if not tab or tab.get("kind") != "options":
        raise Refuse(f"{ctx.sid} has no options table")

    again = ctx.param("_retry")
    columns = [c["name"] for c in tab["columns"]]

    if again:
        row = again["row"]
        used = list(again["used"])
        left = [c for c in columns if c not in used]
        if not left:
            raise Refuse(
                f"{ctx.sid}: every option on row {row} has been tried and "
                f"failed. The booklet does not say what happens then - rule it "
                f"by hand.")
        ctx.note(f"back to the options for row {row}; "
                 f"{', '.join(used)} already tried", cite=ctx.sid)
    else:
        ctx.read(ctx.sid)
        die = yield ctx.die(tab.get("die", "1d6"), why=ctx.sid)
        row = str(die + ctx.mod) if ctx.mod else str(die)
        if row not in tab["rows"]:
            raise Refuse(f"{ctx.sid}: no row {row} on the table")
        used, left = [], columns

    choice = yield ctx.choose(*left, why=ctx.sid, prompt="Which option?")
    cell = tab["rows"][row][choice]
    ref = resolve_ref(ctx.book, cell)

    ctx.offer(row=row, used=used, chosen=choice)

    if ref.sid is None:
        raise Refuse(
            f"{ctx.sid} row {row}, {choice}: the table cell names no section "
            f"({cell!r}). The rule is in a footnote under the table - read it "
            f"with `python3 src/bp.py show {ctx.sid} --raw` and rule it by hand.")

    params = {"event": ctx.param("event") or ctx.sid}
    if ref.amount is not None:
        params["amount"] = ref.amount
    return ctx.goto(ref.sid, **params)
