# 08 — Economy, transport and items

**Goal:** the accounting the rest of the game leans on — what treasure you get,
what you can carry, what you leave behind, and what the special possessions do.

**Depends on:** 05. Independent of 06 and 07; can run in parallel with either.

**Done when:** a won fight produces the right treasure, an overloaded party is
told so and made to choose, and every possession e180–e194 has a handler or a
documented refusal.

---

## Treasure (r225, r226)

`procedures.treasure_rows` already reads the r226 grid and `bp treasure` already
resolves a wealth code against a `1d6`. What is missing is the plumbing.

- [ ] After a won fight, one `1d6` per dead enemy against its wealth code
      (plan 03 collects the codes). Routed enemies yield nothing.
- [ ] Letter suffixes are possessions, not gold: `A`, `B`, `C` each map to a row
      of six event references (`e180`–`e194`). Roll `1d6` on the right row.
- [ ] A dash in the grid means no result — `Refuse`, do not substitute zero.
- [ ] r225 is also the starting-gold roll at wealth code 2; plan 05's setup
      already uses it. One implementation, both callers.

## Transport (r206)

The rule the game quietly enforces and no player tracks by hand:

```
1 food unit                = 1 load
each 100 gold, or part     = 1 load
each person carried        = 20 loads
weapons                    = 0

man on foot   10 loads
mount         30 loads, or a rider plus 10
```

`state.capacity` and `state.loads` already compute both sides, including the
starvation halving from r216b.

- [ ] Check capacity whenever gold or food increases, and at dawn.
- [ ] When over capacity, do not silently drop anything: report the excess and ask
      what to leave behind or cache (r214).
- [ ] A mount that starves to zero capacity dies (r216); a starving winged mount
      cannot fly, which changes travel options (plan 04). Wire both.

## Caches (r214)

- [ ] `cache_action(ctx)` — leave goods in the current hex, recorded on the sheet
      with the hex id.
- [ ] The r203 "search for a cache" action retrieves one.
- [ ] Caches are per-hex and persist for the game. Add `caches: {}` to the save.

## Wounds and endurance (r221, r222)

- [ ] Confirm the death and unconsciousness thresholds in `state.dead`,
      `unconscious` and `serious` against r221 — they were written for the
      combat resolver and are now load-bearing everywhere.
- [ ] Poison wounds: a `poison` count per character that r222 healing skips.
      Plan 03 raises this; decide it in one place and implement it here.
- [ ] Healing is one wound per character per full rest day, capped at original
      endurance, and suppressed by e120-style conditions (plan 06).

## Special possessions (e180–e194)

Fifteen sections, of which `e189` is absent from the source PDF. They are
persistent items with ongoing effects — the closest thing the game has to
character abilities, and mostly not one-shot events.

- [ ] Add `possessions: []` to the save, each with the section id and any state.
- [ ] `state.sheet()` lists them.
- [ ] Each becomes a handler *and* a set of hooks: some modify rolls (charm,
      charisma), some fire on death (`e192` Resurrection Necklace, which plan 06's
      queue handles), some grant travel abilities.
- [ ] `e189` must refuse with the errata note rather than fall through. It is
      referenced by e037, e043, e144 and e195, so it is genuinely reachable.

## Rafting (r213)

Reachable only from special events (e122 Raftsmen and others). `travel.json`
carries the eleven-row raft table already.

- [ ] `raft_travel(ctx)` — no lost check (r205b), the raft table for events.
- [ ] Defer until plan 07 batch G unless an earlier batch reaches it.

## Trap locks (r227) and True Love (r228)

Two small rules sections referenced from events. `r227` resolves trap injuries
(e031 and others); `r228` is its own thing. Both are single handlers.

## Work items

- [ ] `src/engine/rules/economy.py` — treasure, loads, caches.
- [ ] `src/engine/rules/items.py` — possessions and their hooks.
- [ ] Save-format additions: `caches`, `possessions`, `poison` per character.
- [ ] Wire capacity checks into the gold/food mutators in `ctx`.

## Tests

- **The whole r226 grid:** every wealth code × every die value returns gold, a
  letter result, or a refusal — and never `None`.
- **Letter rows:** `A`, `B`, `C` each map to their six sections; `e189` refuses.
- **Load arithmetic:** the boundary cases — exactly 100 gold, 101 gold, a rider
  plus baggage, a starving mount at each halving step.
- **Overload:** gaining treasure past capacity prompts rather than silently
  succeeding or silently dropping.
- **Caches:** leave, travel away, return, retrieve; and a cache in a hex you never
  return to does not leak into the sheet's totals.
- **Possessions:** each item's hook fires where it should and nowhere else.

## Open questions

- **Is gold weightless below 100?** "Every 100 gold pieces, or fraction thereof,
  counts as one load" reads as 1 load for 1–100 gold, so any gold at all costs a
  load. `state.loads` may currently round differently — check it before writing
  tests around it.
- **Do possessions transfer when a follower dies or deserts?** The rules are
  quiet. Recommended: ask the player once and record the answer, rather than
  picking a rule.
