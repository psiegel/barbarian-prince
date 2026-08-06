"""Parse the die-roll tables out of section bodies.

Three shapes occur in the game text:

  options   - an option matrix: columns are choices (talk / evade / fight),
              rows are die results.  e003, e006, e007, ...
  inline    - a run-on list, "roll two dice: 2-e133; 3-e135; ...".  r208, r226
  table     - one or more labelled 2d6 result tables.  r209, r211, r212

The awkward one is `table`: pdftotext renders a vertically centred cell with the
die number on the *middle* line of the wrapped text, so the number is not a
reliable line-start delimiter.  Each text line is therefore assigned to whichever
number line is nearest, which reconstructs the original cells.
"""

import re

# Header of an option matrix, e.g. "die roll*   talk   **evade   fight".
OPT_HEADER_RE = re.compile(r"^\s*die\s+rolls?(\*{0,3})\s+(\S.*)$", re.IGNORECASE)
# A row of an option matrix: leading die number, then the cells.
OPT_ROW_RE = re.compile(r"^\s*(\d{1,2})\s\s+(\S.*)$")
# Footnote under a table, e.g. "*  <condition affecting this column>".
FOOTNOTE_RE = re.compile(r"^\s*(\*{1,3}|†|‡)\s*(\S.*)$")
# "2-e133", "3 - e135", and the open-ended "2(or less)-r310" / "12(or more)-r300".
INLINE_RE = re.compile(
    r"\b(\d{1,2})\s*(\((?:or\s+)?(?:less|more)\))?\s*[-–]\s*([re]\d{3})\b"
)

# A die key at the start of a table row. The game writes these many ways:
# "2", "6,7,8", "11+", "4-5", "2 thru 8", "7 (or less)", "12 (or more)".
KEY_BODY = r"\d{1,2}(?:\s*(?:,|-|–|thru|through|to)\s*\d{1,2})*"
KEY_TAIL = r"(?:\s*\((?:or\s+)?(?:less|more)\)|\s+or\s+(?:less|more)|\+)?"
KEY_RE = re.compile(rf"^({KEY_BODY}{KEY_TAIL})$", re.IGNORECASE)
# Same, anchored at the start of an indented row, with the result text after it.
ROW_RE = re.compile(
    rf"^\s{{2,}}({KEY_BODY}{KEY_TAIL})\s+(?=[A-Za-z\"'(])(.*)$", re.IGNORECASE
)
# A key on its own line (the vertically centred case).
KEY_ONLY_RE = re.compile(rf"^\s{{2,}}({KEY_BODY}{KEY_TAIL})\s*$", re.IGNORECASE)


def expand_key(key: str, die: str = "2d6") -> list[int]:
    """Turn a die key into the list of rolls it covers.

    "6,7,8" -> [6,7,8];  "4-5" -> [4,5];  "2 thru 8" -> [2..8];
    "11+" and "12 (or more)" -> up to the die maximum;  "7 (or less)" -> down
    to the minimum.
    """
    lo, hi = (2, 12) if die == "2d6" else (1, 6)
    k = key.strip().lower()
    open_up = bool(re.search(r"\+|\(?\s*or\s+more\s*\)?", k))
    open_down = bool(re.search(r"\(?\s*or\s+less\s*\)?", k))
    nums = [int(n) for n in re.findall(r"\d{1,2}", k)]
    if not nums:
        return []
    if open_up:
        return list(range(min(nums), max(hi, max(nums)) + 1))
    if open_down:
        return list(range(min(lo, min(nums)), max(nums) + 1))
    if re.search(r"-|–|thru|through|\bto\b", k) and len(nums) >= 2:
        return list(range(min(nums), max(nums) + 1))
    return sorted(set(nums))


# Label introducing a sub-table, e.g. "Seeking an Audience at any Town:".
LABEL_RE = re.compile(r"^(Seeking [^:]*|[A-Z][^:]{6,60}):?\s*$")

DIE_HINT = [
    (re.compile(r"roll\s+two\s+dice|roll\s+2\s*d\s*6", re.I), "2d6"),
    (re.compile(r"roll\s+one\s+die|roll\s+a\s+die|roll\s+1\s*d\s*6", re.I), "1d6"),
]


