# 01 — Engine core

**Goal:** the smallest thing that can suspend at a player decision, persist,
resume, and drive a rule handler to a conclusion. Everything after this is
rules; this is the machine that runs them.

**Depends on:** nothing.

**Done when:** you can start a game, be asked for a die roll by a handler written
as a generator, kill the process, restart, and land back at the same question with
the same state — and a headless test can drive the same flow from a list of
inputs.

---

## Design

### The three types

```python
# types.py

@dataclass(frozen=True)
class Event:
    kind: str        # prose | rule | roll | state | table | warn | banner
    text: str
    voice: bool = False   # would a person say this out loud? (D3)
    cite: str | None = None    # section id this came from

@dataclass(frozen=True)
class Ask:
    kind: str        # die | choice | number | confirm | name | hex
    prompt: str
    spec: dict       # kind-specific: {"die": "2d6"} / {"options": [...]} / {"min":0}
    why: str | None = None    # rule cite, shown but not spoken

class Outcome: ...   # base; see the verbs below
```

`Ask.spec` is what `ui/parse.py` needs to interpret an answer without a model.

### Control-flow verbs

A handler returns exactly one of these. The machine, not the handler, decides what
happens next.

| Verb | Meaning |
|---|---|
| `Goto(sid, mod=0)` | tail-call another section; `mod` adjusts its next die roll |
| `EnterCombat(spec)` | hand off to the combat flow (plan 03) |
| `Retry(why)` | pop to the caller's option list and re-offer, minus this option |
| `EndEvent()` | this event is finished; return to whatever was interrupted |
| `EndDay(why)` | the event consumed the rest of the day |
| `EscapeHex()` / `HideHere()` | r218 outcomes (plan 03 implements) |
| `Schedule(when, effect)` | queue a deferred effect (plan 06); does not end the handler |

`Refuse(reason)` is an exception, not a verb — it aborts to the UI (D5). Reuse
`procedures.Refuse`.

### The context object

`ctx` is the only thing a handler may touch. It is the enforcement point for D2.

```python
class Ctx:
    # asking (these are yielded)
    def die(self, spec, why=None) -> Ask         # "1d6", "2d6"
    def choose(self, *options, why=None) -> Ask
    def number(self, prompt, min=0, max=None) -> Ask
    def confirm(self, prompt) -> Ask
    def pick_char(self, among=None) -> Ask

    # emitting (these are called, not yielded)
    def say(self, text, cite=None)               # voice=True
    def note(self, text, cite=None)              # voice=False
    def read(self, sid, part=None)               # book prose via bp.to_prose

    # reading state (never mutates)
    def wits(self) -> int
    def party(self) -> list[dict]                # living men
    def mounts(self) -> list[dict]
    def gold(self) -> int
    def food(self) -> int
    def hex(self) -> str
    def day(self) -> int
    def all_mounted(self) -> bool
    def all_flying(self) -> bool

    # mutating (logged; each writes a state Event)
    def spend(self, n, why); def gain(self, n, why)
    def wound(self, who, n, why); def heal(self, who, n, why)
    def add_follower(self, ch); def drop(self, who, why)
    def eat(self, n)

    # verbs (return an Outcome)
    def goto(self, sid, mod=0); def retry(self, why); def end_event()
    def hide(self); def escape(); def end_day(why)
```

`ctx` holds no dice. `ctx.die` returns an `Ask`; the number comes back through the
journal.

### The journal and replay (D1)

```
save = {
  ...existing sheet fields...,
  "day_start": { <full sheet as of dawn today> },
  "journal":   [ {"ask": "die", "value": 4}, {"ask":"choice","value":"fight"}, ... ],
  "cursor":    { "flow": "day", "args": {...} }     # what to re-enter on resume
}
```

Resume:
1. Restore the sheet from `day_start`.
2. Re-enter the flow named by `cursor`.
3. Feed journal entries to each `Ask` in order, **with all `Event`s suppressed**.
4. When the journal is exhausted, stop at the next `Ask` and hand it to the UI.

Commit (end of day): write the post-dusk sheet as the new `day_start`, clear the
journal, advance the cursor to the new day.

Undo: drop the last journal entry and replay. A single `undo` command falls out
for free; expose it.

