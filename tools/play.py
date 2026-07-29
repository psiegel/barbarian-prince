"""Procedural commands: the sequences the booklet describes in prose.

`show` and `travel` answer "what does this section say". The commands here answer
"what happens next, in what order" - the part that otherwise gets reassembled from
r203-r205 by hand every turn, which is where wrong rulings come from.

Nothing here invents game content. The order and the thresholds come from
data/procedures.json and data/travel.json; every step cites the section it is
carrying out, so a ruling can be checked against the printed page.
"""

import re
import sys

# Spellings a player or a narrator will reach for that are not the table's own
# row names. "cross river" in particular has a space in it, so an unquoted
# `bp travel cross river 2` would fail and push the caller back on memory.
TERRAIN_ALIASES = {
    "river": "cross river",
    "cross-river": "cross river",
    "crossriver": "cross river",
    "crossing": "cross river",
    "ford": "cross river",
    "road": "on road",
    "on-road": "on road",
    "onroad": "on road",
    "roads": "on road",
    "air": "airborne",
    "fly": "airborne",
    "flying": "airborne",
    "winged": "airborne",
    "raft": "rafting",
    "rafting": "rafting",
    "farm": "farmland",
    "fields": "farmland",
    "country": "countryside",
    "hill": "hills",
    "mountain": "mountains",
    "mtn": "mountains",
    "mtns": "mountains",
    "marsh": "swamp",
    "woods": "forest",
    "wood": "forest",
    "desert": "desert",
}


class Refuse(Exception):
    """A lookup that must fail loudly rather than guess."""


# --- hex geometry ---------------------------------------------------------
#
# Hexes are XXYY, origin top-left at 0101, flat-topped, laid out in vertical
# columns with the EVEN columns pushed down half a hex. So 0201 is south-east of
# 0101, and 0301 is north-east of 0201.
#
# This is pure arithmetic and needs no map data. It is checked against the two
# examples the booklet works through: r205d says a party in 1017 may next try
# 1016, 1117 or 1118, and names a bridge between 1318 and 1319. r205c's drift
# table (1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW) is the same six directions in the same
# order, which is where DRIFT comes from.

HEX_RE = re.compile(r"^\d{4}$")
DRIFT = {1: "N", 2: "NE", 3: "SE", 4: "S", 5: "SW", 6: "NW"}


def parse_hex(hid: str) -> tuple[int, int]:
    hid = hid.strip()
    if not HEX_RE.match(hid):
        raise Refuse(f"{hid!r} is not a hex id - they are four digits, XXYY, "
                     f"like 1017")
    return int(hid[:2]), int(hid[2:])


def fmt_hex(x: int, y: int) -> str | None:
    """None for anything off the four-digit grid, so edge hexes report an edge
    rather than a made-up id like '10098'."""
    if not (0 <= x <= 99 and 0 <= y <= 99):
        return None
    return f"{x:02d}{y:02d}"


def neighbours(x: int, y: int) -> dict[str, str]:
    """The six adjacent hexes, by compass direction."""
    if x % 2 == 1:                       # odd column sits high
        diag = {"NE": (1, -1), "SE": (1, 0), "NW": (-1, -1), "SW": (-1, 0)}
    else:                                # even column pushed down half a hex
        diag = {"NE": (1, 0), "SE": (1, 1), "NW": (-1, 0), "SW": (-1, 1)}
    out = {"N": fmt_hex(x, y - 1), "S": fmt_hex(x, y + 1)}
    for d, (dx, dy) in diag.items():
        out[d] = fmt_hex(x + dx, y + dy)
    return {d: out[d] for d in ("N", "NE", "SE", "S", "SW", "NW") if out[d]}


def direction_between(a: str, b: str) -> str | None:
    for d, h in neighbours(*parse_hex(a)).items():
        if h == b:
            return d
    return None


OPPOSITE_EDGE = {"N": "S", "S": "N", "NE": "SW", "SW": "NE", "NW": "SE", "SE": "NW"}


def hex_entry(book, hid: str) -> dict | None:
    return (book.map or {}).get("hexes", {}).get(hid)


