#!/usr/bin/env python3
"""Extract Barbarian Prince rule/event sections from the source PDFs into JSON.

Reads the PDFs in pdfs/ via `pdftotext -layout` and writes:
  data/sections.json  - every r### / e### section, keyed by id
  data/travel.json    - the r207 travel table, r231-r280 refs, r230 raft table

Re-run this any time the PDFs change:  python3 src/extract.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tables as tables_mod

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "pdfs"
DATA_DIR = ROOT / "data"

# Which PDF supplies which sections. Order matters: earlier files win on conflict.
SOURCES = [
    ("rules", "barbarianprince_rules.pdf"),
    ("events", "barbarianprince_events.pdf"),
    ("travel", "barbarianprince_travel.pdf"),
]

# A section header sits at column 0: an id, whitespace, then a Title-Case title.
# This deliberately rejects wrapped prose lines that happen to start with an id
# (e.g. "r330 and subtract one (-1)...") and indented continuations ("  e028.").
HEADER_RE = re.compile(r"^([re])(\d{3})[ \t]+([A-Z(“\"'].*?)\s*$")

# Cross-references to other sections, used to build the link graph.
REF_RE = re.compile(r"\b([re])(\d{3})\b")

# Page furniture that pdftotext leaves behind.
NOISE_RE = re.compile(
    r"(Barbarian Prince Copyright|Word file provided by|^\s*EVENTS BOOKLET\s*$|^\s*\d{4}\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def pdf_text(path: Path) -> str:
    """Run pdftotext -layout and return the text, page breaks stripped."""
    out = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.replace("\f", "\n")


def clean_body(lines: list[str]) -> str:
    """Drop page furniture and collapse runs of blank lines."""
    kept = [ln.rstrip() for ln in lines if not NOISE_RE.search(ln)]
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # The source sets a lowercase L for the 1 in some ids: "el56" is e156.
    # Unambiguous (there is no r/e section below 001), so normalise it here.
    text = re.sub(r"\bel(\d{2})\b", r"e1\1", text)
    # Likewise a lowercase i for the r in one reference: "i309" is r309.
    text = re.sub(r"\bi(3\d{2})\b", r"r\1", text)
    # And a lowercase L for the 1 in a die-result key: "l-e034" is "1-e034".
    text = re.sub(r"(?<![A-Za-z0-9])l(\s*[-–]\s*[re]\d{3})", r"1\1", text)
    return text.strip()


def parse_sections(text: str, source: str) -> dict:
    """Split a booklet into sections, enforcing monotonically increasing ids.

    The monotonic rule is the second line of defence against false headers: a
    real header always advances the numbering within a booklet.
    """
    sections = {}
    current = None
    buf: list[str] = []
    last_num = -1

    for line in text.split("\n"):
        m = HEADER_RE.match(line)
        if m:
            letter, num, title = m.group(1), int(m.group(2)), m.group(3)
            # Titles are short labels, never full sentences of prose.
            looks_like_title = len(title) <= 70 and not title.endswith((".", ";", ","))
            if looks_like_title and num > last_num:
                if current:
                    sections[current["id"]] = current
                    current["body"] = clean_body(buf)
                sid = f"{letter}{num:03d}"
                current = {"id": sid, "title": title, "source": source, "body": ""}
                buf = []
                last_num = num
                continue
        if current:
            buf.append(line)

    if current:
        current["body"] = clean_body(buf)
        sections[current["id"]] = current

    return sections


def parse_travel(text: str) -> dict:
    """Parse the r207 terrain table, the r231-r280 ref matrix, and r230 raft."""
    terrain = {}
    # e.g. "Farmland  10+  8+  e009 r231 r232 r233 r234 r235  yes  yes"
    row_re = re.compile(
        r"^\s*([A-Z][A-Za-z ]+?)\s+(\d+\+|never)\s+(\d+\+|never)\s+"
        r"((?:[re]\d{3}\s+){6})(\*?no|yes|-)\s+(\*?no|yes|-)\s*$"
    )
    for line in text.split("\n"):
        m = row_re.match(line)
        if m:
            name, lost, event, refs, hunt, fodder = m.groups()
            terrain[name.strip().lower()] = {
                "name": name.strip(),
                "lost_on": lost,
                "event_on": event,
                "event_refs": refs.split(),
                "hunt": hunt.strip(),
                "fodder": fodder.strip(),
            }

    # e.g. "r231  e018  e018  e022  e022  e023  e130"
    refs = {}
    ref_re = re.compile(r"^\s*(r\d{3})\s+((?:[re]\d{3}\s*){6})$")
    for line in text.split("\n"):
        m = ref_re.match(line)
        if m:
            refs[m.group(1)] = m.group(2).split()

    # r230 raft: "2   e125" pairs on their own lines.
    raft = {}
    in_raft = False
    for line in text.split("\n"):
        if line.startswith("r230"):
            in_raft = True
            continue
        if in_raft:
            if line.startswith("r231"):
                break
            m = re.match(r"^\s*(\d{1,2})\s+([re]\d{3})\s*$", line)
            if m:
                raft[m.group(1)] = m.group(2)

    return {"terrain": terrain, "refs": refs, "raft_2d6": raft}


def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    all_sections: dict[str, dict] = {}
    travel_text = ""

    missing = [f for _, f in SOURCES if not (PDF_DIR / f).exists()]
    if missing:
        print(
            "The game booklets are not in pdfs/. They are copyright Reaper\n"
            "Miniatures and are not distributed with this repository.\n\n"
            "Download them (free) from the publisher-authorised site:\n"
            "  https://dwarfstar.brainiac.com/ds_barbarianprince.html\n\n"
            "Put these in pdfs/ and run this again:\n"
            + "".join(f"  {f}\n" for f in missing),
            file=sys.stderr,
        )
        return 1

    for source, filename in SOURCES:
        path = PDF_DIR / filename
        text = pdf_text(path)
        if source == "travel":
            travel_text = text
        found = parse_sections(text, source)
        for sid, sec in found.items():
            if sid not in all_sections:
                all_sections[sid] = sec

    # Repair the handful of typeset defects listed in errata.json before
    # anything downstream reads the text.
    errata_path = DATA_DIR / "errata.json"
    if errata_path.exists():
        fixes = json.loads(errata_path.read_text()).get("source_fixes", {})
        for sid, pairs in fixes.items():
            sec = all_sections.get(sid)
            if not sec:
                print(f"warning: source_fix for unknown section {sid}", file=sys.stderr)
                continue
            for find, replace, _why in pairs:
                if find not in sec["body"]:
                    print(f"warning: source_fix for {sid} no longer matches: "
                          f"{find[:40]!r}", file=sys.stderr)
                    continue
                sec["body"] = sec["body"].replace(find, replace, 1)

    # Build the cross-reference graph, excluding self-references.
    for sid, sec in all_sections.items():
        refs = {f"{a}{int(b):03d}" for a, b in REF_RE.findall(sec["body"])}
        sec["refs"] = sorted(r for r in refs if r != sid)

    ordered = {k: all_sections[k] for k in sorted(all_sections)}
    (DATA_DIR / "sections.json").write_text(json.dumps(ordered, indent=2) + "\n")

    travel = parse_travel(travel_text)
    (DATA_DIR / "travel.json").write_text(json.dumps(travel, indent=2) + "\n")

    tables = {}
    for sid, sec in ordered.items():
        parsed = tables_mod.parse_section(sec)
        if parsed:
            tables[sid] = parsed
    (DATA_DIR / "tables.json").write_text(json.dumps(tables, indent=2) + "\n")

    rules = [k for k in ordered if k.startswith("r")]
    events = [k for k in ordered if k.startswith("e")]
    print(f"sections: {len(ordered)}  ({len(rules)} rules, {len(events)} events)")
    print(f"travel:   {len(travel['terrain'])} terrains, "
          f"{len(travel['refs'])} refs, {len(travel['raft_2d6'])} raft rows")
    kinds = {}
    for t in tables.values():
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(f"tables:   {len(tables)}  {kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
