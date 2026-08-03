#!/usr/bin/env python3
"""Play Barbarian Prince against a local model, with the narration spoken.

The point of this program is the routing. A model can be told to read a section
out and simply not do it - that is the failure this replaces. Here the model
never carries the booklet's prose at all: it asks for a `bp` command, this
program runs it, and this program decides what the player sees and hears.

    bp stdout   -> screen, speaker, and back to the model
    bp stderr   -> back to the model, and the screen only in referee mode
    a fight log -> screen and the model, but never spoken (see UNSPOKEN)
    model text  -> screen and speaker
    model think -> discarded

The stdout/stderr split is `bp`'s own contract (see CLAUDE.md), so the routing
table above is the whole design. Nothing depends on the model remembering to
relay anything.

    ./play                 # start playing, then /start for a new game
    ./play --referee       # also show stderr and the tool calls
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import markup
# The map rules - which side of the Tragoth a hex is on, whether it can be
# hunted - are read straight from bp's own implementation rather than copied.
# Everything that changes the game still goes through the bp subprocess.
import procedures

ROOT = Path(__file__).resolve().parent.parent
# Invoked as a module with this same interpreter rather than through a
# wrapper script: one less process, and a venv-run client cannot end up
# calling whatever `python3` happens to be first on PATH.
BP = [sys.executable, str(ROOT / "src" / "bp.py")]

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.environ.get("BP_MODEL", "qwen3.6:latest")
# The model's own context is far larger, but Ollama's default is not: pin it so
# an Ollama upgrade cannot silently start truncating the system prompt.
NUM_CTX = int(os.environ.get("BP_NUM_CTX", "32768"))
# Thinking costs tokens the player never sees or hears. Off unless asked for.
THINK = os.environ.get("BP_THINK", "").lower() in ("1", "true", "yes")

# A rules referee wants less entropy than a storyteller, and qwen3.6's Modelfile
# ships storyteller defaults: temperature 1.0 and presence_penalty 1.5.
#
# The penalty is the worse of the two here. It docks tokens for having appeared
# already, which is precisely wrong for a game whose proper nouns recur - over
# 20 samples of one day's narration the default settings produced an invented
# word every single time, including Aquilonia and Hyboria (Conan's setting, not
# this one) and six different spellings of the town Ogon: Ogdon, Ogona, Ogond,
# Ogone, Ogont, Ogonyi. The model was being penalised for saying "Ogon" twice.
#
# At 0.4 with no penalty the out-of-vocabulary words were almost all ordinary
# inflections. 0.4 rather than 0 so that seventy days of narration do not come
# out word-for-word identical.
TEMPERATURE = float(os.environ.get("BP_TEMP", "0.4"))
PRESENCE_PENALTY = float(os.environ.get("BP_PRESENCE", "0.0"))
OPTIONS = {"num_ctx": NUM_CTX, "temperature": TEMPERATURE,
           "presence_penalty": PRESENCE_PENALTY}

DIM, PROSE, ASIDE, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[0m"


# --- the system prompt ----------------------------------------------------

PROMPT = ROOT / "src" / "prompts" / "system.md"

# Keep it short. Measured on qwen3.6, same model and same turn: with CLAUDE.md's
# ~6,900 tokens of guidance it invented an opening passage whole ("the last
# Prince of Kesh, the evil wizard Gorgon"); with a few hundred tokens saying only
# "you don't know the book, call the tool" it fetched the real one. Length is not
# a tidiness question here - it is the difference between narration and fiction.
BUDGET = 2000


def system_prompt() -> str:
    text = PROMPT.read_text()
    if len(text) // 4 > BUDGET:
        print(f"{ASIDE}[warning] {PROMPT.name} is ~{len(text) // 4} tokens. Past "
              f"roughly {BUDGET} a small model starts inventing prose instead of "
              f"fetching it - trim it, or move the detail into bp.{RESET}",
              file=sys.stderr)
    return text


# --- speech ---------------------------------------------------------------

# A place it is safe to cut the stream: end of a line, or end of a sentence.
# Flushing here rather than per-chunk is what lets the voice start while the
# model is still generating, and it keeps `**bold**` intact - rendering a chunk
# boundary that fell between the two asterisks would print the markers raw.
SEGMENT_END = re.compile(r"\n|(?<=[.!?:])\s+")


def split_ready(buf: str) -> tuple[str, str]:
    """Split off as much as ends on a boundary, keeping the unfinished tail."""
    last = None
    for last in SEGMENT_END.finditer(buf):
        pass
    return (buf[:last.end()], buf[last.end():]) if last else ("", buf)

# A model that is told not to repeat the prose can still invent it instead, and
# its own words reach the player unchecked. In testing a local model opened the
# game with a passage it had made up whole - right shape, wrong kingdom. So any
# span the model presents AS the booklet (a blockquote, or a long quotation) is
# checked against what bp actually printed this turn, and flagged when it does
# not appear there. This catches invention, not paraphrase; it is a smoke alarm,
# not a proof.
QUOTED = re.compile(r"^[ \t]*>[ \t]*(\S.{20,})$|\"([^\"]{60,})\"", re.M)


def unsourced_quotes(text: str, delivered: str) -> list[str]:
    """Quoted spans in the model's narration that bp never printed."""
    said = " ".join(delivered.split()).lower()
    out = []
    for m in QUOTED.finditer(text):
        span = " ".join((m.group(1) or m.group(2)).split())
        # Match on a distinctive slice rather than the whole span: the model may
        # re-wrap or trim what it quotes without inventing it.
        probe = " ".join(span.lower().split()[:8])
        if probe and probe not in said:
            out.append(span)
    return out


