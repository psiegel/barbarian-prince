#!/usr/bin/env python3
"""Barbarian Prince reference CLI.

  bp start                 how to begin a game: the setup sequence, in order
  bp day town              the actions available today, and the end-of-day checks
  bp move 1017 1118        the ordered travel checks for one hex of movement
  bp show r203 e001        print one or more sections
  bp show e001#caravan     print one passage of a section read in sittings
  bp show e001 --parts     which passages a section has, and where each stops
  bp search "food"         full-text search
  bp travel forest         travel table row for a terrain
  bp travel forest 3       ...resolved for a die roll of 3
  bp travel forest 3 5     ...and the second die, down to the actual event
  bp travel river --lost 9 is that 2d6 a failure to cross?
  bp treasure 2 4          the r226 grid, by wealth code and die
  bp roll 2d6              roll dice
  bp refs r220             what a section links to, and what links to it
  bp list r                list section ids (optionally filtered by prefix)
  bp say                   read stdin aloud (local Kokoro, ElevenLabs, or `say`)

the character sheet - the numbers this game is played with, kept in saves/:

  bp game                  day, food, gold, party, and what state they are in
  bp game new --wits 4 --gold 30 --hex 0101   start tracking a game
  bp time +1               advance the day counter (70 days, ten weeks)
  bp food -3 / bp gold +40 spend or gain
  bp party add Lancer --cs 5 --end 5 --pay 3  a follower joins (r210)
  bp party wound Lancer +2 wounds, and what they do to him (r220c, r221)
  bp eat                   the evening meal, and who goes without (r215, r216)
  bp pay                   the day's wages to hired followers (r333)
  bp lodge                 rooms and stables for the night (r217)
  bp foe add Dwarf --cs 6 --end 7 --wealth 3  enemies, for this fight only
  bp fight auto            roll out a whole combat, round by round (r220)
  bp fight quick --us "Cal Arath" 8 9 --them Goblin 4 5 3 4   the same, no save
  bp encounter             how many of them there are, once it has been read out
"""

import argparse
import contextlib
import json
import os
import re
import random
import signal
import subprocess
import sys
import tempfile
import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import combat
import creatures
import procedures
import state

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ID_RE = re.compile(r"^[re]\d{3}$", re.IGNORECASE)

# Stock ElevenLabs voice, used when neither --voice nor ELEVENLABS_VOICE_ID is set.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


