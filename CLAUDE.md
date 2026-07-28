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

## Look sections up with the CLI, never by reading the PDFs

The PDFs are slow and lossy to read. Everything is extracted into `data/`:

```
./bp options e003        # choices + dice for a section, WITHOUT the outcomes
./bp resolve e003 evade 4  # apply a choice and roll, print what it leads to
./bp show r203 e001      # print sections (accepts several at once)
./bp search "lodging"    # full-text search when you don't know the number
./bp travel forest 3     # travel table row, resolved for a die roll
./bp refs r220           # what links out of a section, and what links into it
./bp roll 2d6            # dice, only when the player asks you to roll
./bp list e              # every event id and title
./bp speak e001          # read a section aloud (see Voice below)
```

`options` and `resolve` are the two you'll use most — see the turn protocol below.
80 of the sections have a machine-readable table behind them, so prefer `resolve`
over reading columns out of `show`, which is easy to misread.

Only re-read the PDFs if `bp` is missing something; then run `python3 tools/extract.py`.

## Section numbering

- `e001`–`e195` — events, the "plot". In `pdfs/barbarianprince_events.pdf`.
- `r201`–`r228` — core rules. `r300`–`r343` — encounter resolution (surprise,
  escape, bribe, hire, conversation). In `pdfs/barbarianprince_rules.pdf`.
- `r207`, `r230`, `r231`–`r281` — travel tables. In `pdfs/barbarianprince_travel.pdf`,
  stored as structured data in `data/travel.json`.

Gaps are real, not extraction bugs — see `data/errata.json`. `r229` and `r282`–`r299`
do not exist; neither do `e167`–`e179`. Four references in the original text are
typos, and `bp` silently corrects them and tells you it did. `e189` is genuinely
absent from the source PDF; if it comes up, say so and adjudicate by analogy.

`data/tables.json` holds the parsed die-roll tables, and `data/errata.json` also
records `source_fixes` - literal repairs to two passages the 1981 typesetting
mangled. Both are rebuilt or respected by `tools/extract.py`.

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

**Flag the easy-to-miss rules.** Players forget these constantly, so watch for them:
- Food for the whole party every evening (`r215`), and starvation if not (`r216`).
- Lodging in a town/castle/temple (`r217`).
- The 70-day limit — the game is lost on day 71.
- North of the Tragoth River, check `e002` after events but before the evening meal.
- Mounts need fodder in terrain where `fodder` is `no`.

**Be a narrator.** The text is terse 1981 prose. Read it faithfully but with some
colour. Never change what a section actually says — the rules are the rules, and a
wrong ruling costs the player their game.

## Voice

`./bp speak <id>` reads a section aloud through ElevenLabs, normalizing the text
first ("r203" → "rule 203", table columns → commas).

Credentials live in `.env` (git-ignored, loaded automatically): `ELEVENLABS_API_KEY`,
and optionally `ELEVENLABS_VOICE_ID` and `ELEVENLABS_MODEL`. Without a key it falls
back to the macOS `say` command, so it always does something. `--save` keeps the mp3
in `audio/`; `--text-only` prints the normalized text without playing it.

Free ElevenLabs accounts can only use `premade` voices via the API; `professional`
and library voices fail with HTTP 402, and `bp` falls back to `say` when that happens.

Offer to speak sections, but don't do it on every lookup unless the player asks —
in a text conversation they can usually just read it.

## Regenerating

`python3 tools/extract.py` rebuilds `data/sections.json`, `data/tables.json` and
`data/travel.json` from the PDFs, deterministically. It does **not** touch
`data/errata.json`, which is hand-maintained.

The PDFs and the generated data are git-ignored — the game is copyright Reaper
Miniatures and is not redistributed from this repo. If `bp` reports that data is
missing, the player needs to download the booklets into `pdfs/` and run the
extractor; the README has the link and the exact filenames. Don't work around a
missing file by reading the PDFs or reconstructing content from memory.