**Replay safety.** Store a `rules_fingerprint` (hash of the `engine/rules/`
sources) beside the journal. On resume, if it differs, warn loudly and offer to
discard the partial day rather than replay through changed code. A day is a small
thing to lose; a silently wrong replay is not.

### The machine

```python
class Machine:
    def __init__(self, save): ...
    def resume(self) -> Turn       # replay journal, stop at first live Ask
    def answer(self, value) -> Turn
    # Turn = (events: list[Event], ask: Ask | None, done: bool)
```

Internally the machine keeps a **stack of running generators** — the encounter
stack. `Goto` pushes, `EndEvent` pops, `Retry` pops to the nearest frame that
offered a choice and re-asks it with the failed option removed. Plan 02 exercises
this properly; plan 01 only needs push and pop.

### Input parsing (no LLM)

`ui/parse.py` maps raw text to a value using `Ask.spec` alone.

| Ask kind | Accepts |
|---|---|
| `die` `1d6` | `3`, `d3`, `roll 3`, `three` |
| `die` `2d6` | `7`, `3 4` (sums, validating each die), `3+4` |
| `choice` | exact label, unique prefix (`f` → `fight`), 1-based index |
| `number` | digits, with range validation |
| `confirm` | `y/yes/ok/sure` · `n/no` |
| `pick_char` | reuse `state.find` — it already refuses on ambiguity |
| any | `?` help, `sheet`, `undo`, `save`, `quit` — handled by the UI, never reach the handler |

Out-of-range and unparseable input re-prompts with the valid set. This is the
whole of "interpret user input when obvious" — it never needs a model, and it must
never silently coerce (a `7` answered to a `1d6` is an error, not a `6`).

### Terminal UI

`ui/term.py`, deliberately plain: print `Event.text`, indent or dim `voice=False`
lines, show `Ask.prompt` and the valid answers, read a line, parse, loop. No
colour library, no curses. Meta-commands (`sheet`, `undo`, `quit`, `log`) are
handled before parsing. `--auto-dice` rolls instead of asking, and still journals
the value, so an auto-dice game replays identically.

## Work items

- [x] `src/engine/types.py` — `Event`, `Ask`, the `Outcome` verbs. Also `Invoke`
      (deviation 1 below).
- [x] `src/engine/ctx.py` — `Ctx` over a `state.py` save dict. Mutators log via
      `state.note` and emit a `state` Event. (`eat` landed as `food_change`; the
      r215 meal itself is plan 05.)
- [x] `src/engine/machine.py` — generator stack, `resume`/`answer`, journal
      append, replay, commit, undo, fingerprint check.
- [x] `src/engine/sections.py` — `@section("r317")` decorator and registry;
      `handler(sid)` falls back to a generic handler. **Partial by intent:** the
      fallback reads the section and then says plainly that nothing resolves it
      yet. Table resolution moved to plan 02, which owns the table-driven
      sections anyway — see deviation 6.
- [x] `src/ui/parse.py` — the table above.
- [x] `src/ui/term.py` — the loop, meta-commands, `--auto-dice`.
- [x] `play2` — entry point: load or create a game, `Machine.resume()`, run.
      `--new` builds a bare save; real setup is plan 05's `new_game` flow.
- [x] Save-format migration: `day_start`, `journal`, `cursor`, `fingerprint`
      under `g["engine"]` (deviation 2); `version` 2, and a v1 save loads.

## Proving it

A throwaway handler is enough for this plan — do not wait for plan 02.

```python
@section("demo")
def demo(ctx):
    ctx.say("A stranger blocks the road.")
    what = yield ctx.choose("talk", "fight", "flee")
    die  = yield ctx.die("1d6", why="r301")
    ctx.note(f"{what} on a {die}")
    return ctx.end_event()
```

Built as `src/engine/rules/demo.py`, which asks one of each shape and invokes a
sub-flow. Delete it when `day` is a real flow.

- [x] Drive it from the terminal.
- [x] Kill the process between the two asks; restart; land on the die question
      with the choice remembered.
- [x] Drive it headless from a list of answers and assert the resulting sheet.
- [x] `undo` after the die returns you to the die question.

## Tests

64 tests: `python3 -m unittest discover -s tests -t tests`.

