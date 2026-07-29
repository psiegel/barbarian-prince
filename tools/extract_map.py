#!/usr/bin/env python3
"""data/map-data.csv -> data/map.json, with the CSV audited on the way through.

The CSV is third-party data, not extracted from the booklets, so it gets checked
rather than trusted: every terrain and feature label must be one we recognise,
the coordinate columns must agree with the hex id, the grid must have no holes,
and every hex the booklet names by coordinate must match what the CSV says is
there. Anything that fails is reported and left marked in the output - nothing is
quietly corrected, because a wrong hex produces a wrong roll forever after.

Deliberate corrections live in data/map-fixes.json, the same way textual defects
live in data/errata.json.
"""

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CSV = DATA / "map-data.csv"

# "Hills / Badlands" is one terrain wearing a two-word name on the map legend, so
# it has to be recognised before any slash-splitting happens.
BADLANDS = "Hills / Badlands"
TERRAIN = {
    "open countryside": "countryside",
    "countryside": "countryside",
    "farmland": "farmland",
    "forest": "forest",
    "hills": "hills",
    "badlands": "hills",
    "mountains": "mountains",
    "mountain": "mountains",   # singular, once, at 1313
    "desert": "desert",
    "swamp": "swamp",
}
FEATURES = {"town", "temple", "castle", "ruins", "ruin", "oasis"}


def norm_terrain(raw: str) -> tuple[list[str], str | None]:
    """-> (terrain keys, complaint). A hex may legitimately straddle two."""
    text = raw.strip()
    if not text:
        return [], "no terrain given"
    parts = [p.strip() for p in text.replace(BADLANDS, "Hills").split("/")]
    out, bad = [], []
    for p in parts:
        key = TERRAIN.get(p.lower())
        (out if key else bad).append(key or p)
    seen = list(dict.fromkeys(out))
    if bad:
        return seen, f"unrecognised terrain {', '.join(repr(b) for b in bad)}"
    return seen, None


def norm_features(raw: str) -> tuple[list[str], str | None]:
    text = raw.strip()
    if not text:
        return [], None
    out, bad = [], []
    for p in (q.strip().lower() for q in text.split(",")):
        if not p:
            continue
        (out if p in FEATURES else bad).append("ruins" if p == "ruin" else p)
    if bad:
        return out, f"unrecognised feature {', '.join(repr(b) for b in bad)}"
    return out, None


def booklet_hexes(sections: dict) -> dict[str, set[str]]:
    """Hexes the rules name by coordinate - the only independent check available."""
    named: dict[str, set[str]] = {}
    for sid, sec in sections.items():
        for m in re.finditer(r"\((\d{4})\)", sec["body"]):
            named.setdefault(m.group(1), set()).add(sid)
        for m in re.finditer(r"\bhex(?:es)? (\d{4})\b", sec["body"]):
            named.setdefault(m.group(1), set()).add(sid)
    return named


STOPWORDS = {"the", "of", "in", "at", "to", "and", "or", "either", "if", "you",
             "return", "hex", "hexes", "ruins", "ruin", "castle", "temple", "town",
             "lord", "lady", "baron", "count", "your", "any", "from", "one", "such",
             "as", "between", "is", "are", "a", "an", "start", "with", "new", "for"}


def phrases_for(sections: dict, sids: set[str], hid: str) -> list[str]:
    """The words the booklet uses immediately before '(hexid)' or 'hex hexid'."""
    out = []
    for sid in sids:
        body = sections[sid]["body"]
        for m in re.finditer(rf"([A-Za-z'’\- ]{{3,40}}?)\s*\({hid}\)", body):
            out.append(m.group(1))
        for m in re.finditer(rf"([A-Za-z'’\- ]{{3,40}}?),?\s*in hex {hid}\b", body):
            out.append(m.group(1))
    return out


def check_place(hid: str, entry: dict, sections: dict, sids: set[str],
                where: str) -> list[str]:
    """Does the CSV agree with the name the booklet puts at this hex?"""
    phrases = phrases_for(sections, sids, hid)
    if not phrases:
        return []
    words = {w.lower().strip(".,'’") for p in phrases for w in p.split()}
    words -= STOPWORDS
    words = {w for w in words if len(w) > 2}
    if not words:
        return []
    csv_name = (entry.get("name") or entry.get("region") or "").lower()
    csv_words = {w.strip(".,'’") for w in csv_name.replace("'s", "").split()}
    if csv_name and (words & csv_words or
                     any(w[:5] in csv_name for w in words if len(w) > 4)):
        return []
    said = "; ".join(sorted(set(phrases))[:2]).strip()
    if not csv_name:
        return [f"{hid}: {where} names a place here (\"{said}\") but the CSV has "
                f"no feature - terrain {'/'.join(entry['terrain']) or '?'}"]
    return [f"{hid}: {where} says \"{said}\" but the CSV says "
            f"{entry.get('name') or entry.get('region')!r}"]


