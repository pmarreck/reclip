---
purpose: Record of local-only environment state reclip depends on but git can't track — the oMLX app and its ~/.omlx/ config (model overrides, installed models, version, applied patches).
audience: both
maintained_by: agent
---

# Local Environment (out-of-repo dependencies)

reclip talks to a locally-running **oMLX** server for STT / LLM / TTS. oMLX and
its config live **outside this repo** (`/Applications/oMLX.app`, `~/.omlx/`), so
changes there leave no trace in git. This file — plus the tracked
`omlx-state.json` snapshot — is that trace.

## The diff mechanism

`omlx-state.json` (repo root) is a **redacted, deterministic snapshot** of
oMLX's local state: app version, installed models, and the model-type
overrides from `~/.omlx/model_settings.json`. Because it's version-controlled,
`jj diff omlx-state.json` shows exactly what local model state changed.

```sh
nix develop -c python scripts/omlx_snapshot.py          # refresh the snapshot
nix develop -c python scripts/omlx_snapshot.py --check   # exit 1 if live state drifted
```

Regenerate and commit `omlx-state.json` whenever you change models, overrides,
or update oMLX. (The `--check` mode is **local-only** — it reads `~/.omlx/` and
the running server, so it is NOT wired into `./test`/Garnix, which run in a
sandbox with neither. The pure redaction/build logic *is* unit-tested in
`tests/test_omlx_snapshot.py`.) Secrets (`api_key`, `secret_key`, `sub_keys`)
are redacted before writing — verified by a serialize-and-scan test.

## Current state — oMLX 0.4.4

Updated 0.3.8rc1 → **0.4.4** on 2026-06-20. The update resolved everything
that was previously patched/overridden locally:

- **WhisperProcessor patch — superseded.** `scripts/fix-omlx-stt.sh` (the
  mistral_common<1.10 fix) is now redundant: our fix was upstreamed as
  [jundot/omlx#1116](https://github.com/jundot/omlx/pull/1116) (merged
  2026-05-09, native in 0.4.4). Script kept for reference / older installs.
- **Parakeet model-type override — removed.** 0.4.4 natively discovers
  `parakeet-tdt-0.6b-v3*` as `audio_stt`, so the override we briefly added on
  0.3.8rc1 (which mis-discovered them as `llm/batched`) is no longer needed.
  `~/.omlx/model_settings.json` is back to its original 3 entries
  (whisper-large-v3-mlx, jina, gemma4). Backups: `model_settings.json.bak.*`.

## STT decision (evidence-based)

On 0.4.4, `word_timestamps=true` finally **populates** — and that picked the
winner. Measured on a 90s multi-speaker clip:

| STT model | segments | words | use |
|---|---|---|---|
| **whisper-large-v3-fp16** | 29 | **295** | ✅ chosen — word-level diarization now works |
| Qwen3-ASR-1.7B-8bit | 1 | 0 | better punctuation, but one blob (no attribution) |
| parakeet-tdt-0.6b-v3 | 0 | 0 | oMLX exposes no timestamps for it → useless here |

**`whisper-large-v3-fp16` is the STT** (already the `RECLIP_STT_MODEL` default).
Its word timestamps make the word-level diarization merge (in `speakers.py`,
inert until this oMLX update) finally active.

**Known upstream gap (optional PR target):** Parakeet natively produces
word/char/segment timestamps, but oMLX's STT engine returns only plain text for
it (0 segments) even on 0.4.4. Fixing that in oMLX/mlx-audio would let Parakeet
(smaller, faster, English) replace Whisper — not needed now that Whisper works.

## Models reclip depends on (referenced in ~/.config/reclip+/config.ini)
- **STT** (`RECLIP_STT_MODEL`): `whisper-large-v3-fp16`.
- **LLM** (`RECLIP_SUMMARIZE_MODEL` etc.): `gemma4-heretical-mlx-8bit`.
- **TTS** (`RECLIP_TTS_MODEL`): `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit`.

**Whisper is the only STT kept.** Removed 2026-06-20 (moved to `~/.Trash/`,
recoverable; ~6 GB reclaimed): both Parakeet variants (oMLX exposes no
timestamps for them) and `Qwen3-ASR-1.7B-8bit` (one-blob segments, no word
timestamps). Qwen3-ASR lingers in `omlx-state.json`'s `installed_models` until
the next oMLX restart refreshes its registry (disk already cleared).

If a referenced model isn't served by oMLX, that feature 500s with an
auth/“model not found” error — check `omlx-state.json`'s `installed_models`.