def hexside(book, a: str, b: str) -> dict[str, bool]:
    """Is there a river or a road on the hexside between two adjacent hexes?

    A river runs along an edge and a road crosses one, so both belong to the
    boundary rather than to either hex, and the map data records each of them
    twice - once from either side. tools/extract_map.py refuses to let the two
    copies disagree, so in practice either one answers the question. Read both
    anyway: if they ever do disagree, a river check that silently does not
    happen is a rule quietly not applied for the rest of the game, and the only
    safe answer is to stop and ask.
    """
    ea, eb = hex_entry(book, a), hex_entry(book, b)
    if ea is None or eb is None:
        missing = a if ea is None else b
        raise Refuse(f"no map data for hex {missing}, so the hexside between {a} "
                     f"and {b} cannot be looked up. Ask the player, and pass "
                     f"--river/--no-river and --road/--no-road.")
    d = direction_between(a, b)
    if d is None:
        raise Refuse(f"{a} and {b} do not share a hexside.")
    back = OPPOSITE_EDGE[d]
    out: dict[str, bool] = {}
    for kind in ("river", "road"):
        mine, theirs = d in ea.get(kind, []), back in eb.get(kind, [])
        if mine != theirs:
            says_yes, says_no = (a, b) if mine else (b, a)
            raise Refuse(
                f"the map data contradicts itself about the {kind} between {a} "
                f"and {b}: {says_yes} records one on that edge and {says_no} does "
                f"not. Do not guess - ask the player what the map shows, pass "
                f"--{kind}/--no-{kind}, and fix data/map-data.csv.")
        out[kind] = mine
    return out


def hex_terrain(book, hid: str) -> str:
    """The travel-table row for a hex id. Refuses rather than assuming."""
    entry = hex_entry(book, hid)
    if entry is None:
        raise Refuse(f"no map data for hex {hid}. Give the terrain by name "
                     f"instead, or check data/map.json was built.")
    terrain = entry.get("terrain") or []
    if not terrain:
        raise Refuse(f"hex {hid} has no terrain in the map data - ask the player "
                     f"what it is.")
    if len(terrain) > 1:
        raise Refuse(f"hex {hid} straddles {' and '.join(terrain)}. Ask the player "
                     f"which applies to this move, then pass it by name.")
    return terrain[0]


def terrain_key(book, name: str) -> str:
    """Map anything a caller might type to a travel-table row name.

    A four-digit hex id resolves through the map data, so `bp move 1017 1118`
    works without the player restating terrain they can already see.
    """
    if HEX_RE.match(name.strip()):
        return hex_terrain(book, name.strip())
    want = " ".join(name.lower().split())
    rows = book.travel["terrain"]
    if want in rows:
        return want
    alias = TERRAIN_ALIASES.get(want) or TERRAIN_ALIASES.get(want.replace(" ", "-"))
    if alias == "rafting":
        raise Refuse(
            "rafting is not a normal travel-table row: you can never get lost, and "
            "events come from r230 instead. See r213 for when rafting is allowed."
        )
    if alias and alias in rows:
        return alias
    hits = [k for k in rows if k.startswith(want)] or [k for k in rows if want in k]
    if len(hits) == 1:
        return hits[0]
    opts = ", ".join(rows)
    if len(hits) > 1:
        raise Refuse(f"{name!r} is ambiguous: {', '.join(hits)}")
    raise Refuse(f"unknown terrain {name!r}. try: {opts}, rafting")


def threshold(spec: str) -> int | None:
    """'8+' -> 8. 'never' -> None."""
    m = re.match(r"(\d+)\+?$", spec.strip())
    return int(m.group(1)) if m else None


# --- dice checks ----------------------------------------------------------


