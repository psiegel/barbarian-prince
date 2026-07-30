"""Markdown the narrator wrote, turned into something a screen or a voice wants.

Models write markdown whether or not you ask them to, and asking costs prompt
budget we would rather spend on the rules. So it is handled here instead, twice
over, because the screen and the synth want opposite things:

    render(text)     markdown -> ANSI, for the terminal
    speakable(text)  markdown -> plain words, for the synth

The synth is the reason this exists. Kokoro reads `**Roll 1d6**` as "asterisk
asterisk roll one d six asterisk asterisk" - sometimes, not reliably, which is
worse than always. Emphasis has no spoken form, so it comes out entirely, while
an ordered list keeps its numbers: a player choosing between "1. Negotiate" and
"3. Fight" needs to hear which is which.

Order matters in both directions. Code spans go first so their contents are not
then italicised, and bold before italic so `**x**` is not read as an emphasised
`*x*`.
"""

import os
import re
import shutil

BOLD, ITALIC, DIM, UNDER, RESET = (
    "\033[1m", "\033[3m", "\033[2m", "\033[4m", "\033[0m")

ANSI = re.compile(r"\033\[[0-9;]*m")

FENCE = re.compile(r"```[^\n]*\n?.*?(?:```|\Z)", re.S)
CODE = re.compile(r"`([^`\n]+)`")
BOLD_RE = re.compile(r"\*\*(\S(?:[^*]*\S)?)\*\*|__(\S(?:[^_]*\S)?)__")
# Single markers only, and not touching whitespace, so "4 * 3" and a bare
# asterisk survive as themselves rather than swallowing the rest of the line.
ITALIC_RE = re.compile(r"(?<![*\w])\*(\S(?:[^*\n]*\S)?)\*(?!\*)"
                       r"|(?<![_\w])_(\S(?:[^_\n]*\S)?)_(?!_)")
HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.*?)[ \t]*#*$", re.M)
BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
ORDERED = re.compile(r"^([ \t]*)(\d+)[.)][ \t]+", re.M)
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
TABLE_ROW = re.compile(r"^[ \t]*\|.*$", re.M)
RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
QUOTE = re.compile(r"^[ \t]*>[ \t]?.*$", re.M)


def _common(text: str) -> str:
    """Constructs neither a screen nor a voice wants as written."""
    text = LINK.sub(r"\1", text)
    text = TABLE_ROW.sub("", text)
    text = RULE.sub("", text)
    return text


def render(text: str) -> str:
    """Markdown -> ANSI. Terminals do bold, italic and underline; the rest is
    turned into something that reads as itself, like a real bullet."""
    text = _common(text)
    text = FENCE.sub(lambda m: f"{DIM}{m.group(0)}{RESET}", text)
    text = CODE.sub(rf"{DIM}\1{RESET}", text)
    text = HEADING.sub(rf"{BOLD}\1{RESET}", text)
    text = BOLD_RE.sub(lambda m: f"{BOLD}{m.group(1) or m.group(2)}{RESET}", text)
    text = ITALIC_RE.sub(lambda m: f"{ITALIC}{m.group(1) or m.group(2)}{RESET}", text)
    text = BULLET.sub(r"\1• ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def speakable(text: str) -> str:
    """Markdown -> what a person would say.

    Code spans go entirely rather than being read: a command mid-sentence
    becomes a hole either way, and "backtick b p show e zero zero one" is the
    worse hole. Blockquotes go too - the narrator has no reason to quote the
    book, so a quote here is either a repeat of what was just spoken or an
    invention, and neither belongs in the player's ear.
    """
    text = FENCE.sub(" ", text)
    text = QUOTE.sub("", text)
    text = _common(text)
    text = CODE.sub(" ", text)
    text = HEADING.sub(r"\1.", text)
    text = BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    # A numbered choice keeps its number - the player answers with it. A bullet
    # has nothing to say, so the marker just goes.
    text = ORDERED.sub(r"\1\2, ", text)
    text = BULLET.sub(r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# --- wrapping -------------------------------------------------------------
#
# A terminal breaks at the column edge without caring where a word ends, so
# wrapping is the application's job. Two things make it more than a textwrap
# call: escape codes are zero-width but textwrap counts them, and the narration
# arrives in fragments, so the wrapper has to remember which column it left the
# cursor in or every segment restarts the line at zero.

MAX_WIDTH = 100   # long measures are hard to read however wide the window is

# A wrapped list item reads as two items unless the continuation is indented
# under the text, and a list of choices is the shape this sees most.
LIST_LEAD = re.compile(r"^[ \t]*(?:\d+[.)]|[-*+\u2022])[ \t]+")


def visible(text: str) -> int:
    """Length as the terminal sees it: escape codes take up no columns."""
    return len(ANSI.sub("", text))


def terminal_width() -> int:
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns,
               int(os.environ.get("BP_WIDTH") or MAX_WIDTH))


class Wrap:
    """Word-wraps a stream, carrying the cursor column between calls."""

    def __init__(self, width: int | None = None):
        self.width = width or terminal_width()
        self.col = 0
        # The hang belongs to the line, not the call: a streamed list item
        # arrives as several segments ("1. Rest:" then the sentence after it),
        # and only the first of them can see the marker.
        self.hang = 0

    def reset(self) -> None:
        """Say that something else has moved the cursor to a fresh line."""
        self.col = self.hang = 0

    def feed(self, text: str) -> str:
        out = []
        for i, line in enumerate(text.split("\n")):
            if i:
                out.append("\n")
                self.col = self.hang = 0
            out.append(self._line(line))
        return "".join(out)

    def _line(self, line: str) -> str:
        if not line.strip():
            return ""
        out = []
        if self.col == 0:
            # Keep a list item's indent; without it "1.  Negotiate" loses its
            # shape the moment it wraps.
            indent = line[:len(line) - len(line.lstrip(" \t"))]
            out.append(indent)
            self.col = len(indent)
            lead = LIST_LEAD.match(line)
            self.hang = visible(lead.group(0)) if lead else len(indent)
        for word in line.split():
            w = visible(word)
            if self.col == 0:
                pass                                  # first word of a line
            elif self.col + 1 + w <= self.width:
                out.append(" ")
                self.col += 1
            else:
                out.append("\n" + " " * self.hang)
                self.col = self.hang
            out.append(word)
            self.col += w                             # over-long words overhang
        return "".join(out)


def wrap(text: str, width: int | None = None) -> str:
    """Wrap a complete block that starts on a fresh line."""
    return Wrap(width).feed(text)