def load_dotenv() -> None:
    """Load ROOT/.env without overriding anything already in the environment."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def api_key() -> str | None:
    """Accept either spelling of the ElevenLabs key variable."""
    return os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_LABS_API_KEY")


def load(name: str, required: bool = True) -> dict | None:
    path = DATA / name
    if not path.exists():
        if not required:
            return None
        sys.exit(
            f"{path} is missing.\n\n"
            "The game data is generated from the booklets rather than shipped "
            "with this repo.\nSee the README for where to download them, then "
            "run:\n  python3 src/extract.py"
        )
    return json.loads(path.read_text())


class Book:
    def __init__(self):
        self.sections = load("sections.json")
        self.travel = load("travel.json")
        self.errata = load("errata.json")
        self.tables = load("tables.json")
        self.procedures = load("procedures.json")
        # Optional: the map is a third-party transcription, so bp works without it.
        self.map = load("map.json", required=False)

    def table(self, sid: str) -> dict | None:
        # A part is handed back carrying its parent's id with a #suffix ("e068#setup"),
        # and the table it is a passage of is the parent's. The setup part is the
        # half that holds the inline outcome run, so looking it up under the full
        # id finds nothing and the run gets read out.
        return self.tables.get(split_id(self.normalize(sid))[0])

    def normalize(self, sid: str) -> str:
        return sid.strip().lower()

    def resolve(self, sid: str) -> tuple[str, str | None]:
        """Return (canonical_id, note). Applies known errata."""
        sid = self.normalize(sid)
        typo = self.errata["typos"].get(sid)
        if typo:
            return typo["read_as"], (
                f"{sid} is a typo in the original text; reading as "
                f"{typo['read_as']}. {typo['note']}"
            )
        return sid, None

    def get(self, sid: str) -> dict | None:
        """Fetch a section, synthesising the travel-table entries that live in
        travel.json rather than as prose (r207, r231-r280)."""
        sid = self.normalize(sid)
        if sid in self.sections:
            return self.sections[sid]
        if sid in self.travel["refs"]:
            rolls = self.travel["refs"][sid]
            body = "\n".join(f"  {i}  {ev}" for i, ev in enumerate(rolls, 1))
            return {
                "id": sid,
                "title": "Travel Event Reference",
                "source": "travel",
                "body": f"Roll one die and read across:\n{body}",
                "refs": sorted(set(rolls)),
            }
        if sid == "r207":
            return {
                "id": "r207",
                "title": "Travel Table",
                "source": "travel",
                "body": self.travel_table_text(),
                "refs": [],
            }
        return None

    def why_missing(self, sid: str) -> str | None:
        """Explain an id that legitimately has no section."""
        sid = self.normalize(sid)
        miss = self.errata["missing_from_source"].get(sid)
        if miss:
            return f"{sid} ({miss['name']}) is missing from the source PDF. {miss['note']}"
        num = int(sid[1:]) if ID_RE.match(sid) else None
        if num is None:
            return None
        if sid[0] == "r" and (num == 229 or 282 <= num <= 299):
            return f"{sid}: {self.errata['nonexistent'].get('r229') or ''}".strip()
        if sid[0] == "r" and num in (223, 224):
            return f"{sid}: {self.errata['nonexistent']['r223-r224']}"
        if sid[0] == "e" and 167 <= num <= 179:
            return f"{sid}: {self.errata['nonexistent']['e167-e179']}"
        return None

    def travel_table_text(self) -> str:
        hdr = f"{'Terrain':<14}{'Lost':<7}{'Event':<7}{'die 1-6':<38}{'hunt':<7}fodder"
        lines = [hdr, "-" * len(hdr)]
        for t in self.travel["terrain"].values():
            refs = " ".join(t["event_refs"])
            lines.append(
                f"{t['name']:<14}{t['lost_on']:<7}{t['event_on']:<7}"
                f"{refs:<38}{t['hunt']:<7}{t['fodder']}"
            )
        lines.append(f"{'Rafting':<14}{'never':<7}{'10+':<7}{'see r230':<38}{'-':<7}-")
        return "\n".join(lines)

    def emit_section(self, sec: dict, note: str | None = None) -> None:
        """So procedures.py can print a section without importing this module back.

        Band sizes are substituted here too: a section is a section however the
        reader arrived at it, and an event reached through the treasure table must
        not read differently from the same event reached through `bp show`.
        """
        sec, counts = creatures.apply(sec)
        emit(sec, note, counts=counts, table=self.table(sec["id"]))

    def incoming(self, sid: str) -> list[str]:
        sid = self.normalize(sid)
        return sorted(k for k, v in self.sections.items() if sid in v["refs"])

    def subid_part(self, sid: str, text: str) -> dict | None:
        """The section's own tail paragraph, if an outcome points at it (e068a).

        `\\b[re]\\d{3}\\b` does not match 'e068a' - the trailing letter is a word
        character - so without this the follow-on search skips past the real
        answer and lands on whatever rule the paragraph happens to mention."""
        for p in self.parts(sid):
            sub = p.get("subid")
            if sub and re.search(rf"\b{re.escape(sub)}\b", text):
                return p
        return None

    def parts(self, sid: str) -> list[dict]:
        """The reading passages for a section that sends you away and back."""
        readings = self.procedures.get("readings", {})
        return [p for p in readings.get(self.normalize(sid), []) if "part" in p]

    def part(self, sid: str, name: str) -> dict:
        """One passage of a section, as a section-shaped dict."""
        sec = self.get(self.normalize(sid))
        if sec is None:
            raise LookupError(self.why_missing(sid) or f"no section {sid}")
        return slice_part(sec, self.parts(sid), name)


def slice_part(sec: dict, parts: list[dict], name: str) -> dict:
    """Cut one named passage out of a section body, refusing rather than
    guessing if an anchor no longer matches the extracted text."""
    sid = sec["id"]
    if not parts:
        raise LookupError(f"{sid} is not split into parts; use `bp show {sid}`")
    names = [p["part"] for p in parts]
    match = [p for p in parts if p["part"] == name]
    if not match:
        raise LookupError(f"{sid} has no part {name!r}. It has: {', '.join(names)}")
    p = match[0]
    body = sec["body"]

    start = 0
    if p.get("from"):
        m = procedures.anchor(body, p["from"])
        if not m:
            raise LookupError(
                f"{sid}#{name}: the opening anchor {p['from']!r} is not in the "
                f"section text any more. data/procedures.json needs fixing; "
                f"read the whole section with `bp show {sid}` meanwhile.")
        start = m.start()
    end = len(body)
    if p.get("until"):
        m = procedures.anchor(body, p["until"])
        if not m:
            raise LookupError(
                f"{sid}#{name}: the closing anchor {p['until']!r} is not in the "
                f"section text any more. data/procedures.json needs fixing; "
                f"read the whole section with `bp show {sid}` meanwhile.")
        end = m.end()

    i = names.index(name)
    return {
        "id": f"{sid}#{name}",
        "title": sec["title"],
        # Only the first passage announces the title out loud; the later ones are
        # a continuation of a section the player has already been read.
        "speech_title": sec["title"] if i == 0 else "",
        "source": sec["source"],
        "body": body[start:end].strip(),
        "refs": [],
        "part": name,
        "part_no": i + 1,
        "part_count": len(parts),
        "what": p.get("what"),
        "then": p.get("then"),
    }


def split_id(raw: str) -> tuple[str, str | None]:
    """`e001#caravan` -> ('e001', 'caravan')."""
    sid, _, part = raw.partition("#")
    return sid, (part or None)


def fmt(sec: dict, note: str | None = None) -> str:
    head = f"{sec['id']} {sec['title']}"
    if sec.get("part"):
        head += f"  [part {sec['part_no']} of {sec['part_count']}]"
    out = [head, "=" * len(head)]
    if note:
        out += [f"[errata] {note}", ""]
    if sec.get("what"):
        out += [f"({sec['what']})", ""]
    out += [sec["body"]]
    if sec.get("refs"):
        out += ["", f"-> {' '.join(sec['refs'])}"]
    if sec.get("then"):
        out += ["", f"-> then: {sec['then']}"]
    return "\n".join(out)


# --- prose ----------------------------------------------------------------
#
# There is one rendering of a section, not a printed one and a spoken one. The
# player's ear and the player's screen get the same words, so stdout carries only
# what a DM would say out loud and everything the referee needs - ids, errata,
# cross-references, band-size notes - goes to stderr, where it can never be read
# aloud by mistake. `--raw` prints the source layout instead, for adjudicating.

NUMBER_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"fifteen|twenty|twenty-five|thirty|fifty|hundred)"
)

# Some sections print their outcome table inline, mid-sentence, as a run of
# shorthand: "then roll two dice 2-e012; 3-e012; 4-e011; ...". It is a table in
# every sense - 39 sections do it, 37 of them with a parsed table behind them -
# so it comes out with the rest of the tables, leaving "then roll two dice." for
# the player and the branches for `resolve`. Items look like "4-e011",
# "5,6-nothing" and "12(or more)-r300", and wrap across lines, which is why this
# runs on a joined paragraph rather than a source line.
# The target is usually a bare id, but e068 writes "5,6-see e068a" - a lettered
# tail paragraph, reached through a word - and that still names which rolls get it.
OUTCOME_TARGET = r"(?:see\s+|read\s+)?(?:[re]\d{3}[a-f]?(?:\s+below)?|nothing|no\s+effect)"
OUTCOME_ITEM = (r"\d{1,2}(?:\s*(?:or\s+(?:less|more)|\([^)]*\)))?(?:\s*,\s*\d{1,2})*"
                rf"\s*[-–]\s*{OUTCOME_TARGET}\b")
INLINE_OUTCOMES_RE = re.compile(
    # A trailing ';' or ',' goes with the run; a '.' is the sentence's own and
    # stays, or "then roll two dice." loses its full stop.
    rf"{OUTCOME_ITEM}(?:\s*[;,]\s*{OUTCOME_ITEM})*\s*[;,]?", re.I)

# ...but that regex only knows three things that can sit right of the dash, and a
# row written as free text - "1-wealth 25", "5,6-no additional event" - is none of
# them. The run has to be contiguous to match, so an unrecognised row splits it and
# only the recognised half comes out: e139 spoke "roll one die: 1-wealth 25;
# 2-wealth 60;" and swallowed the four events after it, r331 read a resolution
# branch aloud. Nine sections were leaking half a table this way.
#
# So the run is found from the parsed table instead. tables.json has already read
# every row of these 30 sections, and the roll keys say where the run starts and
# where each item gives way to the next - no guess at what an outcome looks like.
#
# What a row records is trusted only for its first word, never matched whole. The
# recorded text is a cleaned-up reading, not a slice of the body: direction lists
# are scrubbed out of it and whitespace is collapsed, so e026's row 6 is held as
# "...roll one die to determine which direction" where the body writes "...which
# direction (1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW)". Matching it literally would find
# nothing. The first word is also the whole of what tells a run from a lookalike:
# e147 opens with a direction list whose items are contiguous and correctly
# numbered for a 2d6 table, and row 2 reading "e066" rather than "NE" rejects it.
#
# So the run's end comes from the sentence rather than from the row: the last item
# stops at the first terminator, which leaves "After you arrive there..." (e026)
# and "If they are paid and do join..." (r331) standing as the prose they are.
#
# A colon ends a run as surely as a full stop does: e068's last row is "5,6-see
# e068a in the paragraph below:", and stopping only at full stops carries the cut
# on into e068a and eats the sentence that opens it.
RUN_END_RE = re.compile(r"[.:](?=\s|$)")


def roll_pattern(key: str) -> str:
    """A table's roll key ("4,5", "12(or more)") as it is written in the prose.

    Built a character at a time because the spacing is the table's, not the
    book's: a key recorded as "1 (or less)" can be printed "1(or less)", and the
    commas in "3,4" may or may not have space around them.
    """
    out = []
    for ch in re.sub(r"\s+", " ", key.strip()):
        if ch == " ":
            out.append(r"\s*")
        elif ch == ",":
            out.append(r"\s*,\s*")
        elif ch == "(":
            out.append(r"\s*\(")
        else:
            out.append(re.escape(ch))
    body = "".join(out)
    return rf"(?<!\d){body}\s*[-–]\s*"


def outcome_rows(table: dict | None) -> list[tuple[re.Pattern, str]]:
    """Each row as (the label that introduces it, the first word of its text)."""
    if not table or table.get("kind") != "inline":
        return []
    rows = []
    for key, res in table["results"].items():
        word = re.match(r"[\w']+", res["text"].strip())
        if not word:
            return []
        rows.append((re.compile(roll_pattern(key), re.I), word.group(0)))
    return rows if len(rows) > 1 else []


def outcome_run(text: str, table: dict | None) -> tuple[int, int] | None:
    """Where this paragraph's inline outcome run starts and ends, or None.

    The span covers the terminator, and the caller puts a full stop back in its
    place: what precedes a run is the instruction that introduces it, and "roll
    one die" wants to end in a full stop whether the book wrote one there (e139)
    or a colon (e068).
    """
    rows = outcome_rows(table)
    if not rows:
        return None
    (first, first_word), rest = rows[0], rows[1:]

    def at(pos: int, word: str) -> int | None:
        m = re.compile(rf"\s*{re.escape(word)}\b", re.I).match(text, pos)
        return m.end() if m else None

    for start in first.finditer(text):
        pos = at(start.end(), first_word)
        if pos is None:
            continue  # a lookalike - e147's "2-NE" is not row 2's "e066"
        for label, word in rest:
            stop = RUN_END_RE.search(text, pos)
            stop = stop.start() if stop else len(text)
            nxt = label.search(text, pos)
            # A row that only turns up after the sentence has ended is not part of
            # this run - the same number can appear again in the prose below it.
            if not nxt or nxt.start() >= stop:
                break
            after = at(nxt.end(), word)
            if after is None:
                break
            pos = after
        end = RUN_END_RE.search(text, pos)
        return start.start(), end.end() if end else len(text)
    return None


PROSE_SUBS = [
    # "add one (+1)" / "subtract three (-3)" - the parenthetical just restates
    # the word before it, so it reads as a stutter. Amounts like "bribe (15)"
    # and hex codes like "(0101)" are not preceded by a number word, so survive.
    (rf"\b({NUMBER_WORD})\s*\([+\-—]?\d+\)", r"\1"),
    (r"\br(\d{3})([a-f])\b", r"rule \1\2"),  # subsections like r220c
    (r"\br(\d{3})\b", r"rule \1"),
    (r"\be(\d{3})\b", r"event \1"),
    (r"\(\+(\d+)\)", r"plus \1"),
    (r"\(([-—])(\d+)\)", r"minus \2"),
    (r"\bCharacter\(s\)", "Characters"),
    (r"\bcharacter\(s\)", "characters"),
    (r"\bmtd\b", "mounted"),
    (r"&", " and "),
]

# A line with two or more runs of whitespace is laid-out table data, not prose.
TABLE_LINE_RE = re.compile(r"\S[ \t]{2,}\S.*?[ \t]{2,}\S")
# ...but r220's wound rows have only one gap ("      14        Three wounds"), so
# a die-roll row leading its line counts too. This is the same shape intro_text
# cuts a table off at, plus a sign: r220's first row is "-1,3,5,8,11  One wound".
# Without this, r220 reads its combat table aloud.
ROW_LINE_RE = re.compile(r"^\s{2,}[-+]?\d{1,2}\b")
# A "die roll" column header, for the two grids whose rows are too sparsely
# spaced to look like tables (r226, r281). Checked against all 252 sections: all
# 24 lines that match are table headers, so this drops no prose anywhere.
DIE_HEADER_RE = re.compile(r"^\s*die\s+rolls?\b", re.I)


def paragraphs(body: str) -> list[str]:
    """Split a section body into spoken paragraphs, dropping laid-out tables.

    pdftotext hard-wraps prose, so naively treating newlines as breaks chops
    sentences in half: wrapped lines are rejoined with a space, and only blank
    lines end a paragraph. Table rows are dropped rather than flattened - the
    outcomes are the referee's to resolve through `options`/`resolve`, and
    reading a column of them out hands over every branch the player didn't take.
    """
    out: list[str] = []
    prose: list[tuple[bool, str]] = []
    in_table = False

    def flush():
        if prose:
            out.append(" ".join(t for _, t in prose))
            prose.clear()

    for raw in body.split("\n"):
        line = raw.strip()
        indented = raw[:2].strip() == ""
        if TABLE_LINE_RE.search(raw) or ROW_LINE_RE.match(raw) or DIE_HEADER_RE.match(raw):
            # A row's text can wrap ABOVE its die number - e160 prints the number
            # on a line of its own, after the outcome it belongs to - so indented
            # lines still pending belong to this row, not to the prose before it.
            while prose and prose[-1][0]:
                prose.pop()
            in_table = True
            flush()
        elif not line:
            flush()
        elif in_table and indented:
            # A table row's own wrapped text. e053 is the case that needs this:
            # its rows wrap over two and three lines, and its die numbers sit on
            # lines of their own between them, so dropping only the numbered line
            # leaves the outcomes behind as orphan sentences.
            flush()
        else:
            # Back out at the left margin: the table is over. e003's footnote
            # starts there, which is how the conditional escape survives.
            in_table = False
            prose.append((indented, re.sub(r"[ \t]{2,}", " ", line)))
    flush()
    return out


def polish(text: str, table: dict | None = None) -> str:
    """One paragraph, said the way a person would say it.

    Whitespace is collapsed horizontally only: the paragraph breaks around this
    are what stop the prose reading as one wall of text on screen.
    """
    # A footnote's marker is a typographic hook back to a table column that is no
    # longer printed, and "asterisk if your party has winged mounts" is not a
    # sentence. The footnote itself is a conditional escape the player needs.
    # Footnote markers: at the start of the paragraph, and again where a second
    # footnote (e007 has two) runs on after the first one's full stop.
    text = re.sub(r"^\*+\s*", "", text.lstrip())
    text = re.sub(r"(?<=[.:])\s*\*+\s*", " ", text)
    # The table first, where there is one; the regex still runs behind it, for the
    # two sections that write outcomes inline with no parsed table to read them
    # from, and for any row the table-driven pass declined to claim.
    span = outcome_run(text, table)
    if span:
        # The full stop stands in for the run's own terminator; the cleanup below
        # then folds it into whatever introduced the run - "roll one die: ." and
        # "roll one die. ." both come out as "roll one die."
        text = text[:span[0]] + "." + text[span[1]:]
    text = INLINE_OUTCOMES_RE.sub("", text)
    for pat, rep in PROSE_SUBS:
        text = re.sub(pat, rep, text)
    text = re.sub(r"[ \t]+", " ", text)
    # Cutting a run out mid-sentence leaves the punctuation stranded: "roll two
    # dice ." and "roll one die: ." both want to end at the instruction.
    text = re.sub(r"\s+([.;,:])", r"\1", text)
    text = re.sub(r":\s*\.", ".", text)
    text = re.sub(r"\.\s*(?=[.,;])", "", text)
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    return text.strip()


def prose_text(body: str, title: str = "", table: dict | None = None) -> str:
    # Each paragraph carries its own terminator, so nothing adds a stray period
    # after a line that already ends in a colon.
    chunks = ([title.rstrip(".") + "."] if title else []) + paragraphs(body)
    said = [polish(c, table) for c in chunks if c.strip()]
    return "\n\n".join(
        c if c.endswith((".", ":", "!", "?")) else c + "." for c in said if c)


def to_prose(sec: dict, table: dict | None = None) -> str:
    # A mid-section passage carries speech_title "" - it continues something the
    # player has already heard announced, so re-reading the title jars.
    # A part keeps its parent's id, so it is handed the parent's table: the run is
    # stripped if the slice contains it and nothing happens if it does not.
    return prose_text(sec["body"], sec.get("speech_title", sec["title"]), table)


def aside(sec: dict, note: str | None = None) -> str:
    """The referee's half: which section this is, and where it leads."""
    head = f"{sec['id']} {sec['title']}"
    if sec.get("part"):
        head += f"  [part {sec['part_no']} of {sec['part_count']}]"
    out = [head]
    if note:
        out.append(f"[errata] {note}")
    if sec.get("what"):
        out.append(f"({sec['what']})")
    if sec.get("refs"):
        out.append(f"-> {' '.join(sec['refs'])}")
    if sec.get("then"):
        out.append(f"-> then: {sec['then']}")
    return "\n".join(out)