def check_lost(book, key: str, total: int, guide: bool = False) -> str:
    """r205: 2d6 against the Lost column for the terrain, guide applies -1 (r205a)."""
    t = book.travel["terrain"][key]
    thr = threshold(t["lost_on"])
    out = []
    if thr is None:
        return (f"{t['name']}: lost on {t['lost_on']} - you cannot get lost here "
                f"(r205b). No roll needed.")
    net = total - 1 if guide else total
    if guide:
        out.append(f"{total} - 1 for your guide (r205a) = {net}")
    lost = net >= thr
    out.append(f"{t['name']}: needs {thr}+ to get lost, you have {net} -> "
               f"{'LOST' if lost else 'not lost'}")
    if lost and guide:
        out.append("Your guide failed: roll 1d6, on 4+ he deserts in mortification "
                   "(r205a). Only one guide leaves, and you are lost regardless.")
    if lost:
        if key == "cross river":
            out.append("You could not find a crossing. You stay where you started and "
                       "there is NO travel event for it (r205d).")
        elif key == "airborne":
            out.append("Drift (r205c): roll 1d6. On 4+, roll 1d6 again for a direction "
                       "(1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW) and move one hex that way "
                       "before landing.")
        else:
            out.append("You are stuck in the hex you tried to leave, and cannot pick "
                       "another action. Still check for a travel event in the hex you "
                       "tried to enter, as if you had entered it (r205).")
    return "\n".join(out)


def check_event(book, key: str, total: int) -> str:
    """r204b: 2d6 against the Event column for the terrain entered."""
    t = book.travel["terrain"][key]
    thr = threshold(t["event_on"])
    happens = total >= thr
    out = [f"{t['name']}: needs {thr}+ for an event, you have {total} -> "
           f"{'EVENT' if happens else 'no event'}"]
    if happens:
        out.append(f"Roll 1d6 and read across: bp travel {key!r} <die>".replace("'", '"'))
    return "\n".join(out)


# --- r226 Treasure Table --------------------------------------------------


def treasure_rows(book) -> dict[str, list[str]]:
    """Parse r226's grid into {wealth code: [six results]}.

    The table is a matrix, not a die-roll table, so tables.json has nothing for
    it - and reading a 20-row grid by eye is exactly the sort of thing that goes
    wrong quietly.
    """
    sec = book.get("r226")
    if not sec:
        raise Refuse("r226 Treasure Table is missing from the data")
    rows: dict[str, list[str]] = {}
    for line in sec["body"].split("\n"):
        m = re.match(r"\s*(\d+|[ABC])\s{2,}(\S.*)$", line)
        if not m:
            continue
        cells = re.split(r"\s{2,}", m.group(2).strip())
        if len(cells) == 6:
            rows[m.group(1)] = cells
        elif m.group(1) == "0" and len(cells) == 1:
            rows["0"] = [cells[0]] * 6
    return rows


POSSESSION_NOTE = ("A letter means a special possession as well as the gold: roll "
                   "1d6 again and read the letter's own line for the item (r225).")


def cmd_treasure(book, args) -> int:
    try:
        rows = treasure_rows(book)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    code = args.code.upper() if args.code.upper() in "ABC" else args.code
    if code not in rows:
        codes = ", ".join(k for k in rows if k not in "ABC")
        print(f"r226 prints no line for wealth code {args.code!r}. "
              f"printed codes: {codes} (plus the A, B and C possession lines)",
              file=sys.stderr)
        return 1
    if args.roll is None:
        print(f"r226 wealth code {code} - roll 1d6:")
        for i, cell in enumerate(rows[code], 1):
            print(f"  {i} -> {cell}")
        return 0
    result = rows[code][args.roll - 1]
    print(f"r226 wealth code {code} on {args.roll}: {result}")
    letter = re.search(r"\+([ABC])\b", result)
    if letter:
        print(f"\n{POSSESSION_NOTE}")
        print(f"  bp treasure {letter.group(1)} <die>")
    ev = re.search(r"\b(e\d{3})\b", result)
    if ev:
        target, note = book.resolve(ev.group(1))
        sec = book.get(target)
        if not sec:
            print("\n" + (book.why_missing(target) or f"-> {target} (not found)"),
                  file=sys.stderr)
            return 1
        print()
        print(book.fmt_section(sec, note))
    return 0


# --- step formatting ------------------------------------------------------


