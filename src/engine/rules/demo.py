"""A throwaway flow that exercises the machine before any real rules exist.

It asks one of each shape, invokes a sub-flow, and ends. Plan 01's proving steps
run against this; plan 02 replaces it with the encounter graph. Delete it once
`day` is a real flow.
"""

from ..sections import section


@section("demo")
def demo(ctx):
    ctx.say("A stranger in a mud-spattered cloak steps into the road ahead.")
    ctx.note(f"demo flow, day {ctx.day()}, hex {ctx.hex()}", cite="demo")

    what = yield ctx.choose("talk", "fight", "flee", prompt="What do you do?")
    ctx.note(f"chose {what}")

    if what == "flee":
        die = yield ctx.die("1d6", why="r315")
        if die <= ctx.wits():
            ctx.say("A few well-chosen words, and the road is yours again.")
            return ctx.end_event(time_cost="minutes")
        ctx.say("He does not move.")

    die = yield ctx.die("2d6", why="r330")
    ctx.rolled(f"2d6 = {die}", cite="r330")

    if die >= 9:
        gold = yield ctx.number("How much gold did he carry?", low=0, high=200)
        if gold:
            ctx.gain(gold, why="demo")
        ctx.say("The stranger thinks better of it and is gone.")
        return ctx.end_event()

    hurt = yield ctx.confirm("Did anyone take a wound?")
    if hurt:
        who = yield ctx.pick_char(prompt="Who?")
        ctx.wound(who, 1, why="demo")

    outcome = yield ctx.invoke("demo-tail")
    ctx.note(f"the sub-flow returned {type(outcome).__name__}")
    return ctx.end_event()


@section("demo-tail")
def demo_tail(ctx):
    """Proves Invoke: a sub-flow runs and hands its Outcome back."""
    die = yield ctx.die("1d6", why="r218a")
    ctx.note(f"direction die {die}", cite="r218a")
    ctx.say("The road bends away, and the day is spent walking it.")
    return ctx.end_event()