def emit(sec: dict, note: str | None = None, raw: bool = False,
         counts: list[str] | None = None, table: dict | None = None) -> None:
    """Print a section: prose to stdout, everything else to stderr."""
    # stdout is block-buffered when piped, so without this the referee's lines
    # jump ahead of the prose they belong to in a captured transcript.
    sys.stdout.flush()
    if raw:
        print(fmt(sec, note))
    else:
        print(aside(sec, note), file=sys.stderr)
        sys.stderr.flush()
        print(to_prose(sec, table))
        sys.stdout.flush()
    creatures.show_notes(counts or [], sys.stderr)


# Local TTS speaks to an OpenAI-compatible /v1/audio/speech endpoint, which is
# what mlx-audio and Kokoro-FastAPI both serve. Keeping it HTTP means this file
# stays dependency-free: the model runs in its own environment, not in ours.
KOKORO_URL = "http://127.0.0.1:8000/v1/audio/speech"
KOKORO_MODEL = "mlx-community/Kokoro-82M-bf16"
KOKORO_VOICE = "bm_george"


def local_url() -> str:
    return os.environ.get("KOKORO_URL", KOKORO_URL)


def local_up(timeout: float = 1.0) -> bool:
    """Is a local speech server listening? Used to pick a backend automatically."""
    host = urllib.parse.urlparse(local_url())
    try:
        with socket.create_connection(
            (host.hostname or "127.0.0.1", host.port or 8000), timeout
        ):
            return True
    except OSError:
        return False


