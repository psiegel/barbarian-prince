#!/usr/bin/env python3
"""Play Barbarian Prince against a local model, with the narration spoken.

The point of this program is the routing. A model can be told to read a section
out and simply not do it - that is the failure this replaces. Here the model
never carries the booklet's prose at all: it asks for a `bp` command, this
program runs it, and this program decides what the player sees and hears.

    bp stdout   -> screen, speaker, and back to the model
    bp stderr   -> back to the model, and the screen only in referee mode
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
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import markup

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
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        while True:
            text = self.q.get()
            if text is None:
                return
            try:
                subprocess.run([*BP, "say", "--stdin"], input=text, text=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=600)
            except (OSError, subprocess.SubprocessError):
                pass  # voice is a preference; never let it end the game

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


def ask_die(prompt: str, die: str) -> int | None:
    """Ask the player for a roll. None if they abandon setup."""
    lo, hi = (1, 6) if die == "1d6" else (2, 12)
    while True:
        try:
            said = input(f"\n{ASIDE}{prompt}\n  roll {die} > {RESET}").strip()
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
            roll = ask_die(step["prompt"], step["die"])
            if roll is None:
                return None
            wits = max(roll, 2)  # r202: a 1 counts as 2
            print(f"{DIM}  wit & wiles {wits}"
                  f"{' (a 1 counts as 2)' if roll == 1 else ''}; "
                  f"{step['fixed']}{RESET}")

        elif step["id"] == "gold":
            roll = ask_die(step["prompt"], step["die"])
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
            roll = ask_die(step["prompt"], step["die"])
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
# The end of a day is six checks in a fixed order (procedures.json, `day`), and
# forgetting them is silent: nobody eats, the guard never rolls, and the date
# never moves. A narrator asked to remember them will sometimes narrate "we move
# to Day 2" having done none of it. So the code walks them, the same way it
# walks setup - the only judgment in the whole sequence is what to do when e002
# actually fires, and that is handed back.

SHEET_HEX = re.compile(r"\bhex (\d{4})\b")
SHEET_DAY = re.compile(r"\bday (\d+) of\b")
LODGING = ("town", "castle", "temple")
E002_BONUS = ("0101", "1501")   # Ogon and Weshor: the guard looks hardest there

# Which day's dusk has already been walked, so advancing time twice or calling
# /dusk again after an e002 fight does not re-roll the guard or re-feed anyone.
_done: dict[int, set[str]] = {}


def sheet(args) -> tuple[int, str] | None:
    """Day and hex, read back off the sheet rather than remembered."""
    p = subprocess.run([*BP, "game", "-b"], capture_output=True, text=True)
    both = p.stdout + p.stderr
    d, h = SHEET_DAY.search(both), SHEET_HEX.search(both)
    return (int(d.group(1)), h.group(1)) if d and h else None


def features(hexid: str) -> list[str]:
    path = ROOT / "data" / "map.json"
    if not path.exists():
        return []
    return (json.loads(path.read_text())["hexes"].get(hexid) or {}).get("features") or []


def run_dusk(speaker: Speaker, referee: bool) -> str | None:
    """Walk the end-of-day checks. Returns a summary for the model, or None."""
    now = sheet(None)
    if now is None:
        print(f"{ASIDE}No game is being tracked - nothing to close out.{RESET}")
        return None
    day, hexid = now
    done = _done.setdefault(day, set())
    feats = features(hexid)
    told = []

    if referee:
        print(f"{DIM}[dusk: day {day}, hex {hexid}, features {feats or 'none'}]{RESET}")

    # e002, north of the Tragoth: after events, before the meal. Whether the
    # party is north of it is not in the map data, so the player says.
    if "e002" not in done:
        bonus = 1 if hexid in E002_BONUS else 0
        prompt = (f"\n{ASIDE}End of day {day}. If you are north of the Tragoth, "
                  f"roll 1d6 for e002 (subtract 3"
                  f"{f', add 1 for {hexid}' if bonus else ''}).\n"
                  f"  1d6, or 's' if you are south of the river > {RESET}")
        while True:
            try:
                said = input(prompt).strip().lower()
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
            print(f"{DIM}  1-6, or 's'.{RESET}")

    if "eat" not in done:
        run_bp(["eat", "--hex", hexid], speaker, referee)
        done.add("eat")
        told.append("the party ate")

    if "pay" not in done:
        run_bp(["pay"], speaker, referee)
        done.add("pay")

    if "lodge" not in done and any(f in LODGING for f in feats):
        run_bp(["lodge"], speaker, referee)
        done.add("lodge")
        told.append("lodging was paid")

    run_bp(["time", "+1"], speaker, referee)
    return (f"Day {day} is closed out in hex {hexid}: the e002 check was made, "
            f"{', '.join(told) or 'nothing was owed'}, and the date is now day "
            f"{day + 1}. Tell the player what the new day looks like and ask for "
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


COMMANDS = {
    "/start": "begin a new game (walks setup in order)",
    "/load": "/load <name> to resume a save, /load alone to list them",
    "/dusk": "close out the day: e002, the meal, wages, lodging, the date",
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
SKIP_NUDGE = re.compile(r"\bbp\s+(game|party|food|gold|time|eat|lodge|pay|foe)\b")


def named_but_uncalled(text: str) -> str | None:
    """A bp command the model wrote out rather than invoking, if any."""
    m = NAMED_COMMAND.search(text or "")
    if not m or SKIP_NUDGE.search(m.group(0)):
        return None  # sheet commands are often mentioned legitimately in prose
    return m.group(1).strip()


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
            if args[:1] == ["time"] and args[1:2] in (["+1"], ["1"]):
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
            except EOFError:
                print()
                break
            if said in ("/quit", "/exit"):
                break
            if said.split()[0] in ("/load", "/resume", "/saves"):
                summary = run_load(" ".join(said.split()[1:]), speaker, args.referee)
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
            if not said:
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