class Speaker:
    """Serialises speech. One utterance at a time, in the order it was queued.

    `bp say` blocks until playback finishes, so a single worker thread is the
    whole queue: without it, two overlapping afplay processes talk over each
    other and the game becomes unlistenable.
    """

    def __init__(self, enabled: bool = True):
        self.q: queue.Queue[str | None] = queue.Queue()
        self.enabled = enabled
        # What is playing right now, so a player who has finished listening can
        # cut it off. Guarded because the worker sets it and the main thread
        # kills it.
        self.playing: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        while True:
            text = self.q.get()
            if text is None:
                return
            try:
                # Its own session, so one signal reaches `bp say` and the afplay
                # it spawned. Killing only the parent orphans the audio, which
                # keeps talking over whatever comes next.
                proc = subprocess.Popen(
                    [*BP, "say", "--stdin"], stdin=subprocess.PIPE, text=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                with self.lock:
                    self.playing = proc
                proc.communicate(text, timeout=600)
            except (OSError, subprocess.SubprocessError):
                pass  # voice is a preference; never let it end the game
            finally:
                with self.lock:
                    self.playing = None

    def stop(self):
        """Drop the backlog and cut off whatever is speaking.

        Typing is the signal: the player has read ahead and is answering, so
        anything still queued is describing a moment they have already left.
        """
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        with self.lock:
            proc = self.playing
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def say(self, text: str):
        # Blockquoted lines are never spoken. The model has no reason to quote
        # the book - bp already delivered it - so a quote here is either a
        # repeat or an invention, and neither belongs in the player's ear.
        kept = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
        text = "\n".join(kept).strip()
        if text and self.enabled:
            self.q.put(text)

    def close(self):
        self.q.put(None)


# --- the bp tool ----------------------------------------------------------

TOOL = {
    "type": "function",
    "function": {
        "name": "bp",
        "description": (
            "Run the Barbarian Prince reference CLI. Returns stdout, stderr and "
            "the exit code. stdout is the prose a DM reads out - it has ALREADY "
            "been shown to the player and read aloud, so never repeat it. "
            "stderr is referee data for you only: section ids, errata, "
            "cross-references and band-size notes."),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        'Arguments to bp, one per element. Examples: '
                        '["start"], ["show", "e001#premise"], '
                        '["options", "e003"], ["resolve", "e003", "evade", "4"], '
                        '["move", "1017", "1118"], ["party", "wound", "Lancer", "+2"]'),
                },
            },
            "required": ["args"],
        },
    },
}


# Shown, but not spoken. The split is normally two-way - stdout is the player's,
# stderr is the referee's - and a combat log is the one thing that falls between:
# twenty-eight lines of "2d6 9 [5+4], skill +3 = 12" is exactly what you want on
# the page to check the arithmetic against, and exactly what nobody wants read to
# them a strike at a time. The model still receives it and says what happened;
# that summary is the spoken version.
UNSPOKEN = {"fight"}


def run_bp(args: list[str], speaker: Speaker, referee: bool) -> str:
    """Run one bp command and route its output. Returns the model's view."""
    # No shell: args go straight to execve, so nothing the model emits can be
    # interpreted as a shell operator. Bad arguments fail in bp's own argparse.
    try:
        p = subprocess.run([*BP, *args], capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return json.dumps({"stdout": "", "stderr": f"could not run bp: {e}",
                           "exit": -1})

    if referee:
        print(f"{DIM}$ ./bp {' '.join(args)}{RESET}")
    if p.stdout.strip():
        print(f"\n{PROSE}{markup.wrap(p.stdout.rstrip())}{RESET}\n")
        if args[:1] and args[0] not in UNSPOKEN:
            speaker.say(p.stdout)
    if p.stderr.strip() and referee:
        print(f"{DIM}{p.stderr.rstrip()}{RESET}")

    return json.dumps({
        "stdout": p.stdout,
        "stderr": p.stderr,
        "exit": p.returncode,
        "note": "stdout was already shown and spoken to the player",
    })


# --- ollama ---------------------------------------------------------------

def stream_chat(messages: list[dict], speaker: Speaker) -> dict:
    """One model call. Streams text to screen and speaker as it arrives.

    Returns the assembled assistant message. Thinking is read off the wire and
    dropped here - it is the one thing that must never reach the speaker.
    """
    body = {
        "model": MODEL,
        "messages": messages,
        "tools": [TOOL],
        "stream": True,
        "think": THINK,
        "options": OPTIONS,
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})

    content, tool_calls, buf, spoke = [], [], "", False
    screen = markup.Wrap()
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message") or {}

            # Tool calls can arrive across several chunks; collect them all.
            for call in msg.get("tool_calls") or []:
                tool_calls.append(call)

            piece = msg.get("content") or ""
            if piece:
                if not spoke:
                    print()  # separate the narration from whatever came before
                    spoke = True
                content.append(piece)
                buf += piece
                # The screen and the speaker want opposite things from the same
                # markdown, so each segment is rendered once and stripped once.
                ready, buf = split_ready(buf)
                if ready:
                    print(screen.feed(markup.render(ready)), end="", flush=True)
                    speaker.say(markup.speakable(ready))

            if chunk.get("done"):
                break

    if buf.strip():
        print(screen.feed(markup.render(buf)), end="", flush=True)
        speaker.say(markup.speakable(buf))
    if spoke:
        print()
    return {"role": "assistant", "content": "".join(content),
            "tool_calls": tool_calls}


# --- directed sequences ---------------------------------------------------
#
# Setup is seven fixed steps with no judgment in any of them: read a passage,
# ask for a die, record the answer. Handing that to a model buys nothing and
# costs the failure this whole program exists to prevent - in testing it
# invented the opening rather than fetching it. So the code walks it, `bp`
# delivers every word, and the model is not consulted until day 1, where there
# is finally something to adjudicate.

# The six caravan destinations come in two shapes - "Ogon (0101)" and a bare
# "hex 0701" - so match the first four digits after "you are in" rather than the
# parenthesised form. First, not any: destination 3 reads "Ruins of Jakor's Keep
# (0901 - e001 prints 0801, which is a typo)", where the second number is the
# typo the errata corrects and would put the party in the wrong hex.
HEX_IN = re.compile(r"you are in\D*?(\d{4})\b")
TREASURE_OUT = re.compile(r":\s*(\d+)\s*$")  # "...on 4: 2"  -> 2


# A walked sequence talks to the player directly - "roll 2d6", "the party eats
# three units" - and those sentences are the only ones in the program that no
# model and no `bp show` composes. They still have to be heard: a player looking
# away from the screen was being asked for a die by nothing but a `>`.
#
# The screen keeps the rule cite and the die notation, because that is what a
# referee checks the walk against. Neither has a spoken form - "(r215b)" comes
# out of the synth as letters, "2d6" as "two d six" - so the voice gets the
# sentence without them, and section ids are read the way bp reads them.
CITE = re.compile(r"\s*\((?:[re]\d{3}[a-f]?)(?:[,;]\s*[re]\d{3}[a-f]?)*\)")
DICE = re.compile(r"\b([12])d6\b")
SECTION = re.compile(r"\b([re])(\d{3})\b")