def pick_backend(requested: str | None) -> str:
    """Resolve which backend to use. 'auto' prefers whatever is already running."""
    choice = (requested or os.environ.get("BP_TTS") or "auto").lower()
    if choice != "auto":
        return choice
    if local_up():
        return "kokoro"
    return "elevenlabs" if api_key() else "say"


def say_fallback(text: str, why: str = "") -> int:
    if why:
        print(f"{why} - falling back to `say`", file=sys.stderr)
    return subprocess.run(["say", text]).returncode


# The server picks the container itself, so trust its content-type rather than
# assuming: mlx-audio defaults to mp3 even when the request says nothing.
CONTENT_EXT = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/flac": "flac", "audio/x-flac": "flac",
    "audio/ogg": "ogg", "audio/opus": "opus",
    "audio/aac": "aac", "audio/pcm": "pcm",
}


def speak_kokoro(text: str, voice: str | None) -> tuple[bytes, str] | None:
    """Synthesise via a local OpenAI-compatible server. Returns (audio, ext)."""
    url = local_url()
    body = {
        "model": os.environ.get("KOKORO_MODEL", KOKORO_MODEL),
        "input": text,
        "voice": voice or os.environ.get("KOKORO_VOICE") or KOKORO_VOICE,
    }
    fmt = os.environ.get("KOKORO_FORMAT")
    if fmt:
        body["response_format"] = fmt
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            audio = r.read()
            if not audio:
                print("local TTS returned no audio - check the server's terminal "
                      "for a traceback", file=sys.stderr)
                return None
            return audio, CONTENT_EXT.get(ctype, "mp3")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print(f"local TTS error {e.code}: {detail}", file=sys.stderr)
    except http.client.IncompleteRead:
        # A failed generation still returns 200 and then dies mid-stream.
        print("local TTS closed the stream early - the request failed server-side; "
              "check the server's terminal for a traceback", file=sys.stderr)
    except OSError as e:
        print(f"no local TTS server at {url} ({e}). Start it with:\n"
              f"  mlx_audio.server --port 8000", file=sys.stderr)
    return None


