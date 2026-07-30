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

    python3 procedures.py                 # start playing
    python3 procedures.py --referee       # also show stderr and the tool calls
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

# Sentence-ish boundary. Speaking a sentence at a time is what lets the voice
# start while the model is still generating, instead of after the full turn.
SENTENCE_END = re.compile(r"(?<=[.!?:])\s+")

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
        print(f"\n{PROSE}{p.stdout.rstrip()}{RESET}\n")
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
        "options": {"num_ctx": NUM_CTX},
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})

    content, tool_calls, buf, spoke = [], [], "", False
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
                print(piece, end="", flush=True)
                content.append(piece)
                buf += piece
                # Speak on sentence boundaries so the voice keeps pace with the
                # text rather than waiting for the turn to finish.
                parts = SENTENCE_END.split(buf)
                if len(parts) > 1:
                    for sentence in parts[:-1]:
                        speaker.say(sentence)
                    buf = parts[-1]

            if chunk.get("done"):
                break

    if buf.strip():
        speaker.say(buf)
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
            m = TREASURE_OUT.search(json.loads(out)["stdout"].strip())
            gold = int(m.group(1)) if m else None
            if gold is None:
                print(f"{ASIDE}  could not read the gold off the treasure "
                      f"table - enter it yourself below{RESET}")

        elif step["id"] == "hex":
            roll = ask_die(step["prompt"], step["die"])
            if roll is None:
                return None
            out = run_bp(["start", str(roll)], speaker, referee)
            m = HEX_IN.search(json.loads(out)["stdout"])
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
            result = run_bp([str(a) for a in args], speaker, referee)
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

    print(f"{ASIDE}Barbarian Prince - {MODEL} via Ollama"
          f"{' (referee view)' if args.referee else ''}\n"
          f"/start to begin a new game, /quit to stop.{RESET}")

    try:
        while True:
            try:
                said = input(f"\n{ASIDE}> {RESET}").strip()
            except EOFError:
                print()
                break
            if said in ("/quit", "/exit"):
                break
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
            messages.append({"role": "user", "content": said})
            take_turn(messages, speaker, args.referee)
    except KeyboardInterrupt:
        print()
    finally:
        speaker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