def guess_die(text: str, keys) -> str:
    """Infer the dice to roll, preferring an explicit instruction in the prose."""
    for pat, die in DIE_HINT:
        if pat.search(text):
            return die
    nums = [int(k.split(",")[0].rstrip("+")) for k in keys if k[:1].isdigit()]
    if nums and max(nums) > 6:
        return "2d6"
    return "1d6"


def split_cells(line: str, n_cols: int, offsets: list[int]) -> list[str]:
    """Split a table row into cells.

    Two runs of spaces reliably separate cells (cell text uses single spaces),
    so try that first; fall back to slicing at the header's column offsets when
    a cell is missing or has run into its neighbour.
    """
    parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
    if len(parts) == n_cols:
        return parts
    cells = []
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(line)
        cells.append(line[start:end].strip())
    return cells


def parse_options(lines: list[str]) -> dict | None:
    """Parse an option matrix (choice columns x die-roll rows)."""
    for i, line in enumerate(lines):
        m = OPT_HEADER_RE.match(line)
        if not m:
            continue
        die_note, rest = m.group(1), m.group(2)

        # Column names, with the offset of each within the raw header line.
        names, offsets = [], []
        for cm in re.finditer(r"(\*{0,3}\s?)([A-Za-z][A-Za-z ]*?)(?=\s{2,}|\s*$)", rest):
            marker = cm.group(1).replace(" ", "")
            name = cm.group(2).strip()
            if not name:
                continue
            names.append({"name": name.lower(), "note": marker or None})
            offsets.append(line.index(rest) + cm.start())
        if len(names) < 2:
            continue

        rows, footnotes = {}, {}
        last_note = None
        for raw in lines[i + 1:]:
            if not raw.strip():
                continue
            fm = FOOTNOTE_RE.match(raw)
            rm = OPT_ROW_RE.match(raw)
            if rm:
                cells = split_cells(raw, len(names) + 1, [0] + offsets)
                # First cell is the die number; drop it.
                cells = cells[1:] if len(cells) > len(names) else cells
                row = {}
                for col, cell in zip(names, cells):
                    cell = cell.strip(" .")
                    if cell and cell not in {"—", "-", "--"}:
                        row[col["name"]] = cell
                if row:
                    rows[rm.group(1)] = row
                last_note = None
            elif fm and fm.group(1) in {"*", "**", "***", "†", "‡"}:
                last_note = fm.group(1)
                footnotes[last_note] = fm.group(2).strip()
            elif last_note and raw.startswith(" "):
                # Footnotes wrap; keep appending until the next row or marker.
                footnotes[last_note] += " " + raw.strip()
        if not rows:
            continue
        return {
            "kind": "options",
            "die": "1d6" if max(int(k) for k in rows) <= 8 else "2d6",
            "rolls": {k: [int(k)] for k in rows},
            "die_note": die_note or None,
            "columns": names,
            "rows": rows,
            "footnotes": footnotes,
        }
    return None


# A compass list, "(1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW)". These sit inside the
# prose and must not be mistaken for the result list.
#
# The brackets are optional because e105a does without them - "instead of making
# your normal travel move: 1-N, 2-NE, 3-SE, 4-S, 5-SW, 6-NW, and then roll one
# die again" - and that list was being read as more rows of e105's table. Only
# rolls 4 and 5 got in, the others colliding with rows already found, which left
# e105 the one table in the game covering a die face twice.
#
# Three items are required before an unbracketed list counts. A real outcome can
# be a bare compass point, and a run has to be long enough that six of them in a
# row is a direction list rather than a table anyone would write.
DIRECTION_RE = re.compile(
    r"\(\s*(?:\d\s*-\s*[NSEW]{1,2}\s*,?\s*)+\)"
    r"|(?:\d\s*-\s*[NSEW]{1,2}\s*,\s*){2,}\d\s*-\s*[NSEW]{1,2}",
    re.IGNORECASE)