def show_steps(title: str, steps: list[dict], tail: list[str] | None = None,
               first: int = 1) -> None:
    """Print an ordered procedure. One line per thing the player must do."""
    print(title)
    print("=" * len(title))
    for i, s in enumerate(steps, first):
        cite = f"  ({s['cite']})" if s.get("cite") else ""
        print(f"\n{i}. {s['what']}{cite}")
        if s.get("die"):
            print(f"   roll {s['die']}" + (f", {s['need']}" if s.get("need") else ""))
        for line in s.get("notes", []):
            print(f"   {line}")
        if s.get("resolve"):
            print(f"   -> {s['resolve']}")
    for line in tail or []:
        print(f"\n{line}")


# --- bp start -------------------------------------------------------------


def setup_steps(book) -> list[dict]:
    steps = []
    for s in book.procedures["setup"]["steps"]:
        step = {"cite": s["cite"], "what": s["prompt"], "die": s.get("die"),
                "notes": [], "resolve": s.get("resolve")}
        if s.get("read"):
            if "#" in s["read"]:
                # One passage of a section that pauses mid-page: the reading comes
                # first, and `resolve` is what to run once they have answered.
                # Passage anchors (e.g. e001#premise) are prose the AI should narrate.
                step["notes"].insert(0, f"read this passage aloud, then stop: "
                                        f"bp speak {s['read']}")
            else:
                step["resolve"] = step["resolve"] or f"bp show {s['read']}"
        if s.get("fixed"):
            step["notes"].append(f"fixed: {s['fixed']}")
        if s.get("note"):
            step["notes"].append(s["note"])
        steps.append(step)
    return steps


def cmd_start(book, args) -> int:
    setup = book.procedures["setup"]
    if args.roll is not None:
        where = setup["hexes"].get(str(args.roll))
        if not where:
            print(f"the caravan table is 1-6; got {args.roll}", file=sys.stderr)
            return 1
        print(f"e001 caravan on {args.roll}: you are in {where}")
        print("\nNow read the closing paragraph: bp show e001#dawn")
        print("Then write the sheet down before day 1: "
              "bp game new <name> --wits <w&w> --gold <gold> --hex <hex>")
        print("Then day 1 begins. Choose an action: bp day")
        return 0

    steps = setup_steps(book)
    if args.step is not None:
        if not 1 <= args.step <= len(steps):
            print(f"setup has steps 1-{len(steps)}; got {args.step}",
                  file=sys.stderr)
            return 1
        s = steps[args.step - 1]
        nxt = (f"Next: bp start --step {args.step + 1}"
               if args.step < len(steps) else "That is the last setup step.")
        show_steps(f"Setup step {args.step} of {len(steps)}", [s], [nxt],
                   first=args.step)
        return 0

    show_steps("Starting a game", steps,
               ["Take one step per message: read or ask, then wait. "
                "`bp start --step N` prints a single step.",
                f"Goal: {setup['goal']}"] +
               [f"Standing rule: {s}" for s in setup["standing"]])
    return 0


# --- bp hex ---------------------------------------------------------------


def describe_hex(book, hid: str) -> list[str]:
    entry = hex_entry(book, hid)
    if entry is None:
        return [f"{hid}: no map data"]
    terrain = " / ".join(entry.get("terrain") or []) or "unknown terrain"
    bits = [terrain]
    if entry.get("features"):
        name = f" {entry['name']}" if entry.get("name") else ""
        bits.append(", ".join(entry["features"]) + name)
    if entry.get("region"):
        bits.append(f"in {entry['region']}")
    if entry.get("corrected"):
        bits.append("[corrected, see map-fixes.json]")
    return [f"{hid}: {'; '.join(bits)}"]