def spoken(line: str) -> str:
    """One of the walk's own lines, as a person would say it."""
    line = CITE.sub("", line)
    line = DICE.sub(lambda m: "one die" if m.group(1) == "1" else "two dice", line)
    line = SECTION.sub(
        lambda m: f"{'rule' if m.group(1) == 'r' else 'event'} {m.group(2)}", line)
    return re.sub(r"\s+([.,;])", r"\1", line).strip()


def tell(speaker: Speaker, line: str) -> None:
    """Say something to the player in the walk's own voice - print it and speak it.

    Everything else the player hears comes from bp or the model. These lines are
    the sequence itself asking and reporting, and they are spoken for the same
    reason bp's prose is: the game is played by ear.
    """
    print(f"\n{ASIDE}{markup.wrap(line)}{RESET}")
    speaker.say(spoken(line))


def ask_die(prompt: str, die: str, speaker: Speaker) -> int | None:
    """Ask the player for a roll. None if they abandon setup."""
    lo, hi = (1, 6) if die == "1d6" else (2, 12)
    tell(speaker, prompt)
    while True:
        try:
            said = input(f"{ASIDE}  roll {die} > {RESET}").strip()
            speaker.stop()
        except EOFError:
            return None
        if said in ("/quit", "/exit"):
            return None
        if said.isdigit() and lo <= int(said) <= hi:
            return int(said)
        print(f"{DIM}  {die} gives {lo}-{hi}.{RESET}")


def run_setup(speaker: Speaker, referee: bool) -> str | None:
    """Walk `bp start` in code. Returns a summary for the model, or None."""
    steps = json.loads((ROOT / "data" / "procedures.json").read_text())["setup"]
    wits = gold = hexid = None

    for n, step in enumerate(steps["steps"], 1):
        if referee:
            print(f"{DIM}[setup {n}/{len(steps['steps'])}: {step['id']} "
                  f"({step['cite']})]{RESET}")

        # A passage to read is delivered by bp, never composed here.
        if step.get("read"):
            run_bp(["show", step["read"]], speaker, referee)

        if step["id"] == "stats":
            roll = ask_die(step["prompt"], step["die"], speaker)
            if roll is None:
                return None
            wits = max(roll, 2)  # r202: a 1 counts as 2
            print(f"{DIM}  wit & wiles {wits}"
                  f"{' (a 1 counts as 2)' if roll == 1 else ''}; "
                  f"{step['fixed']}{RESET}")

        elif step["id"] == "gold":
            roll = ask_die(step["prompt"], step["die"], speaker)
            if roll is None:
                return None
            out = run_bp(["treasure", "2", str(roll)], speaker, referee)
            got = json.loads(out)
            m = TREASURE_OUT.search((got["stdout"] + got["stderr"]).strip())
            gold = int(m.group(1)) if m else None
            if gold is None:
                print(f"{ASIDE}  could not read the gold off the treasure "
                      f"table - enter it yourself below{RESET}")

        elif step["id"] == "hex":
            roll = ask_die(step["prompt"], step["die"], speaker)
            if roll is None:
                return None
            out = run_bp(["start", str(roll)], speaker, referee)
            got = json.loads(out)
            m = HEX_IN.search(got["stdout"] + got["stderr"])
            hexid = m.group(1) if m else None

        elif step["id"] == "record":
            try:
                name = input(f"\n{ASIDE}Name your game (a save file) > "
                             f"{RESET}").strip() or "prince"
                if gold is None:
                    gold = int(input(f"{ASIDE}Starting gold > {RESET}").strip())
                if hexid is None:
                    hexid = input(f"{ASIDE}Starting hex > {RESET}").strip()
            except (EOFError, ValueError):
                return None
            run_bp(["game", "new", name, "--wits", str(wits),
                    "--gold", str(gold), "--hex", hexid], speaker, referee)

    return (f"Setup is complete and the player heard every passage of it. "
            f"Wit & wiles {wits}, combat skill 8, endurance 9, {gold} gold, "
            f"starting in hex {hexid}. The sheet is written. Begin day 1: call "
            f"bp with [\"day\", \"{hexid}\"] and take it from there.")


# --- dusk -----------------------------------------------------------------
#
# The end of a day is a fixed order of checks (procedures.json, `day`), and
# forgetting them is silent: nobody eats, the guard never rolls, and the date
# never moves. A narrator asked to remember them will sometimes narrate "we move
# to Day 2" having done none of it. So the code walks them, the same way it
# walks setup - the judgment is the player's (hunt or not, buy or eat stores)
# and what to do when e002 actually fires is handed back to the model.
#
# Nothing here asks the player a question the map can answer. Which side of the
# Tragoth they are on and whether the hex can be hunted are both in the data,
# and asking instead put the burden of the rules back on the person who came to
# play a game.

SHEET_HEX = re.compile(r"\bhex (\d{4})\b")
SHEET_DAY = re.compile(r"\bday (\d+) of\b")
SHEET_FOOD = re.compile(r"\bfood (\d+) units?\b")
SHEET_GOLD = re.compile(r"\bgold (\d+)\b")
LODGING = ("town", "castle", "temple")
E002_BONUS = ("0101", "1501")   # Ogon and Weshor: the guard looks hardest there

# `bp game log` keeps a line per change, tagged with the day it happened on. A
# step recorded there today has been done - by this walk, by the model calling
# `bp eat` itself, or by an earlier /dusk - and doing it twice buys a second
# dinner and a second night's rooms with money the party has not got.
LOG_ROW = re.compile(r"^\s*day\s+(\d+)\s+(.*)$", re.M)
LOG_MARKS = {"meal:": "eat", "went without food": "eat", "wages:": "pay",
             "lodging:": "lodge", "hunting": "hunt"}
ALREADY = {"eat": "the meal", "pay": "the wages", "lodge": "the lodging",
           "hunt": "a hunt"}

# Which day's dusk has already been walked, so advancing time twice or calling
# /dusk again after an e002 fight does not re-roll the guard or re-feed anyone.
_done: dict[int, set[str]] = {}


