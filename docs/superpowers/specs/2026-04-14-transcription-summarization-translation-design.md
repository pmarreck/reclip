# ReClip: Transcription, Summarization & Translation

**Date:** 2026-04-14
**Status:** Approved

## Overview

Merge yt-transcriber's transcription/summarization/translation capabilities into ReClip's web UI. All LLM operations use the OpenAI-compatible wire format, targeting oMLX Server, Ollama, LM Studio, or OpenAI as backends. Each function (STT, summarize, translate) is independently configurable. A flat-file cache indexed by normalized URL prevents redundant downloads and reprocessing. Playlist support enables batch operations across all videos in a YouTube playlist.

## Cache Layer

### Location & Size

- **`RECLIP_CACHE_DIR`**: defaults to `$XDG_CACHE_HOME/reclip`, which itself defaults to `~/.cache/reclip`. Created on startup if absent.
- **`RECLIP_CACHE_MAX_MB`**: defaults to `1024` (1 GB). On each new cache write, if total size exceeds the limit, evict entries by oldest access time (mtime) until under budget.

### Structure

Keyed by SHA-256 of the normalized URL:

```
$RECLIP_CACHE_DIR/
  ab3f91.../
    meta.json                  # {url, title, thumbnail_url, duration, uploader, fetched_at, last_accessed}
    audio.mp3                  # extracted audio (for transcription input)
    video.mp4                  # downloaded video (with audio)
    transcript.txt             # whisper output
    summary.txt                # LLM summary of transcript
    translation-spanish.txt    # translated transcript
    summary-spanish.txt        # translated summary
```

### URL Normalization

Strip tracking params (`utm_*`, `smid`, `si`, `feature`, `unlocked_article_code`, etc.), strip trailing slashes, lowercase scheme+host. Same video with different tracking params hits the same cache entry.

### Behavior

- Every API operation checks cache first.
- Downloading video caches **both** `video.mp4` (with audio) and `audio.mp3` (extracted via ffmpeg) as separate files.
- Transcribe checks for `transcript.txt`. Summarize checks for `summary.txt`. Translate checks for `translation-{lang}.txt` or `summary-{lang}.txt`.
- Cache hits skip the expensive operation entirely.
- `meta.json` `last_accessed` is updated on every cache hit (for LRU eviction).

## LLM Backend Configuration

Three independently-configurable backends, one per function. All use the OpenAI wire format.

### Transcription (Speech-to-Text)

| Env Var | Default |
|---------|---------|
| `RECLIP_STT_URL` | `http://localhost:8000/v1/audio/transcriptions` |
| `RECLIP_STT_API_KEY` | (empty) |
| `RECLIP_STT_MODEL` | `mlx-community/whisper-large-v3-turbo` |
| `RECLIP_STT_PROMPT` | (empty — optional priming for domain-specific vocabulary) |

### Summarization

| Env Var | Default |
|---------|---------|
| `RECLIP_SUMMARIZE_URL` | `http://localhost:8000/v1/chat/completions` |
| `RECLIP_SUMMARIZE_API_KEY` | (empty) |
| `RECLIP_SUMMARIZE_MODEL` | `gemma4-heretical-mlx-8bit` |
| `RECLIP_SUMMARIZE_PROMPT` | See below |

### Translation

| Env Var | Default |
|---------|---------|
| `RECLIP_TRANSLATE_URL` | `http://localhost:8000/v1/chat/completions` |
| `RECLIP_TRANSLATE_API_KEY` | (empty) |
| `RECLIP_TRANSLATE_MODEL` | `gemma4-heretical-mlx-8bit` |
| `RECLIP_TRANSLATE_PROMPT` | See below (contains `{language}` placeholder) |

### Default Prompts

**`RECLIP_SUMMARIZE_PROMPT`:**

> Please summarize the most pertinent elements of the following transcript or narrative. If it (or any part of it) presents a list of things (questions, points, tasks, steps, sequential events, etc.), please list those out without collapsing them further. If there is an issue with the content (such as it appearing to be missing), mention that prefixed with 'Problem: '. Don't comment on the summary itself. If there is a metadata section, output it verbatim at the top of the summary.

