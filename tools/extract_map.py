#!/usr/bin/env python3
"""data/map-data.csv -> data/map.json, with the CSV audited on the way through.

The CSV is third-party data, not extracted from the booklets, so it gets checked
rather than trusted: every terrain and feature label must be one we recognise,
the coordinate columns must agree with the hex id, the grid must have no holes,
and every hex the booklet names by coordinate must match what the CSV says is
there. Anything that fails is reported and left marked in the output - nothing is
quietly corrected, because a wrong hex produces a wrong roll forever after.

Rivers and roads get a stronger check than that. They describe hexsides rather
than hexes, so the CSV records each one twice, once from either side - and the
two copies have to agree. That makes the edge data self-checking without the map
being present at all, which is the only validation here that does not ultimately
rest on someone eyeballing a JPG.

Deliberate corrections live in data/map-fixes.json, the same way textual defects
live in data/errata.json. `--mirror` proposes the mechanical subset of them.
"""

import csv
import json
import re
import sys
from pathlib import Path

import play

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CSV = DATA / "map-data.csv"

# The order the six edge columns appear in, repeated for rivers then roads.
EDGES = ("NE", "SE", "S", "SW", "NW", "N")
OPPOSITE = {"N": "S", "S": "N", "NE": "SW", "SW": "NE", "NW": "SE", "SE": "NW"}

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


def norm_edges(cells: list[str]) -> list[str]:
    """The six river (or road) columns -> the edges that carry one."""
    return [e for e, c in zip(EDGES, cells) if c.strip()]


def check_edges(hexes: dict[str, dict]) -> tuple[dict, dict, list[str], list[str]]:
    """-> (skipped, contradicted, crossed, offboard).

    A river runs along a hexside and a road crosses one, so either way the mark
    describes an edge shared by two hexes and has to appear on both of them: a
    river on the S edge of 0101 is the N edge of 0102. Every edge is therefore
    recorded twice, which makes the transcription self-checking - and one-sided
    marks are the only class of error findable without the map itself.

    The two dicts are keyed (hex, kind) -> [(edge, hex that claims it)], and the
    split between them is what tells you how much to trust the claim:

    skipped       the hex carries no marks of that kind at all, so the row was
                  simply never entered and the neighbour is the only witness
    contradicted  the hex does carry other marks of that kind, so someone
                  recorded this edge and someone left it out deliberately or
                  by slip; only the map can say which

    `crossed` is a third case and a much sharper one: both hexes agree an edge
    is there and disagree about what it is, one saying river and the other road.
    Nothing on the map looks like that, so it is always a mark entered in the
    wrong block of six columns, and it says which hex to correct rather than
    only that something is wrong.
    """
    skipped: dict[tuple[str, str], list] = {}
    contradicted: dict[tuple[str, str], list] = {}
    crossed: list[str] = []
    offboard: list[str] = []
    other_kind = {"river": "road", "road": "river"}
    for hid in sorted(hexes):
        nb = play.neighbours(*play.parse_hex(hid))
        for kind in ("river", "road"):
            for edge in hexes[hid][kind]:
                other = nb.get(edge)
                back = OPPOSITE[edge]
                if other is None or other not in hexes:
                    offboard.append(f"{hid} {edge}: {kind} leaves the mapboard")
                elif back in hexes[other][kind]:
                    continue
                elif back in hexes[other][other_kind[kind]]:
                    if kind == "river":      # report the pair once, not twice
                        crossed.append(
                            f"{hid} {edge} / {other} {back} is the same hexside, "
                            f"but {hid} calls it a river and {other} a road")
                else:
                    bucket = contradicted if hexes[other][kind] else skipped
                    bucket.setdefault((other, kind), []).append((back, hid))
    return skipped, contradicted, crossed, offboard


def mirror(hexes: dict[str, dict], skipped: dict, fixes_path: Path) -> int:
    """Write the skipped edges into map-fixes.json for review.

    Only the skipped ones: where a hex has no marks of that kind, the single
    neighbour claiming the edge is uncontradicted evidence and copying it across
    restores what the transcription lost. Where a hex has other marks, the two
    entries genuinely disagree and guessing would bury the disagreement.
    """
    fixes = json.loads(fixes_path.read_text()) if fixes_path.exists() else {}
    out = fixes.setdefault("hexes", {})
    for (hid, kind), claims in sorted(skipped.items()):
        edges = sorted({e for e, _ in claims}, key=EDGES.index)
        out.setdefault(hid, {})[kind] = sorted(
            set(hexes[hid][kind]) | set(edges), key=EDGES.index)
    fixes["_mirrored"] = ("Entries added by `extract_map.py --mirror`: edges a "
                          "neighbour recorded and this hex did not. Verify against "
                          "the map before trusting them.")
    fixes_path.write_text(json.dumps(fixes, indent=2) + "\n")
    return len(skipped)


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


