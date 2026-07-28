#!/usr/bin/env python3
"""Barbarian Prince reference CLI.

  bp show r203 e001        print one or more sections
  bp search "food"         full-text search
  bp travel forest         travel table row for a terrain
  bp travel forest 3       ...resolved for a die roll of 3
  bp roll 2d6              roll dice
  bp refs r220             what a section links to, and what links to it
  bp list r                list section ids (optionally filtered by prefix)
  bp speak e001            read a section aloud (local Kokoro, ElevenLabs, or `say`)
"""

import argparse
import json
import os
import re
import random
import subprocess
import sys
import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
AUDIO = ROOT / "audio"

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


def load(name: str) -> dict:
    path = DATA / name
    if not path.exists():
        sys.exit(
            f"{path} is missing.\n\n"
            "The game data is generated from the booklets rather than shipped "
            "with this repo.\nSee the README for where to download them, then "
            "run:\n  python3 tools/extract.py"
        )
    return json.loads(path.read_text())


class Book:
    def __init__(self):
        self.sections = load("sections.json")
        self.travel = load("travel.json")
        self.errata = load("errata.json")
        self.tables = load("tables.json")

    def table(self, sid: str) -> dict | None:
        return self.tables.get(self.normalize(sid))

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

    def incoming(self, sid: str) -> list[str]:
        sid = self.normalize(sid)
        return sorted(k for k, v in self.sections.items() if sid in v["refs"])


def fmt(sec: dict, note: str | None = None) -> str:
    head = f"{sec['id']} {sec['title']}"
    out = [head, "=" * len(head)]
    if note:
        out += [f"[errata] {note}", ""]
    out += [sec["body"]]
    if sec.get("refs"):
        out += ["", f"-> {' '.join(sec['refs'])}"]
    return "\n".join(out)


# --- speech ---------------------------------------------------------------

NUMBER_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"fifteen|twenty|twenty-five|thirty|fifty|hundred)"
)