# The clause that introduces a result list, up to its colon.
INTRO_RE = re.compile(r"roll[^:;.]*?\bdi(?:e|ce)\b[^:\d]*:?", re.IGNORECASE)
# One "key - outcome" item within a result list.
#
# A comma is ambiguous here: it joins keys in "1,2-nothing" but separates items
# in "3-e043, 4-e044". Splitting on it therefore cannot work, so items are
# matched positionally instead - the key is greedy (taking "1,2" whole) and the
# outcome runs until the next key or a semicolon. The leading lookbehind stops a
# key being found inside a section id, where "e043, 4" would otherwise match.
#
# The sentence ends it too. Without that the run's last item has no next key and
# no semicolon to stop at, so it ran to the end of the section and took the prose
# after the table with it: e195's row 12 held the game's copyright credits, e068's
# held the whole of e068a, r331's three sentences of aftermath - 8 of the 30
# inline tables. A colon closes an item as surely as a full stop, because e068
# writes "5,6-see e068a in the paragraph below:" and the passage follows it.
#
# Only the sentence break counts, not a bare '.', so "wealth 110 (see r225)" and
# the like are untouched. Checked against every inline table: no item other than
# the ones this repairs contains one.
ITEM_RE = re.compile(
    rf"(?<![A-Za-z0-9])({KEY_BODY}{KEY_TAIL})\s*[-–]\s*"
    rf"(.+?)(?=\s*;|\s*,\s*{KEY_BODY}\s*[-–]|[.:](?:\s|$)|\s*$)",
    re.IGNORECASE,
)


def parse_inline(body: str) -> dict | None:
    """Parse a run-on list such as 'roll two dice: 2-e133; 3-e135; ...'.

    Outcomes are not always section references: the text also writes results
    like "6-nothing" or "8-wealth 110 (see r225)", and those still need to
    resolve, so the outcome is kept as free text with any section id noted.
    """
    text = DIRECTION_RE.sub(" ", re.sub(r"\s+", " ", body))
    if len(INLINE_RE.findall(text)) < 3:
        return None

    def items(segment: str) -> dict:
        found = {}
        for m in ITEM_RE.finditer(segment):
            key, outcome = m.group(1).strip(), m.group(2).strip(" .,")
            if not outcome:
                continue
            ref = re.match(r"^([re]\d{3})\b", outcome)
            found.setdefault(key, {"text": outcome, "goto": ref.group(1) if ref else None})
        return found

    # A section may roll more than once ("roll one die for direction... then
    # roll two dice to see what you find"), and only one of those clauses is
    # followed by the result list. Try each and keep whichever yields the most.
    candidates = [text[m.end():] for m in INTRO_RE.finditer(text)] or [text]
    results = max((items(c) for c in candidates), key=len, default={})

    if len(results) < 3:
        return None
    die = guess_die(body, results.keys())
    return {
        "kind": "inline",
        "die": die,
        "results": results,
        "rolls": {k: expand_key(k, die) for k in results},
    }


def is_open(text: str) -> bool:
    """True when a result's text is unfinished and may continue on the next line.

    A full stop ends it, and so does a trailing section reference: the text
    writes cells like "if you investigate see e009" with no closing period, and
    treating those as unfinished would swallow the following result.
    """
    t = text.rstrip()
    # A trailing bracket closes a parenthetical that rounds off the cell, as in
    # "(deflected in the skidding)".
    return not (
        t.endswith((".", "!", "?", ":", ")"))
        or re.search(r"\b[re]\d{3}$", t)
    )


def assign_to_numbers(block: list[tuple[int, str, str]]) -> dict:
    """Group (index, key, text) lines into cells.

    pdftotext centres a wrapped cell vertically on its die number, so text
    belonging to one result can sit *above* that result's number.  Proximity
    alone mis-assigns those lines, so completed sentences delimit cells: an
    unattached line continues the previous result only while that result's text
    is still mid-sentence, otherwise it belongs to the result below it.
    """
    results: dict[str, list[str]] = {}
    order: list[str] = []
    pending: list[str] = []   # orphan lines awaiting the next die number
    current: str | None = None

    def open_cell(key: str) -> None:
        nonlocal current
        if key not in results:
            results[key] = []
            order.append(key)
        current = key

    for _, key, text in block:
        if key:
            open_cell(key)
            results[key].extend(pending)
            pending = []
            if text:
                results[key].append(text)
        elif current and results[current] and is_open(results[current][-1]) \
                and (text[:1].islower() or text.startswith("(")):
            # The current result is unfinished and this line carries on from it
            # in lower case, so it is a continuation rather than a new cell.
            results[current].append(text)
        else:
            # Otherwise it is the start of the next result, whose die number
            # appears below it on the centred key line.
            pending.append(text)

    if pending and current:
        results[current].extend(pending)
    return {k: " ".join(results[k]).strip() for k in order if results[k]}