**`RECLIP_TRANSLATE_PROMPT`:**

> You are an expert translator. Please translate the following into {language}. For idioms, words, or expressions that do not translate perfectly: (1) Make your best translation attempt (2) Add footnotes with explanations in both the source and target languages. Do not output anything but the translation and footnotes. If there is an unresolvable issue, mention it prefixed with 'Problem: ' in both languages. Preserve all formatting.

### Provider Compatibility

| Provider | Chat endpoint | STT endpoint | Auth |
|----------|-------------|-------------|------|
| oMLX Server | `localhost:8000/v1/chat/completions` | `localhost:8000/v1/audio/transcriptions` | Bearer token |
| Ollama | `localhost:11434/v1/chat/completions` | `localhost:11434/v1/audio/transcriptions` | None |
| LM Studio | `localhost:1234/v1/chat/completions` | `localhost:1234/v1/audio/transcriptions` | None |
| OpenAI | `api.openai.com/v1/chat/completions` | `api.openai.com/v1/audio/transcriptions` | Bearer token |

No provider-specific code. We POST to whatever URL the user configures, with an optional `Authorization: Bearer <key>` header if an API key is set.

## API Endpoints

### Existing (unchanged)

- `GET /` — serve web UI
- `POST /api/info` — fetch video metadata via `yt-dlp -j`
- `POST /api/download` — download video/audio, return job_id
- `GET /api/status/<job_id>` — poll job status
- `GET /api/file/<job_id>` — download completed file

### New

**`POST /api/playlist`** — `{url}`
1. Call `yt-dlp -j --flat-playlist <url>` to get the entry list
2. Return `{playlist_title, entries: [{url, title, duration, thumbnail}...]}`

**`POST /api/transcribe`** — `{url, title?}`
1. Check cache for `transcript.txt` -> return immediately if hit
2. Check cache for `audio.mp3` -> if miss, download via yt-dlp, cache video + audio
3. POST audio to STT endpoint (multipart form: `file` + `model` + optional `prompt`)
4. Cache `transcript.txt`, return `{job_id}` (async, polled via `/api/status`)

**`POST /api/summarize`** — `{url, title?}`
1. Check cache for `summary.txt` -> return if hit
2. Ensure `transcript.txt` exists (trigger transcribe pipeline if needed)
3. POST transcript to chat/completions with summarize prompt
4. Cache `summary.txt`, return `{job_id}`

**`POST /api/translate`** — `{url, title?, language, source: "transcript"|"summary"}`
1. Determine source file: `transcript.txt` or `summary.txt`
2. Check cache for `translation-{lang}.txt` or `summary-{lang}.txt` -> return if hit
3. Ensure source text exists (trigger upstream pipeline if needed)
4. POST source to chat/completions with translate prompt (`{language}` substituted)
5. Cache result, return `{job_id}`

**`GET /api/text/<job_id>`** — download completed text result as `.txt` file

**`GET /api/cache/stats`** — `{location, max_mb, used_mb, entry_count}`

### Pipeline Dependency Chain

```
download (video + audio) -> transcribe -> summarize --> translate summary
                                       \-> translate transcript
```

Each step checks cache first. Entering the pipeline at any point skips already-completed upstream work. Requesting "Translate Summary" on a fresh URL triggers the entire chain automatically.

### Job System

All long-running operations (transcribe, summarize, translate) use the same async job pattern as existing downloads: spawn a thread, return a `job_id`, client polls `/api/status/<job_id>`. Text results are served via `/api/text/<job_id>` (analogous to `/api/file/<job_id>` for media).

The `/api/status/<job_id>` response gains a `type` field: `"media"` (existing downloads) or `"text"` (transcribe/summarize/translate). The UI uses this to decide whether to call `/api/file/<job_id>` or `/api/text/<job_id>` for the save action.

### Changes to Existing Download Endpoint

