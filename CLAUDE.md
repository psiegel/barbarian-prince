# Barbarian Prince — working on this repo

This turns the 1981 solitaire gamebook *Barbarian Prince* into something an AI
can run as a narrator. **These are notes for working on the code.** The
instructions the game-running model receives are `src/prompts/system.md` — if
you are changing how the narrator behaves, that is the file, not this one.

## Two programs

| | |
|---|---|
| `src/bp.py` | the reference. Looks sections up, resolves tables, keeps the sheet. Knows nothing about models. |
| `src/narrator.py` | the client (`./play`). Runs a local model through Ollama, calls `bp` as its one tool, and routes the output. |

`bp` is invoked as a subprocess with `sys.executable`, so a venv-run client uses
its own interpreter. There is no `./bp` wrapper — run it directly if you need to:
`python3 src/bp.py show e003`.

## The contract: stdout is heard, stderr is not

This is the invariant everything rests on, and the one to check when adding or
changing a command.

**stdout** is prose a DM would say. `./play` prints it and speaks it.
**stderr** is the referee's: section ids, errata, `-> r330`, `-> then:`,
band-size notes, procedure checklists, and the whole character sheet.

So: `show`, `options`, `resolve`, `travel`, `treasure` speak. `start`, `day`,
`move`, and every `state.py` command are silent — they print scaffolding, and
read aloud a checklist with rule citations is unlistenable and spoils the
sequence. `state.py` enforces this centrally in `sheet_command()`, wrapped
around every command at registration; `procedures.py` does it per-command with
`contextlib.redirect_stdout`.

**A new command that prints anything must decide which side it is on.** The test
is simple: would a person say this sentence out loud to a player? A table, a rule
cite, or a command to run next is a no.

`--raw` on `show` prints the source layout — tables, ids, refs — for adjudicating
the sections with no machine-readable table. It is never for reading out.

## Keep the system prompt short

`src/prompts/system.md` is ~900 tokens and should stay near there.

This is measured, not taste. Same model (`qwen3.6`), same conversation, same
turn: with CLAUDE.md's old ~6,900-token prompt it invented an opening passage
whole — "the last Prince of Kesh, the evil wizard Gorgon", none of which is in
the book. With a few hundred tokens saying only *you don't know the book, call
the tool*, it fetched the real one. `narrator.py` warns past ~2,000 tokens.

If the narrator needs to know a procedure, **put it in `bp` and let it ask**,
rather than in the prompt. That is what `data/procedures.json` is for.

## Let code drive known sequences

Setup is seven fixed steps — read a passage, roll a die, write it down — with no
judgment in any of them. `run_setup()` in `narrator.py` walks it directly from
`data/procedures.json`; the model is not consulted until day 1. Handing a fixed
sequence to a model buys nothing and costs invention.

Dusk is the same: `run_dusk()` walks e002, the hunt, the meal, wages, lodging and
the date, and the model only sees the summary. `["time", "+1"]` and a few
spellings of "dusk" (`is_dusk()`) are intercepted rather than passed to `bp`. Two
rules the walk keeps:

- **Ask the player for judgment, never for a lookup.** Hunt or not, buy or eat
  stores — theirs. Which side of the Tragoth they are on, whether the hex can be
  hunted — the map's, via `procedures.north_of_tragoth()` and `hunting_here()`.
  `narrator.py` imports `procedures` for exactly these two, so there is one
  implementation of each rule rather than two that can disagree.
- **A step already on the sheet is not done again.** `already_today()` reads
  `bp game log`, so a meal the model bought itself is not bought twice by the
  walk. And a step that refuses stops the walk: the day stays open, and the
  refusal goes back to the model instead of being swallowed.
