# 06 — Deferred effects

**Goal:** the piece of state the game has always needed and never had. Thirty-five
sections schedule a consequence for later — tomorrow at dawn, tonight before the
meal, every day until you leave the desert — and nothing in the save can hold one.

**Depends on:** 05 (it defines the hook points these fire at).

**Done when:** an event that says "at the start of tomorrow, roll one die for each
follower" does exactly that, tomorrow, without anybody remembering to.

---

## Why this is structural

Today a deferred effect lives in the model's context window, which is where it
gets lost. Under a deterministic engine it has nowhere to live at all. Every
handler in plan 07 that says "tomorrow" is blocked on this, so it comes before
the bulk of the event work.

The sections that schedule something:

```
e009 e010 e011 e035 e053 e060 e064 e077 e078 e079 e092 e095 e096 e107 e114
e115 e117 e120 e122 e123 e133 e150 e151 e155 e156 e160 e161 e192
r203 r205 r209 r212 r216 r222 r332
```

Note the rules sections in that list: `r216` starvation penalties, `r222` healing,
`r332` wages starting tomorrow. The queue is not only for exotic events — the core
loop needs it too.

## Design

### An effect is data, not a closure

It has to survive a save file, so it is a JSON record naming a registered handler:

```json
{
  "id": "e010-desertion",
  "kind": "desertion_check",
  "cite": "e010",
  "fire": "dawn",
  "on_day": 12,
  "until": null,
  "params": {"die": "1d6", "deserts_on": 3, "who": "followers"},
  "note": "the party's temper after refusing the farmer"
}
```

- `fire` — which hook: `dawn`, `pre_meal`, `dusk`, `night`, `enter_hex`,
  `on_settlement`, `before_roll`.
- `on_day` — an absolute day for one-shots. Convert "tomorrow" at schedule time,
  never store a relative offset.
- `until` — a named predicate for standing conditions, evaluated at each fire:
  `left_desert`, `at_oasis`, `left_hex`, `wounds_healed`, `day_reached`.
- `params` — everything the handler needs. No references to live objects.

`ctx.schedule(...)` from plan 01 appends one. `ctx.effects()` reads them. The
engine fires them; handlers never poll.

### The five shapes

| Shape | Example | Handler behaviour |
|---|---|---|
| One-shot roll per character | e010 desertion, e079 colds, e133 madness | roll, apply, remove |
| Recurring condition with an exit predicate | e120 desert exhaustion, e092/e096 | apply penalties each fire; check `until`; remove when true |
| Weather / repeating event chain | e079's rains continuing, e078 | a one-shot that may reschedule itself |
| Modifier on a future roll | e065 subtracting one from e060 | consumed by the next matching roll, then removed |
| Party-terms enforcement | r332 wages from tomorrow, r331 equal share, r335 leaves at a settlement | fires at dusk or on entering a settlement |

That last row is what makes plan 02's hire terms real. Record them there; enforce
them here.

### Standing conditions vs effects

Some are better modelled as flags on a character or on the game than as queued
effects — `starve` already is one. Rule of thumb: if it is a **modifier consulted
by other rules**, it is a field (`starve`, `poison`, `no_healing`); if it is a
**thing that happens at a time**, it is an effect. e120 is both: it sets
`no_healing` and `capacity_halved` fields, and queues an effect that clears them
when the party leaves the desert.

- [ ] Add the condition fields the events need: `no_healing`, `cannot_ride`,
      `capacity_halved`, `poison`, `cannot_fly`. Put them on the character where
      they apply per character, on the game where they apply to everyone.
- [ ] Every field is cleared by exactly one effect. An orphan condition with no
      effect to clear it is a bug — assert it in the invariant tests.

### Ordering and visibility

- Effects fire in insertion order within a hook. Record the order; do not rely on
  dict iteration.
- Every fire emits an Event citing the section that scheduled it, so the player can
  see *why* their follower just deserted. This matters more than it sounds: an
  unexplained roll at dawn is indistinguishable from a bug.
- `sheet` shows pending effects in plain language. `state.sheet()` grows a section.

## Work items

- [ ] `src/engine/rules/effects.py` — the record schema, `schedule()`, `fire(hook)`,
      the predicate registry, the handler registry.
- [ ] Save-format addition: `effects: []`. It lives in `day_start` so replay
      restores it correctly.
- [ ] Wire the plan 05 hooks.
- [ ] Condition fields on `state.new_char` and the game dict.
- [ ] Retrofit the three rules sections that need it: `r216` penalties,
      `r222` healing suppression, `r332`/`r333` wage start dates.
- [ ] Extend `state.sheet()` to show pending effects and active conditions.

## Tests

- **Round-trip:** an effect scheduled today survives a save, a restart, and fires
  tomorrow.
- **Ordering:** two effects on the same hook fire in the order scheduled.
- **Exit predicates:** e120's exhaustion clears on leaving the desert and on
  reaching an oasis, and not before.
- **Self-rescheduling:** e079's rains continue on a 4+, stop otherwise, and always
  stop by the third day.
- **No orphans:** every condition field set by some handler is cleared by some
  effect. Run it as a static check over the registry, not a play test.
- **Replay:** a day containing a fired effect replays to an identical sheet.

## Open questions

- **Does an effect fire while the party is mid-encounter at dawn?** It cannot be —
  the day commits at dusk, so dawn is always a clean boundary. Confirm this
  survives contact with "skip N days" events, which advance the clock inside a
  day's flow.
- **Interaction between two weather events.** e079 says the rains stop "unless
  this event occurs again". Two overlapping instances should merge, not stack.
  Give each effect an `id` and make scheduling idempotent by id.
