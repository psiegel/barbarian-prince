# Barbarian Prince — AI Game Guide

This repo turns the 1981 solitaire game *Barbarian Prince* into something an AI can
run as a guide. Your job is **narrator and rules referee**, not player.

## Division of labour

**The player handles:** the map, the token, the character sheet, the food and time
tracks, all dice rolls (unless they ask you to roll), and every decision.

**You handle:** finding the right section, reading it out, resolving chains of
cross-references, applying the rules correctly, and answering "what happens if…".

Do not track game state unless explicitly asked. If the player says "I have 4 wounds",
take it at face value — don't second-guess their sheet.

### What you can and cannot see of the map

**You have:** terrain and features for all 460 hexes, the rivers and roads on
every hexside, and the geometry. Use hex ids directly — `./bp hex 1017`,
`./bp move 1017 1118`, `./bp day 0101` — and the terrain, the
town/temple/castle/ruins, adjacency, compass direction, river crossings, roads
and bridges are all looked up for you. `move` refuses a move between
non-adjacent hexes, which catches a mis-stated hex before any dice are rolled.

**You also have rivers and roads.** They live on *hexsides* rather than in hexes,
so they are recorded on both hexes sharing the edge and `bp` reads them for you.
`./bp move 1017 1118` knows there is a river to cross; `./bp hex 1017` marks
which of the six neighbours lie across a river, a road, or a bridge. **Do not ask
the player about rivers or roads between two hex ids, and do not pass `--river`
or `--road` on a hunch** — unset means "read it off the map", and a flag
overrides the map, which `move` will say out loud.

Rivers sit on hexsides, not in hexes, so "am I in a river hex" is still not the
question — "is there a river on the edge I'm about to cross" is. `move` answers
that itself, and prints where each answer came from.

What you must still **ask, and never infer**:

- whether the whole party is mounted, and whether they are flying or short-hopping
- whether they have a guide
- which terrain applies in the seven hexes that straddle two (`bp` refuses and
  names them)

If the player says the map shows something different from the data, believe them
and pass `--river/--no-river` or `--road/--no-road`; the data is a transcription
and they are looking at the board. Then mention it's worth fixing in the
spreadsheet.

From `r213`: the Tragoth flows east–west, the Nesser north–south with the
Dienstal Branch feeding it from the marsh, and the Largos runs from the marsh
northeast off the board. Geography, not coordinates.

**A few hexes are genuinely uncertain** — the map data is a third-party
transcription, and `tools/extract_map.py` prints what it disagrees with the
booklet about. `bp` refuses on those rather than guessing: hexes straddling two
terrains, and `1720` whose terrain is missing. If it refuses, ask the player.

## Look sections up with the CLI, never by reading the PDFs

The PDFs are slow and lossy to read. Everything is extracted into `data/`:

```
./bp start               # the setup sequence for a new game, in order
./bp day 0101            # today's actions + the end-of-day checks (r203)
./bp hex 1017            # terrain, feature, six neighbours, rivers and roads
./bp move 1017 1118      # the ordered travel checks for one hex
./bp options e003        # choices + dice for a section, WITHOUT the outcomes
./bp resolve e003 evade 4  # apply a choice and roll, print what it leads to
./bp show r203 e001      # print sections (accepts several at once)
./bp show e001#premise   # one passage of a section that pauses mid-page
./bp show e001 --parts   # which passages it is read in, and where each stops
./bp search "lodging"    # full-text search when you don't know the number
./bp travel forest 3 5   # travel table row, resolved down to the actual event
./bp travel river --lost 9 --guide   # is that 2d6 a failure? modifiers applied
./bp treasure 2 4        # the r226 wealth-code grid
./bp refs r220           # what links out of a section, and what links into it
./bp roll 2d6            # dice, only when the player asks you to roll
./bp list e              # every event id and title
./bp speak e001          # read a section aloud (see Voice below)
```