class Reference:
    """Just enough of bp's Book for the map lookups in procedures.py.

    Which side of the Tragoth a hex lies on, and whether it can be hunted, are
    rules - so they are read from the one implementation in procedures.py rather
    than worked out again here. Two answers to "is this north of the river" is
    one answer too many.
    """

    def __init__(self):
        self.map = read_data("map.json")
        self.travel = read_data("travel.json")


def read_data(name: str) -> dict | None:
    path = ROOT / "data" / name
    return json.loads(path.read_text()) if path.exists() else None


_book: Reference | None = None


def book() -> Reference:
    global _book
    if _book is None:
        _book = Reference()
    return _book


def sheet(args) -> tuple[int, str] | None:
    """Day and hex, read back off the sheet rather than remembered."""
    p = subprocess.run([*BP, "game", "-b"], capture_output=True, text=True)
    both = p.stdout + p.stderr
    d, h = SHEET_DAY.search(both), SHEET_HEX.search(both)
    return (int(d.group(1)), h.group(1)) if d and h else None


def purse() -> tuple[int, int] | None:
    """Food and gold as the sheet has them now, or None if it cannot be read.

    What a dusk step actually cost is the difference across it. Reading the two
    numbers off the sheet either side of the command is the one way of knowing
    that does not depend on parsing the sentence bp wrote about it - so a
    reworded `bp eat` cannot quietly turn the report into a lie.
    """
    p = subprocess.run([*BP, "game", "-b"], capture_output=True, text=True)
    both = p.stdout + p.stderr
    f, g = SHEET_FOOD.search(both), SHEET_GOLD.search(both)
    return (int(f.group(1)), int(g.group(1))) if f and g else None


def moved(before: tuple[int, int] | None) -> tuple[tuple[int, int] | None, int, int]:
    """(the sheet now, food gained, gold spent) across a step just run.

    An unreadable sheet reports no movement rather than guessing at one: a
    report that says nothing is a gap the player can ask about, and a report
    that says the wrong number is one they cannot.
    """
    now = purse()
    if before is None or now is None:
        return now, 0, 0
    return now, now[0] - before[0], before[1] - now[1]


def features(hexid: str) -> list[str]:
    hexes = (book().map or {}).get("hexes", {})
    return (hexes.get(hexid) or {}).get("features") or []


def north_of_river(hexid: str) -> bool | None:
    """True north of the Tragoth, False south, None if the map cannot say."""
    return procedures.north_flag(book(), hexid) if book().map else None


def may_hunt(hexid: str) -> bool | None:
    """True if r215b allows a hunt in this hex, None if the map cannot say."""
    if not (book().map and book().travel):
        return None
    return procedures.hunt_flag(book(), hexid)


def already_today(day: int) -> set[str]:
    """The dusk steps the sheet's own log says have been done today."""
    p = subprocess.run([*BP, "game", "log", "-n", "60"], capture_output=True,
                       text=True)
    out = set()
    for d, what in LOG_ROW.findall(p.stdout + p.stderr):
        if int(d) != day:
            continue
        for mark, name in LOG_MARKS.items():
            if what.lower().startswith(mark):
                out.add(name)
    return out


def run_step(cmd: list[str], speaker: Speaker, referee: bool) -> tuple[bool, str]:
    """One dusk command. (did it work, what bp said on stderr)."""
    out = json.loads(run_bp(cmd, speaker, referee))
    return out["exit"] == 0, (out["stderr"] or "").strip()


def blocked(day: int, cmd: list[str], err: str) -> str:
    """What to hand the model when a dusk step refuses - the day stays open."""
    return (f"Day {day} is NOT closed out. `bp {' '.join(cmd)}` refused, so "
            f"nothing after it has happened: no wages, no lodging, and the date "
            f"has not moved. bp said, to you only:\n\n{err}\n\nThe player heard "
            f"none of that. Put the choice to them in your own words, do what "
            f"they decide, and then tell them to type /dusk to finish the day.")


def units(n: int) -> str:
    return f"{n} food unit" + ("" if n == 1 else "s")


def ask_hunter(speaker: Speaker) -> str | None:
    """Who hunts tonight, or None. r215b: any one character in the party."""
    prince, others = roster()
    who = ", ".join([prince or "", *others]).strip(", ")
    tell(speaker, "Hunting is allowed here (r215b). Name the hunter, or skip it.")
    print(f"{DIM}  the hunter's name, enter for {prince or 'nobody'}, "
          f"or 'n' to skip{' - party: ' + who if others else ''}{RESET}")
    try:
        said = ask(f"  {ASIDE}>{RESET} ")
    except Abandoned:
        return None
    speaker.stop()
    if said.lower() in ("n", "no", "skip"):
        return None
    return said or prince


