"""Turning what the player typed into what the engine asked for.

No model is involved and none is needed: `Ask.spec` says exactly what the valid
answers are. A "3" answered to a 1d6 question is a 3; a "7" is an error, not a
6. Nothing here coerces silently - an answer the rules cannot accept comes back
as a message saying what would be accepted instead.
"""

import re
from dataclasses import dataclass

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

YES = {"y", "yes", "yeah", "yep", "ok", "okay", "sure", "aye", "do it", "true"}
NO = {"n", "no", "nope", "nah", "don't", "dont", "never", "false"}

HEX_RE = re.compile(r"^\d{4}$")
# "roll 3", "d3", "3." - the noise around a number that a person types.
NOISE_RE = re.compile(r"^(?:i\s+)?(?:roll(?:ed)?|rolls?|got|a|an|the)\s+", re.I)


@dataclass
class Result:
    ok: bool
    value: object = None
    error: str = ""


def ok(v) -> Result:
    return Result(True, v)


def bad(msg: str) -> Result:
    return Result(False, None, msg)


def as_int(word: str) -> int | None:
    w = word.strip().lower().lstrip("+")
    if w in WORDS:
        return WORDS[w]
    if w.startswith("d") and w[1:].isdigit():      # "d4" for a d6 result of 4
        w = w[1:]
    try:
        return int(w)
    except ValueError:
        return None


def clean(text: str) -> str:
    t = " ".join(str(text).strip().split())
    t = NOISE_RE.sub("", t)
    return t.rstrip(".!").strip()


def parse(ask, text: str) -> Result:
    fn = _PARSERS.get(ask.kind)
    if fn is None:
        return ok(clean(text))
    return fn(ask, clean(text))


# --- per-kind -------------------------------------------------------------


def _die(ask, t: str) -> Result:
    spec = ask.spec
    n, sides = spec.get("n", 1), spec.get("sides", 6)
    low, high = spec.get("min", n), spec.get("max", n * sides)
    if not t:
        return bad(f"give the result of {spec.get('die', f'{n}d{sides}')}")

    parts = [p for p in re.split(r"[\s,+]+", t) if p]
    nums = [as_int(p) for p in parts]
    if any(v is None for v in nums):
        return bad(f"{t!r} is not a number. Roll "
                   f"{spec.get('die', f'{n}d{sides}')} and type the result.")

    if len(nums) == 1:
        total = nums[0]
        if n > 1 and 1 <= total <= sides and total < low:
            # "3" for 2d6 is below the minimum of 2 only when it is a single
            # die; anything under `low` cannot be a total, so say so plainly.
            return bad(f"{total} is not a possible total for "
                       f"{spec.get('die')} ({low}-{high}). Give the total of "
                       f"both dice, or both numbers.")
        if not low <= total <= high:
            return bad(f"{total} is outside {low}-{high} for "
                       f"{spec.get('die', f'{n}d{sides}')}")
        return ok(total)

    if len(nums) != n:
        return bad(f"{spec.get('die', f'{n}d{sides}')} is {n} dice - "
                   f"give {n} numbers or their total, not {len(nums)}")
    for v in nums:
        if not 1 <= v <= sides:
            return bad(f"a d{sides} cannot roll {v}")
    return ok(sum(nums))


def _choice(ask, t: str) -> Result:
    options = list(ask.spec.get("options") or [])
    if not options:
        return bad("no options were offered")
    listing = ", ".join(options)
    if not t:
        return bad(f"choose one of: {listing}")

    low = t.lower()
    for o in options:
        if o.lower() == low:
            return ok(o)

    if low.isdigit():
        i = int(low)
        if 1 <= i <= len(options):
            return ok(options[i - 1])
        return bad(f"there are {len(options)} options: {listing}")

    hits = [o for o in options if o.lower().startswith(low)]
    if len(hits) == 1:
        return ok(hits[0])
    if len(hits) > 1:
        return bad(f"{t!r} matches {', '.join(hits)} - say which")

    hits = [o for o in options if low in o.lower()]
    if len(hits) == 1:
        return ok(hits[0])
    return bad(f"{t!r} is not one of: {listing}")


def _number(ask, t: str) -> Result:
    low = ask.spec.get("min")
    high = ask.spec.get("max")
    v = as_int(t) if t else None
    if v is None:
        return bad(f"{t!r} is not a number")
    if low is not None and v < low:
        return bad(f"{v} is below the minimum of {low}")
    if high is not None and v > high:
        return bad(f"{v} is above the maximum of {high}")
    return ok(v)


def _confirm(ask, t: str) -> Result:
    low = t.lower()
    if low in YES:
        return ok(True)
    if low in NO:
        return ok(False)
    return bad("answer yes or no")


def _pick_char(ask, t: str) -> Result:
    names = list(ask.spec.get("names") or [])
    if not names:
        return bad("there is nobody to choose")
    if not t:
        return bad(f"name one of: {', '.join(names)}")
    low = t.lower()
    if low.isdigit():
        i = int(low)
        if 1 <= i <= len(names):
            return ok(names[i - 1])
        return bad(f"there are {len(names)}: {', '.join(names)}")
    exact = [n for n in names if n.lower() == low]
    if exact:
        return ok(exact[0])
    # state.find refuses rather than picking one of two; match its behaviour.
    hits = [n for n in names if low in n.lower()]
    if len(hits) == 1:
        return ok(hits[0])
    if len(hits) > 1:
        return bad(f"{t!r} matches {', '.join(hits)} - say which")
    return bad(f"nobody called {t!r}. In the party: {', '.join(names)}")


def _hex(ask, t: str) -> Result:
    h = t.replace(" ", "")
    if not HEX_RE.match(h):
        return bad(f"{t!r} is not a hex id - they are four digits, like 1301")
    return ok(h)


_PARSERS = {
    "die": _die,
    "choice": _choice,
    "number": _number,
    "confirm": _confirm,
    "pick_char": _pick_char,
    "hex": _hex,
}


def expected(ask) -> str:
    """One line saying what would be accepted. Shown, never spoken."""
    spec = ask.spec
    if ask.kind == "die":
        return f"{spec.get('die', '1d6')} -> {spec.get('min')}-{spec.get('max')}"
    if ask.kind == "choice":
        return " / ".join(spec.get("options") or [])
    if ask.kind == "confirm":
        return "yes / no"
    if ask.kind == "pick_char":
        return " / ".join(spec.get("names") or [])
    if ask.kind == "number":
        low, high = spec.get("min"), spec.get("max")
        if low is not None and high is not None:
            return f"a number, {low}-{high}"
        if low is not None:
            return f"a number, {low} or more"
        return "a number"
    if ask.kind == "hex":
        return "a hex id, like 1301"
    return ""