def split_embedded_keys(sub: dict, die: str) -> None:
    """Recover a result whose text was typeset inside the previous cell.

    In at least one table the compositor ran a row into the one above it, so the
    cell for an earlier roll ends with something like "<sentence>. 4-<text>" and
    die roll 4 has no row of its own. Where a key is missing from the die's range
    and some cell contains that key followed by a dash, the cell is split there.
    """
    lo, hi = (2, 12) if die == "2d6" else (1, 6)
    covered = {r for rolls in sub["rolls"].values() for r in rolls}
    for missing in sorted(set(range(lo, hi + 1)) - covered):
        for key, text in list(sub["results"].items()):
            m = re.search(rf"(?<=[.;])\s*{missing}\s*[-–]\s*(?=\S)", text)
            if not m:
                continue
            sub["results"][key] = text[: m.start()].strip()
            sub["results"][str(missing)] = text[m.end():].strip()
            sub["rolls"][str(missing)] = [missing]
            # Keep rows in die order so the recovered one is not left at the end.
            order = sorted(sub["rolls"], key=lambda k: min(sub["rolls"][k] or [99]))
            sub["results"] = {k: sub["results"][k] for k in order}
            sub["rolls"] = {k: sub["rolls"][k] for k in order}
            break


def parse_tables(lines: list[str]) -> dict | None:
    """Parse one or more labelled 2d6 result tables."""
    tables, block, label, note = [], [], None, []
    in_note = False

    def flush():
        nonlocal block, label, note
        results = assign_to_numbers(block)
        if results:
            tables.append({
                "label": label,
                "note": " ".join(note).strip() or None,
                "results": results,
            })
        block, label, note = [], None, []

    # The first cell of a table can begin *above* its die number, before any row
    # has been seen, so indented lines are held here until a number arrives.
    lead: list[tuple[int, str, str]] = []

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        # A row: die key in the left column, with or without text alongside.
        m = ROW_RE.match(raw) or KEY_ONLY_RE.match(raw)
        if m:
            in_note = False
            block.extend(lead)
            lead = []
            key = re.sub(r"\s+", " ", m.group(1).strip())
            text = m.group(2).strip() if m.lastindex and m.lastindex > 1 else ""
            block.append((idx, key, text))
            continue
        if line.startswith("Note:"):
            note.append(line)
            lead = []
            in_note = True
            continue
        # A note wraps across unindented lines until the table's rows begin.
        if in_note and not raw.startswith("  "):
            note.append(line)
            continue
        lm = LABEL_RE.match(line)
        if lm and (raw.startswith(" ") is False or line.endswith(":")):
            if block:
                flush()
            label = lm.group(1).strip()
            lead = []
            in_note = False
            continue
        # Indented continuation text belongs to the current cell.
        if block and raw.startswith("  "):
            block.append((idx, "", line))
        elif block:
            flush()
        elif raw.startswith("    "):
            lead.append((idx, "", line))
        else:
            # Unindented prose is not part of any table.
            lead = []

    if block:
        flush()
    if not tables:
        return None
    keys = [k for t in tables for k in t["results"]]
    text = "\n".join(lines)
    for t in tables:
        t["die"] = guess_die(text, t["results"].keys())
        t["rolls"] = {k: expand_key(k, t["die"]) for k in t["results"]}
        split_embedded_keys(t, t["die"])
    return {"kind": "table", "die": guess_die(text, keys), "tables": tables}


# Reference matrices, not roll-to-result tables: r207 is terrain x die, r220's
# combat table is strike-total x wounds, r226 is wealth code x die. They are read,
# never "resolved", so parsing them as roll tables would only mislead.
SKIP = {"r207", "r220", "r226"}


def parse_section(sec: dict) -> dict | None:
    """Pick whichever table shape a section uses, if any."""
    if sec["id"] in SKIP:
        return None
    lines = sec["body"].split("\n")
    return (
        parse_options(lines)
        or parse_tables(lines)
        or parse_inline(sec["body"])
    )