def run_dusk(speaker: Speaker, referee: bool) -> str | None:
    """Walk the end-of-day checks. Returns a summary for the model, or None."""
    now = sheet(None)
    if now is None:
        print(f"{ASIDE}No game is being tracked - nothing to close out.{RESET}")
        return None
    day, hexid = now
    done = _done.setdefault(day, set())
    logged = already_today(day)
    done |= logged
    feats = features(hexid)
    north = north_of_river(hexid)
    hunting = may_hunt(hexid)
    told = []

    if referee:
        side = {True: "north of the Tragoth", False: "south of the Tragoth",
                None: "side of the Tragoth unknown"}[north]
        print(f"{DIM}[dusk: day {day}, hex {hexid}, features {feats or 'none'}, "
              f"{side}, hunting {hunting}, already done "
              f"{', '.join(sorted(done)) or 'nothing'}]{RESET}")

    # e002, north of the Tragoth: after events, before the meal. South of it
    # there is no check and the player is not asked; north of it they are asked
    # for the die and nothing else. Only an unreadable map brings back the
    # question of which side they are on.
    if "e002" not in done and north is not False:
        bonus = 1 if hexid in E002_BONUS else 0
        where = (f"You are north of the Tragoth, in {hexid}, so the mercenary "
                 f"royal guardsmen may find you."
                 if north else
                 f"The map cannot tell me which side of the Tragoth {hexid} is on.")
        escape = "" if north else " Or say 's', if you know you are south of it."
        retry = "1-6." if north else "1-6, or 's'."
        tell(speaker, f"End of day {day}. {where} Roll 1d6 for e002 and "
                      f"subtract 3{f', adding 1 for {hexid}' if bonus else ''}."
                      f"{escape}")
        while True:
            try:
                said = input(f"{ASIDE}  roll 1d6 > {RESET}").strip().lower()
                speaker.stop()
            except EOFError:
                return None
            if said in ("s", "south", "skip"):
                done.add("e002")
                break
            if said.isdigit() and 1 <= int(said) <= 6:
                score = int(said) - 3 + bonus
                done.add("e002")
                print(f"{DIM}  {said} - 3{f' + {bonus}' if bonus else ''} = {score}"
                      f" - {'the guard finds you' if score >= 1 else 'no event'}"
                      f" (e002){RESET}")
                if score >= 1:
                    # Hand back what bp printed, not just the news that it
                    # fired. A director that runs a command and keeps the output
                    # to itself leaves the narrator describing an encounter it
                    # has never seen, which is how you get atmosphere instead of
                    # the three choices the player is waiting for.
                    out = json.loads(run_bp(["options", "e002"], speaker, referee))
                    return (f"It is the end of day {day} in hex {hexid}. The e002 "
                            f"check scored {score}, so the guardsmen have found the "
                            f"party. The prose below has already been shown and "
                            f"spoken - do not repeat it. Offer the player the "
                            f"choices and ask for the die.\n\n"
                            f"{out['stdout'].strip()}\n\n"
                            f"[referee only]\n{out['stderr'].strip()}\n\n"
                            f"The meal, any wages, lodging and the date have NOT "
                            f"happened yet; once the encounter is settled tell the "
                            f"player to type /dusk to close the day out.")
                break
            print(f"{DIM}  {retry}{RESET}")

    # The hunt (r215b), before the meal and only where the rules allow it. It is
    # the step the model never offered at all: a party in open country can eat
    # for nothing, and instead it silently ran the stores down.
    if "hunt" not in done and hunting:
        hunter = ask_hunter(speaker)
        if hunter:
            die = ask_die(f"{hunter} hunts for tonight's food (r215b).", "2d6",
                          speaker)
            if die is not None:
                was = purse()
                ok, err = run_step(["hunt", hunter, str(die), "--hex", hexid],
                                   speaker, referee)
                if not ok:
                    # Not fatal - the meal can still come out of stores - but not
                    # silent either. bp said why it would not roll, and the
                    # player is the one who has to decide what to do instead.
                    tell(speaker, f"No hunt: "
                                  f"{err.splitlines()[0] if err else 'bp refused'}")
                elif die == 12:
                    # r215b: a 12 hurts the hunter whatever else happened, and
                    # nothing is banked until the wound is rolled - if it knocks
                    # him out the hunt failed after all and he dies. That is a
                    # die and a judgment, so it goes back rather than being
                    # walked, and the day stays open behind it.
                    done.add("hunt")
                    tell(speaker, f"The roll was 12 exactly, so {hunter} was hurt "
                                  f"in the hunt (r215b). Nothing is on the sheet "
                                  f"until the wound is rolled.")
                    return (f"It is the end of day {day} in hex {hexid}. {hunter} "
                            f"hunted and rolled 12 exactly, so he is hurt (r215b) "
                            f"and neither the food nor the wound is on the sheet "
                            f"yet. The player has been told that much and no more. "
                            f"Ask for 1d6 and do what bp says below.\n\n"
                            f"[referee only]\n{err}\n\n"
                            f"The meal, any wages, lodging and the date have NOT "
                            f"happened yet; once the wound is settled tell the "
                            f"player to type /dusk to close the day out.")
                else:
                    # Only a hunt that happened is done. Declining is not: the
                    # meal below may refuse for want of the food, and the way
                    # out of that is the offer coming round again on the next
                    # /dusk rather than being remembered as settled.
                    done.add("hunt")
                    have, got, _ = moved(was)
                    if got > 0:
                        say = (f"{hunter} brings back {units(got)}"
                               + (f", so the party has {have[0]}." if have else "."))
                    else:
                        say = (f"{hunter} comes back empty-handed - the hunt "
                               f"brought in nothing tonight (r215b).")
                    tell(speaker, say)
                    told.append(say)

    if "eat" not in done:
        # r215d: in a town, castle or temple a meal can be bought instead of
        # eating stores, and hunting is prohibited. Which one is the player's
        # money, so it is the player's call.
        buy = ""
        if any(f in LODGING for f in feats):
            tell(speaker, "There is somewhere here to buy a meal. Buy tonight's "
                          "meals at a gold a head, or eat stores? (r215d)")
            try:
                buy = ask_one("  [buy/stores]", {"buy": "--buy", "stores": ""}, "")
            except Abandoned:
                return None
            speaker.stop()   # they have answered; the question can stop asking
        cmd = ["eat", "--hex", hexid] + ([buy] if buy else [])
        was = purse()
        ok, err = run_step(cmd, speaker, referee)
        if not ok:
            return blocked(day, cmd, err)
        done.add("eat")
        have, got, paid = moved(was)
        if paid:
            say = f"The meals are bought, {paid} gold."
        elif got < 0:
            say = f"The party eats {units(-got)}."
        else:
            say = "The party eats."
        if have:
            say += f" {units(have[0])} left, and {have[1]} gold."
        tell(speaker, say)
        told.append(say)

    if "pay" not in done:
        was = purse()
        ok, err = run_step(["pay"], speaker, referee)
        if not ok:
            return blocked(day, ["pay"], err)
        done.add("pay")
        have, _, paid = moved(was)
        # A party of one owes nobody, and "no wages were paid" every night for
        # seventy nights is noise. Only money that actually moved is reported.
        if paid:
            say = (f"Wages are paid, {paid} gold"
                   + (f", leaving {have[1]}." if have else "."))
            tell(speaker, say)
            told.append(say)

    if "lodge" not in done and any(f in LODGING for f in feats):
        was = purse()
        ok, err = run_step(["lodge"], speaker, referee)
        if not ok:
            return blocked(day, ["lodge"], err)
        done.add("lodge")
        have, _, paid = moved(was)
        say = (f"Rooms and stables for the night, {paid} gold"
               + (f", leaving {have[1]}." if have else "."))
        tell(speaker, say)
        told.append(say)

    run_bp(["time", "+1"], speaker, referee)
    if logged:
        told.append("The sheet already had " +
                    " and ".join(sorted(ALREADY[k] for k in logged)) +
                    " for today, so none of that was done twice.")
    checked = ("The e002 check was made." if north is not False else
               f"{hexid} is south of the Tragoth, so there was no e002 check.")
    # The walk now speaks its own results as they happen - the player hears what
    # the hunt brought in and what the meal cost while the dice are still in
    # their hand, rather than waiting for the narration. So this hands back what
    # they have already been told, marked as told: a narrator that repeats it
    # says the numbers twice, and one that is not given them contradicts them.
    return (f"Day {day} is closed out in hex {hexid}, and the date is now day "
            f"{day + 1}. {checked}\n\nThe player has already been shown and told "
            f"the following, in these words. Do not repeat it and do not "
            f"contradict it:\n"
            + "\n".join(f"  {t}" for t in told or ["Nothing was owed."])
            + f"\n\nTell the player what the new day looks like and ask for "
              f"their action.")