def cmd_hex(book, args) -> int:
    try:
        x, y = parse_hex(args.id)
    except Refuse as e:
        print(e, file=sys.stderr)
        return 1
    if not book.map:
        print("no map data - run tools/extract_map.py", file=sys.stderr)
        return 1
    if hex_entry(book, args.id) is None:
        grid = sorted(book.map["hexes"])
        print(f"{args.id} is not on the map. It runs {grid[0]} to {grid[-1]}, "
              f"{len({h[:2] for h in grid})} columns.", file=sys.stderr)
        return 1

    if args.drift is not None:
        d = DRIFT[args.drift]
        dest = neighbours(x, y).get(d)
        print(f"r205c drift on {args.drift}: {d}, {args.id} -> {dest or 'off the edge'}")
        if dest is None or hex_entry(book, dest) is None:
            print("  That is off the mapboard. The rules do not cover drifting off "
                  "the edge - adjudicate with the player.")
            return 0
        for line in describe_hex(book, dest):
            print(f"  {line}")
        return 0

    for line in describe_hex(book, args.id):
        print(line)
    entry = hex_entry(book, args.id)
    if entry and len(entry.get("terrain") or []) > 1:
        print("  this hex straddles two terrains - ask which one applies before "
              "rolling")
    print("\nadjacent:")
    for d, h in neighbours(x, y).items():
        known = hex_entry(book, h)
        detail = describe_hex(book, h)[0].split(": ", 1)[1] if known else "off-map"
        # The hexside matters more than the hex when planning a move, so say what
        # is on it: a river is a crossing check, a road cancels the lost check and
        # bridges the river underneath it (r205b, r205d).
        on_edge = [k for k in ("river", "road") if d in (entry.get(k) or [])]
        edge = f"  [{' + '.join(on_edge)}]" if on_edge else ""
        if len(on_edge) == 2:
            edge += " bridge"
        print(f"  {d:<2} {h}  {detail}{edge}")
    print("\nRivers and roads shown above are on the hexside, so they apply to that "
          "move in either direction. bp move reads them itself.")
    return 0


# --- bp day ---------------------------------------------------------------


def cmd_day(book, args) -> int:
    day = book.procedures["day"]
    hexes = [h.lower() for h in (args.hex_type or [])]

    # If a game is being tracked, lead with its numbers: the day, the food and
    # the gold are exactly what the checks below are about, and a narrator
    # reciting yesterday's figures from memory is the failure this prevents.
    # Imported here rather than at the top because state.py subclasses Refuse
    # from this module, and importing it back at load time is a cycle.
    import state
    line = state.summary_line(args)
    if line:
        print(line + "\n")

    # A hex id stands in for its feature, so the narrator can say where the party
    # is rather than what kind of place it is.
    located = None
    for i, h in enumerate(list(hexes)):
        if HEX_RE.match(h):
            entry = hex_entry(book, h)
            if entry is None:
                print(f"no map data for hex {h}", file=sys.stderr)
                return 1
            located = h
            hexes[i:i + 1] = entry.get("features") or []
            name = f" ({entry['name']})" if entry.get("name") else ""
            terrain = " / ".join(entry.get("terrain") or []) or "unknown"
            feats = ", ".join(entry.get("features") or []) or "no feature"
            print(f"hex {h}: {terrain}, {feats}{name}\n")
    unknown = [h for h in hexes
               if not any(h in (a["hexes"] if isinstance(a["hexes"], list) else [])
                          for a in day["actions"]) and h != "wilderness"]
    where = f" - in {located}" if located else ""
    if hexes:
        where += f" - {', '.join(hexes)}" if located else f" - in a {', '.join(hexes)} hex"
    elif located:
        where += " - open terrain, no feature"
    head = "r203 Daily Actions" + where
    print(head)
    print("=" * len(head))
    print()
    for a in day["actions"]:
        allowed = a["hexes"] == "any" or (hexes and any(h in a["hexes"] for h in hexes))
        if hexes and not allowed:
            continue
        where = "" if a["hexes"] == "any" else f"  [{'/'.join(a['hexes'])} only]"
        print(f"  {a['name']:<18} {a['cite']}{where}")
        if a.get("note"):
            print(f"  {'':<18} {a['note']}")
        if a.get("resolve"):
            print(f"  {'':<18} -> {a['resolve']}")
    if not hexes:
        print("\n  (pass the hex type - town, castle, temple, ruins - to filter these)")
    print(f"\n{day['one_action_note']}")

    print("\nEnd of day, in this order:")
    for c in day["endofday"]:
        when = c.get("when")
        if when and when != "north" and hexes and not any(h in when for h in hexes):
            continue
        flag = "  (only north of the Tragoth)" if when == "north" else ""
        print(f"  - {c['what']} ({c['cite']}){flag}")
    if unknown:
        print(f"\nnote: no action is gated on {', '.join(unknown)} - "
              f"treat it as open terrain.", file=sys.stderr)
    return 0