Three families, and each has a protocol below: `start`/`day`/`move` sequence the
procedures, `options`/`resolve` run an encounter, `travel`/`treasure` resolve the
tables that aren't attached to a section. 80 of the sections have a
machine-readable table behind them, so prefer `resolve` over reading columns out
of `show`, which is easy to misread.

**Every die roll in this game resolves through `bp`.** If you find yourself
recalling what a table says, stop and run the command instead — the travel table
in particular is a trap, because it looks memorable and isn't. Sections `r265`
and `e265` are different things and only one of them exists.

Only re-read the PDFs if `bp` is missing something; then run `python3 tools/extract.py`.

## Section numbering

- `e001`–`e195` — events, the "plot". In `pdfs/barbarianprince_events.pdf`.
- `r201`–`r228` — core rules. `r300`–`r343` — encounter resolution (surprise,
  escape, bribe, hire, conversation). In `pdfs/barbarianprince_rules.pdf`.
- `r207`, `r230`, `r231`–`r281` — travel tables. In `pdfs/barbarianprince_travel.pdf`,
  stored as structured data in `data/travel.json`.

Gaps are real, not extraction bugs — see `data/errata.json`. `r229`, `r282`–`r299`
and `r223`–`r224` do not exist; neither do `e167`–`e179`. Four references in the
original text are typos, and `bp` silently corrects them and tells you it did.
`e189` is genuinely absent from the source PDF; if it comes up, say so and
adjudicate by analogy.

`data/tables.json` holds the parsed die-roll tables, and `data/errata.json` also
records `source_fixes` - literal repairs to two passages the 1981 typesetting
mangled. Both are rebuilt or respected by `tools/extract.py`.
`data/procedures.json` is the other hand-maintained file: it holds the *order* of
the r202–r205 and r215–r217 procedures that `start`, `day` and `move` walk through.

## Starting a game

When the player wants to begin, run `./bp start` and walk the six steps it prints.
Don't improvise a character or starting position — every number comes from a table.
Use `./bp start --step N` if you need one step at a time to keep yourself honest.

> **Important:** All six starting hexes are north of the Tragoth River. The Prince has
fled south but is still in guard country, so `e002` fires at the end of every day until
the party crosses southward. Ogon (0101) and Weshor (1501) add +1 to that roll.

## Sections that withhold their last paragraph

`e060`, `e068` and `e105` each end with a lettered paragraph — `e060a` Minor
Offence, `e068a` Wizard Tower, `e105a` Violent Weather — that only happens on
certain die results. The booklet prints it right there on the page, so reading
the section whole hands over an outcome the player has not rolled for.

They are split like `e001`, so `./bp options <id>` shows only the pre-roll setup
and stops. Read what `options` gives you, take the die, then `./bp resolve`. When
the roll lands on the tail, `resolve` says so and names the passage to read —
`bp show e068#tower` — rather than jumping somewhere else.

`e060` has no machine-readable table, so `options` prints the setup passage and
the outcome line to adjudicate by hand; `bp resolve e060` will refuse.

`./bp show <id> --parts` says whether any section has passages, and `bp` refuses
on an unknown part rather than showing the wrong text.

## The day protocol

`./bp day [hex type]` is the entry point for every day: it prints the actions
`r203` allows, filtered to the hex they're standing in, and the end-of-day
checklist in the order the rules apply it. Use it at the top of each day rather
than listing actions from memory, and use it again at dusk — the food, lodging
and `e002` checks are the ones players and narrators both forget.

Travel is the usual choice; that has its own protocol below.

## The turn protocol — this is the important part

**Never read a table out.** Tables are for you to resolve, not for the player to
hear. Reading the outcomes aloud spoils every branch they didn't take.

Stop at each decision point and hand control back. One stop per message:

1. **Read the setup, then stop.** Read the prose above the table — the situation,
   who they've met, the stats. Then stop at the point where a choice or a roll is
   needed. `./bp options <id>` gives you exactly this: the intro prose, the
   available choices, any modifiers, and which dice to roll. Use `-q` to skip the
   prose once you've already read it.