# --- loading a game -------------------------------------------------------

SAVE_ROW = re.compile(r"^\*?\s*(\S+)\s+day \d+ of\b", re.M)
# The party table is column-aligned and names contain spaces, so split on the
# gaps rather than on whitespace: "Cal Arath  player  8 ..." is one name, not two.
PARTY_ROW = re.compile(r"^\s+(\S.*?)\s{2,}(player|follower|mount)\b", re.M)


def roster() -> tuple[str | None, list[str]]:
    """(the player's own name, everyone else travelling with them).

    Read rather than described: told only that a table exists, the model called
    the Prince his own companion - "your companion Cal Arath stands ready beside
    you" - because it never looked at the `kind` column.
    """
    p = subprocess.run([*BP, "party"], capture_output=True, text=True)
    rows = PARTY_ROW.findall(p.stdout + p.stderr)
    player = next((n for n, k in rows if k == "player"), None)
    return player, [f"{n} ({k})" for n, k in rows if k != "player"]


def saved_games() -> tuple[list[str], str]:
    """The save names, and the listing bp printed for them.

    `bp game list` writes to stderr like every sheet command, and marks the
    current game with a leading `*` - which is a column, not part of the name.
    """
    p = subprocess.run([*BP, "game", "list"], capture_output=True, text=True)
    listing = (p.stdout + p.stderr).rstrip()
    # Match the shape of a save row rather than "the first word", so that bp's
    # own "no games recorded. bp game new <name> ..." is not read as a game
    # called "no".
    names = SAVE_ROW.findall(listing)
    return names, listing


def greeting(referee: bool) -> str:
    """What the player sees before anything else: where they left off, and how
    to pick up or begin."""
    names, listing = saved_games()
    out = [f"{ASIDE}Barbarian Prince{RESET}",
           f"{DIM}{MODEL} via Ollama{' - referee view' if referee else ''}{RESET}",
           ""]

    choices = [("/start", "begin a new game")]
    if names:
        out += [f"{ASIDE}Saved games:{RESET}", listing, ""]
        # Name a real save rather than a placeholder: the player can copy the
        # line as printed instead of working out what goes in the angle brackets.
        choices.append((f"/resume {names[0]}",
                        "continue that game" if len(names) == 1
                        else "or any other name above"))
    else:
        out += [f"{DIM}No saved games yet.{RESET}", ""]
    choices.append(("/help", "every command"))

    pad = max(len(c) for c, _ in choices)
    out += [f"  {ASIDE}{c:<{pad}}{RESET}  {what}" for c, what in choices]
    return "\n".join(out)


def run_load(name: str, speaker: Speaker, referee: bool) -> str | None:
    """Switch to a save and tell the narrator where it has just arrived."""
    names, listing = saved_games()

    if not name:
        if not names:
            print(f"{ASIDE}No saved games. /start begins one.{RESET}")
        else:
            print(f"{ASIDE}Saved games:{RESET}\n{listing}")
            print(f"{ASIDE}Resume one with /resume <name>.{RESET}")
        return None

    p = subprocess.run([*BP, "game", "use", name], capture_output=True, text=True)
    if p.returncode != 0:
        print(f"{ASIDE}{(p.stderr or p.stdout).strip()}{RESET}")
        return None

    # Hand the model the sheet rather than a claim about it: it is about to
    # narrate a game it has no memory of, and every number must come from disk.
    #
    # The brief line plus the party, not the full sheet. `bp game` ends with the
    # band sizes rolled today - enemies - and a model reading straight through
    # turned "e002 guardsmen: 4 mercenary guardsmen" into four loyal companions.
    # Saying "these are enemies, not companions" did not fix it; removing them
    # did. They are not needed here anyway: bp remembers a band's size itself and
    # replays it whenever the section is read again, which is the whole point of
    # recording it. Ambiguity the model cannot resolve is better deleted than
    # annotated.
    state = subprocess.run([*BP, "game", "-b"], capture_output=True, text=True)
    player, others = roster()
    if referee:
        print(f"{DIM}$ bp game use {name}{RESET}")
    print(f"{ASIDE}Loaded {name}.{RESET}")
    who = (f"The player IS {player}, the Barbarian Prince - not a companion of "
           f"his. " if player else "")
    who += (f"Travelling with him: {', '.join(others)}." if others
            else "He travels alone; there is nobody else in the party.")
    return (f"The player has loaded the saved game '{name}'. You have no memory "
            f"of it and must not invent any part of it - no companions, no "
            f"places, no events that are not written below. Greet them briefly, "
            f"say where they are and what day it is, and ask what they want to "
            f"do.\n\n"
            f"{who}\n\n"
            f"{(state.stdout + state.stderr).strip()}")


# --- looking something up -------------------------------------------------
#
# The narrator is not in this one. A player who wants to hear r220 again is
# reading the book, not asking a question, and routing that through the model
# only risks a paraphrase of a passage bp can deliver word for word. So bp
# prints it, the speaker says it, and the model is told afterwards what the
# player has just seen so that it does not read it back to them.


def run_lookup(ids: list[str], speaker: Speaker, referee: bool) -> str | None:
    """Read sections out on the player's say-so. Returns a note for the model."""
    if not ids:
        print(f"{ASIDE}/lookup <section> - r220, e052, e001#premise. Several at "
              f"once is fine.{RESET}")
        return None
    if any(i.startswith("-") for i in ids):
        # --raw is the source layout: tables, ids, cross-references. It exists to
        # adjudicate with, and it is the one thing that must never be spoken.
        print(f"{ASIDE}/lookup takes section ids, not flags.{RESET}")
        return None

    out = json.loads(run_bp(["show", *ids], speaker, referee))
    if not out["stdout"].strip():
        print(f"{ASIDE}{out['stderr'].strip() or 'nothing to read there'}{RESET}")
        return None
    return (f"The player looked up {', '.join(ids)} for themselves. bp printed it "
            f"and it was read aloud to them just now - they were reading, not "
            f"asking you anything, so do not repeat it, summarise it or act on it "
            f"unless they say something next. It is below only so that you know "
            f"what they have seen.\n\n{out['stdout'].strip()}")