The existing `POST /api/download` and `run_download()` are modified to:
1. Compute the normalized URL cache key
2. Check cache for existing video/audio files before downloading
3. After downloading, cache both `video.mp4` and extract+cache `audio.mp3` (via ffmpeg) as a side-effect
4. This ensures that a subsequent "Transcribe" on the same URL finds cached audio immediately

## Playlist Support

- **Detection**: When `yt-dlp -j` returns multiple JSON objects for a URL, or the URL matches known playlist patterns, treat it as a playlist.
- **API**: `POST /api/playlist` returns the entry list without downloading anything.
- **UI**: Playlist header bar with title + entry count + batch buttons (`Download All`, `Transcribe All`, `Summarize All`). Below it, individual video cards with their own action buttons.
- **Batch operations**: Iterate over entries, processing each through the pipeline. Cache prevents redundant work for already-processed entries.

## Web UI Changes

### Card Actions

After Fetch loads a video card, the action area shows:

```
[Download]  [Transcribe]  [Summarize]  [Translate v]
```

- **Download**: existing behavior (MP4 or MP3 per the pill toggle)
- **Transcribe**: triggers STT pipeline, shows spinner, becomes "Save" on completion
- **Summarize**: triggers summarize pipeline (auto-transcribes if needed), shows spinner, becomes "Save"
- **Translate**: expands to show:
  - A text input for target language (freeform, defaults to "English")
  - Two buttons: **Translate Transcript** and **Translate Summary**
  - Each triggers the translate pipeline for the chosen source

Each button has independent status (spinner -> done/save -> error/retry).

### Playlist UI

When a playlist URL is detected:
- Playlist header bar: title, entry count, batch action buttons
- Individual video cards below, each with full action buttons

### Config Footer (Loopback Only)

Visible only when request originates from `127.0.0.1`, `::1`, or `localhost`:

```
Cache: ~/.cache/reclip (42 MB / 1024 MB, 7 entries)  *  STT: localhost:8000  *  LLM: localhost:8000
```

### No Breaking Changes

The MP4/MP3 pill toggle, fetch flow, and download behavior stay as-is. All new features are additive.

## Environment Variables Summary

| Variable | Default | Description |
|----------|---------|-------------|
| `RECLIP_CACHE_DIR` | `$XDG_CACHE_HOME/reclip` | Cache directory location |
| `RECLIP_CACHE_MAX_MB` | `1024` | Maximum cache size in MB |
| `RECLIP_STT_URL` | `http://localhost:8000/v1/audio/transcriptions` | Speech-to-text endpoint |
| `RECLIP_STT_API_KEY` | (empty) | STT API key |
| `RECLIP_STT_MODEL` | `mlx-community/whisper-large-v3-turbo` | STT model name |
| `RECLIP_STT_PROMPT` | (empty) | Optional STT priming prompt |
| `RECLIP_SUMMARIZE_URL` | `http://localhost:8000/v1/chat/completions` | Summarization endpoint |
| `RECLIP_SUMMARIZE_API_KEY` | (empty) | Summarization API key |
| `RECLIP_SUMMARIZE_MODEL` | `gemma4-heretical-mlx-8bit` | Summarization model name |
| `RECLIP_SUMMARIZE_PROMPT` | (see above) | Summarization system prompt |
| `RECLIP_TRANSLATE_URL` | `http://localhost:8000/v1/chat/completions` | Translation endpoint |
| `RECLIP_TRANSLATE_API_KEY` | (empty) | Translation API key |
| `RECLIP_TRANSLATE_MODEL` | `gemma4-heretical-mlx-8bit` | Translation model name |
| `RECLIP_TRANSLATE_PROMPT` | (see above) | Translation system prompt (use `{language}`) |

## Testing Strategy

TDD throughout. Failing test first, minimal code to pass, repeat.

- **Unit tests**: cache layer (normalization, lookup, eviction, size accounting), config loading, prompt substitution, URL normalization
- **Integration tests**: API endpoint round-trips with mocked LLM backends (Flask test client + httpretty/responses for external calls)
- **CLI tests**: Bash test script exercising the full pipeline via curl against a running server with mock backends
- All tests run via `./test`. No sleeps, no timing hacks — use callbacks or injected state for async operations.