def speak_elevenlabs(text: str, voice: str | None) -> tuple[bytes, str] | None:
    key = api_key()
    if not key:
        print("no ELEVENLABS_API_KEY set", file=sys.stderr)
        return None
    # explicit --voice wins, then the environment, then a stock voice
    voice_id = voice or os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    model = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=json.dumps({
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
        }).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read(), "mp3"
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print(f"ElevenLabs error {e.code}: {detail}", file=sys.stderr)
    except OSError as e:
        print(f"ElevenLabs unreachable: {e}", file=sys.stderr)
    return None


def speak(text: str, voice: str | None = None, backend: str | None = None) -> int:
    """Say some text. Knows nothing about sections - `bp say` is fed prose that
    has already been rendered, whether by a command or by the Stop hook."""
    text = text.strip()
    if not text:
        return 0
    chosen = pick_backend(backend)
    if chosen == "off":
        return 0
    if chosen == "say":
        return say_fallback(text)

    synth = {"kokoro": speak_kokoro, "elevenlabs": speak_elevenlabs}.get(chosen)
    if synth is None:
        print(f"unknown TTS backend {chosen!r}. use: kokoro, elevenlabs, say, off",
              file=sys.stderr)
        return 1

    result = synth(text, voice)
    if result is None:
        return say_fallback(text, f"{chosen} unavailable")

    audio, ext = result
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(audio)
        out = f.name
    try:
        return subprocess.run(["afplay", out]).returncode
    finally:
        os.unlink(out)


# --- commands -------------------------------------------------------------

def fetch(book: Book, raw: str) -> tuple[dict, str | None]:
    """A section, or one passage of it if the id carries a #part."""
    ref, part = split_id(raw)
    sid, note = book.resolve(ref)
    if part:
        return book.part(sid, part), note
    sec = book.get(sid)
    if not sec:
        raise LookupError(book.why_missing(sid) or f"no section {sid}")
    return sec, note


def cmd_show(book: Book, args) -> int:
    rc = 0
    for i, raw in enumerate(args.ids):
        if i:
            print("\n")
        if args.parts:
            sid, _ = book.resolve(split_id(raw)[0])
            parts = book.parts(sid)
            if not parts:
                print(f"{sid} is read in one go - it has no parts", file=sys.stderr)
                rc = 1
                continue
            # Which passages a section is read in, and what to do between
            # them: a plan for the narrator, ids and follow-on commands and all.
            print(f"{sid} reads in {len(parts)} parts, in this order:",
                  file=sys.stderr)
            for n, p in enumerate(parts, 1):
                print(f"  {n}. {sid}#{p['part']:<9} {p.get('what', '')}",
                      file=sys.stderr)
                if p.get("then"):
                    print(f"     then: {p['then']}", file=sys.stderr)
            continue
        try:
            sec, note = fetch(book, raw)
        except LookupError as e:
            print(e, file=sys.stderr)
            rc = 1
            continue
        base = sec["id"]
        tail = None if split_id(raw)[1] or args.raw else withheld_tail(book, base)
        if tail:
            try:
                sec = book.part(base, tail_setup(book, base))
            except LookupError as e:
                # The anchor no longer matches the extracted text. Saying so is
                # the only safe move: the alternative is printing the whole
                # section, which is the outcome this is here to withhold.
                print(f"[warning] {e}\n[warning] refusing to print {base}: its "
                      f"tail paragraph cannot be separated from the setup",
                      file=sys.stderr)
                rc = 1
                continue
        sec, counts = creatures.apply(sec, no_counts=args.no_counts)
        emit(sec, note, raw=args.raw, counts=counts, table=book.table(sec["id"]))
        if tail:
            print(f"-> {tail['subid']} is this section's own tail paragraph, the "
                  f"outcome of one die result. Read it only after the roll: "
                  f"bp show {base}#{tail['part']}", file=sys.stderr)
    return rc


def tail_setup(book: Book, sid: str) -> str:
    """The passage read before the die - everything up to the tail paragraph."""
    return book.parts(sid)[0]["part"]


def withheld_tail(book: Book, sid: str) -> dict | None:
    """The part of a section that must not be read until the die has been rolled.

    e060, e068 and e105 each end in a lettered paragraph (e060a, e068a, e105a)
    that only happens on some results. `options` already stops short of it, but
    `show` was printing the whole body - handing over the outcome on the most
    obvious command there is, which is the one thing splitting them was for.

    A part carrying a subid is that shape and nothing else is: e001's three parts
    and e002's one are pacing, not spoilers, and are still read whole.
    """
    return next((p for p in book.parts(sid) if p.get("subid")), None)


def travel_row_hint(book: Book, q: str) -> str | None:
    """Travel-table rows are not sections, so a search for 'cross river' or
    'road' would otherwise come back empty and send the caller to memory."""
    for key in book.travel["terrain"]:
        if q in key or key in q:
            return (f"r207   Travel Table row {key!r} - lost/event thresholds and the "
                    f"die 1-6 references.\n       -> bp travel \"{key}\"")
    alias = procedures.TERRAIN_ALIASES.get(q)
    if alias:
        return (f"r207   Travel Table: {q!r} is the {alias!r} row.\n"
                f"       -> bp travel \"{alias}\"")
    return None


def cmd_search(book: Book, args) -> int:
    """Full-text search - an index into the book, not anything read aloud."""
    with contextlib.redirect_stdout(sys.stderr):
        return _search(book, args)


def _search(book: Book, args) -> int:
    query = " ".join(args.query) if isinstance(args.query, list) else args.query
    q = " ".join(query.lower().split())
    hits = []
    for sid, sec in book.sections.items():
        hay = f"{sec['title']}\n{sec['body']}".lower()
        if q in hay:
            score = hay.count(q) + (10 if q in sec["title"].lower() else 0)
            hits.append((score, sid, sec))
    hint = travel_row_hint(book, q)
    if hint:
        print(hint)
    if not hits:
        if hint:
            return 0
        print(f"no matches for {query!r}", file=sys.stderr)
        return 1
    hits.sort(key=lambda h: (-h[0], h[1]))
    for _, sid, sec in hits[: args.limit]:
        line = next(
            (l.strip() for l in sec["body"].split("\n") if q in l.lower()),
            sec["body"].split("\n")[0] if sec["body"] else "",
        )
        print(f"{sid:<6} {sec['title'][:34]:<34} {line[:70]}")
    if len(hits) > args.limit:
        print(f"... {len(hits) - args.limit} more (use -n)")
    return 0


