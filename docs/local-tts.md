# Local text-to-speech

`bp say` supports three backends. It picks one automatically, but you can force
the choice with `--backend` or `BP_TTS`.

| Backend | What it is | Cost | Quality |
|---|---|---|---|
| `kokoro` | Kokoro-82M running locally via mlx-audio | free, offline | very good |
| `elevenlabs` | the hosted API | paid | best |
| `say` | macOS built-in | free, offline | robotic |

`auto` (the default) uses `kokoro` if a local server is listening, else
`elevenlabs` if a key is set, else `say`. Nothing ever hard-fails: if a backend is
unreachable, it falls back to `say` and tells you why.

## Why Kokoro rather than VibeVoice

**Kokoro is the right choice for this project.** It is 82M parameters (~330 MB),
Apache-2.0, and runs many times faster than real time on Apple Silicon. Sections
here are a few paragraphs of single-narrator prose, which is exactly its strength.

**VibeVoice is not recommended, for a practical reason rather than a technical
one.** Microsoft open-sourced it in August 2025 and then removed the code from the
repository about ten days later, citing uses inconsistent with its intent. The
official repo now carries documentation only. The weights remain on Hugging Face
and community forks preserve the code, but depending on a withdrawn model in a
public repository is a fragile foundation — and VibeVoice is built for long-form
multi-speaker dialogue (podcasts, up to 90 minutes, 4 speakers), which is far more
machinery than reading one game section aloud needs.

