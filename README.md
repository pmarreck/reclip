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
| `RECLIP_STT_MODEL` | `whisper-large-v3-turbo-8bit` | Whisper model name |
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