def cmd_travel(book: Book, args) -> int:
    if not args.terrain:
        print(book.travel_table_text(), file=sys.stderr)
        return 0
    try:
        key = procedures.terrain_key(book, args.terrain)
    except procedures.Refuse as e:
        print(e, file=sys.stderr)
        return 1
    terrain = book.travel["terrain"][key]

    # The two 2d6 gates come first: they decide whether the 1d6 below happens.
    if args.lost is not None:
        print(procedures.check_lost(book, key, args.lost, args.guide),
              file=sys.stderr)
        return 0
    if args.event is not None:
        print(procedures.check_event(book, key, args.event), file=sys.stderr)
        return 0

    print(f"{terrain['name']}: lost on {terrain['lost_on']} (2d6), "
          f"event on {terrain['event_on']} (2d6), "
          f"hunt {terrain['hunt']}, fodder {terrain['fodder']}", file=sys.stderr)
    if args.roll is None:
        for i, ref in enumerate(terrain["event_refs"], 1):
            print(f"  die {i} -> {ref}", file=sys.stderr)
        return 0

    ref = terrain["event_refs"][args.roll - 1]
    print(f"  die {args.roll} -> {ref}", file=sys.stderr)
    if ref not in book.travel["refs"]:
        # A row that points straight at an event, with no second roll (e009 in
        # farmland, the whole desert row).
        sec = book.get(ref)
        if sec:
            print()
            book.emit_section(sec)
        return 0

    rolls = book.travel["refs"][ref]
    if args.roll2 is None:
        # Stopping here would hand back six outcomes the player has not earned,
        # so name the second roll instead of listing what it leads to.
        print(f"\n{ref} is a second table: roll 1d6 again.", file=sys.stderr)
        print(f"  -> bp travel {key!r} {args.roll} <die>".replace("'", '"'),
              file=sys.stderr)
        return 0
    target, note = book.resolve(rolls[args.roll2 - 1])
    print(f"  {ref} on {args.roll2} -> {target}", file=sys.stderr)
    sec = book.get(target)
    if not sec:
        print(book.why_missing(target) or f"{target} not found", file=sys.stderr)
        return 1
    print()
    book.emit_section(sec, note)
    return 0


def intro_text(sec: dict, table: dict, parts: list[dict] | None = None) -> str:
    """The prose above a table - the setup to read out before asking to roll.

    Normally the outcomes are laid out as table rows, so cutting at the first row
    is enough. e060/e068/e105 instead write one outcome as a trailing paragraph
    (e068a and friends), which is prose and survives that cut - so a section split
    into parts is truncated at the end of its first part as well.
    """
    body = sec["body"]
    if parts:
        try:
            body = slice_part(sec, parts, parts[0]["part"])["body"]
        except LookupError as e:
            # Falling back to the whole body silently would put the spoiler back
            # without anyone noticing, so say it out loud instead.
            print(f"[warning] {e}\n[warning] the text below may include an "
                  f"outcome the player has not rolled for", file=sys.stderr)
    if table["kind"] == "options":
        cut = re.search(r"^\s*die\s+rolls?\b", body, re.M | re.I)
    else:
        cut = re.search(r"^\s{2,}\d{1,2}\b", body, re.M)
    return (body[: cut.start()] if cut else body).strip()


def match_choice(table: dict, choice: str) -> tuple[str | None, list[str]]:
    """Resolve a user's choice to a column or sub-table. Returns (key, available)."""
    if table["kind"] == "options":
        names = [c["name"] for c in table["columns"]]
    elif table["kind"] == "table":
        names = [t["label"] or "(unlabelled)" for t in table["tables"]]
    else:
        return None, []
    if not choice:
        return (names[0], names) if len(names) == 1 else (None, names)
    want = choice.strip().lower()
    exact = [n for n in names if n.lower() == want]
    partial = [n for n in names if want in n.lower()]
    hit = exact or partial
    return (hit[0] if len(hit) == 1 else None), names


def cmd_options(book: Book, args) -> int:
    """Show what the player must choose and roll, without revealing outcomes."""
    sid, note = book.resolve(args.id)
    sec = book.get(sid)
    if not sec:
        print(book.why_missing(sid) or f"no section {sid}", file=sys.stderr)
        return 1
    if note:
        print(f"[errata] {note}", file=sys.stderr)
    parts = book.parts(sid)
    table = book.table(sid)
    if not table:
        if parts:
            # No parsed table, but the section still withholds a tail paragraph -
            # sending the reader at the whole section would give it away.
            # The outcome line is the referee's, and e060's names every branch, so
            # it goes to stderr with the rest of the adjudication notes.
            print(f"{sid} {sec['title']}: no die-roll table. Read the setup and "
                  f"resolve it by hand.", file=sys.stderr)
            print(f"  -> bp show {sid}#{parts[0]['part']}", file=sys.stderr)
            if parts[0].get("then"):
                print(f"  then: {parts[0]['then']}", file=sys.stderr)
            return 0
        print(f"{sid} {sec['title']}: no die-roll table; just read the section.",
              file=sys.stderr)
        return 0

    print(f"{sid} {sec['title']}", file=sys.stderr)
    # Substitute into the intro rather than the whole body: the anchors sit in the
    # prose above the table, and rewriting the body first could move a part anchor.
    intro, counts = creatures.apply_text(sid, intro_text(sec, table, parts),
                                         no_counts=args.no_counts, partial=True)
    if not args.quiet:
        # The title is already on stderr, so the prose starts at the situation.
        # An inline table has no laid-out rows for intro_text to cut at, so the
        # run is still in here and it is this table that takes it out.
        print(prose_text(intro, table=table))
        print()
    creatures.show_notes(counts, sys.stderr)

    # The choices are bp's own rendering of the table's columns, not the book's
    # words - "choice: negotiate, choice: evade, bracket asterisk if your party
    # all winged mounts" is not a sentence anyone says. The narrator turns them
    # into the question it asks, so they go to the referee's channel.
    err = sys.stderr
    if table["kind"] == "options":
        for col in table["columns"]:
            mark = ""
            fn = table["footnotes"].get(col["note"] or "")
            if col["note"] and fn:
                mark = f"   [{col['note']}] {fn}"
            elif col["note"]:
                mark = f"   [{col['note']}] (marker printed with no footnote in the source)"
            print(f"  choice: {col['name']}{mark}", file=err)
        if table.get("die_note"):
            fn = table["footnotes"].get(table["die_note"])
            if fn:
                print(f"  die modifier [{table['die_note']}]: {fn}", file=err)
    elif table["kind"] == "table":
        for sub in table["tables"]:
            label = sub["label"] or "(unlabelled)"
            print(f"  choice: {label}", file=err)
            if sub.get("note"):
                print(f"          {sub['note']}", file=err)
    else:
        print("  no choice to make - just roll", file=err)
    print(f"\n  then roll {table['die']}", file=err)
    return 0