# --- bp move --------------------------------------------------------------


def event_step(book, key: str, why: str, cite: str) -> dict:
    t = book.travel["terrain"][key]
    return {
        "what": f"Travel event check, {why} ({t['name']})",
        "cite": cite,
        "die": "2d6",
        "need": f"event on {t['event_on']}",
        "resolve": f'bp travel "{key}" --event <2d6>',
    }


def lost_step(book, key: str, why: str, cite: str, guide: bool) -> dict:
    t = book.travel["terrain"][key]
    step = {
        "what": f"Lost check, {why} ({t['name']})",
        "cite": cite,
        "die": "2d6",
        "need": f"lost on {t['lost_on']}",
        "notes": [],
        "resolve": f'bp travel "{key}" --lost <2d6>' + (" --guide" if guide else ""),
    }
    if guide:
        step["notes"].append("-1 for your guide; if you get lost anyway he may desert "
                             "(r205a)")
    return step


def plan_move(book, frm: str, to: str, args) -> tuple[list[dict], list[str]]:
    steps: list[dict] = []
    tail: list[str] = []
    proc = book.procedures["move"]
    guide = args.guide

    if args.airborne:
        steps.append(lost_step(book, "airborne", "flying out of the hex", "r205c", guide))
        steps.append(event_step(book, "airborne", "while flying", "r204d"))
        steps.append(event_step(book, to, "in the hex you land in, only if no airborne "
                                          "event occurred", "r204d"))
        tail.append("Flying crosses rivers for free: no crossing check, no river event "
                    "(r204e, r205d).")
        tail.append("The hex you land in may not be the one you aimed at, if an airborne "
                    "event moved you - the terrain check follows where you actually land "
                    "(r204d).")
        tail.append(proc["speeds"]["airborne"])
        return steps, tail

    if args.road:
        steps.append({"what": "No lost check - you are following the road (r204c, r205b)",
                      "cite": "r205b"})
        steps.append(event_step(book, "on road", "on the road", "r204c"))
        steps.append(event_step(book, to, "in the terrain you entered, only if no road "
                                          "event occurred", "r204c"))
        tail.append("If a road event did occur you need not check the terrain line at "
                    "all, though you may if you want to (r204c).")
        if args.river:
            tail.append("Crossing a river by road is automatic - the road implies a "
                        "bridge (r205d).")
        return steps, tail

    if args.river:
        steps.append(lost_step(book, "cross river", "finding a way over the river",
                               "r205d", guide))
        steps.append({"what": "If you got lost crossing: you stay put, and there is no "
                              "travel event at all. The move is over.", "cite": "r205d"})
        steps.append(event_step(book, "cross river", "crossing the river", "r204e"))
        steps.append(lost_step(book, to, "entering the far hex", "r205d", guide))
        steps.append({"what": "If you got lost entering the far hex: you count as over "
                              "the river but end back where you started, and may try "
                              "any hex on the far side tomorrow.", "cite": "r205d"})
        steps.append(event_step(book, to, "in the hex you entered", "r204b"))
        tail.append(proc["lost_terrain_conflict"])
    else:
        steps.append(lost_step(book, frm, "leaving the hex", "r205", guide))
        steps.append({"what": "If you got lost: you stay put and lose the day, but still "
                              "check the event below as if you had entered.",
                      "cite": "r205"})
        steps.append(event_step(book, to, "in the hex you entered", "r204b"))
        tail.append("Note the asymmetry: the lost check uses the terrain you LEAVE, the "
                    "event check uses the terrain you ENTER (r205, r204b).")

    tail.append(proc["after_event"])
    return steps, tail


