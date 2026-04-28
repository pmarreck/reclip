# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3, **transcribe with Whisper, summarize, and translate using local LLMs**.

[![Garnix](https://img.shields.io/endpoint.svg?url=https%3A%2F%2Fgarnix.io%2Fapi%2Fbadges%2Fpmarreck%2Freclip%3Fbranch%3Dmain)](https://garnix.io/repo/pmarreck/reclip)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> **This fork** adds AI-powered transcription, summarization, and translation — all running locally via [oMLX Server](https://omlx.dev), [Ollama](https://ollama.com), or any OpenAI-compatible API. No data leaves your machine unless you configure it to.

## Features

### Original
- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4 video or MP3 audio extraction
- Quality/resolution picker
- Bulk downloads — paste multiple URLs at once
- Automatic URL deduplication
- Clean, responsive UI — no frameworks, no build step

### Added in this fork
- **Transcription** — speech-to-text via Whisper (oMLX, Ollama, or OpenAI-compatible endpoint)
- **Summarization** — LLM-powered summaries of transcripts
- **Translation** — translate transcripts or summaries to any language
- **Inline results** — view transcripts, summaries, and translations directly in the UI with expand/collapse and save-to-file
- **Playlist support** — paste a YouTube playlist URL, get all videos as cards with batch Download All / Transcribe All / Summarize All
- **Flat-file cache** — normalized-URL-keyed cache avoids redundant downloads, transcriptions, and LLM calls. Size-configurable with LRU eviction
- **Metadata headers** — transcripts include video title, channel, date, duration, and URL for context
- **Server-Sent Events** — real-time status updates instead of polling
- **CLI interface** — `python cli.py transcribe|summarize|translate|info|cache|config`
- **Multi-video page support** — pages with multiple embedded videos (e.g. NYT articles) are handled correctly
- **Nix flake** — reproducible dev environment, no venv needed
- **Independently configurable backends** — separate URL, model, API key, and prompt for STT, summarization, and translation
- **62 tests** — config, cache, LLM client, and API integration tests

## Quick Start

### With Nix (recommended)

```bash
git clone https://github.com/pmarreck/reclip.git
cd reclip
RECLIP_API_KEY=your_omlx_key ./run
```

### Without Nix

```bash
pip install flask requests
brew install yt-dlp ffmpeg    # or apt install ffmpeg && pip install yt-dlp
RECLIP_API_KEY=your_omlx_key python app.py
```

Open **http://localhost:8899**.

### CLI

```bash
# Fetch video info
python cli.py info 'https://youtube.com/watch?v=...'

# Transcribe (downloads audio, runs Whisper, caches result)
python cli.py transcribe 'https://youtube.com/watch?v=...'

# Summarize (transcribes first if needed)
python cli.py summarize 'https://youtube.com/watch?v=...'

# Translate a transcript to Spanish
python cli.py translate 'https://youtube.com/watch?v=...' Spanish

# Translate a summary to French
python cli.py translate 'https://youtube.com/watch?v=...' French --source summary

# Show cache stats
python cli.py cache

# Show current config
python cli.py config
```

## LLM Backend Configuration

All three AI functions (transcription, summarization, translation) are independently configurable via environment variables. They all speak the OpenAI-compatible wire format, so any compatible server works.

### Common shortcut

Set `RECLIP_API_KEY` to apply the same API key to all three backends:

```bash
RECLIP_API_KEY=your_key ./run
```

### Full configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RECLIP_API_KEY` | (empty) | Common API key fallback for all backends |
| `RECLIP_CACHE_DIR` | `$XDG_CACHE_HOME/reclip` | Cache directory |
| `RECLIP_CACHE_MAX_MB` | `1024` | Max cache size in MB (LRU eviction) |
| `RECLIP_STT_URL` | `http://localhost:8000/v1/audio/transcriptions` | Speech-to-text endpoint |
| `RECLIP_STT_API_KEY` | `$RECLIP_API_KEY` | STT-specific API key |
| `RECLIP_STT_MODEL` | `whisper-large-v3-fp16` | Whisper model name (see [STT model notes](#stt-model-notes)) |
| `RECLIP_STT_PROMPT` | (empty) | Optional STT priming prompt |
| `RECLIP_SUMMARIZE_URL` | `http://localhost:8000/v1/chat/completions` | Summarization endpoint |
| `RECLIP_SUMMARIZE_API_KEY` | `$RECLIP_API_KEY` | Summarization-specific API key |
| `RECLIP_SUMMARIZE_MODEL` | `gemma4-heretical-mlx-8bit` | Summarization model |
| `RECLIP_SUMMARIZE_PROMPT` | (built-in) | Custom summarization system prompt |
| `RECLIP_TRANSLATE_URL` | `http://localhost:8000/v1/chat/completions` | Translation endpoint |
| `RECLIP_TRANSLATE_API_KEY` | `$RECLIP_API_KEY` | Translation-specific API key |
| `RECLIP_TRANSLATE_MODEL` | `gemma4-heretical-mlx-8bit` | Translation model |
| `RECLIP_TRANSLATE_PROMPT` | (built-in, uses `{language}`) | Custom translation system prompt |

### Supported providers

| Provider | Default port | Auth |
|----------|-------------|------|
| oMLX Server | 8000 | Bearer token |
| Ollama | 11434 | None |
| LM Studio | 1234 | None |
| OpenAI | api.openai.com | Bearer token |

### STT model notes

The default `RECLIP_STT_MODEL` is `whisper-large-v3-fp16` because it ships with the HuggingFace processor/feature-extractor configuration that oMLX needs to run transcription. Other tested options on oMLX:

| Model | Status | Notes |
|-------|--------|-------|
| `mlx-community/whisper-large-v3-fp16` | ✅ Recommended | ~3 GB, full precision, HF processor included. Default. |
| `mlx-community/whisper-large-v3-8bit` | ✅ Works | ~860 MB, quantized non-turbo. HF processor included. |
| `mlx-community/whisper-large-v3-turbo-8bit` | ⚠️ Avoid on current oMLX | Smaller (~860 MB) but the repo is missing/has stripped the HuggingFace processor files. oMLX 0.3.8.dev1 throws `Processor not found. Make sure the model was loaded with a HuggingFace processor.` even after manually copying `preprocessor_config.json`, `tokenizer.json`, `tokenizer_config.json`, etc. from upstream `openai/whisper-large-v3-turbo`. The auto-recovery in ReClip+ unloads and retries once, but the underlying load still fails. May be fixed in a future oMLX release — until then, prefer `fp16` or `8bit`. |
| `mlx-community/whisper-large-v3-mlx` | ❌ Doesn't load | Ships `weights.npz` instead of `model.safetensors`; oMLX's model discovery ignores it. |

ReClip+ has a workaround for the oMLX "stale processor" caching bug: when it sees `Processor not found` from `/v1/audio/transcriptions`, it POSTs to `/v1/models/{model}/unload` and retries once. That fix handles transient stale-state cases, but won't help when the underlying repo genuinely lacks the processor files (which is the turbo-8bit situation above).

#### oMLX 0.3.x: WhisperProcessor fails for ALL models

oMLX 0.3.8rc1's bundle pins `transformers 5.x` against `mistral_common 1.9.x`. `transformers/tokenization_mistral_common.py` imports `ReasoningEffort` (added in `mistral_common 1.10`), and `WhisperProcessor.from_pretrained()` walks the transformers module map during processor discovery, so it hits the broken import and silently falls back to `_processor=None`. oMLX then misreports this as "missing `preprocessor_config.json`" — even on models with complete HF processor files.

If you see that error on oMLX 0.3.x, run:

```bash
nix run .#fix-omlx        # apply the patch
nix run .#fix-omlx -- --check    # report patched/unpatched
nix run .#fix-omlx -- --revert   # restore from .reclip-backup
```

Or invoke the script directly without Nix: `./scripts/fix-omlx-stt.sh`. It idempotently patches `oMLX.app`'s bundled `tokenization_mistral_common.py` to make the `ReasoningEffort` import optional. Restart oMLX after patching. Upstream fix will be a one-line dep bump in oMLX's `pyproject.toml` — see `docs/upstream/jundot-omlx-issue-draft.md` for the issue body.

### TTS model notes

`RECLIP_TTS_VOICE` controls how the speak pipeline picks a voice. It accepts three forms, in priority order:

1. **A voice description string** (default): routed via the OpenAI `voice` field. With `Qwen3-TTS-12Hz-1.7B-Base-8bit` (the default model) oMLX maps it to mlx-audio's `instruct=` kwarg, so phrases like *"warm feminine voice with a soft sultry tone, gentle and engaging"* (the built-in default) work directly. With Kokoro or `Qwen3-TTS-CustomVoice` it selects a preset voice by name (e.g. `af_bella`, `am_michael`).
2. **A filesystem path** to a reference audio clip: used as `ref_audio` for voice cloning. ReClip+ runs STT once on the clip to derive the required `ref_text` and caches it; you can also pre-set it via `RECLIP_TTS_VOICE_TEXT`.
3. **Empty**: fall back to per-video voice cloning. Extracts a 7-second clip from the middle of the cached audio (matching Qwen3-TTS-Base's recommended ~10s ceiling — longer clips increase voice variance), transcribes it for `ref_text`, and uses both as the voice reference. Each `Listen` thus reads in the original speaker's voice.

**Tested TTS models** (all from `mlx-community` on HuggingFace, downloadable from oMLX's admin UI):

| Model | Voice control | Notes |
|-------|--------------|-------|
| `Qwen3-TTS-12Hz-1.7B-Base-8bit` (default) | Description prompt OR ref_audio cloning | ~860 MB. Best when you want either freeform descriptions or cloning. The Base model is known to roll different voices across calls when given only a sample (issue [#80](https://github.com/QwenLM/Qwen3-TTS/issues/80)) — providing both `ref_audio` AND `ref_text` (which ReClip+ does automatically) is the documented stable form. Pronunciation can occasionally slip (e.g. "fix" → "feeks"); switching to CustomVoice or Kokoro is the fix. |
| `Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` | 9 fixed premium voices | Far more consistent. Pick a voice by name via `RECLIP_TTS_VOICE`. No cloning. |
| `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` | Pure description-driven generation | Built specifically for natural-language voice descriptions. Best fit for the default *"warm feminine voice…"* style. |
| `Voxtral-4B-TTS-2603-mlx-4bit` | Preset voices | Mistral's TTS, larger but very high quality. No cloning. |
| `Marvis-AI/marvis-tts-250m-v0.2-MLX-8bit` | Preset voices | Tiny, designed for streaming. |
| Kokoro variants in mlx-audio | 54 preset voices | Most consistent narrator quality on the open-source side; no cloning. |

Switching models is just a config edit — change `RECLIP_TTS_MODEL` in the Settings modal (or `~/.config/reclip+/config.ini`); hot-reload picks it up within ~5 seconds. No restart needed.

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download**, **Transcribe**, **Summarize**, or **Translate**
6. Results appear inline — expand, read, and save as needed
7. For playlists, use batch buttons: **Download All**, **Transcribe All**, **Summarize All**

## Cache

The cache stores downloaded audio, video, transcripts, summaries, and translations keyed by normalized URL (tracking parameters stripped). Repeated operations on the same URL are instant.

```bash
# Check cache usage
python cli.py cache

# Clear a specific cache entry
rm -rf ~/.cache/reclip/<hash>/

# Clear all
rm -rf ~/.cache/reclip/
```

When accessed from localhost, the web UI footer shows cache stats and configured LLM endpoints.

## Testing

```bash
./test           # runs full suite via nix + pytest
./test -v        # verbose
./test -k cache  # run only cache tests
```

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, NYT, and many more.

## Stack

- **Backend:** Python 3.12 + Flask
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **AI:** Any OpenAI-compatible API (oMLX Server, Ollama, LM Studio, OpenAI)
- **Dependencies:** Flask, requests, yt-dlp, ffmpeg
- **Dev:** Nix flake, pytest

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
