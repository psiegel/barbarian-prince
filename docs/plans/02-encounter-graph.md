# 02 — The encounter graph (r300–r343)

**Goal:** resolve the entire encounter-resolution graph in code, from any event's
option table through to a combat setup, an escape, a hire, or the end of the
event.

**Depends on:** 01 (needs the generator stack and `Retry`).

**Done when:** every one of `e003`'s eighteen option cells resolves to a terminal
state without a model, `r330` → `r3xx` → combat entry works, and a failed hide
returns to the option list.

---

## Why this is first

It is the largest closed subgraph in the book and the best-behaved:

- 44 sections, `r300`–`r343`, with no gaps.
- All 44 are entry points reached from events; 38 event sections lead into it.
- Three of them already have extracted tables (`r330`, `r341`, `r342`); five more
  have inline ones (`r331`, `r332`, `r333`, `r337`, `r340`).
- The remaining 36 reduce to **eight primitives**. This is not 44 handlers.

## The primitives

```python
Check(stat, op)         # stat: "wits" | "party_size";  op: "ge" | "gt" | "lt" | "le"
                        # party_size is living MEN only, not mounts (state.men())
Fight(initiative)       # "surprise_us" | "we_first" | "they_first" | "surprise_them"
Escape(requires=None)   # None | "all_mounted" | "all_flying"   -> r218
Hide(abandon=False)     # -> r218
Bribe(amount)           # amount supplied by the calling section
Join(terms)             # follower or henchman, with terms
Pass()                  # event ends
Battle(mod)             # -> r330 with a die modifier
```

`Check` is a `1d6` compared against a party stat. Note the two operators are
genuinely different sections (`>=` vs `>`) — do not collapse them.

## The classification

> This table encodes branch structure, not prose, and none of the 36 sections it
> hand-encodes has a table — the eight that do are read from the generated
> `tables.json`. See overview Q1 for why that split is the copyright boundary.

| Section | Shape |
|---|---|
| r300 | `Fight(surprise_us)` |
| r301 | `Check(wits, ge)` → `Fight(surprise_us)` : `Fight(we_first)` |
| r302 | `Check(wits, gt)` → `Fight(surprise_us)` : `Fight(we_first)` |
| r303 | `Check(party_size, lt)` → `Fight(surprise_us)` : `Fight(we_first)` |
| r304 | `Fight(we_first)` |
| r305 | `Check(wits, ge)` → `Fight(we_first)` : `Fight(they_first)` |
| r306 | `Check(wits, gt)` → `Fight(we_first)` : `Fight(they_first)` |
| r307 | `Fight(they_first)` |
| r308 | `Check(wits, ge)` → `Fight(they_first)` : `Fight(surprise_them)` |
| r309 | `Check(wits, gt)` → `Fight(they_first)` : `Fight(surprise_them)` |
| r310 | `Fight(surprise_them)` |
| r311 | `Escape()` |
| r312 | `Escape(all_mounted)`, else choose: abandon the unmounted, or `Battle(0)` |
| r313 | `Escape(all_flying)`, else choose: abandon the earthbound, or `Battle(0)` |
| r314 | `Check(wits, ge)` → `Escape()` : `Retry` |
| r315 | `Check(wits, gt)` → `Escape()` : `Retry` |
| r316 | `Hide()` |
| r317 | `Check(wits, ge)` → `Hide()` : `Retry` |
| r318 | `Check(wits, gt)` → `Hide()` : `Retry` |
| r319 | `Check(party_size, le)` → `Hide(abandon=False)` : `Retry` |
| r320 | `Check(party_size, lt)` → `Hide(abandon=False)` : `Retry` |
| r321 | `Bribe(amt)` → `Pass()` : `Battle(+1)` |
| r322 | `Bribe(amt)` → `Pass()` : `Battle(0)` |
| r323 | `Bribe(amt)` → `Pass()` : `Battle(-1)` |
| r324 | `Bribe(amt)` → `Pass()` : `Fight(they_first)` |
| r325 | `Pass()` |
| r326 | `Check(wits, ge)` → `Pass()` : `Battle(+1)` |
| r327 | `Check(wits, ge)` → `Pass()` : `Battle(0)` |
| r328 | `Check(wits, gt)` → `Pass()` : `Battle(0)` |
| r329 | `Check(wits, gt)` → `Pass()` : `Battle(-1)` |
| r330 | 2d6 table → r300–r310. **The one place `mod` is consumed.** |
| r331 | `Join(bonus=amt, pay=0, terms=[equal_share, no_abandon])`; decline → 1d6 |
| r332 | `Join(bonus=amt, pay=2/day from tomorrow, group=all_or_none)`; decline → 1d6 |
| r333 | `Join(pay=2/day, today due now, some_or_all)`; decline → 1d6 |
| r334 | `Join(free follower)` |
| r335 | `Join(free follower, terms=[leaves_at_settlement])` |
| r336 | `Check(wits, ge)` → `Join(free follower)` : `Pass()` |
| r337 | `Check(wits, gt)` → `Join(free follower)` : 1d6 table |
| r338 | `Check(wits, gt)` → `Join(pay=1/day)`; on equality `Join(pay=2/day)`; else `Pass()` |
| r339 | choose to talk or pass; if talk `Check(wits, gt)` → `Join(pay=2/day, today now)` : 1d6 |
| r340 | choose to pass or convince; `Check(wits, ge)` → `Join(equal_share)` : 1d6 table |
| r341 | 2d6 table → r305, r308, r331–r340 |
| r342 | 2d6 table → r306, r309, r325, r330, r333, r336–r340 |
| r343 | victim selection loop — implemented in plan 03, called from here |