- **A walk speaks for itself.** The player hears bp's prose and the model's
  narration; the walk's own lines — "roll 2d6", "Cal Arath brings back four food
  units" — belong to neither, and were being printed silently while the player
  was looking away from the screen. `tell()` prints and speaks them, and
  `spoken()` takes the rule cite and the die notation out of the voice's copy
  because "(r215b)" and "2d6" have no spoken form. So `ask_die()` and
  `ask_hunter()` are heard, and so is what each step did.
- **Report what the sheet says, not what bp said.** A `state.py` command is
  silent by contract, so the walk reads food and gold either side of it
  (`purse()`, `moved()`) and says what moved. Nothing parses bp's sentences, so
  rewording `bp eat` cannot turn the report into a lie, and an unreadable sheet
  reports nothing rather than a guess. The numbers then go to the model marked
  as already said, so the narration neither repeats nor contradicts them.

Encounters (`options` → `resolve`) are the next candidate.

## Regenerating data

```sh
python3 src/extract.py       # PDFs -> sections.json, tables.json, travel.json
python3 src/extract_map.py   # map-data.csv -> map.json
```

Deterministic; no manual editing step. Hand-maintained and **not** regenerated:
`errata.json` (defects in the printed text), `procedures.json` (the order of the
r202–r205 and r215–r217 procedures), `creatures.json` (band-size rewrites),
`map-fixes.json`.

The PDFs and generated data are git-ignored — the game is copyright Reaper
Miniatures and is not redistributed here. If data is missing, say so; don't work
around it by reading the PDFs or reconstructing content from memory.

## Section numbering

- `e001`–`e195` events, `r201`–`r228` core rules, `r300`–`r343` encounter
  resolution, `r207`/`r230`/`r231`–`r281` travel tables.
- Gaps are real, not extraction bugs: `r229`, `r282`–`r299`, `r223`–`r224` and
  `e167`–`e179` do not exist. `e189` is genuinely absent from the source PDF.
- Four references in the original are typos; `bp` corrects them and says so.
- `r265` and `e265` are different things and only one exists.

The map data is a third-party transcription. `extract_map.py` prints what it
disagrees with the booklet about, and `bp` refuses on the genuinely uncertain
hexes — those straddling two terrains, and `1720`, whose terrain is missing.

Which side of the Tragoth a hex is on is derived, not stored: walk north up the
column, and a river on any hex's N edge means the party is south of the Tragoth.
The river found need not *be* the Tragoth — the others wander east and west too
— but the Tragoth is the most northerly on the board, so every river is either
it or south of it, and either way you are south of it. Reaching row 01 with
nothing found is the other half: the Tragoth crosses all twenty columns, so if
it is not above you, you are north of it. That leaves rows 01–02 as the
Northlands, all six starting hexes among them; `1401`, where the Tragoth touches
the top of the map, is correctly south. A re-transcribed `map-data.csv` that
moved an N edge into row 01 or 02 would move the river, so check `bp hex 0101`
still says north and `bp hex 0102` south.

## Testing

**Set `BP_GAME` for anything that touches a game.** Commands write to whatever
save is current — `bp show e052` files a band size, `bp gold` moves money — so a
bare test command edits the player's live save. Clean up the save afterwards, and
don't leave `saves/current` pointing at a save you then delete.

**The leak test.** Prose output must never contain a table row. Render all
sections through `bp.to_prose()` and assert nothing matches `TABLE_LINE_RE`,
`ROW_LINE_RE`, `DIE_HEADER_RE` or `INLINE_OUTCOMES_RE`. Several sections print
outcomes inline mid-sentence ("then roll two dice 2-e012; 3-e012; …"), and
`e053`/`e160` wrap table rows across lines with the die number on its own line —
those are the shapes that break naive stripping.

**Model behaviour is not a unit test.** Changes to `system.md` need an actual run:
`./play --referee --quiet`, `/start`, and read what it does. The failure to watch
for is invented prose presented as the book. `narrator.py` flags quoted spans
that `bp` never printed, and never speaks blockquoted lines, but neither catches
paraphrase.