def main(argv: list[str]) -> int:
    do_mirror = "--mirror" in argv
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
            row = (row + [""] * 18)[:18]
            xs, ys, hid, terr, feat, name = (c.strip() for c in row[:6])
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
            entry = {"terrain": terrain, "features": features,
                     "river": norm_edges(row[6:12]), "road": norm_edges(row[12:18])}
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

    # Rivers and roads are stored per hex but describe hexsides, so each one is
    # recorded twice and the two records have to agree.
    skipped, contradicted, crossed, offboard = check_edges(hexes)
    edge_problems = [
        f"{hid} has no {kind} on its "
        f"{', '.join(e for e, _ in sorted(claims))} edge(s), but "
        f"{', '.join(sorted(h for _, h in claims))} say(s) it should"
        for (hid, kind), claims in sorted({**skipped, **contradicted}.items())] + crossed

    payload = {
        "_comment": ("Generated by tools/extract_map.py from data/map-data.csv, a "
                     "third-party transcription. Corrections in data/map-fixes.json "
                     "are applied and marked with \"corrected\": true. \"river\" and "
                     "\"road\" list the edges of that hex carrying one; an edge is "
                     "shared, so it is recorded on both hexes. Where the two "
                     "disagree the edge is listed in \"edge_conflicts\" and must "
                     "not be relied on - ask the player."),
        "hexes": hexes,
        "warnings": warnings,
        "edge_conflicts": edge_problems,
    }
    (DATA / "map.json").write_text(json.dumps(payload, indent=2) + "\n")

    feats = sum(1 for h in hexes.values() if h["features"])
    rivers = sum(len(h["river"]) for h in hexes.values())
    roads = sum(len(h["road"]) for h in hexes.values())
    print(f"hexes:    {len(hexes)} across {len(cols)} columns")
    print(f"features: {feats}")
    print(f"edges:    {rivers} river marks, {roads} road marks "
          f"({len(offboard)} leaving the mapboard)")
    print(f"fixed:    {sum(1 for h in hexes.values() if h.get('corrected'))} "
          f"from map-fixes.json")
    # Findings already checked against the map stay in the output, but demoted -
    # a list you have to re-read the same three lines of is a list you stop
    # reading, and the next real defect hides in it.
    confirmed = fixes.get("confirmed", {})
    known = [(w, note) for w in warnings
             for key, note in confirmed.items() if key in w]
    warnings = [w for w in warnings if not any(k in w for k in confirmed)]
    if warnings:
        print(f"\n{len(warnings)} thing(s) to look at:")
        for w in warnings:
            print(f"  - {w}")
    if known:
        print(f"\n{len(known)} checked against the map already:")
        for w, note in known:
            print(f"  - {w.split(':')[0]}: {note}")
    if crossed:
        print(f"\n{len(crossed)} hexside(s) recorded as a river on one side and a "
              f"road on the other - an x in the wrong block of columns:")
        for p in crossed:
            print(f"  - {p}")
    if skipped:
        print(f"\n{len(skipped)} hex(es) carry no river/road data where a "
              f"neighbour says they should - most likely a skipped row:")
        for (hid, kind), claims in sorted(skipped.items()):
            edges = ", ".join(e for e, _ in sorted(claims))
            says = ", ".join(sorted(h for _, h in claims))
            print(f"  - {hid}: no {kind} on its {edges} edge; {says} say(s) there is")
        if not do_mirror:
            print("    Re-run with --mirror to copy these across into "
                  "map-fixes.json for review.")
    if contradicted:
        print(f"\n{len(contradicted)} hex(es) contradict a neighbour - both were "
              f"transcribed, so check these against the map:")
        for (hid, kind), claims in sorted(contradicted.items()):
            edges = ", ".join(e for e, _ in sorted(claims))
            says = ", ".join(sorted(h for _, h in claims))
            print(f"  - {hid}: has {kind} {hexes[hid][kind]} but {says} say(s) "
                  f"it also needs {edges}")
    if offboard:
        print(f"\n{len(offboard)} edge(s) run off the mapboard - expected where a "
              f"river or road leaves the map, otherwise a stray mark:")
        for p in offboard:
            print(f"  - {p}")
    if do_mirror and skipped:
        n = mirror(hexes, skipped, fixes_path)
        print(f"\n--mirror: wrote {n} hex(es) into {fixes_path.relative_to(ROOT)}. "
              f"Re-run without the flag to apply and re-check.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
