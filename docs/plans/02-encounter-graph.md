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

> If open question Q1 in the overview resolves to (b), this table and the JSON it
> becomes move out of git together. It encodes branch structure, not prose.

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

- [ ] Write `parse_ref(cell)` — extracts `(target_sid, amount)` from a table cell
      or result string. Cells look like `bribe (5) r322`, `escape mtd r312`,
      `converse r341`, `surprise r303`.
- [ ] Run it over every `options` table and every `table`/`inline` result in
      `data/tables.json` and assert every cell yields exactly one target. Anything
      that does not is either an errata case or a parser bug — resolve both before
      writing handlers.

## Retry semantics

`Retry` is the reason the machine needs a stack. The failing sections
(r314, r315, r317–r320) say to return to the previous section and select another
option.

Specify it as: **re-offer the calling section's options for the row already
rolled, minus the option just attempted.** The die has been rolled; the row is
fixed; only the column is back in play.

- [ ] Frames record `(sid, row, offered_columns, used_columns)`.
- [ ] `Retry` pops to the nearest frame with offered columns and re-asks.
- [ ] If every column is used up, `Refuse` and let the player rule — do not
      invent a default.

**Open question:** may a player re-attempt the same column? The text does not
forbid it, but re-rolling the same check until it passes is clearly not intended.
The spec above forbids it. Make it a flag so it is easy to change.

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

- [ ] Resolve overview Q1 and Q3.
- [ ] `data/graph.json` — the table above, one entry per section, with a
      `"cite"` field naming the section it came from and no prose.
- [ ] `src/engine/rules/graph.py` — the eight primitives, plus explicit handlers
      for the seven sections that do not fit (r312, r313, r331, r332, r338, r339,
      r340).
- [ ] `parse_ref` plus its exhaustiveness test.
- [ ] Wire the five table-bearing sections through the existing table resolution
      rather than re-encoding them.
- [ ] `Join` needs terms the party model does not have yet: `pay_per_day`,
      `pay_starts`, `equal_share`, `leaves_at_settlement`, `no_abandon`,
      `group_all_or_none`. Add them to `state.new_char` and make the dusk wage
      step honour `pay_per_day`. Enforcement of `equal_share` and
      `leaves_at_settlement` is plan 06's queue — for now, record the terms and
      note them on the sheet.

## Tests

- **Reachability:** from every r3xx entry point, every path terminates in
  `EnterCombat`, `EscapeHex`, `HideHere`, `EndEvent`, or `Refuse`. Enumerate by
  driving each section with every die value 1–6 (and 2–12) at every `Ask`. No
  path may loop forever — `r330` → `r3xx` must not return to `r330`.
- **e003 sweep:** all six rows × three columns resolve.
- **`parse_ref`:** exhaustive over `tables.json`, as above.
- **Retry:** a forced failure at r317 from e003 re-offers talk and fight, not
  evade.
- **Modifiers:** `Battle(+1)` and `Battle(-1)` shift the r330 lookup and clamp at
  the 2-or-less and 12-or-more ends.

## Notes

- Every section in the graph is a *procedure*, never prose to read aloud. Their
  bodies exist in `sections.json` and the handler may `ctx.note` a one-line
  summary, but the player hears the outcome, not the section.
- `r343` is used from outside the graph too (events that attack one character).
  Implement it once, in plan 03.