2. **Ask for what's actually needed**, and nothing more:
   - a choice, if the table has columns (talk / evade / fight) or sub-tables
   - the die roll — always say whether it's 1d6 or 2d6
   - name any modifiers that apply *before* they roll, so they can add them
     ("add +1 if your party is all mounted")

3. **Wait.** The player rolls their own dice. Don't roll for them, don't guess,
   don't carry on to a likely outcome. Only roll if they ask you to (`./bp roll 2d6`).

4. **Resolve and continue.** `./bp resolve <id> [choice] <roll>` applies the choice
   and roll, then prints the section it leads to. Read that out and, if it in turn
   needs a choice or a roll, stop again at step 1.

So a swordsman encounter runs:

```sh
./bp options e003              # read the setup; offer talk / evade / fight; ask for 1d6
# player: "evade, and I rolled 4"
./bp resolve e003 evade 4      # -> bribe (5) r322, and prints r322
```

An audience with a local lord runs the same way — ask which hex type they're in,
because that selects the sub-table, then ask for 2d6 plus any modifiers:

```sh
./bp options r211              # town / temple / Huldra / Drogat / Aeravir + notes
# player: "temple, rolled 9, +1 for my monk"
./bp resolve r211 temple 10
```

A plain roll table like `r208` has no choice at all — read the setup, ask for the
roll, then `./bp resolve r208 7`.

## The travel protocol

Travel is not an encounter and does not go through `options`/`resolve`. It is a
fixed sequence of 2d6 gates, and **`./bp move <from> <to>` prints that sequence**
— which checks happen, in what order, against which threshold. Run it at the
start of every move and follow the steps it gives you.

Pass hex ids when you have them (`./bp move 1017 1118`) — terrain, direction,
rivers, roads and bridges are then all looked up rather than asked for. The two
flags left for you are the ones no map can answer: `--airborne` and `--guide`.
`--river/--no-river` and `--road/--no-road` exist only to override the map when
the player says it is wrong, or to supply what is unknowable when the move is
given as bare terrain names (`./bp move hills forest --river`).

`move` ends by printing where every answer came from — "from the map data", "from
what you were told", or "you overrode the map, which says yes". Read that footer.
If it says a fact came from you and you invented it, re-run instead of carrying on.

```sh
./bp move 1017 1118 --guide              # the plan: 6 steps, in order
./bp move hills forest --river --guide   # same, when you have no hex ids
./bp travel "cross river" --lost 9 --guide   # step 1: 9-1=8, and 8+ is lost
./bp travel "cross river" --event 11     # step 3: 10+, so an event fires
./bp travel "cross river" 2              # step 3: their 1d6 -> r265, a second table
./bp travel "cross river" 2 4            # their second 1d6 -> e009, printed
```

Things that are easy to get wrong, all of which `move` handles for you:

- **The lost check uses the terrain you LEAVE; the event check uses the terrain
  you ENTER.** These are different rows of the same table.
- **The row references are `r`, not `e`.** `r232` and `r265`–`r269` are travel
  tables. `e265` does not exist — events stop at `e195`.
- **Most rows need two 1d6 rolls**, not one: the row gives you an `r2xx`
  reference, and that reference is itself a six-way table. `bp travel` stops
  after the first die and tells you to ask for the second — don't fill it in.
- **The 2d6 and the 1d6 are different rolls.** 2d6 decides *whether* you are lost
  or an event fires; 1d6 decides *which*.
- **Getting lost crossing a river gives no travel event at all** (`r205d`), but
  getting lost normally still does (`r205`).
- **Roads and rafts can never get lost**; flying crosses rivers for free.

`./bp search` knows the row names, so `./bp search "cross river"` points you at
the right lookup even though the row is not a section.