- [x] Round-trip: state + journal → replay → identical state.
      `test_resume_lands_on_the_same_question`, `test_replay_is_deterministic`.
- [x] Replay emits only the events since the last answered question — *not* no
      events at all, which would leave the player facing a question with no
      context (deviation 4). `test_resume_shows_only_the_current_segment`.
- [x] Every `Ask.spec` shape has a parse test including the rejection cases.
      `tests/test_parse.py`, 21 tests.
- [x] A v1 save loads. `test_a_version_1_save_loads`.
- [x] `BP_GAME` set for all of it; save deleted afterwards.
      `harness.temp_game`, asserted by `TestHygiene`.

Beyond the plan:

- [x] The handler contract fails loudly — yielding a non-`Ask`, returning a
      non-verb, a non-generator handler, and a `goto` loop that never asks all
      raise with a message naming the mistake. `TestHandlerContract`.
- [x] Decision D2 enforced by an AST walk: no `random`, `time`, `datetime`,
      `secrets`, `uuid` or `os.environ` anywhere under `engine/rules/`.
      `tests/test_determinism.py`.
- [x] `bp` still reads a save the engine has written. `TestInteroperability`.

## Notes

- Do not touch `bp.py`, `narrator.py`, or `src/prompts/system.md` in this plan.
  The old client must keep working while the new one is built beside it.
- Resist putting rules in `ctx`. `ctx.all_mounted()` is a state query; "can this
  party escape mounted" is r312 and belongs in plan 02.

---

## As built

Landed. `./play2`, 64 tests (`python3 -m unittest discover -s tests -t tests`).
Six things came out differently from the sketch above; plan 02 inherits them.

**1. A fourth concept: `Invoke`.** The sketch had only `Ask` yielded. But a day
flow needs to run an event *and get its outcome back* — it must know whether a
travel event cost the rest of the day (r204f). So a handler may yield `Invoke(sid)`
to run a sub-flow and receive its `Outcome`:

```python
outcome = yield ctx.invoke("e003")
```

`Goto` remains the tail call — the caller is finished and does not resume.
`Invoke` is the sub-call. The machine's stack holds both; `Goto` replaces the top
frame, so whoever invoked it still waits underneath.

**2. Save fields nest under `engine`.** `day_start`, `journal`, `cursor` and
`fingerprint` live in `g["engine"]` rather than at the top level, which keeps
`bp`'s view of the sheet unchanged. `snapshot()` excludes the block, `restore()`
preserves it.

**3. Parameters ride on `ctx`, not on the signature.** Handlers are always called
as `fn(ctx)`. What the calling section supplied is `ctx.param("amount")` /
`ctx.need("amount")`, with `ctx.sid` and `ctx.mod` alongside. This matters for
plan 02, where bribe amounts come from the caller and most sections take none.

**4. Replay keeps the last segment's events.** Suppressing *all* events during
replay leaves the player staring at a question with no context. Each replayed
answer clears the outbox first, so what survives is exactly the events since the
last answered question — verified by `test_resume_shows_only_the_current_segment`.

**5. `Retry` refuses, by design.** No frame sets `retry_to` yet, so a `Retry`
raises a `Refuse` naming plan 02. `Frame.retry_to` and `Machine._retry` are in
place for it.

**6. The generic handler reads but does not resolve.** The sketch had it resolve
a section's table when `tables.json` has one. It reads the section and then says
which plan owns the gap — plan 02 for a tabled section, plan 07 for a prose one.
Resolving a table means knowing what its cells *mean* (`bribe (5) r322` is a
target plus a parameter), and that parsing is plan 02's first work item. Better a
handler that says what it cannot do than one that half-does it.

Also worth knowing:

- `state.VERSION` is 2. The bump is additive — `migrate()` only stamps the
  number, since a v1 save is a v2 save with no engine block yet.
- An **empty journal refreshes `day_start`** on load. That is what makes using
  `bp` on a save between days harmless: with nothing recorded since dawn,
  whatever the sheet says now *is* dawn.
- Meta-commands take a `/` prefix (`/sheet`, `/undo`, `/log`, `/quit`) so they
  cannot collide with a choice label. `?` alone still works.
- `src/engine/rules/demo.py` is scaffolding. Delete it when `day` is a real flow.