# --- a fight, rolled out --------------------------------------------------
#
# `bp fight quick` wants both sides on one command line, which is a poor thing
# to type at a prompt, so the typing happens a line at a time here: a name and
# its numbers, blank line when the side is done. Nothing is read from the sheet
# and nothing is written to it - this is the fight you want to see resolved,
# not the one being recorded.

INT = re.compile(r"\d+")


class Abandoned(Exception):
    """The player backed out of the questions rather than answering them."""


def ask(prompt: str) -> str:
    try:
        said = input(prompt).strip()
    except EOFError:
        raise Abandoned
    if said.lower() in ("/quit", "/exit", "/cancel", "/stop"):
        raise Abandoned
    return said


def fighter(said: str, most: int) -> list[str] | None:
    """'Cal Arath 8 9 2' -> ['Cal Arath', '8', '9', '2'].

    Read from the right: the trailing numbers are the numbers and whatever comes
    before them is the name, so a name with a space in it needs no quoting.
    """
    parts = said.split()
    nums: list[str] = []
    while parts and len(nums) < most and INT.fullmatch(parts[-1]):
        nums.insert(0, parts.pop())
    if not parts or len(nums) < 2:
        return None
    return [" ".join(parts), *nums]


def ask_side(what: str, fields: str, example: str, most: int) -> list[list[str]]:
    """Collect one side of the fight, one line each. Raises Abandoned."""
    print(f"\n{ASIDE}{what}{RESET}\n{DIM}  name, {fields} - e.g. \"{example}\", "
          f"and a blank line when that is everyone{RESET}")
    got: list[list[str]] = []
    while True:
        said = ask(f"  {ASIDE}>{RESET} ")
        if not said:
            if got:
                return got
            print(f"{DIM}  nobody yet - /cancel to call the fight off{RESET}")
            continue
        one = fighter(said, most)
        if not one:
            print(f"{DIM}  a name, and then {fields}{RESET}")
            continue
        got.append(one)
        print(f"{DIM}  {one[0]}: {', '.join(one[1:])}{RESET}")


def ask_one(prompt: str, answers: dict[str, str], default: str) -> str:
    """One question with fixed answers, matched on any prefix. Raises Abandoned."""
    while True:
        said = ask(f"\n{ASIDE}{prompt}{RESET} ").lower()
        if not said:
            return default
        for word, value in answers.items():
            if word.startswith(said):
                return value
        print(f"{DIM}  {', '.join(answers)}, or enter{RESET}")


def run_fight(speaker: Speaker, referee: bool) -> str | None:
    """Ask for both sides, roll the fight out, hand the result to the narrator."""
    print(f"\n{ASIDE}A fight rolled out by code (r220).{RESET} {DIM}Both sides are "
          f"typed here: nothing is read from the sheet and nothing is written to "
          f"it.{RESET}")
    try:
        us = ask_side("Your side - the first one named is the Prince.",
                      "combat skill, endurance, and wounds already taken",
                      "Cal Arath 8 9", 3)
        them = ask_side("The enemy.",
                        "combat skill, endurance, wealth code, how many of them",
                        "Goblin 4 5 3 9", 4)
        first = ask_one("Who strikes first each round - us or them? (r220a) [US/them]",
                        {"us": "us", "them": "them"}, "us")
        surprise = ask_one("Surprise, one free strike before the rounds? (r220d) "
                           "[NO/us/them]",
                           {"no": "", "us": "us", "them": "them"}, "")
        rout = ask_one("Rout check on every round you kill one? (r220f) [y/N]",
                       {"yes": "--rout", "no": ""}, "")
    except Abandoned:
        print(f"\n{ASIDE}No fight.{RESET}")
        return None

    cmd = ["fight", "quick"]
    for one in us:
        cmd += ["--us", *one]
    for one in them:
        cmd += ["--them", *one]
    cmd += ["--first", first]
    if surprise:
        cmd += ["--surprise", surprise]
    if rout:
        cmd.append(rout)

    out = json.loads(run_bp(cmd, speaker, referee))
    if out["exit"] != 0:
        print(f"{ASIDE}{out['stderr'].strip()}{RESET}")
        return None

    ours = ", ".join(one[0] for one in us)
    theirs = ", ".join(one[0] for one in them)
    return (f"The player asked for a fight to be rolled out and it is done: "
            f"{ours} against {theirs}. The strike log below was printed for them "
            f"but NOT read aloud, so tell them what happened - who fell, who is "
            f"still standing and what it cost - in a few sentences. Every number "
            f"in it was typed in for this fight and none of it is on the "
            f"character sheet, so do not record wounds or treasure unless they "
            f"ask you to.\n\n{out['stdout'].strip()}")


COMMANDS = {
    "/start": "begin a new game (walks setup in order)",
    "/load": "/load <name> to resume a save, /load alone to list them",
    "/lookup": "/lookup r220 - read a section of the book aloud, no narration",
    "/fight": "roll a whole fight out: you type both sides, nothing is saved",
    "/dusk": "close out the day: e002, the hunt, the meal, wages, lodging, the date",
    "/referee": "toggle showing the tool calls and bp's stderr",
    "/help": "this list",
    "/quit": "stop",
}


# --- the loop -------------------------------------------------------------

MAX_TOOL_ROUNDS = 12  # a turn that never stops calling tools is a bug, not a game

# A small model will often write the command instead of calling it - "Please
# execute: `bp show e001#premise`" - which hands the player a chore and leaves
# the prose undelivered. That is recoverable without prompting harder: notice
# the named command, say so, and let it try again. Nudging beats pleading.
NAMED_COMMAND = re.compile(r"`?\bbp\s+([a-z]+(?:\s+[^\s`\n]+){0,5})`?")
# The sheet commands that a narrator mentions legitimately - "you have food for
# three more days" cites `bp food` without owing the player a call. `time`,
# `eat`, `pay` and `lodge` are deliberately not on this list: writing one of
# those and not calling it is the end of the day never happening, which is the
# failure the nudge exists for.
SKIP_NUDGE = re.compile(r"\bbp\s+(game|party|food|gold|foe)\b")