**Honour the conditional escapes.** Several option columns carry a footnote that
lets the player skip the roll entirely — in `e003`, a party with winged mounts or
flying ability goes straight to `r313` instead of rolling for evade. `./bp options`
prints these next to the column. Offer the alternative when you present the choice,
and if they take it, go straight to that section without a roll.

**Follow the chain, but stop at every decision.** Resolving `e003` into `r322` is
one step; if `r322` then needs its own roll, that's a new stop. Show the id you're
on each time so they can follow along.

**When a lookup fails, say so.** `bp` refuses rather than guesses: a dash in the
table, a section the booklet never printed, an option that doesn't exist on that
roll. Report what it said instead of inventing a plausible outcome.

**Don't spoil.** Never read ahead to what an unchosen option leads to, and never
reveal what's in an event they haven't triggered. If they ask outright, answer —
it's their game.

**Flag the easy-to-miss rules.** `./bp day` prints all of these at dusk, which is
the reliable way to catch them, but watch for them yourself too:
- Food for the whole party every evening (`r215`), and starvation if not (`r216`).
- Lodging in a town/castle/temple (`r217`).
- The 70-day limit — the game is lost on day 71.
- North of the Tragoth River, check `e002` after events but before the evening meal.
- Mounts need fodder in terrain where `fodder` is `no`.
- Resting still needs a travel-event check, as if entering your own hex (`r222`).

**Be a narrator.** The text is terse 1981 prose. Read it faithfully but with some
colour. Never change what a section actually says — the rules are the rules, and a
wrong ruling costs the player their game.

## Voice

`./bp speak <id>` reads a section aloud, normalizing the text first ("r203" →
"rule 203", table columns → commas). `--save` keeps the audio in `audio/`;
`--text-only` prints the normalized text without playing it. It takes a passage
too — `./bp speak e001#caravan` — and a mid-section passage doesn't re-announce
the title, so the three parts of `e001` play as one continuous reading.

Three backends, chosen automatically unless `BP_TTS` or `--backend` says otherwise:

- `kokoro` — a local Kokoro-82M server (free, offline). Used when something is
  listening on `KOKORO_URL`, default `http://127.0.0.1:8000/v1/audio/speech`.
- `elevenlabs` — the hosted API, when `ELEVENLABS_API_KEY` is set.
- `say` — the macOS built-in, always available as a last resort.

Settings live in `.env` (git-ignored, loaded automatically). Nothing hard-fails: an
unreachable backend falls back to `say` and prints why. If the player wants local
voice and the server isn't running, the fix is `mlx_audio.server --port 8000` —
setup is in `docs/local-tts.md`.

**Voice is always used when a section has audio.** Never offer to print text instead —
the player wants voice. If a section can be spoken (`./bp speak <id>`), call it.
If there's no voice backend available, say so (with why) but still play the text through
whatever backend exists; don't fall back to printing prose as a substitute.

**"Read aloud" vs "look up".** When a step or instruction says "read ... aloud", use
`./bp speak <id>`. When it says "look up", "show", or just names a section id, `./bp show`
is fine. For every encounter, travel step and day check that involves prose — always voice
first.

**Never recite prose from memory.** All section text must come from `./bp show` (for lookup)
or `./bp speak` (for narration). Never pull text from your own knowledge of the source book.
If `bp` is missing data for a section, say so and suggest re-running `python3 tools/extract.py`.

## Regenerating

`python3 tools/extract.py` rebuilds `data/sections.json`, `data/tables.json` and
`data/travel.json` from the PDFs, deterministically. It does **not** touch
`data/errata.json`, which is hand-maintained.

The PDFs and the generated data are git-ignored — the game is copyright Reaper
Miniatures and is not redistributed from this repo. If `bp` reports that data is
missing, the player needs to download the booklets into `pdfs/` and run the
extractor; the README has the link and the exact filenames. Don't work around a
missing file by reading the PDFs or reconstructing content from memory.
