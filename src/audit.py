#!/usr/bin/env python3
"""Check that nothing the player hears is scaffolding.

The whole design rests on one split: stdout is prose a DM would say and is
printed, spoken and handed to the model; stderr is the referee's and reaches
only the model. Six separate times a command has been found printing rule
cites, section ids or `-> bp ...` follow-ons to stdout, where the synthesiser
dutifully read them out - "e002 evade on 3 attack r304" is not a sentence.

Each was found by a player hearing it. This finds them first.

    python3 src/audit.py            # both checks, exit 1 on any failure

Two checks:

  channels  every command's stdout, against the shapes that mark referee data
  prose     every section rendered through to_prose(), against table rows

Neither knows what good prose looks like - they know what data looks like, and
data on the player's channel is the bug.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BP = [sys.executable, str(ROOT / "src" / "bp.py")]

# What referee data looks like. A section id, a cross-reference arrow, a command
# to run next, an errata tag, or a die threshold: none is ever said aloud.
SCAFFOLD = [
    ("section id", re.compile(r"\b[re]\d{3}[a-f]?\b")),
    ("follow-on arrow", re.compile(r"->")),
    ("a bp command", re.compile(r"\bbp [a-z]+")),
    ("errata tag", re.compile(r"^\[errata\]|\[e\d{3}\]")),
    ("die threshold", re.compile(r"\b(?:lost on|event on) \d|\bdie \d ->")),
    ("a table column", re.compile(r"^\s*choice:|^\s*then roll ")),
]

# Enough of the surface that every printing path is exercised at least once.
COMMANDS = [
    "show e003", "show e001#premise", "show r322", "show e053", "show r220",
    "show e001 --parts", "options e003", "options e002", "options e060",
    "options e068", "options e105", "options r211", "resolve e003 evade 4",
    "resolve e002 evade 3", "resolve e068 5", "resolve r208 7", "travel",
    "travel farmland 1", "travel forest 3 5", "travel river --lost 9",
    "travel river --event 11", "treasure 2", "treasure 2 4", "treasure 5 5",
    "start", "start 3", "start --step 1", "day town", "day 0101",
    "move 1017 1118", "hex 1017", "refs r220", "list e", "search lodging",
    "encounter check", "game list", "roll 2d6",
    # A hunt in the mountains, which r215b never allows: it exercises the
    # command without moving a food unit on whatever save happens to be current.
    "hunt Cal 7 --hex 0201",
]


SCRATCH = "audit-scratch"


@contextlib.contextmanager
def scratch_save():
    """Run the commands against a save of our own, and put the desk back.

    Every one of these commands writes to whichever save is current - `options
    e002` files a band size, `move` changes the hex - so running the audit on a
    live game edits it. `bp game new` also repoints saves/current, so the pointer
    is put back by hand; leaving it aimed at a save that is about to be deleted
    is worse than where it started.
    """
    saves = ROOT / "saves"
    pointer = saves / "current"
    was = pointer.read_text() if pointer.exists() else None
    env = {**os.environ, "BP_GAME": SCRATCH}
    subprocess.run([*BP, "game", "new", SCRATCH, "--wits", "4", "--gold", "10",
                    "--hex", "0901", "--force"], capture_output=True, text=True)
    try:
        yield env
    finally:
        (saves / f"{SCRATCH}.json").unlink(missing_ok=True)
        if was is None:
            pointer.unlink(missing_ok=True)
        else:
            pointer.write_text(was)


def channels() -> int:
    bad = 0
    with scratch_save() as env:
        for cmd in COMMANDS:
            p = subprocess.run([*BP, *cmd.split()], capture_output=True,
                               text=True, env=env)
            for line in p.stdout.splitlines():
                if not line.strip():
                    continue
                for what, rx in SCAFFOLD:
                    if rx.search(line):
                        print(f"  bp {cmd}\n    {what}: {line.strip()[:70]}")
                        bad += 1
                        break
    print(f"channels: {len(COMMANDS)} commands, "
          f"{bad or 'no'} scaffolding line{'' if bad == 1 else 's'} on stdout")
    return bad


def prose() -> int:
    """No section's spoken form may contain a laid-out table row.

    The shapes that break naive stripping: rows whose die number sits on its own
    line between wrapped text (e053, e160), outcomes written inline mid-sentence
    ("then roll two dice 2-e012; 3-e012"), and r220's combat table, whose rows
    have only one column gap.

    The inline check is against the rows tables.json actually parsed, not against
    the regex that strips them. Testing the stripper with its own regex is circular
    - a row it cannot recognise going in is a row it cannot flag coming out - and
    that blind spot hid nine sections that were each speaking part of their table,
    e139 among them, for as long as this check has been running.

    Parts are rendered too, not just whole sections. A section that sends you away
    and back is read one passage at a time, so its parts are what the player
    actually hears, and the setup passage is the half that carries the run.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import bp

    tests = [("table row", bp.TABLE_LINE_RE.search),
             ("die-roll row", bp.ROW_LINE_RE.match),
             ("die-roll header", bp.DIE_HEADER_RE.match),
             ("inline outcomes", bp.INLINE_OUTCOMES_RE.search)]
    book = bp.Book()
    sections = json.loads((ROOT / "data" / "sections.json").read_text())
    bad = 0
    for sid, sec in sections.items():
        rendered = [(sid, sec)]
        for p in book.parts(sid):
            try:
                rendered.append((f"{sid}#{p['part']}", book.part(sid, p["part"])))
            except LookupError as e:
                print(f"  {sid}\n    part will not slice: {e}")
                bad += 1
        for name, piece in rendered:
            table = book.table(name)
            spoken = bp.to_prose(piece, table)
            for what, roll in surviving_rows(bp, spoken, table):
                print(f"  {name}\n    {what}: roll {roll}")
                bad += 1
            for line in spoken.split("\n"):
                if not line.strip():
                    continue
                for what, test in tests:
                    if test(line):
                        print(f"  {name}\n    {what}: {line.strip()[:70]}")
                        bad += 1
                        break
    print(f"prose:    {len(sections)} sections, "
          f"{bad or 'no'} table row{'' if bad == 1 else 's'} in spoken output")
    return bad


def surviving_rows(bp, spoken: str, table: dict | None):
    """Rows of a parsed inline table still readable in the spoken text.

    A row counts as leaked when its own roll still introduces its own words -
    "1-wealth 25". The roll alone is not enough: "1-N, 2-NE" is a direction, and
    e026, e105 and e147 all print one as prose the player needs.

    Both spellings of the word are tried, because by the time prose is spoken
    PROSE_SUBS has turned a row reading "e038" into "event 038".
    """
    for label, word in bp.outcome_rows(table):
        said = word
        for pat, rep in bp.PROSE_SUBS:
            said = re.sub(pat, rep, said)
        alts = "|".join(re.escape(w) for w in dict.fromkeys([word, said]))
        for m in label.finditer(spoken):
            if re.compile(rf"\s*(?:{alts})\b", re.I).match(spoken, m.end()):
                yield "inline outcome row", m.group(0).strip()
                break


if __name__ == "__main__":
    if not (ROOT / "data" / "sections.json").exists():
        sys.exit("data/sections.json is missing - run python3 src/extract.py")
    sys.exit(1 if channels() + prose() else 0)
