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

## Active local changes

### 1. Parakeet STT model-type override  (added 2026-06-20)
- **What:** `~/.omlx/model_settings.json` gives `parakeet-tdt-0.6b-v3` and
  `parakeet-tdt-0.6b-v3-mlx-bf16` `"model_type_override": "audio_stt"`.
- **Why:** oMLX **0.3.8rc1** mis-discovers Parakeet as `type: llm, engine:
  batched`, so transcription 500s with `KeyError: 'model_type'`. The override
  forces it onto the STT engine (same mechanism oMLX uses for
  `whisper-large-v3-mlx` and `jina-code-embeddings`).
- **Status / caveat:** overrides apply only at oMLX **model-pool discovery**
  (startup), so an oMLX **restart** is required to take effect. Even then, if
  0.3.8's bundled mlx-audio lacks a Parakeet STT loader the override fixes
  routing but not capability — updating oMLX to ≥0.4.x is the durable fix.
- **Revert:** delete those two entries from `model_settings.json` (timestamped
  backups: `~/.omlx/model_settings.json.bak.*`).
- **Verify:** `curl -F file=@clip.wav -F model=parakeet-tdt-0.6b-v3 …
  /v1/audio/transcriptions` returns text, not a `'model_type'` error.

### 2. oMLX WhisperProcessor patch  (superseded — now upstream)
- **What:** `scripts/fix-omlx-stt.sh` patched a locally-installed oMLX so its
  bundled transformers could build `WhisperProcessor` (mistral_common <1.10 vs
  transformers 5.x mismatch). Applied to the **0.3.8rc1** install.
- **Status:** **superseded.** Our fix was upstreamed as
  [jundot/omlx#1116](https://github.com/jundot/omlx/pull/1116) (**merged
  2026-05-09**, native in releases after that incl. 0.4.x). Updating oMLX makes
  the local patch redundant — the script stays for reference / older installs.

## Models reclip depends on (referenced in ~/.config/reclip+/config.ini)
- **STT** (`RECLIP_STT_MODEL`): `whisper-large-v3-fp16` (current default).
  Candidates under evaluation: `Qwen3-ASR-1.7B-8bit` (best text, 1 segment —
  bad for diarization), `parakeet-tdt-0.6b-v3` (word timestamps — blocked on
  the override above).
- **LLM** (`RECLIP_SUMMARIZE_MODEL` etc.): `gemma4-heretical-mlx-8bit`.
- **TTS** (`RECLIP_TTS_MODEL`): `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit`.

If a referenced model isn't served by oMLX, the corresponding feature 500s with
an auth/“model not found” error — check `omlx-state.json`'s `installed_models`.