def cmd_resolve(book: Book, args) -> int:
    """Given a choice and a die roll, jump to the resulting section."""
    sid, note = book.resolve(args.id)
    sec = book.get(sid)
    if not sec:
        print(book.why_missing(sid) or f"no section {sid}", file=sys.stderr)
        return 1
    table = book.table(sid)
    if not table:
        print(f"{sid} has no die-roll table", file=sys.stderr)
        return 1
    if note:
        print(f"[errata] {note}", file=sys.stderr)

    choice, roll = args.choice, args.roll
    # The choice is optional; with only a number, treat it as the roll.
    if roll is None and choice is not None and re.fullmatch(r"-?\d+", choice):
        choice, roll = None, int(choice)
    if roll is None:
        print("need a die roll", file=sys.stderr)
        return 1

    key, names = match_choice(table, choice)
    if names and key is None:
        if not choice:
            why = "required"
        else:
            hits = [n for n in names if choice.strip().lower() in n.lower()]
            why = "ambiguous" if hits else f"{choice!r} not recognised"
        print(f"choice {why}. options: {', '.join(names)}", file=sys.stderr)
        return 1

    # Locate the row covering this roll.
    if table["kind"] == "options":
        rows, rolls = table["rows"], table["rolls"]
        row_key = next((k for k, cov in rolls.items() if roll in cov), None)
        if row_key is None and rolls:
            # Modifiers can push a roll past the printed range; clamp to it.
            ks = sorted(rolls, key=int)
            row_key = ks[0] if roll < int(ks[0]) else ks[-1]
            print(f"(roll {roll} is outside the table; using row {row_key})",
                  file=sys.stderr)
        outcome = rows[row_key].get(key) if row_key else None
        if outcome is None:
            avail = ", ".join(sorted(rows.get(row_key, {}))) or "none"
            print(f"the table prints no {key} result on {roll} (the source shows a "
                  f"dash). available on that roll: {avail}", file=sys.stderr)
            return 1
        # Which cell the roll landed in, named by section id: the referee's
        # bookkeeping. The prose it leads to is printed below, on stdout.
        print(f"{sid} {key} on {roll}: {outcome}", file=sys.stderr)
        own = book.subid_part(sid, outcome)
        dest = None if own else re.search(r"\b([re]\d{3})\b", outcome)
    else:
        if table["kind"] == "table":
            sub = next(t for t in table["tables"]
                       if (t["label"] or "(unlabelled)") == key)
            results, rolls = sub["results"], sub["rolls"]
        else:
            results, rolls = table["results"], table["rolls"]
        row = next((k for k, cov in rolls.items() if roll in cov), None)
        if row is None and rolls:
            # Modifiers push a roll past the printed range, and the table itself
            # expects it: e065 sends the reader to e060 with a -1, which is what
            # e060's "1 (or less)" row is for. Clamp to the end it fell off, the
            # way the options branch above already does. Ordered by what each row
            # covers rather than by its key, because "1 (or less)" is not an int.
            lo = min(rolls, key=lambda k: min(rolls[k], default=99))
            hi = max(rolls, key=lambda k: max(rolls[k], default=-1))
            row = lo if roll < min(rolls[lo], default=99) else hi
            print(f"(roll {roll} is outside the table; using row {row})",
                  file=sys.stderr)
        if row is None:
            print(f"no row for a roll of {roll} in {sid}", file=sys.stderr)
            return 1
        entry = results[row]
        text = entry if isinstance(entry, str) else entry["text"]
        named = key and key != "(unlabelled)" and table["kind"] == "table"
        label = f" [{key}]" if named else ""
        print(f"{sid}{label} on {roll}: {text}", file=sys.stderr)
        own = book.subid_part(sid, text)
        dest = None if own else re.search(r"\b([re]\d{3})\b", text)

    if own:
        # The outcome is this section's own tail paragraph. Any rule it cites is
        # part of that outcome, not somewhere to jump next - and the table cell
        # sometimes holds only the first line of it, so read the passage itself.
        print(f"\n-> that is {own['subid']}, this section's own tail paragraph. "
              f"Read it in full: bp show {sid}#{own['part']}", file=sys.stderr)
        return 0
    if not dest:
        return 0
    target, tnote = book.resolve(dest.group(1))
    nxt = book.get(target)
    if not nxt:
        print(book.why_missing(target) or f"-> {target} (not found)", file=sys.stderr)
        return 1
    if args.no_follow:
        print(f"-> {target} {nxt['title']}", file=sys.stderr)
        return 0
    nxt, counts = creatures.apply(nxt)
    print()
    emit(nxt, tnote, counts=counts, table=book.table(nxt["id"]))
    return 0


def cmd_roll(book: Book, args) -> int:
    m = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", args.dice.lower())
    if not m:
        print("format: 2d6, d6, 2d6+1", file=sys.stderr)
        return 1
    n = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    rolls = [random.randint(1, sides) for _ in range(n)]
    total = sum(rolls) + mod
    detail = " + ".join(map(str, rolls))
    if mod:
        detail += f" {'+' if mod > 0 else '-'} {abs(mod)}"
    print(f"{total}   [{detail}]" if n > 1 or mod else str(total))
    return 0


def cmd_refs(book: Book, args) -> int:
    """Cross-references in and out - a lookup, never read aloud."""
    with contextlib.redirect_stdout(sys.stderr):
        return _refs(book, args)


def _refs(book: Book, args) -> int:
    sid, note = book.resolve(args.id)
    sec = book.get(sid)
    if not sec:
        print(book.why_missing(sid) or f"no section {sid}", file=sys.stderr)
        return 1
    if note:
        print(f"[errata] {note}", file=sys.stderr)
    print(f"{sid} {sec['title']}")
    print(f"  out: {' '.join(sec['refs']) or '(none)'}")
    print(f"  in:  {' '.join(book.incoming(sid)) or '(none)'}")
    return 0


def cmd_list(book: Book, args) -> int:
    """Section ids and titles - a directory, never spoken."""
    with contextlib.redirect_stdout(sys.stderr):
        return _list(book, args)


def _list(book: Book, args) -> int:
    pre = (args.prefix or "").lower()
    for sid, sec in book.sections.items():
        if sid.startswith(pre):
            print(f"{sid:<6} {sec['title']}")
    return 0


