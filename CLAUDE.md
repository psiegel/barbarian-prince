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