r338 is the only three-way check (`>` / `==` / else). Do not force it into the
two-way `Check`.

## Parameters travel with the reference

Bribe and join amounts are set by the **calling** section, not by the r3xx
section. The amounts in the book are 5, 8, 10, 15, 20, 25 and 30, appearing as
`bribe (5) r322` in an option cell or `needs 10 gold now, r331` in a table result.

So `Goto` carries parameters:

```python
Goto("r322", mod=0, params={"amount": 5})
```

- [x] Write `parse_ref(cell)` — extracts `(target_sid, amount)` from a table cell
      or result string. `src/engine/refs.py`.
- [x] Run it over every `options` table and assert every cell yields exactly one
      target. **398 parsed, 2 legitimately targetless, 0 failures.** The amounts
      are exactly 5, 8, 10, 15, 20, 25, 30. Scoped to option cells: `table`-kind
      results are prose sentences carrying two references apiece
      (`r340`'s "…combat (r220); see r330…"), so they are encoded per row
      instead — see "As built".

## Retry semantics

`Retry` is the reason the machine needs a stack. The failing sections
(r314, r315, r317–r320) say to return to the previous section and select another
option.

Specify it as: **re-offer the calling section's options for the row already
rolled, minus the option just attempted.** The die has been rolled; the row is
fixed; only the column is back in play.

- [x] Frames record `(row, used, chosen)`, set by `ctx.offer()`.
- [x] `Retry` pops to the nearest frame with offered columns and re-enters it.
- [x] If every column is used up, `Refuse` and let the player rule.

**Open question, decided:** a player may not re-attempt the same column. It is
behaviour, not a flag — the used column is filtered out of the next `ctx.choose`,
so making it configurable would mean threading an option through the option
handler for a rule nobody has asked to change. Revisit if it grates in play.

## Combat entry

`Fight(...)` becomes `EnterCombat(spec)` for plan 03:

```python
EnterCombat(
    foes=...,                # from creatures.py / the event's band roll
    initiative="they_first", # or we_first
    surprise=None,           # "us" | "them" | None  -> the free bonus strike
    target="party",          # or "player" for r341's assassin and r340's 1,2
)
```

Until plan 03 lands, `EnterCombat` may fall back to the existing
`combat.fight()` auto-resolver so the graph is testable end to end.

## Work items

- [x] `data/graph.json` — one entry per section with a `cite` and no prose.
      Tracked; `.gitignore` and the LICENSE scope note both updated. Checked for
      text overlap against `sections.json`: **0 shared 8-word sequences.**
- [x] `src/engine/rules/graph.py` — the eight primitives, plus explicit handlers
      for **nine** sections (r312, r313, r331, r332, r333, r337, r338, r339,
      r340). r333 and r337 joined the list: both open with a decision before
      their table.
- [x] `parse_ref` plus its exhaustiveness test.
- [x] Wire the table-bearing sections through the existing resolution rather
      than re-encoding them. The five inline tables (r330, r331, r332, r333,
      r337) already carry an explicit `goto` per roll and are read straight from
      `data/tables.json`.
- [x] `Join` terms: `pay_starts` and a `terms` list added to `state.new_char`.
      The existing `pay` field carries the daily wage, so `state.wages` needs no
      change. Enforcement of `equal_share` and `leaves_at_settlement` is plan
      06's queue; they are recorded now.
- [x] The generic **options-table handler**, which was not on this list but is
      what the e003 sweep requires. One implementation serves all 22 option
      tables in the book; `sections._fallback` routes to it.

## Tests

`tests/test_graph.py`, 48 tests. Suite total 112, about 25 seconds — the sweep is
most of it.

- [x] **Reachability:** every path from every r3xx entry point ends in
      `EnterCombat`, `EscapeHex`, `HideHere`, `EndEvent`, a `Refuse`, or a
      cycle. ~4,000 paths. The refusing set is asserted *exactly*, so a new
      refusal cannot appear unnoticed.
- [x] **e003 sweep:** all six rows × three columns resolve, plus a check that
      every one of the 22 option tables in the book can be entered.
- [x] **`parse_ref`:** exhaustive over every option cell, plus unit tests for
      each damaged shape.
- [x] **Retry:** a forced failure at r317 from e003 re-offers talk and fight,
      not evade; the row is not re-rolled; and it survives a save and reload.
- [x] **Modifiers:** `Battle(±1)` shifts the r330 lookup and clamps at both ends.
- [x] Beyond the plan: the four initiative shapes, the three-way r338, wages due
      on hiring versus starting tomorrow, an empty purse skipping the bribe
      question, and `data/graph.json` structural checks (all 44 encoded, every
      goto target exists, the `>=`/`>` pairs not collapsed).

## Notes

- Every section in the graph is a *procedure*, never prose to read aloud. Their
  bodies exist in `sections.json` and the handler may `ctx.note` a one-line
  summary, but the player hears the outcome, not the section.
- `r343` is used from outside the graph too (events that attack one character).
  Implement it once, in plan 03.

---

## As built

Landed. 112 tests. `./play2 --flow e003` plays the whole thing with no model.

### The graph has a real cycle in it

**r337 → r342 → r337.** r337's decline table ends at r342, and r342 on a 10
returns to r337. This is in the 1981 rules, not an encoding mistake. It
terminates in play because most rolls leave the loop, but it means the flat
assertion above — "no path may loop forever" — was wrong, and a walk of this
graph cannot follow every edge.

`Machine.trace` records every section entered so a caller can see a repeat
coming. The test explorer stops a path at its first revisit and asserts the
looping set is exactly `{r337, r342}`, so a cycle introduced by a coding mistake
would still fail.

### Retry needed a change to the machine

A `Goto` is a tail call: it pops the caller and pushes the target. That destroys
the very frame `Retry` has to come back to. So a frame whose handler called
`ctx.offer()` is now **kept on the stack across a Goto**, marked `spent` — its
generator is finished and can never be sent anything, but its sid, params and
`retry_to` survive. Terminal outcomes pop through spent frames on the way down.

`Retry` then re-enters the option section with `_retry: {row, used}` in its
params, rather than resuming it.

### What the sweep found

- 398 cells parsed, **0 failures**. The damaged shapes are all recoverable:
  `hider318` and `passr325` lost a space, `pe r311` and `p e116` lost their
  verb, `be-pass (5) r321` lost half of "bribe". Every one still names exactly
  one section.
- Two cells legitimately name none. `e021` row 6 talk is "roll again"; `e130`
  row 4 talk is `**audience`, whose rule is entirely in a footnote. Both refuse
  with a pointer to `python3 src/bp.py show <sid> --raw`.
- `e071` has **rows 0 and 7**. Its note gives ±1 on the option die for an elf or
  a dwarf in the party, so the table really does run 0–7. The `mod` design
  handles it and a test pins it.
- `parse_ref` refuses rather than guesses when a cell names two sections, and
  will not read a bare number as an id — "bribe 100 gold" is not a jump to e100.

### Deferred, deliberately

- **Foe stats are asked for.** `do_join` asks how many, their combat skill and
  their endurance, because the graph has no access to the encounter record. Plan
  03 wires `creatures.py` in and these three questions disappear.
- **r341 costs the rest of the day** and the handler only notes it. Time cost is
  plan 04's model (r204f); the note names the rule so it is easy to find.
- **The bribe sum carries forward.** Declining r331 or r332 sends you to
  r321–r324, which ask for "the amount indicated" with nothing indicating it.
  The same sum is carried and a note says so. This is a reading, not a certainty.
- **`_r333` does not let you hire some rather than all.** The section allows it;
  the handler asks how many join, which amounts to the same thing without
  modelling the group.

### Smaller things

- `Machine(autosave=False)` — the path explorer runs thousands of short games and
  writing a save for each made the sweep too slow for the fast suite.
- `state.new_char` gained `pay_starts` and `terms`. Both are new keys on new
  characters only; anything reading them off an older save must use `.get()`.
- `sections._generic` became `_fallback` and now routes options tables to
  `graph.encounter_options` before falling through to "no handler yet".