def cmd_move(book, args) -> int:
    try:
        frm = terrain_key(book, args.frm)
        to = terrain_key(book, args.to)
    except Refuse as e:
        # Terrain can be ambiguous where the hexside is not - a straddle hex
        # still has a perfectly definite river on it. Say so, so the answer is
        # not lost when the caller comes back with the terrain named by hand.
        extra = ""
        if HEX_RE.match(args.frm.strip()) and HEX_RE.match(args.to.strip()):
            try:
                found = hexside(book, args.frm.strip(), args.to.strip())
                on = [k for k in ("river", "road") if found[k]]
                extra = ("\nThe hexside itself is not in doubt: "
                         + (" and ".join(on) + " on that edge" if on
                            else "no river and no road on that edge")
                         + ". bp move will read that itself once the terrain is "
                           "settled - re-run with the hex ids and name the terrain "
                           "only if it still refuses.")
            except Refuse:
                pass
        print(f"{e}{extra}", file=sys.stderr)
        return 1
    if args.airborne and args.road:
        print("pick one of --road or --airborne", file=sys.stderr)
        return 1

    a = book.travel["terrain"][frm]["name"]
    b = book.travel["terrain"][to]["name"]
    label_a, label_b = a, b
    direction = None
    source = {}          # what each answer came from, for the footer

    # When both ends are hex ids the geometry is checkable, so check it: a move
    # between non-adjacent hexes is a mistake worth catching before any dice.
    hexish = HEX_RE.match(args.frm.strip()) and HEX_RE.match(args.to.strip())
    if hexish:
        h1, h2 = args.frm.strip(), args.to.strip()
        label_a, label_b = f"{h1} ({a})", f"{h2} ({b})"
        direction = direction_between(h1, h2)
        if direction is None and not args.airborne:
            print(f"{h1} and {h2} are not adjacent. You can't skip or jump a hex "
                  f"(r204). Adjacent to {h1}: "
                  f"{', '.join(f'{k} {v}' for k, v in neighbours(*parse_hex(h1)).items())}",
                  file=sys.stderr)
            return 1

        # Rivers and roads are on the hexside, and the map data knows them. Only
        # look them up where they are actually knowable - between two adjacent
        # hexes - and let an explicit flag win, since the player is looking at
        # the board and this data is a transcription of it.
        if direction:
            try:
                found = hexside(book, h1, h2)
                for kind in ("river", "road"):
                    if getattr(args, kind) is None:
                        setattr(args, kind, found[kind])
                        source[kind] = "map"
                    elif getattr(args, kind) != found[kind]:
                        source[kind] = ("you overrode the map, which says "
                                        f"{'yes' if found[kind] else 'no'}")
                    else:
                        source[kind] = "you said so, and the map agrees"
            except Refuse as e:
                print(f"{e}\n", file=sys.stderr)

    for kind in ("river", "road"):
        if getattr(args, kind) is None:
            setattr(args, kind, False)

    steps, tail = plan_move(book, frm, to, args)
    how = " by road" if args.road else " flying" if args.airborne else ""
    how += ", over a river" if args.river else ""
    if direction:
        how = f" {direction}{how}"
    if args.road and args.river:
        how += " on a bridge"

    show_steps(f"Move: {label_a} -> {label_b}{how}", steps, tail)

    known = source.get("river") == "map" and source.get("road") == "map"
    print("\nThis plan rests on:" if known else "\nThis plan assumes:")
    print(f"  leaving {label_a}, entering {label_b}")
    for kind, label in (("river", "river on the hexside between them"),
                        ("road", "leaving by road")):
        got = "yes" if getattr(args, kind) else "NO"
        why = source.get(kind)
        note = (" - from the map data" if why == "map"
                else f" - {why}" if why else " - from what you were told")
        print(f"  {label}: {got}{note}")
    print(f"  flying, not short-hopping: {'yes' if args.airborne else 'NO'} "
          f"- from what you were told")
    if not known:
        print("  Anything not read from the map is a fact only the player can "
              "see, and a wrong one silently produces a plausible, wrong plan.")
    print("\nStop after each check and let the player roll. Do not read the "
          "outcomes ahead of them.")
    return 0