def main() -> int:
    if not CSV.exists():
        sys.exit(f"{CSV} is missing.")
    fixes_path = DATA / "map-fixes.json"
    fixes = json.loads(fixes_path.read_text()) if fixes_path.exists() else {}
    overrides = fixes.get("hexes", {})

    warnings: list[str] = []
    hexes: dict[str, dict] = {}

    with CSV.open() as fh:
        for n, row in enumerate(csv.reader(fh), 1):
            if n == 1 or not any(row):
                continue
            row = (row + [""] * 6)[:6]
            xs, ys, hid, terr, feat, name = (c.strip() for c in row)
            if not re.fullmatch(r"\d{4}", hid):
                warnings.append(f"line {n}: bad hex id {hid!r}")
                continue
            if xs.isdigit() and ys.isdigit() and f"{int(xs):02d}{int(ys):02d}" != hid:
                warnings.append(
                    f"{hid}: x,y columns say {int(xs):02d}{int(ys):02d} but the hex "
                    f"id says {hid} - the id is used")
            if hid in hexes:
                warnings.append(f"{hid}: appears twice in the CSV")

            terrain, t_bad = norm_terrain(terr)
            features, f_bad = norm_features(feat)
            for bad in (t_bad, f_bad):
                if bad:
                    warnings.append(f"{hid}: {bad}")
            entry = {"terrain": terrain, "features": features}
            if name:
                if features:
                    entry["name"] = name
                else:
                    # A label with no feature is a region caption (LLEWYLLA MOOR),
                    # not a place you can seek an audience in.
                    entry["region"] = name
            if hid in overrides:
                entry.update(overrides[hid])
                entry["corrected"] = True
            hexes[hid] = entry

    # Holes in the grid. Even columns are pushed down half a hex, so a stray row
    # 00 is worth naming rather than assuming.
    cols = sorted({h[:2] for h in hexes})
    rows_by_col = {c: sorted(int(h[2:]) for h in hexes if h[:2] == c) for c in cols}
    for c, rs in rows_by_col.items():
        gaps = [r for r in range(min(rs), max(rs) + 1) if r not in rs]
        if gaps:
            warnings.append(f"column {c}: no data for row(s) "
                            f"{', '.join(f'{g:02d}' for g in gaps)}")
    odd = {c: rs[0] for c, rs in rows_by_col.items() if rs[0] == 0}
    if odd and len(odd) < len(cols):
        warnings.append(
            f"only columns {', '.join(sorted(odd))} have a row 00; the other "
            f"{len(cols) - len(odd)} columns start at row 01")

    # Two places can't share a name, and a castle can't be in two hexes.
    by_name: dict[str, list[str]] = {}
    for hid, h in hexes.items():
        if h.get("name"):
            by_name.setdefault(h["name"].upper(), []).append(hid)
    for name, hids in sorted(by_name.items()):
        if len(hids) > 1:
            warnings.append(f"{name} is listed in {len(hids)} hexes: "
                            f"{', '.join(sorted(hids))}")

    # The one independent check on content: hexes the booklet names by coordinate.
    sections_path = DATA / "sections.json"
    if sections_path.exists():
        sections = json.loads(sections_path.read_text())
        for hid, sids in sorted(booklet_hexes(sections).items()):
            where = ", ".join(sorted(sids))
            if hid not in hexes:
                warnings.append(f"{hid} is named in {where} but is not in the CSV")
                continue
            warnings.extend(check_place(hid, hexes[hid], sections, sids, where))

    payload = {
        "_comment": ("Generated by tools/extract_map.py from data/map-data.csv, a "
                     "third-party transcription. Corrections in data/map-fixes.json "
                     "are applied and marked with \"corrected\": true. Rivers and "
                     "roads are NOT in this data - they live on hexsides and must "
                     "come from the player."),
        "hexes": hexes,
        "warnings": warnings,
    }
    (DATA / "map.json").write_text(json.dumps(payload, indent=2) + "\n")

    feats = sum(1 for h in hexes.values() if h["features"])
    print(f"hexes:    {len(hexes)} across {len(cols)} columns")
    print(f"features: {feats}")
    print(f"fixed:    {sum(1 for h in hexes.values() if h.get('corrected'))} "
          f"from map-fixes.json")
    if warnings:
        print(f"\n{len(warnings)} thing(s) to look at:")
        for w in warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
