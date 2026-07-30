#!/usr/bin/env python3
"""Stop hook: read the narrator's last message aloud.

There is no `bp speak <id>` any more. `bp show` prints the prose a DM would say,
the narrator relays it, and this hook speaks whatever was said - so the player
hears exactly what is on screen, including the questions, the dusk checklist and
the rulings, none of which a section-reading command could ever have covered.

Voice is a preference, not a feature: with BP_TTS=off, or no backend reachable,
this exits 0 and says nothing. It must never be the reason a turn fails.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def last_assistant_text(transcript: Path) -> str:
    """The text blocks of the most recent assistant message in the transcript.

    Read backwards: the file is the whole session, and only the last message is
    new. Malformed lines are skipped rather than fatal - a half-written line at
    the tail is normal while a session is live.
    """
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            said = [b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"]
            if any(s.strip() for s in said):
                return "\n".join(said)
            # A message that was pure tool calls has nothing to say; keep looking
            # back would re-read the previous turn, so stop here.
            return ""
    return ""


# Markdown is for the eye. Commands, ids and table pipes read as noise, and the
# whole point of this hook is that what is heard matches what a DM would say.
STRIP = [
    (re.compile(r"```.*?```", re.S), " "),        # fenced code
    (re.compile(r"`[^`]*`"), " "),                # inline code: ./bp move 1017
    (re.compile(r"^\s*#{1,6}\s*", re.M), ""),     # headings
    (re.compile(r"^\s*>\s?", re.M), ""),          # block quotes
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),      # bullets
    (re.compile(r"^\s*\|.*$", re.M), ""),         # table rows
    (re.compile(r"^\s*[-=|:]{3,}\s*$", re.M), ""),  # rules and table dividers
    (re.compile(r"\*\*|__|\*|_~"), ""),           # emphasis
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),  # links keep their text
]


def spoken(text: str) -> str:
    for pat, rep in STRIP:
        text = pat.sub(rep, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0

    path = payload.get("transcript_path")
    if not path:
        return 0
    text = spoken(last_assistant_text(Path(path).expanduser()))
    if not text:
        return 0

    # A new turn supersedes the last one: stop reading the previous answer rather
    # than queueing behind it, or the voice falls a turn behind the game.
    subprocess.run(["pkill", "-x", "afplay"], capture_output=True)

    bp = ROOT / "bp"
    try:
        subprocess.run([str(bp), "say", "--stdin"], input=text, text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=600)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