def cmd_say(book: Book, args) -> int:  # noqa: D401
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    """Read text aloud. There is no `bp speak <id>` any more: `bp show` already
    prints what a DM would say, so the Stop hook can speak the narration itself
    and the player hears exactly what is on screen."""
    text = " ".join(args.words) if args.words and not args.stdin else sys.stdin.read()
    return speak(text, args.voice, args.backend)


def main() -> int:
    p = argparse.ArgumentParser(prog="bp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def counts_flag(s):
        s.add_argument("--no-counts", action="store_true",
                       help="read band sizes as the booklet prints them, without "
                            "substituting the count rolled for this encounter")

    s = sub.add_parser("show", help="print sections, or one passage: e001#caravan")
    s.add_argument("ids", nargs="+")
    s.add_argument("--parts", action="store_true",
                   help="list the passages a section is read in, without the text")
    # --raw means the same thing everywhere: exactly what the source printed.
    # Compose the two for the true booklet text: --raw --no-counts.
    s.add_argument("--raw", action="store_true",
                   help="print the source layout - tables, ids, refs - instead of "
                        "the prose a DM would read out")
    counts_flag(s)
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("search", help="full-text search")
    # Quoting is easy to forget, and an unquoted `search cross river` erroring out
    # is exactly the dead end that sends a caller back to guessing.
    s.add_argument("query", nargs="+", help="words to look for; quoting optional")
    s.add_argument("-n", "--limit", type=int, default=12)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("travel", help="travel table lookup")
    s.add_argument("terrain", nargs="?")
    s.add_argument("roll", nargs="?", type=int, choices=range(1, 7),
                   help="the 1d6 that picks the event reference")
    s.add_argument("roll2", nargs="?", type=int, choices=range(1, 7),
                   help="the second 1d6, when that reference is itself a table")
    s.add_argument("--lost", type=int, metavar="2d6",
                   help="check the Lost column instead (r205)")
    s.add_argument("--event", type=int, metavar="2d6",
                   help="check the Event column instead (r204b)")
    s.add_argument("--guide", action="store_true",
                   help="party includes a guide: -1 to the lost roll (r205a)")
    s.set_defaults(fn=cmd_travel)

    s = sub.add_parser("start", help="how to begin a game (e001, r202, r225)")
    s.add_argument("roll", nargs="?", type=int, choices=range(1, 7),
                   help="the caravan die, once they roll it")
    s.add_argument("--step", type=int, metavar="N",
                   help="print only step N - one stop per message")
    s.set_defaults(fn=procedures.cmd_start)

    s = sub.add_parser("day", help="today's actions and the end-of-day checks (r203)")
    s.add_argument("hex_type", nargs="*",
                   help="a hex id like 0101, or town/castle/temple/ruins")
    s.set_defaults(fn=procedures.cmd_day)

    s = sub.add_parser("hex", help="what is in a hex, and what is next to it")
    s.add_argument("id", help="four digits, XXYY, like 1017")
    s.add_argument("--drift", type=int, choices=range(1, 7), metavar="1-6",
                   help="resolve an r205c airborne drift die to a destination hex")
    s.set_defaults(fn=procedures.cmd_hex)

    s = sub.add_parser("move", help="the ordered checks for one hex of travel (r204/r205)")
    s.add_argument("frm", metavar="from", help="hex id or terrain you are leaving")
    s.add_argument("to", help="hex id or terrain you are entering")
    # Tri-state: unset means "read it off the map", which is what happens whenever
    # both ends are hex ids. The flags are for overriding that, or for supplying
    # what the map cannot when the move is given as bare terrain names.
    s.add_argument("--river", default=None, action=argparse.BooleanOptionalAction,
                   help="override the map on whether a river is crossed (r204e)")
    s.add_argument("--road", default=None, action=argparse.BooleanOptionalAction,
                   help="override the map on whether you leave by road (r204c)")
    s.add_argument("--airborne", action="store_true", help="flying, not short-hopping (r204d)")
    s.add_argument("--guide", action="store_true", help="party includes a guide (r205a)")
    s.set_defaults(fn=procedures.cmd_move)

    s = sub.add_parser("treasure", help="the r226 grid, by wealth code")
    s.add_argument("code", help="wealth code, or A/B/C for the possession lines")
    s.add_argument("roll", nargs="?", type=int, choices=range(1, 7))
    s.set_defaults(fn=procedures.cmd_treasure)

    s = sub.add_parser("options", help="what to choose and roll, without spoilers")
    s.add_argument("id")
    s.add_argument("-q", "--quiet", action="store_true", help="omit the intro prose")
    counts_flag(s)
    s.set_defaults(fn=cmd_options)

    s = sub.add_parser("resolve", help="apply a choice and die roll, then follow on")
    s.add_argument("id")
    s.add_argument("choice", nargs="?", help="column or sub-table (omit if none)")
    s.add_argument("roll", nargs="?", type=int)
    s.add_argument("--no-follow", action="store_true",
                   help="name the next section instead of printing it")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("roll", help="roll dice, e.g. 2d6")
    s.add_argument("dice")
    s.set_defaults(fn=cmd_roll)

    s = sub.add_parser("refs", help="cross-references in and out")
    s.add_argument("id")
    s.set_defaults(fn=cmd_refs)

    s = sub.add_parser("list", help="list sections")
    s.add_argument("prefix", nargs="?")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("say", help="read text aloud (stdin, or words)")
    s.add_argument("words", nargs="*", help="text to say; omit to read stdin")
    s.add_argument("--stdin", action="store_true", help="read the text from stdin")
    s.add_argument("--voice", help="voice id/name for the chosen backend")
    s.add_argument("--backend", choices=["auto", "kokoro", "elevenlabs", "say", "off"],
                   help="TTS backend (default: $BP_TTS, else auto)")
    # Saying something needs no game data, and the Stop hook must still work in a
    # checkout that has no booklets extracted yet.
    s.set_defaults(fn=cmd_say, needs_book=False)

    # bp encounter - the band sizes rolled for what is happening right now, which
    # are recorded so that what is read aloud and what reaches the sheet agree.
    creatures.register(sub)

    # game, time, food, gold, party, eat, starve, lodge, foe - the recorded state
    # of one playthrough, rather than what the booklet says. Registered from
    # state.py so the sheet's dozen subcommands don't drown this function.
    state.register(sub)

    # bp fight auto - the one command that rolls its own dice, because a long
    # fight resolved a strike at a time is where a game quietly goes wrong.
    combat.register(sub)

    args = p.parse_args()
    load_dotenv()
    book = Book() if getattr(args, "needs_book", True) else None
    return args.fn(book, args)


if __name__ == "__main__":
    sys.exit(main())
