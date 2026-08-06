# 10 — Narration

**Goal:** put the LLM back, in the one place it is worth having — outside the
rules, downstream of resolved state, with no ability to change anything.

**Depends on:** 05. The game must be fully playable and enjoyable without this.

**Done when:** `play2 --narrate` reads the game beautifully, `play2` alone plays
it identically but plainly, and pulling the plug on Ollama mid-session degrades
the prose and nothing else.

---

## The rule

**The narrator consumes events. It never produces them.**

No tool calls, no state writes, no control flow, no deciding what happens next.
It receives a list of `Event`s and returns prose. If it hallucinates, the result
is a badly-worded sentence, not a wrong ruling — which is the entire reason to
build the previous nine plans first.

This also means the ~900-token system prompt shrinks dramatically. Most of
`src/prompts/system.md` exists to stop the model doing things it will no longer be
able to do: it will not know section ids, will not be asked to keep the sheet,
will not choose when the day ends.

## The four jobs

Ordered by value per token.

### 1. Combat recaps

The highest-value one, and the user-identified original case. A twelve-round fight
is forty lines of arithmetic. Feed the log, get three sentences. The wounds are
already applied; the model is describing, not adjudicating.

### 2. Resume summaries

On loading a save: day, gold, food, party, wounds, position, pending effects,
recent log. "You are eleven days out, two hundred short, and Garth is still
limping." A structured sheet cannot say that and it is the thing you actually want
when you sit back down.

### 3. Day and journey colour

The connective tissue between the book's terse 1981 prose and the engine's flat
statements. Weather, terrain, the mood of the party. Strictly ornamental.

### 4. Free-text intent (optional)

"Sell the horse and buy food" → engine commands. Genuine model work, but a
menu-driven UI removes the need entirely, and every use is a chance to
misunderstand. Recommended: leave it off by default and only enable it for the
`sheet`-adjacent commands where a mistake is visible and harmless.

## Design

```python
class Narrator:
    def on_events(self, events: list[Event], scene: str) -> str | None
```

- Receives only `Event`s and a read-only snapshot of the sheet.
- Returns prose or `None`. `None` on any error, timeout, or unavailable backend —
  **the UI must render fine either way**, and a narrator failure is never fatal.
- Runs *after* the engine has emitted, so the player sees the mechanical result
  immediately and the prose arrives beside it, not instead of it.

### Guards to keep from the current client

`narrator.py` earned these and they still apply:

- [ ] **Quoted-span check.** Flag quoted text the engine never emitted. It catches
      invented book prose.
- [ ] **Never speak blockquotes.** Same reason.
- [ ] **Keep commands out of narration.** It is read aloud; a command mid-sentence
      becomes a hole.
- [ ] **Do not repeat what the player already heard.** Under the new design this
      is exact rather than heuristic: the UI knows precisely which events it
      rendered, so pass that set and instruct the model to add, not restate.

### The prompt

Start from scratch rather than trimming `system.md`. Target under 300 tokens. It
needs to say roughly: you are describing events that have already happened in a
1981 sword-and-sorcery solitaire game; you do not know the book; do not invent
outcomes, numbers, names or places; do not ask questions; two or three sentences.

The measured finding from CLAUDE.md still holds and should be re-checked here: a
long prompt made the model invent an opening passage whole; a short one telling it
what it does not know made it behave. Keep the token-count warning.

## Voice

`Event.voice` (D3) carries the read-aloud decision that stdout/stderr used to. The
TTS path takes `voice=True` events plus the narrator's prose; section ids, rule
cites, die notation and tables never reach it. Port `narrator.spoken()`'s
transformations — stripping "(r215b)" and "2d6" out of the spoken copy — since
those have no spoken form.

`docs/local-tts.md` documents the current backends; the speaking layer moves over
unchanged.

## Work items

- [ ] `src/narrate/__init__.py` — the `Narrator` protocol and a null implementation
      used when narration is off.
- [ ] An Ollama backend with a hard timeout and silent failure.
- [ ] The four scene types, each with its own short prompt.
- [ ] The quoted-span guard, ported.
- [ ] TTS wiring off `voice=True`.
- [ ] `play2 --narrate`, off by default.

## Retiring the old client

Once this works:

- [ ] `./play` points at `play2`.
- [ ] `narrator.py` is reduced to the narration backend or deleted; its walks
      (`run_setup`, `run_dusk`) have already moved to plan 05 and must not be left
      duplicated.
- [ ] `src/prompts/system.md` is replaced by the short scene prompts.
- [ ] `bp.py` stays. It remains the reference CLI for checking the engine against
      the book by hand, which is exactly what you want when a handler is wrong.
- [ ] Rewrite `CLAUDE.md`. Its central invariant — stdout is heard, stderr is not —
      becomes `Event.voice`, and its "let code drive known sequences" section
      becomes the whole architecture rather than an aspiration for setup and dusk.

## Open question

**Does narration belong in the loop or beside it?** Streaming prose after each
event is immersive but slow, and a local model adds a second or two per turn.
Recommended: narrate at scene boundaries only — after a fight, after a day, on
resume — so the turn-by-turn play stays instant. Measure before choosing;
this is the difference between a game that feels responsive and one that does not.