If you want it anyway, see [VibeVoice](#vibevoice-if-you-still-want-it) at the end.

## Install Kokoro

Kokoro runs in its own environment and `bp` talks to it over HTTP, so this adds no
dependencies to this repository.

```sh
brew install ffmpeg          # only needed for mp3/flac output; wav works without it
uv tool install --force "mlx-audio[server]" --with "misaki[en]" --with soundfile \
  --with "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
```

The `[server]` extra matters: without it you get the CLI but no HTTP server, and
starting it fails with `ModuleNotFoundError: No module named 'uvicorn'`. The extra
pulls in fastapi, uvicorn, python-multipart and webrtcvad.

`misaki` is Kokoro's text front-end (grapheme-to-phoneme) and is required — Kokoro
will not run without it. Note the **`[en]`** extra: plain `misaki` installs the
package but not its English support, and Kokoro then fails at generation time.
`soundfile` handles wav encoding.

The spacy model wheel is the third thing that has to be pre-installed. `misaki.en`
otherwise tries to fetch `en_core_web_sm` *at generation time* by shelling out to a
pip install, which fails inside a `uv tool` environment with `error: No virtual
environment found` — and the request then hangs. Installing it up front avoids that
entirely. Match the model to your spacy major version: spacy 3.8.x takes
`en_core_web_sm-3.8.0`.

That installs two commands on your PATH:

```
mlx_audio.server          the OpenAI-compatible HTTP server
mlx_audio.tts.generate    one-shot CLI generation
```

### Confirm it works

The model downloads on first use (~330 MB, from Hugging Face):

```sh
mlx_audio.tts.generate \
  --model mlx-community/Kokoro-82M-bf16 \
  --text "Evil events have overtaken your Northlands Kingdom." \
  --voice bm_george --play
```

If you hear the line, you're done. Generation is several times faster than
real time on an M-series chip, so a long section takes a few seconds.

## Run the server

`bp say` talks to a server rather than loading the model per call — that avoids
paying model load time on every section.

```sh
mlx_audio.server --port 8000
```

Leave it running in a spare terminal. To check it by hand:

```sh
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"mlx-community/Kokoro-82M-bf16","input":"Hello.","voice":"bm_george"}' \
  --output /tmp/test.wav && afplay /tmp/test.wav
```

## Point `bp` at it

`.env.example` is an annotated template covering every option; `cp .env.example .env`
and edit. The relevant lines are:

```sh
BP_TTS=kokoro
KOKORO_VOICE=bm_george
# defaults, override only if you moved the server or want another model:
# KOKORO_URL=http://127.0.0.1:8000/v1/audio/speech
# KOKORO_MODEL=mlx-community/Kokoro-82M-bf16
# KOKORO_FORMAT=wav      # server defaults to mp3; set this to override
```

`bp` names the temp file from the response's content-type, so playback gets a
correctly-labelled `.mp3` or `.wav` whichever the server chooses.

Then:

```sh
bp show e001 | bp say --stdin              # uses whatever BP_TTS says
bp say --backend kokoro "the Prince rides"   # force local
bp say --voice bm_lewis "a swordsman"        # override the voice for one call
bp say --backend say "no model at all"
```

You can leave `BP_TTS` unset entirely: with the server running, `auto` finds it.

## Voices

Voice ids are `<language><gender>_<name>`: `a` = American English, `b` = British.

**British male** — `bm_george`, `bm_daniel`, `bm_fable`, `bm_lewis`
**British female** — `bf_alice`, `bf_emma`, `bf_isabella`, `bf_lily`
**American male** — `am_michael`, `am_adam`, `am_eric`, `am_fenrir`, `am_liam`,
`am_onyx`, `am_puck`, `am_echo`, `am_santa`
**American female** — `af_heart` (the default), `af_bella`, `af_nicole`, `af_sarah`,
`af_alloy`, `af_aoede`, `af_jessica`, `af_kore`, `af_nova`, `af_river`, `af_sky`

`bm_george` is suggested as the closest match to the ElevenLabs narrator this
project was set up with. Kokoro also covers several non-English languages; see the
[voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).

To audition a few quickly:

```sh
for v in bm_george bm_daniel bm_lewis am_michael; do
  echo ">> $v"; bp show r316 | bp say --stdin --backend kokoro --voice "$v"
done
```

## Dropping ElevenLabs entirely

Once Kokoro sounds good to you, delete `ELEVENLABS_API_KEY` and
`ELEVENLABS_VOICE_ID` from `.env` and set `BP_TTS=kokoro`. Nothing else refers to
them. The `elevenlabs` backend stays in the code as an option; it simply reports
that no key is set and falls through to `say`.

## Troubleshooting

**"no local TTS server at ..."** — the server isn't running, or is on another port.
Start `mlx_audio.server --port 8000`, or set `KOKORO_URL`.

**"No Metal device available"** — MLX needs real GPU access. This appears in
sandboxed, headless, or remote sessions; run in a normal terminal on the Mac itself.

**`error: No virtual environment found`** in the server log, and the client hangs —
`misaki.en` is trying to download the spacy model at runtime. Install the model
wheel as shown above, then restart the server.

**`ModuleNotFoundError: No module named 'uvicorn'`** when starting the server — you
installed mlx-audio without the `[server]` extra. Reinstall:

```sh
uv tool install --force "mlx-audio[server]" --with "misaki[en]" --with soundfile \
  --with "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
```

**`Kokoro requires the optional 'misaki' package`, but misaki *is* installed** —
this message is misleading. The real failure is usually a missing dependency of
`misaki.en`, most often `num2words`, which only arrives with the `[en]` extra. Look
further up the server's traceback for the underlying `ModuleNotFoundError`, then
reinstall with `--with "misaki[en]"` as above.

The server logs this on its own stdout, not in the HTTP response: a failed
generation returns `200 OK` with chunked encoding and then dies mid-stream, so the
client just sees an empty file or `IncompleteRead`. When a request produces no
audio, read the server's terminal.

**`uv` resolution fails or picks nothing** — mlx-audio sometimes publishes
pre-release versions; add `--prerelease=allow` to the install command.

**First call is slow** — that's the model downloading and loading. Subsequent calls
against the running server are fast.

**Wrong pronunciation of rule numbers** — that's this repo, not Kokoro. `bp` rewrites
`r203` to "rule 203" before synthesis; see `to_prose()` in `src/bp.py`, and check
the exact text with `bp show <id>`.

## VibeVoice, if you still want it

The code is no longer distributed by Microsoft. The route is a community fork plus
weights from Hugging Face:

- Community fork: <https://github.com/vibevoice-community/VibeVoice>
- Weights: `microsoft/VibeVoice-1.5B` (the 7B was withdrawn but is mirrored)

It has no MLX port, so on Apple Silicon it runs through PyTorch with the `mps`
backend, which is slower and heavier than Kokoro — the 1.5B model wants several GB
of RAM and is roughly real-time rather than many times faster. It does not serve an
OpenAI-compatible endpoint, so `bp` would need a new backend function; the shape to
copy is `speak_kokoro()` in `src/bp.py`, which only has to return
`(audio_bytes, extension)`.

Its real advantage is multi-speaker dialogue — distinct voices for the Prince and
each character encountered. That is a genuinely appealing idea for this game, but
it is a much larger project than swapping a TTS engine, and it rests on a codebase
its author pulled.