def named_but_uncalled(text: str) -> str | None:
    """A bp command the model wrote out rather than invoking, if any."""
    m = NAMED_COMMAND.search(text or "")
    if not m or SKIP_NUDGE.search(m.group(0)):
        return None  # sheet commands are often mentioned legitimately in prose
    return m.group(1).strip()


def is_dusk(args: list[str]) -> bool:
    """Is this tool call an attempt to close the day out?

    `bp time +1` is the documented way and the model reaches for it, but a model
    told "the dusk checks" will also just call `["dusk"]` - which bp has no
    command for, so argparse rejects it and the day quietly never ends. Since
    dusk is the one thing here that is not a bp command at all, accept the
    obvious names for it rather than failing on a spelling.
    """
    first = args[:1]
    if first == ["time"] and args[1:2] in (["+1"], ["1"]):
        return True
    joined = " ".join(a.lower() for a in args)
    return joined in ("dusk", "endday", "end day", "day end", "evening",
                      "nightfall", "time dusk")


def take_turn(messages: list[dict], speaker: Speaker, referee: bool):
    """Run one player turn: model, tools, model, ... until it speaks."""
    delivered = ""   # everything bp printed this turn, for the invention check
    nudged = False   # at most one "you named it but did not call it" per turn
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            reply = stream_chat(messages, speaker)
        except urllib.error.URLError as e:
            print(f"{ASIDE}Cannot reach Ollama at {OLLAMA_URL} ({e.reason}).\n"
                  f"Is `ollama serve` running?{RESET}")
            return
        messages.append(reply)

        for span in unsourced_quotes(reply["content"], delivered):
            print(f"{ASIDE}[!] the narrator quoted text that bp never printed - "
                  f"treat it as invented, not as the book:\n    "
                  f"\"{span[:120]}...\"{RESET}")

        if not reply["tool_calls"]:
            missed = named_but_uncalled(reply["content"])
            if missed and not nudged:
                nudged = True  # once per turn, so this cannot become a loop
                if referee:
                    print(f"{DIM}[nudge] named `bp {missed}` without calling "
                          f"it{RESET}")
                messages.append({
                    "role": "user",
                    "content": (
                        f"You wrote out `bp {missed}` instead of calling it. The "
                        f"player cannot run commands and has heard nothing. Call "
                        f"the bp tool with those arguments now, then narrate."),
                })
                continue
            return
        for call in reply["tool_calls"]:
            fn = call.get("function") or {}
            args = (fn.get("arguments") or {}).get("args") or []
            if isinstance(args, str):        # some models emit a bare string
                args = args.split()
            args = [str(a) for a in args]
            if is_dusk(args):
                summary = run_dusk(speaker, referee)
                messages.append({"role": "tool", "tool_name": "bp", "content":
                                 json.dumps({"stdout": "", "stderr": summary or
                                             "the day was not closed out", "exit": 0})})
                continue
            result = run_bp(args, speaker, referee)
            messages.append({"role": "tool", "tool_name": "bp",
                             "content": result})
            delivered += json.loads(result).get("stdout", "")

    print(f"{ASIDE}[the model called tools {MAX_TOOL_ROUNDS} times without "
          f"speaking; stopping the turn]{RESET}")


def main() -> int:
    global MODEL
    ap = argparse.ArgumentParser(prog="play",
                                description=__doc__.split("\n")[0])
    ap.add_argument("--referee", action="store_true",
                    help="show the tool calls and bp's stderr")
    ap.add_argument("--quiet", action="store_true", help="no speech")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    if not (ROOT / "data" / "sections.json").exists():
        print("data/sections.json is missing - run python3 src/extract.py "
              "first (see the README).", file=sys.stderr)
        return 1

    MODEL = args.model
    speaker = Speaker(enabled=not args.quiet)
    messages = [{"role": "system", "content": system_prompt()}]

    print(greeting(args.referee))

    try:
        while True:
            try:
                said = input(f"\n{ASIDE}> {RESET}").strip()
                speaker.stop()
            except EOFError:
                print()
                break
            if said in ("/quit", "/exit"):
                break
            if not said:
                continue  # before anything reads said.split()[0]
            if said.split()[0] in ("/load", "/resume", "/saves"):
                summary = run_load(" ".join(said.split()[1:]), speaker, args.referee)
                if summary:
                    messages.append({"role": "user", "content": summary})
                    take_turn(messages, speaker, args.referee)
                continue
            if said.split()[0] in ("/lookup", "/show", "/read"):
                # No turn is taken: the player is reading, not asking. The note
                # rides along on their next message instead.
                note = run_lookup(said.split()[1:], speaker, args.referee)
                if note:
                    messages.append({"role": "user", "content": note})
                continue
            if said.split()[0] in ("/fight", "/combat"):
                if said.split()[1:]:
                    print(f"{ASIDE}/fight takes no arguments - both sides are "
                          f"asked for below.{RESET}")
                summary = run_fight(speaker, args.referee)
                if summary:
                    messages.append({"role": "user", "content": summary})
                    take_turn(messages, speaker, args.referee)
                continue
            if said == "/help":
                for name, what in COMMANDS.items():
                    print(f"{ASIDE}  {name:<10}{RESET} {what}")
                continue
            if said == "/dusk":
                summary = run_dusk(speaker, args.referee)
                if summary:
                    messages.append({"role": "user", "content": summary})
                    take_turn(messages, speaker, args.referee)
                continue
            if said in ("/start", "/new"):
                summary = run_setup(speaker, args.referee)
                if summary:
                    messages.append({"role": "user", "content": summary})
                    take_turn(messages, speaker, args.referee)
                continue
            if said == "/referee":
                args.referee = not args.referee
                print(f"{DIM}referee view {'on' if args.referee else 'off'}{RESET}")
                continue
            if said.startswith("/"):
                print(f"{ASIDE}No such command: {said.split()[0]}. "
                      f"Try /help.{RESET}")
                continue
            messages.append({"role": "user", "content": said})
            take_turn(messages, speaker, args.referee)
    except KeyboardInterrupt:
        print()
    finally:
        speaker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
