# ReClip

A self-hosted, open-source video, audio, and image downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, Threads, Reddit, and 1000+ other sites — download as MP4 / MP3 / image-grid, **transcribe with Whisper, summarize, translate, and counter-argue using local LLMs**, and **strip image carousels from social sites that block direct saves**.

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
- **Speaker diarization + naming** — local *who-spoke-when* via [speakrs](https://github.com/avencera/speakrs) (consumed through the [speakrs_ffi](https://github.com/pmarreck/speakrs_ffi) C FFI; ~220× realtime on Apple Silicon), word-level speaker attribution from Whisper word timestamps, and an LLM pass that names speakers from context cues (self-intros, address terms, video metadata) — confidence-gated so it never invents names
- **Configurable actions** — Summarize / Translate / Counterargue ship as defaults in `~/.config/reclip+/actions.json`; add your own named button by adding `{id, name, source, system_prompt, params}` to the file (hot-reloaded). Actions chain: `source` is `transcript`, `diarized`, or another action's id, and missing upstream steps run automatically
- **Text-to-speech** — local TTS playback of summaries/translations via Qwen3-TTS / Voxtral / Kokoro
- **Image-host extraction** — Instagram / Threads / Reddit / X / Pinterest / Tumblr / Imgur / Flickr / DeviantArt carousels render as a 2-up grid via [gallery-dl](https://github.com/mikf/gallery-dl); long-press / right-click saves images directly. Tap to open raw image in a new tab.
- **Recent cache view** — Cached entries hydrate as cards on page load (newest first), unified with fresh fetches. Pin to keep from LRU eviction, delete individually, or "Show in Finder" / "Open Folder" (loopback only).
- **Inline results** — view transcripts, summaries, and translations directly in the UI with expand/collapse and save-to-file
- **Playlist support** — paste a YouTube playlist URL, get all videos as cards with batch Download All / Transcribe All / Summarize All
- **Flat-file cache** — normalized-URL-keyed cache avoids redundant downloads, transcriptions, and LLM calls. Size-configurable with LRU eviction
- **Metadata headers** — transcripts include video title, channel, date, duration, and URL for context
- **Server-Sent Events** — real-time status updates instead of polling
- **Remote access** — opt-in bind to all interfaces for LAN / Tailscale use, with loopback-only Settings UI gate
- **CLI interface** — `python cli.py transcribe|summarize|translate|info|cache|config`
- **Multi-video page support** — pages with multiple embedded videos (e.g. NYT articles) are handled correctly
- **Nix flake** — reproducible dev environment, no venv needed
- **Independently configurable backends** — separate URL, model, and API key for STT, summarization, translation, counterargument, and TTS; prompts live in `actions.json`
- **Service-safe secrets** — `~/.config/reclip+/secrets.ini` (0600, hot-reloaded) supplies API keys to the launchd/systemd service, which never sees your shell env
- **Whisper accuracy helpers** — the video's own title/description seeds Whisper's decoder so proper nouns transcribe correctly (`RECLIP_STT_METADATA_PROMPT`), and word timestamps are requested for diarization alignment (`RECLIP_STT_WORD_TIMESTAMPS`)
- **300+ tests** — config, cache, LLM client, diarizer, speaker pipeline, actions registry, media extractor, service, and API integration tests

## Quick Start

### With Nix (recommended)

```bash
git clone https://github.com/pmarreck/reclip.git
cd reclip
RECLIP_API_KEY=your_omlx_key ./run
```

### Without Nix

```bash
pip install flask requests gallery-dl
brew install yt-dlp ffmpeg gallery-dl    # or: apt install ffmpeg && pip install yt-dlp gallery-dl
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
| `RECLIP_HOST` | `127.0.0.1` | Server bind address. Set to `0.0.0.0` for LAN/Tailscale (see [Remote access](#remote-access)) |
| `RECLIP_PORT` | `8899` | Server port |
| `RECLIP_CACHE_DIR` | `$XDG_CACHE_HOME/reclip` | Cache directory |
| `RECLIP_CACHE_MAX_MB` | `1024` | Max cache size in MB (LRU eviction) |
| `RECLIP_GALLERY_DL_BROWSER` | `firefox` | Browser to extract cookies from for image-host auth (see [Image-host extraction](#image-host-extraction)). Set to `""` to disable. |
| `RECLIP_GALLERY_DL_COOKIES` | (empty) | Path to a Netscape-format `cookies.txt` file. Wins over `_BROWSER` when both are set. |
| `RECLIP_STT_URL` | `http://localhost:8000/v1/audio/transcriptions` | Speech-to-text endpoint |
| `RECLIP_STT_API_KEY` | `$RECLIP_API_KEY` | STT-specific API key |
| `RECLIP_STT_MODEL` | `whisper-large-v3-turbo-8bit` | Whisper model name (see [STT model notes](#stt-model-notes)) |
| `RECLIP_STT_PROMPT` | (empty) | Optional STT priming prompt |
| `RECLIP_SUMMARIZE_URL` | `http://localhost:8000/v1/chat/completions` | Summarization endpoint |
| `RECLIP_SUMMARIZE_API_KEY` | `$RECLIP_API_KEY` | Summarization-specific API key |
| `RECLIP_SUMMARIZE_MODEL` | `gemma4-heretical-mlx-8bit` | Summarization model |
| `RECLIP_SUMMARIZE_PROMPT` | (built-in) | Custom summarization system prompt |
| `RECLIP_TRANSLATE_URL` | `http://localhost:8000/v1/chat/completions` | Translation endpoint |
| `RECLIP_TRANSLATE_API_KEY` | `$RECLIP_API_KEY` | Translation-specific API key |
| `RECLIP_TRANSLATE_MODEL` | `gemma4-heretical-mlx-8bit` | Translation model |
| `RECLIP_TRANSLATE_PROMPT` | (built-in, uses `{language}`) | Custom translation system prompt |
| `RECLIP_COUNTERARGUE_URL` | `http://localhost:8000/v1/chat/completions` | Counter-argument endpoint |
| `RECLIP_COUNTERARGUE_API_KEY` | `$RECLIP_API_KEY` | Counter-argument API key |
| `RECLIP_COUNTERARGUE_MODEL` | `gemma4-heretical-mlx-8bit` | Counter-argument model |
| `RECLIP_COUNTERARGUE_PROMPT` | (built-in) | Custom counter-argument system prompt |
| `RECLIP_TTS_URL` | `http://localhost:8000/v1/audio/speech` | TTS endpoint |
| `RECLIP_TTS_API_KEY` | `$RECLIP_API_KEY` | TTS API key |
| `RECLIP_TTS_MODEL` | `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` | TTS model (see [TTS model notes](#tts-model-notes)) |
| `RECLIP_TTS_VOICE` | *warm feminine voice…* | Voice description, preset name, or path to a reference clip |
| `RECLIP_TTS_VOICE_TEXT` | (empty) | Transcript of `RECLIP_TTS_VOICE` clip (only if VOICE is a path) |

### Supported providers

| Provider | Default port | Auth |
|----------|-------------|------|
| oMLX Server | 8000 | Bearer token |
| Ollama | 11434 | None |
| LM Studio | 1234 | None |
| OpenAI | api.openai.com | Bearer token |

### STT model notes

The default `RECLIP_STT_MODEL` is `whisper-large-v3-turbo-8bit` on oMLX 0.4.4+.
It retains word timestamps for diarization and passed ReClip's long-form
regression sample without the repeated-token and repeated-segment failures that
occurred with the former fp16 default. Other tested options on oMLX:

| Model | Status | Notes |
|-------|--------|-------|
| `whisper-large-v3-turbo-8bit` | ✅ Recommended | ~860 MB; word timestamps work on oMLX 0.4.4+ and it is ReClip's default. |
| `whisper-large-v3-fp16` | ✅ Alternate | ~3 GB, full precision. It remains selectable, but repeated-token/segment corruption was observed in ReClip's long-form regression sample. |
| `mlx-community/whisper-large-v3-8bit` | ✅ Works | ~860 MB, quantized non-turbo. HF processor included. |
| `mlx-community/whisper-large-v3-mlx` | ❌ Doesn't load | Ships `weights.npz` instead of `model.safetensors`; oMLX's model discovery ignores it. |

ReClip+ has a workaround for the oMLX "stale processor" caching bug: when it sees `Processor not found` from `/v1/audio/transcriptions`, it POSTs to `/v1/models/{model}/unload` and retries once. That handles transient stale-state cases. The Turbo warning above applied to oMLX 0.3.x and is superseded by the 0.4.4 verification.

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

1. **A voice description string** (default): routed via the OpenAI `voice` field. With `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` (the default model) oMLX maps it to mlx-audio's `instruct=` kwarg, so phrases like *"warm feminine voice with a soft sultry tone, gentle and engaging"* (the built-in default) work directly. With Kokoro or `Qwen3-TTS-CustomVoice` it selects a preset voice by name (e.g. `af_bella`, `am_michael`). With `Qwen3-TTS-Base` it's also accepted but the Base model is prone to voice drift across calls.
2. **A filesystem path** to a reference audio clip: used as `ref_audio` for voice cloning. ReClip+ runs STT once on the clip to derive the required `ref_text` and caches it; you can also pre-set it via `RECLIP_TTS_VOICE_TEXT`.
3. **Empty**: fall back to per-video voice cloning. Extracts a 7-second clip from the middle of the cached audio (matching Qwen3-TTS-Base's recommended ~10s ceiling — longer clips increase voice variance), transcribes it for `ref_text`, and uses both as the voice reference. Each `Listen` thus reads in the original speaker's voice.

**Tested TTS models** (all from `mlx-community` on HuggingFace, downloadable from oMLX's admin UI):

| Model | Voice control | Notes |
|-------|--------------|-------|
| `Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit` (default) | Pure description-driven generation | Built specifically for natural-language voice descriptions. Best fit for the default *"warm feminine voice…"* style and the most consistent of the Qwen3-TTS variants when no ref_audio is supplied. |
| `Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` | 9 fixed premium voices | Pick a voice by name via `RECLIP_TTS_VOICE`. No cloning. |
| `Qwen3-TTS-12Hz-1.7B-Base-8bit` | Description prompt OR ref_audio cloning | ~860 MB. The only Qwen3-TTS variant that supports cloning, but known to roll different voices across calls when given only a sample (issue [#80](https://github.com/QwenLM/Qwen3-TTS/issues/80)) — providing both `ref_audio` AND `ref_text` (which ReClip+ does automatically) is the documented stable form. Pronunciation can occasionally slip (e.g. "fix" → "feeks"). |
| `Voxtral-4B-TTS-2603-mlx-4bit` | Preset voices | Mistral's TTS, larger but very high quality. No cloning. |
| `Marvis-AI/marvis-tts-250m-v0.2-MLX-8bit` | Preset voices | Tiny, designed for streaming. |
| Kokoro variants in mlx-audio | 54 preset voices | Most consistent narrator quality on the open-source side; no cloning. |

Switching models is just a config edit — change `RECLIP_TTS_MODEL` in the Settings modal (or `~/.config/reclip+/config.ini`); hot-reload picks it up within ~5 seconds. No restart needed.

## Image-host extraction

Many social sites (Instagram, Threads, Reddit, X/Twitter, Pinterest, Tumblr, Imgur, Flickr, DeviantArt) block easy image saves — long-pressing only saves a thumbnail, or the site overlays a tap-trap. ReClip+ routes those URLs through [gallery-dl](https://github.com/mikf/gallery-dl) instead of yt-dlp, then renders the items as a 2-up scrollable grid:

- **Long-press** (mobile) or **right-click** (desktop) → native "Save Image"
- **Tap** the image → opens the raw bytes in a new tab (no HTML framing)
- **Download All (zip)** → bundles every item from the post into a single zip

`/api/info` only runs gallery-dl's metadata-fetch step (`--dump-json`, ~3-8s typical) and the grid renders with `<img src="…cdninstagram.com/…">` pointing at the host's CDN directly. The actual cached download happens lazily on the first **Download All** click. CDN URLs from Instagram typically expire after 24-48h; refetch from `/api/info` to refresh.

### Authentication for protected hosts

Instagram and most modern social sites bounce anonymous requests to a login wall. gallery-dl can extract session cookies from your already-logged-in browser:

```ini
# ~/.config/reclip+/config.ini
RECLIP_GALLERY_DL_BROWSER=firefox          # default
```

For multi-profile browsers (Firefox Nightly, Developer Edition, named profiles), pass the **directory name** under the profiles directory, not the friendly profile name:

```bash
ls ~/Library/Application\ Support/Firefox/Profiles
# 882i5035.default-nightly  jhwe9mqz.default-beta  6brha7lz.default
```

```ini
RECLIP_GALLERY_DL_BROWSER=firefox:882i5035.default-nightly
```

For Chromium-derivatives that aren't built-in (e.g. ChatGPT Atlas):

```ini
RECLIP_GALLERY_DL_BROWSER=chromium:/Users/me/Library/Application Support/ChatGPT Atlas
```

For an exported Netscape-format cookies.txt (e.g. via the *cookies.txt* browser extension), use `RECLIP_GALLERY_DL_COOKIES=/path/to/cookies.txt`. It wins over `_BROWSER` when both are set.

> **Security:** when `RECLIP_HOST` is non-loopback, anyone on your LAN/Tailscale who can reach the server can trigger image fetches that *ride* your session cookies (they cannot read them, but they can use them on the configured host). ReClip+ prints a red startup warning when both conditions hold. Set `RECLIP_GALLERY_DL_BROWSER=""` to disable, or pin `RECLIP_HOST=127.0.0.1`.

## Remote access

Default bind is `127.0.0.1` (loopback only). To reach ReClip+ from your phone over Tailscale or other devices on your LAN:

```ini
# ~/.config/reclip+/config.ini
RECLIP_HOST=0.0.0.0          # all interfaces
# or pin to a specific interface:
RECLIP_HOST=100.x.y.z        # `tailscale ip -4` on this machine
```

Then restart `python app.py` (host bind requires a restart; everything else hot-reloads). On a non-loopback bind, you'll see a yellow warning at startup. The Settings modal stays loopback-gated — only the local user can change config — but transcription/summarization/TTS endpoints become reachable to any peer who can route to the box.

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

**Video** — anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md): YouTube, TikTok, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Facebook, LinkedIn, NYT, and 1000+ more.

**Image hosts** (routed via [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md)) — Instagram, Threads, Reddit, Twitter/X, Pinterest, Pinimg, Tumblr, Imgur, Flickr, DeviantArt. URLs from these hosts auto-route to the image-grid view; everything else stays on the video pipeline.

## Stack

- **Backend:** Python 3.12 + Flask
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Video engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Image engine:** [gallery-dl](https://github.com/mikf/gallery-dl)
- **AI:** Any OpenAI-compatible API (oMLX Server, Ollama, LM Studio, OpenAI)
- **Dependencies:** Flask, requests, yt-dlp, gallery-dl, ffmpeg
- **Dev:** Nix flake, pytest

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