SPEECH_SUBS = [
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


def to_speech(sec: dict) -> str:
    """Flatten a section into something that sounds right read aloud.

    pdftotext hard-wraps prose, so naively turning newlines into sentence breaks
    chops sentences in half. Wrapped prose lines are rejoined with a space;
    only blank lines and table rows become spoken pauses.
    """
    chunks: list[str] = [sec["title"].rstrip(".") + "."]
    prose: list[str] = []

    def flush():
        if prose:
            chunks.append(" ".join(prose))
            prose.clear()

    for raw in sec["body"].split("\n"):
        line = raw.strip()
        if not line:
            flush()
        elif TABLE_LINE_RE.search(raw):
            flush()
            # Columns become comma pauses so the row reads as a list.
            chunks.append(re.sub(r"[ \t]{2,}", ", ", line))
        else:
            prose.append(re.sub(r"[ \t]{2,}", " ", line))
    flush()

    # Each chunk carries its own terminator, so joining adds no stray periods
    # after a line that already ends in a colon.
    text = " ".join(c if c.endswith((".", ":", "!", "?")) else c + "."
                    for c in chunks if c)
    for pat, rep in SPEECH_SUBS:
        text = re.sub(pat, rep, text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\.\s*(?=[.,;])", "", text)
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    return text.strip()


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


def speak(sec: dict, voice: str | None, save: bool, backend: str | None = None) -> int:
    text = to_speech(sec)
    chosen = pick_backend(backend)

    if chosen == "say":
        return say_fallback(text)

    synth = {"kokoro": speak_kokoro, "elevenlabs": speak_elevenlabs}.get(chosen)
    if synth is None:
        print(f"unknown TTS backend {chosen!r}. use: kokoro, elevenlabs, say",
              file=sys.stderr)
        return 1

    result = synth(text, voice)
    if result is None:
        return say_fallback(text, f"{chosen} unavailable")

    audio, ext = result
    AUDIO.mkdir(exist_ok=True)
    out = AUDIO / f"{sec['id']}.{ext}"
    out.write_bytes(audio)
    rc = subprocess.run(["afplay", str(out)]).returncode
    if save:
        print(f"saved {out}", file=sys.stderr)
    else:
        out.unlink(missing_ok=True)
    return rc


# --- commands -------------------------------------------------------------

def cmd_show(book: Book, args) -> int:
    rc = 0
    for i, raw in enumerate(args.ids):
        if i:
            print("\n")
        sid, note = book.resolve(raw)
        sec = book.get(sid)
        if not sec:
            reason = book.why_missing(sid) or f"no section {sid}"
            print(reason, file=sys.stderr)
            rc = 1
            continue
        print(fmt(sec, note))
    return rc


def cmd_search(book: Book, args) -> int:
    q = args.query.lower()
    hits = []
    for sid, sec in book.sections.items():
        hay = f"{sec['title']}\n{sec['body']}".lower()
        if q in hay:
            score = hay.count(q) + (10 if q in sec["title"].lower() else 0)
            hits.append((score, sid, sec))
    if not hits:
        print(f"no matches for {args.query!r}", file=sys.stderr)
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
        print(book.travel_table_text())
        return 0
    key = args.terrain.lower()
    terrain = book.travel["terrain"].get(key)
    if not terrain:
        opts = ", ".join(book.travel["terrain"])
        print(f"unknown terrain {args.terrain!r}. try: {opts}, rafting", file=sys.stderr)
        return 1
    print(f"{terrain['name']}: lost on {terrain['lost_on']} (2d6), "
          f"event on {terrain['event_on']} (2d6), "
          f"hunt {terrain['hunt']}, fodder {terrain['fodder']}")
    if args.roll is None:
        for i, ref in enumerate(terrain["event_refs"], 1):
            print(f"  die {i} -> {ref}")
        return 0

    ref = terrain["event_refs"][args.roll - 1]
    print(f"  die {args.roll} -> {ref}")
    if ref in book.travel["refs"]:
        print(f"\n{ref} - roll one die again:")
        for i, ev in enumerate(book.travel["refs"][ref], 1):
            print(f"  {i} -> {ev}")
    else:
        sec = book.get(ref)
        if sec:
            print()
            print(fmt(sec))
    return 0


def intro_text(sec: dict, table: dict) -> str:
    """The prose above a table - the setup to read out before asking to roll."""
    body = sec["body"]
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
        print(f"[errata] {note}")
    table = book.table(sid)
    if not table:
        print(f"{sid} {sec['title']}: no die-roll table; just read the section.")
        return 0

    print(f"{sid} {sec['title']}")
    if not args.quiet:
        print()
        print(intro_text(sec, table))
    print()

    if table["kind"] == "options":
        for col in table["columns"]:
            mark = ""
            fn = table["footnotes"].get(col["note"] or "")
            if col["note"] and fn:
                mark = f"   [{col['note']}] {fn}"
            elif col["note"]:
                mark = f"   [{col['note']}] (marker printed with no footnote in the source)"
            print(f"  choice: {col['name']}{mark}")
        if table.get("die_note"):
            fn = table["footnotes"].get(table["die_note"])
            if fn:
                print(f"  die modifier [{table['die_note']}]: {fn}")
    elif table["kind"] == "table":
        for sub in table["tables"]:
            label = sub["label"] or "(unlabelled)"
            print(f"  choice: {label}")
            if sub.get("note"):
                print(f"          {sub['note']}")
    else:
        print("  no choice to make - just roll")
    print(f"\n  then roll {table['die']}")
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
        print(f"[errata] {note}")

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
            print(f"(roll {roll} is outside the table; using row {row_key})")
        outcome = rows[row_key].get(key) if row_key else None
        if outcome is None:
            avail = ", ".join(sorted(rows.get(row_key, {}))) or "none"
            print(f"the table prints no {key} result on {roll} (the source shows a "
                  f"dash). available on that roll: {avail}", file=sys.stderr)
            return 1
        print(f"{sid} {key} on {roll}: {outcome}")
        dest = re.search(r"\b([re]\d{3})\b", outcome)
    else:
        if table["kind"] == "table":
            sub = next(t for t in table["tables"]
                       if (t["label"] or "(unlabelled)") == key)
            results, rolls = sub["results"], sub["rolls"]
        else:
            results, rolls = table["results"], table["rolls"]
        row = next((k for k, cov in rolls.items() if roll in cov), None)
        if row is None:
            print(f"no row for a roll of {roll} in {sid}", file=sys.stderr)
            return 1
        entry = results[row]
        text = entry if isinstance(entry, str) else entry["text"]
        named = key and key != "(unlabelled)" and table["kind"] == "table"
        label = f" [{key}]" if named else ""
        print(f"{sid}{label} on {roll}: {text}")
        dest = re.search(r"\b([re]\d{3})\b", text)

    if not dest:
        return 0
    target, tnote = book.resolve(dest.group(1))
    nxt = book.get(target)
    if not nxt:
        print(book.why_missing(target) or f"-> {target} (not found)", file=sys.stderr)
        return 1
    if args.no_follow:
        print(f"-> {target} {nxt['title']}")
        return 0
    print()
    print(fmt(nxt, tnote))
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
    sid, note = book.resolve(args.id)
    sec = book.get(sid)
    if not sec:
        print(book.why_missing(sid) or f"no section {sid}", file=sys.stderr)
        return 1
    if note:
        print(f"[errata] {note}")
    print(f"{sid} {sec['title']}")
    print(f"  out: {' '.join(sec['refs']) or '(none)'}")
    print(f"  in:  {' '.join(book.incoming(sid)) or '(none)'}")
    return 0


def cmd_list(book: Book, args) -> int:
    pre = (args.prefix or "").lower()
    for sid, sec in book.sections.items():
        if sid.startswith(pre):
            print(f"{sid:<6} {sec['title']}")
    return 0


def cmd_speak(book: Book, args) -> int:
    sid, note = book.resolve(args.id)
    sec = book.get(sid)
    if not sec:
        print(book.why_missing(sid) or f"no section {sid}", file=sys.stderr)
        return 1
    if note:
        print(f"[errata] {note}", file=sys.stderr)
    if args.text_only:
        print(to_speech(sec))
        return 0
    return speak(sec, args.voice, args.save, args.backend)


def main() -> int:
    p = argparse.ArgumentParser(prog="bp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="print sections")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("search", help="full-text search")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=12)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("travel", help="travel table lookup")
    s.add_argument("terrain", nargs="?")
    s.add_argument("roll", nargs="?", type=int, choices=range(1, 7))
    s.set_defaults(fn=cmd_travel)

    s = sub.add_parser("options", help="what to choose and roll, without spoilers")
    s.add_argument("id")
    s.add_argument("-q", "--quiet", action="store_true", help="omit the intro prose")
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

    s = sub.add_parser("speak", help="read a section aloud")
    s.add_argument("id")
    s.add_argument("--voice", help="voice id/name for the chosen backend")
    s.add_argument("--backend", choices=["auto", "kokoro", "elevenlabs", "say"],
                   help="TTS backend (default: $BP_TTS, else auto)")
    s.add_argument("--save", action="store_true", help="keep the audio in audio/")
    s.add_argument("--text-only", action="store_true", help="print speech text, don't play")
    s.set_defaults(fn=cmd_speak)

    args = p.parse_args()
    load_dotenv()
    return args.fn(Book(), args)


if __name__ == "__main__":
    sys.exit(main())
