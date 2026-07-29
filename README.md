# Barbarian Prince — AI game guide

Tools for playing the 1981 Heritage USA solitaire game *Barbarian Prince* with an AI
acting as narrator and rules referee. You move the token and keep your character
sheet; the AI finds sections, resolves cross-reference chains, and reads them out.

**No game content is distributed here.** The repository contains only tooling. You
download the booklets from the publisher-authorised site and generate the data
locally, in one command. See [Game files](#game-files) below.

## Setup

Requires `python3` (standard library only) and `pdftotext`:

```sh
brew install poppler        # or: apt install poppler-utils
```

### Game files

Download these three PDFs from the official Dwarfstar archive, which Reaper
Miniatures permits to be distributed free of charge:

<https://dwarfstar.brainiac.com/ds_barbarianprince.html>

| File | Listed on the page as |
|---|---|
| `barbarianprince_rules.pdf` | Rules (PDF) |
| `barbarianprince_events.pdf` | Events (PDF) |
| `barbarianprince_travel.pdf` | Travel & Events (PDF) |

Put them in `pdfs/`, then build the data:

```sh
python3 tools/extract.py
```

```
sections: 252  (71 rules, 181 events)
travel:   10 terrains, 50 refs, 11 raft rows
tables:   80  {'options': 22, 'table': 28, 'inline': 30}
```

That is the whole setup. Everything under `data/` is generated except the three
hand-written files (`errata.json`, `procedures.json`, `map-fixes.json`), and
regenerating is deterministic — there is no manual editing step. You'll also want
the map and game tracks from the same page to actually play.

### Map data (optional)

The mapboard is not in the booklets, so hex terrain, features, rivers and roads
are transcribed into a separate spreadsheet:

<https://docs.google.com/spreadsheets/d/1dTSBhHSiYCicDYqMUvQJaI1KP6ajTc4ltDH2RHGBllg/edit?usp=sharing>

Download it as CSV to `data/map-data.csv` and build:

```sh
python3 tools/extract_map.py
```

The CLI then takes hex ids directly — `./bp hex 1017`, `./bp move 1017 1118`,
`./bp day 0101` — and looks up terrain, adjacency and compass direction for you
instead of asking. Without it everything still works; you just name the terrain
yourself.

That spreadsheet is transcribed from the game map, so like the booklets it is
covered by Reaper's distribution agreement rather than this repository's MIT
licence. A copy of the agreement is posted in the sheet, as its terms require.

The extractor audits the CSV rather than trusting it: terrain and feature labels
against the booklet, and rivers and roads against themselves. An edge belongs to
two hexes and is recorded on both, so the transcription proves its own
consistency — one-sided marks are reported, and `--mirror` proposes the repairs
that are purely mechanical.

### Voice (optional)

`./bp speak` has three backends and picks one automatically: a local Kokoro server
if one is listening, else ElevenLabs if a key is set, else the macOS `say` command.
It never hard-fails — an unreachable backend falls back to `say` and says why. With
no configuration at all you get `say`, and the rest of the CLI is unaffected.

Settings live in `.env`, which is git-ignored and loaded automatically. Start from
the annotated template, which documents every option:

```sh
cp .env.example .env
```

**Local and free** (recommended) — Kokoro-82M via mlx-audio, on Apple Silicon:

```sh
uv tool install --force "mlx-audio[server]" --with "misaki[en]" --with soundfile \
  --with "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
mlx_audio.server --port 8000
```

All three of those `--with` packages are required; the reasons, the voice list, and
why Kokoro rather than VibeVoice are in **[docs/local-tts.md](docs/local-tts.md)**.

**Hosted** — set `ELEVENLABS_API_KEY` in `.env`. The key needs text-to-speech
permission, plus voices-read if you want to list voices. Free accounts can only use
`premade` voices via the API — `professional` and library voices return HTTP 402.

## Usage

```sh
./bp start                 # the setup sequence for a new game
./bp day 0101              # today's actions, and the end-of-day checks
./bp hex 1017              # terrain, feature and the six adjacent hexes
./bp move 1017 1118 --river      # the ordered checks for one hex of travel
./bp options e003          # the choices and dice for a section, without the outcomes
./bp resolve e003 evade 4  # apply a choice and roll, print what it leads to
./bp show r203 e001        # print sections
./bp search "lodging"      # full-text search
./bp travel forest 3 5     # travel table, resolved down to the event
./bp treasure 2 4          # the r226 wealth-code grid
./bp refs r220             # cross-references in and out
./bp roll 2d6              # dice
./bp list e                # all event ids and titles
./bp speak e001            # read aloud  (--save keeps the mp3, --text-only prints)
```

`options` and `resolve` exist so the AI never has to read a table out loud, which
would spoil the branches you didn't take. `options` shows only what you must decide
and which dice to throw; you roll your own dice; `resolve` then jumps to the result.
80 sections have a machine-readable table behind them.

`start`, `day` and `move` cover the other half: the procedures the booklet
describes in prose rather than in a table. Travel especially is a fixed order of
checks spread across r204, r205 and r207, and `move` prints that order instead of
leaving the AI to reassemble it — which is where wrong rulings come from.

Start a game by asking the AI to start a new game, or run `./bp start` yourself.

## Layout

```
CLAUDE.md            how the AI should run the game
LICENSE              MIT, covering this tooling only
.env.example         annotated template for voice settings (copy to .env)
docs/local-tts.md    running Kokoro locally instead of ElevenLabs
bp                   CLI entry point
tools/extract.py     PDFs -> data/   (re-run if the PDFs change)
tools/tables.py      die-roll table parser used by extract.py
tools/bp.py          the CLI
tools/play.py        start / day / move / hex / treasure - procedural commands
tools/extract_map.py map-data.csv -> data/map.json, with the CSV audited
data/errata.json     hand-written notes on defects in the printed text (tracked)
data/procedures.json hand-written ordering for r202-r205, r215-r217 (tracked)
data/map-fixes.json  hand-written corrections to the map CSV (tracked)
data/map-data.csv    map transcription, downloaded from the sheet (not tracked)
data/*.json          generated by extract.py (not tracked)
pdfs/                you supply these (not tracked)
```

## Section numbering

| Range | Contents | Source |
|---|---|---|
| `e001`–`e195` | events (the plot) | events booklet |
| `r201`–`r228` | core rules | rules booklet |
| `r300`–`r343` | encounter resolution | rules booklet |
| `r207`, `r230`, `r231`–`r281` | travel tables | travel sheet |

The gaps are in the original game, not extraction bugs: `r229` and `r282`–`r299`
don't exist, nor do `e167`–`e179`. Four references in the printed text are typos
(`r119`, `e341`, `e250`, `e226`); `bp` resolves them automatically and says so.
`e189` (Charisma Talisman) is referenced but missing from the source PDF.

`data/errata.json` records these, plus OCR-level id corruptions (`el56` for `e156`,
`i309`, `l-e034`, `e-e046`) which are normalised during extraction. Structural
defects — a table row typeset inside the row above it — are repaired by rules in
`tools/tables.py`, not by storing corrected text. `bp` refuses rather than guesses
when a lookup genuinely has no answer, so an unexpected message usually means the
booklet really is silent there.

These notes were derived independently while parsing; the publisher also distributes
an official errata document on the page linked above, which is worth consulting.

## Licence and game copyright

The tooling in this repository is MIT licensed — see [LICENSE](LICENSE).

*Barbarian Prince* is copyright © 1981 Heritage USA and **remains the sole copyright
of Reaper Miniatures**. It is not public domain. Reaper permits the digitised game
files to be downloaded and distributed free of charge, but that permission:

- does not authorise **any fee** to be charged for distribution of or access to them;
- does not release them into the public domain;
- requires a copy of the permission agreement to be posted conspicuously wherever
  the game files are hosted;
- may be revoked at any time at Reaper's sole discretion.

Full terms: <https://dwarfstar.brainiac.com/ds_distribution.html>

This repository therefore ships **no game text, tables, artwork, or map**, and
`pdfs/`, `data/map-data.csv` and the generated `data/` files are all git-ignored.
The [map spreadsheet](#map-data-optional) is hosted outside this repository and
carries its own copy of the permission agreement, as those terms require.

If you fork this and commit the extracted data, the map CSV or the PDFs, you become
the one distributing the game content, and the terms above are yours to honour —
including hosting the permission agreement alongside them. The simplest way to stay
clear of all of it is to leave those paths ignored, as configured.
