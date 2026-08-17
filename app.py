import io
import re
import os
import sys
import uuid
import glob
import json
import subprocess
import threading
import time
from flask import Flask, request, jsonify, send_file, render_template, Response

from config import load_config
from cache import Cache, cache_key
import hashlib
import zipfile
import mimetypes
from llm_client import transcribe as llm_transcribe, chat_completion, text_to_speech, LLMError
from service import ServiceManager, is_running_as_service
import media_extractor
from media_extractor import classify_url
from diarizer import diarize_file, DiarizerError, available as diarizer_available
from actions import Actions as ActionsRegistry, Action, ActionParam, ActionError
from speakers import (
    merge_speakers, format_diarized, speaker_label_map,
    build_naming_prompt, parse_naming_response, apply_names,
)
from paragraphize import paragraphize_transcript

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

cfg = load_config()
cache = Cache(cfg["cache_dir"], cfg["cache_max_mb"])
actions_registry = ActionsRegistry()

jobs = {}


def parse_ytdlp_json(stdout):
    """Parse yt-dlp JSON output.

    With ``-j`` yt-dlp prints one JSON object per line. Some extractors
    emit multiple videos even with ``--no-playlist``, so stdout contains
    several objects and a plain ``json.loads`` raises "Extra data".
    Return the first valid object.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    raise ValueError("yt-dlp returned no data")


@app.before_request
def _reload_config_if_needed():
    """Hot-reload config.ini when it changes (throttled to every 5s)."""
    cfg.maybe_reload()


def _is_loopback(req):
    addr = req.remote_addr or ""
    return addr in ("127.0.0.1", "::1", "localhost")


def _log(tag, msg, *args):
    """Emit a tagged, timestamped line to stderr.

    Used to surface long-running op lifecycle (start / done / error / cache
    hit) so the operator can see what's happening from the terminal without
    having to watch the UI. Format: '[HH:MM:SS] [tag] message'.
    """
    if args:
        msg = msg % args
    ts = time.strftime("%H:%M:%S")
    sys.stderr.write(f"[{ts}] [{tag}] {msg}\n")
    sys.stderr.flush()


def _format_duration(seconds):
    """Format seconds into H:MM:SS or M:SS."""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fetch_and_cache_metadata(url):
    """Fetch video metadata via yt-dlp and cache it. Returns the metadata dict."""
    meta = cache.read_meta(url)
    if meta.get("title"):
        return meta
    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    meta = {
                        "title": info.get("title", "Unknown"),
                        "uploader": info.get("uploader", info.get("channel", "Unknown")),
                        "upload_date": info.get("upload_date", "Unknown"),
                        "duration": info.get("duration", 0),
                        "thumbnail": info.get("thumbnail", ""),
                        "kind": "video",
                        "url": url,
                    }
                    cache._write_meta(url, meta)
                    return meta
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return {"title": "Unknown", "uploader": "Unknown", "upload_date": "Unknown", "duration": 0, "url": url}


def _metadata_header(url):
    """Build a metadata header string for prepending to transcripts."""
    meta = _fetch_and_cache_metadata(url)
    return (
        f"=== Video Metadata ===\n"
        f"Title: {meta.get('title', 'Unknown')}\n"
        f"Channel: {meta.get('uploader', 'Unknown')}\n"
        f"Upload Date: {meta.get('upload_date', 'Unknown')}\n"
        f"Duration: {_format_duration(meta.get('duration'))}\n"
        f"URL: {meta.get('url', url)}\n"
        f"=== Transcript ===\n\n"
    )


STT_BIAS_PROMPT_MAX_CHARS = 600  # Whisper's prompt window is ~224 tokens
# YouTube currently permits some progressive MP4s while returning 403 for the
# corresponding DASH streams. Prefer one self-contained source for speech
# workflows; retain the adaptive selection for hosts without that format.
AUDIO_SOURCE_FORMATS = (
    "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]",
    "bestvideo+bestaudio/best",
)
AUDIO_SOURCE_FORMAT = AUDIO_SOURCE_FORMATS[0]
DIARIZED_SOURCE = "diarized"
RAW_SOURCE = "transcript"
PREFER_DIARIZED_ACTION_IDS = frozenset({
    "summarize", "translate", "translate_transcript", "counterargue",
})


def _last_stderr_line(result):
    text = (getattr(result, "stderr", "") or "").strip()
    if not text:
        return "Command failed"
    return text.split("\n")[-1]


def _source_files_for_template(out_template):
    glob_pattern = out_template.replace("%(ext)s", "*")
    return glob.glob(glob_pattern)


def _download_audio_from_video_source(url, source_template, audio_path):
    """Create an MP3 from a progressive source, with adaptive media fallback.

    Direct audio extraction can receive YouTube 403 responses. Prefer a
    self-contained MP4, then retry the prior adaptive video-plus-audio path
    for hosts that do not expose a usable progressive source.
    """
    last_error = "Command failed"
    for source_format in AUDIO_SOURCE_FORMATS:
        cmd = [
            "yt-dlp", "--no-playlist", "--no-part", "-f", source_format,
            "--merge-output-format", "mp4", "-o", source_template, url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            break
        last_error = _last_stderr_line(result)
    else:
        raise RuntimeError(last_error)

    source_files = [p for p in _source_files_for_template(source_template) if p != audio_path]
    source_files.sort(key=lambda p: (not p.endswith(".mp4"), p))
    if not source_files:
        raise RuntimeError("Audio source download completed but no source file was found")

    source_path = source_files[0]
    extract_cmd = [
        "ffmpeg", "-i", source_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        audio_path, "-y",
    ]
    result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(_last_stderr_line(result))
    if not os.path.isfile(audio_path):
        raise RuntimeError("Audio extraction completed but no mp3 file was found")

    for path in source_files:
        try:
            os.remove(path)
        except OSError:
            pass
    return audio_path


def _stt_bias_prompt(meta, explicit_prompt, enabled):
    """Whisper biasing prompt: an explicit RECLIP_STT_PROMPT always wins;
    otherwise (when enabled) the video's own title/uploader/description seed
    the decoder so proper nouns — guest names usually appear in descriptions —
    transcribe correctly instead of phonetic guesses."""
    if explicit_prompt:
        return explicit_prompt
    if not enabled:
        return ""
    parts = [str(meta.get(k) or "").strip() for k in ("title", "uploader", "description")]
    combined = ". ".join(p for p in parts if p)
    return combined[:STT_BIAS_PROMPT_MAX_CHARS]


def _ensure_audio(url):
    """Ensure audio.mp3 exists in cache. Downloads via yt-dlp if needed."""
    if cache.has_file(url, "audio.mp3"):
        return cache.entry_path(url, "audio.mp3")
    # Fetch metadata before downloading (cheap, and useful later)
    _fetch_and_cache_metadata(url)
    audio_path = cache.entry_path(url, "audio.mp3")
    return _download_audio_from_video_source(
        url, cache.entry_path(url, "audio_source.%(ext)s"), audio_path
    )


def _save_transcript(url, raw_text, segments=None):
    """Prepend metadata header and cache the transcript. Returns full text.

    `segments` (list of {start, end, text} from the STT backend) is cached
    as transcript_segments.json — the diarization merge step aligns speaker
    turns against these timestamps. The readable raw artifact gains only
    lossless paragraph whitespace; segments remain untouched for alignment.
    """
    full = _metadata_header(url) + paragraphize_transcript(raw_text, segments)
    cache.write_text(url, "transcript.txt", full)
    if segments:
        cache.write_text(url, "transcript_segments.json", json.dumps(segments))
    return full


def _translate_filename(source, language, source_variant=RAW_SOURCE):
    lang = language.lower().strip().replace(" ", "-")
    prefix = "summary" if source == "summary" else "translation"
    if source_variant == DIARIZED_SOURCE:
        prefix += "-diarized"
    return f"{prefix}-{lang}.txt"


def _action_or_builtin(action_id):
    """Registry action, falling back to the shipped builtin if the user's
    actions.json removed/renamed it (legacy routes must keep working)."""
    actions_registry.maybe_reload()
    a = actions_registry.get(action_id)
    if a is not None:
        return a
    from actions import _builtin_actions
    builtin = next((b for b in _builtin_actions() if b.id == action_id), None)
    if builtin is None:
        raise RuntimeError(f"unknown action: {action_id!r}")
    return builtin


def _ensure_transcript(url):
    """transcript.txt contents, running download + STT first if missing."""
    text = cache.read_text(url, "transcript.txt")
    if text is not None:
        return text
    audio_path = _ensure_audio(url)
    result = llm_transcribe(
        audio_path=audio_path,
        url=cfg["stt_url"],
        model=cfg["stt_model"],
        api_key=cfg["stt_api_key"],
        prompt=_stt_bias_prompt(_fetch_and_cache_metadata(url),
                                cfg["stt_prompt"], cfg["stt_metadata_prompt"]),
        api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
        word_timestamps=cfg["stt_word_timestamps"],
    )
    return _save_transcript(url, result["text"], segments=result.get("segments"))


def _slugify_params(params):
    """Stable filename fragment from param values (legacy-compatible for
    translate's language: 'Brazilian Portuguese' → 'brazilian-portuguese')."""
    vals = [str(params[k]).lower().strip().replace(" ", "-")
            for k in sorted(params) if str(params.get(k, "")).strip()]
    return "-".join(vals)


def _action_output_filename(action, params, source_variant=RAW_SOURCE):
    """Cache filename for an action's output. The three legacy ids keep their
    historical filenames (other features — TTS source map, recents — read
    them); diarized inputs receive a separate sibling artifact so their output
    cannot be mistaken for a raw-transcript result."""
    params = params or {}
    suffix = "-diarized" if source_variant == DIARIZED_SOURCE else ""
    if action.id == "summarize":
        return f"summary{suffix}.txt"
    if action.id == "counterargue":
        return f"counterargue{suffix}.txt"
    if action.id in ("translate", "translate_transcript") and "language" in params:
        legacy_source = "summary" if action.source == "summarize" else "transcript"
        return _translate_filename(legacy_source, params["language"], source_variant)
    slug = _slugify_params(params)
    return f"action-{action.id}{suffix}{'-' + slug if slug else ''}.txt"


def _action_source_variant(url, action):
    """Choose source lineage once per shipped action without forcing diarization.

    The normal action buttons gain attribution only when a user has already
    requested Diarize. Explicit diarized custom actions retain their existing
    eager behavior; other custom actions keep their raw-transcript default.
    """
    if action.source == DIARIZED_SOURCE:
        return DIARIZED_SOURCE
    if (action.id in PREFER_DIARIZED_ACTION_IDS
            and cache.read_text(url, "transcript_diarized.txt") is not None):
        return DIARIZED_SOURCE
    return RAW_SOURCE


def _chat_cfg_for(action_id):
    """Return endpoint, model, key, and exact setting hints for an action.

    Legacy action ids retain their dedicated backend configuration;
    ``translate_transcript`` deliberately shares Translate's settings. Custom
    actions inherit Summary's backend, so model-not-found errors name the
    setting a user can actually change.
    """
    config_action_id = "translate" if action_id == "translate_transcript" else action_id
    if cfg.get(f"{config_action_id}_url"):
        prefix = f"RECLIP_{config_action_id.upper()}"
        return (cfg[f"{config_action_id}_url"], cfg[f"{config_action_id}_model"],
                cfg[f"{config_action_id}_api_key"],
                f"{prefix}_API_KEY or RECLIP_API_KEY",
                f"{prefix}_MODEL", f"{prefix}_URL")
    return (cfg["summarize_url"], cfg["summarize_model"], cfg["summarize_api_key"],
            "RECLIP_SUMMARIZE_API_KEY or RECLIP_API_KEY",
            "RECLIP_SUMMARIZE_MODEL", "RECLIP_SUMMARIZE_URL")


def _resolve_source_text(url, source, source_variant=RAW_SOURCE):
    """Text for an action source: 'transcript' or another action's id.
    Missing upstream outputs are computed recursively (auto-chain) with
    empty params and cached, so repeated chains are cheap."""
    if source == RAW_SOURCE:
        if source_variant == DIARIZED_SOURCE:
            return _resolve_source_text(url, DIARIZED_SOURCE, source_variant)
        return _ensure_transcript(url)
    if source == DIARIZED_SOURCE:
        text = cache.read_text(url, "transcript_diarized.txt")
        if text is None:
            _log("action", "diarized transcript not cached — running pipeline first")
            text = _diarize_sync(url)
        return text
    actions_registry.maybe_reload()
    upstream = actions_registry.get(source)
    if upstream is None:
        raise RuntimeError(f"unknown action source: {source!r}")
    upstream_variant = source_variant
    if upstream.source == DIARIZED_SOURCE:
        upstream_variant = DIARIZED_SOURCE
    fname = _action_output_filename(upstream, {}, upstream_variant)
    text = cache.read_text(url, fname)
    if text is None:
        _log("action", "source %r not cached — running it first", source)
        text = _run_action_sync(url, upstream, {}, upstream_variant)
    return text


def _run_action_sync(url, action, params, source_variant=None):
    """Generic LLM action: resolve source text, interpolate {params} into the
    system prompt, run the chat call, cache the output. Returns the text."""
    params = params or {}
    for p in action.params:
        if p.required and not str(params.get(p.name, "")).strip():
            raise RuntimeError(f"missing required parameter: {p.name}")

    source_variant = source_variant or _action_source_variant(url, action)
    source_text = _resolve_source_text(url, action.source, source_variant)
    system_prompt = action.system_prompt
    for p in action.params:
        system_prompt = system_prompt.replace("{" + p.name + "}", str(params.get(p.name, "")))
    if source_variant == DIARIZED_SOURCE:
        system_prompt += (
            "\nThe input contains speaker labels. Preserve attribution for important "
            "claims and disagreements; do not merge different speakers' positions."
        )

    chat_url, chat_model, chat_key, chat_hint, chat_model_hint, chat_url_hint = _chat_cfg_for(action.id)
    _log("action", "%s: calling LLM (model=%s, source=%d chars)",
         action.id, chat_model, len(source_text))
    t0 = time.time()
    out = chat_completion(
        url=chat_url,
        model=chat_model,
        api_key=chat_key,
        system_prompt=system_prompt,
        user_content=source_text,
        api_key_hint=chat_hint,
        model_hint=chat_model_hint,
        url_hint=chat_url_hint,
    )
    filename = _action_output_filename(action, params, source_variant)
    cache.write_text(url, filename, out)
    _log("action", "%s: done in %.1fs (%d chars -> %s)",
         action.id, time.time() - t0, len(out), filename)
    return out


def _run_action_job(job_id, url, action, params, source_variant=None):
    """Job wrapper for _run_action_sync — populates the jobs dict like every
    other background op."""
    job = jobs[job_id]
    try:
        source_variant = source_variant or _action_source_variant(url, action)
        out = _run_action_sync(url, action, params, source_variant)
        job["status"] = "done"
        job["text"] = out
        job["filename"] = _action_output_filename(action, params, source_variant)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log("action", "%s: ERROR: %s", action.id, e)


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    try:
        if format_choice == "audio":
            _download_audio_from_video_source(
                url, out_template, os.path.join(DOWNLOAD_DIR, f"{job_id}.mp3")
            )
        else:
            cmd = ["yt-dlp", "--no-playlist", "-o", out_template]
            if format_id:
                cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
            else:
                cmd += ["-f", AUDIO_SOURCE_FORMAT, "--merge-output-format", "mp4"]
            cmd.append(url)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                job["status"] = "error"
                job["error"] = _last_stderr_line(result)
                return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        job["status"] = "done"
        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        # Sanitize title for filename
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)

        # Cache the downloaded file (best-effort — failures must not break the download)
        try:
            if format_choice == "audio":
                cache.write_file(url, "audio.mp3", chosen)
            else:
                cache.write_file(url, "video.mp4", chosen)
                # Also extract and cache audio for future transcription use
                audio_cache_path = cache.entry_path(url, "audio.mp3")
                if not os.path.isfile(audio_cache_path):
                    extract_cmd = [
                        "ffmpeg", "-i", chosen,
                        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                        audio_cache_path, "-y",
                    ]
                    subprocess.run(extract_cmd, capture_output=True, timeout=120)
            import time as _time
            cache._write_meta(url, {
                "url": url,
                "title": job.get("title", ""),
                "fetched_at": _time.time(),
            })
        except Exception:
            pass  # Cache failure must not break the download
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out (5 min limit)"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def _run_transcribe(job_id, url):
    job = jobs[job_id]
    _log("transcribe", "start job=%s model=%s url=%s", job_id, cfg["stt_model"], url)
    try:
        audio_path = _ensure_audio(url)
        size_mb = os.path.getsize(audio_path) / (1024 * 1024) if os.path.isfile(audio_path) else 0
        _log("transcribe", "audio ready (%.1f MB), calling STT", size_mb)
        t0 = time.time()
        result = llm_transcribe(
            audio_path=audio_path,
            url=cfg["stt_url"],
            model=cfg["stt_model"],
            api_key=cfg["stt_api_key"],
            prompt=_stt_bias_prompt(_fetch_and_cache_metadata(url),
                                    cfg["stt_prompt"], cfg["stt_metadata_prompt"]),
            api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
            word_timestamps=cfg["stt_word_timestamps"],
        )
        transcript = _save_transcript(url, result["text"], segments=result.get("segments"))
        job["status"] = "done"
        job["text"] = transcript
        job["filename"] = "transcript.txt"
        _log("transcribe", "done in %.1fs (%d chars)", time.time() - t0, len(transcript))
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log("transcribe", "ERROR: %s", e)


def _diarize_sync(url):
    """Diarization pipeline, synchronous: transcript segments × speakrs
    speaker turns → merged, speaker-labeled transcript; a best-effort LLM
    pass names speakers from context cues + video metadata (non-fatal on
    failure). Returns the final text; caches transcript_diarized.txt +
    speakers.json."""
    # 1. Transcript segments (auto-chain transcription if missing)
    seg_raw = cache.read_text(url, "transcript_segments.json")
    if seg_raw is None:
        # Re-transcribe even when transcript.txt exists: pre-segments cache
        # entries have no timestamps to align speaker turns against.
        _log("diarize", "no cached segments — running transcription first")
        audio_path = _ensure_audio(url)
        result = llm_transcribe(
            audio_path=audio_path,
            url=cfg["stt_url"],
            model=cfg["stt_model"],
            api_key=cfg["stt_api_key"],
            prompt=_stt_bias_prompt(_fetch_and_cache_metadata(url),
                                    cfg["stt_prompt"], cfg["stt_metadata_prompt"]),
            api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
            word_timestamps=cfg["stt_word_timestamps"],
        )
        _save_transcript(url, result["text"], segments=result.get("segments"))
        seg_raw = json.dumps(result.get("segments") or [])
    transcript_segments = json.loads(seg_raw)
    if not transcript_segments:
        raise RuntimeError(
            "STT backend returned no segment timestamps — diarization "
            "needs them (oMLX Whisper models provide segments)"
        )

    # 2. Speaker turns from the audio (speakrs via C FFI)
    audio_path = _ensure_audio(url)
    t0 = time.time()
    diar = diarize_file(audio_path)
    _log("diarize", "diarization done in %.1fs (%d speakers, %d turns)",
         time.time() - t0, len(diar["speakers"]), len(diar["segments"]))

    # 3. Merge + base formatting
    merged = merge_speakers(transcript_segments, diar["segments"])
    base_text = format_diarized(merged)
    labels = speaker_label_map(merged)

    # 4. Name speakers via the configured chat model (non-fatal)
    names = {}
    naming_error = None
    if labels:
        try:
            meta = _fetch_and_cache_metadata(url)
            system_prompt, user_content = build_naming_prompt(base_text, meta)
            t0 = time.time()
            resp_text = chat_completion(
                url=cfg["summarize_url"],
                model=cfg["summarize_model"],
                api_key=cfg["summarize_api_key"],
                system_prompt=system_prompt,
                user_content=user_content,
                api_key_hint="RECLIP_SUMMARIZE_API_KEY or RECLIP_API_KEY",
            )
            naming = parse_naming_response(resp_text)
            names = apply_names(labels, naming)
            _log("diarize", "naming done in %.1fs: %s", time.time() - t0,
                 names or "(no confident names)")
        except Exception as e:
            naming_error = str(e)
            _log("diarize", "naming failed (non-fatal): %s", e)

    final_text = format_diarized(merged, names=names) if names else base_text
    full = _metadata_header(url) + final_text
    cache.write_text(url, "transcript_diarized.txt", full)
    cache.write_text(url, "speakers.json", json.dumps({
        "turns": diar["segments"],
        "speakers": diar["speakers"],
        "labels": labels,
        "names": names,
        "naming_error": naming_error,
    }))
    _log("diarize", "done (%d chars, %d named speakers)", len(full), len(names))
    return full


def _run_diarize(job_id, url):
    """Job wrapper over _diarize_sync."""
    job = jobs[job_id]
    _log("diarize", "start job=%s url=%s", job_id, url)
    try:
        job["progress"] = "diarizing"
        full = _diarize_sync(url)
        job["status"] = "done"
        job["text"] = full
        job["filename"] = "transcript_diarized.txt"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log("diarize", "ERROR: %s", e)


@app.route("/")
def index():
    return render_template("index.html")


def _info_images(url):
    """Fast metadata-only path for image-host URLs.

    Runs ONLY gallery-dl --dump-json (no download). Returns CDN URLs the
    frontend can render directly via <img src=...>. Actual cached download
    happens lazily in /api/download-all/<hash> when the user clicks save.
    Keeping this synchronous-but-fast (<10s typical) avoids tying up the
    request thread for the full carousel download (30-60s).
    """
    _log("info-images", "gallery-dl --dump-json url=%s", url)
    t0 = time.time()
    try:
        items = media_extractor.dump_images(
            url,
            cookies=cfg.get("gallery_dl_cookies") or None,
            cookies_from_browser=cfg.get("gallery_dl_browser") or None,
            timeout=30,
        )
    except RuntimeError as e:
        _log("info-images", "ERROR after %.1fs: %s", time.time() - t0, e)
        return jsonify({"error": str(e)}), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching image metadata"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    h = cache_key(url)
    out = []
    for it in items:
        cdn = it.get("url") or ""
        if not cdn:
            continue
        out.append({
            "filename": it.get("filename") or "",
            "url": cdn,
            "width": it.get("width"),
            "height": it.get("height"),
        })

    cache._write_meta(url, {"url": url, "kind": "images", "item_count": len(out)})
    _log("info-images", "done in %.1fs (%d items)", time.time() - t0, len(out))
    return jsonify({"kind": "images", "items": out, "entry_hash": h})


@app.route("/media/<entry_hash>/<path:filename>")
def serve_media(entry_hash, filename):
    """Serve a cached image/video file from a per-URL cache entry.

    Path is `<cache>/<entry_hash>/media/<filename>`. Path traversal is
    blocked by rejecting any filename that escapes the media subdirectory.
    """
    if not entry_hash or "/" in entry_hash or ".." in entry_hash:
        return jsonify({"error": "Bad request"}), 400
    base = os.path.realpath(os.path.join(cache.cache_dir, entry_hash, "media"))
    target = os.path.realpath(os.path.join(base, filename))
    if not target.startswith(base + os.sep) and target != base:
        return jsonify({"error": "Bad request"}), 400
    if not os.path.isfile(target):
        return jsonify({"error": "Not found"}), 404
    mime, _ = mimetypes.guess_type(target)
    return send_file(target, mimetype=mime or "application/octet-stream")


@app.route("/api/download-all/<entry_hash>")
def download_all(entry_hash):
    """Stream a zip of every file under <entry>/media/ for the given hash.

    If the media dir is empty (because /api/info now only fetches metadata,
    not bytes), trigger gallery-dl on demand via the URL stored in meta.json.
    """
    if not entry_hash or "/" in entry_hash or ".." in entry_hash:
        return jsonify({"error": "Bad request"}), 400
    entry_dir = os.path.realpath(os.path.join(cache.cache_dir, entry_hash))
    media_dir = os.path.join(entry_dir, "media")

    has_files = os.path.isdir(media_dir) and any(
        os.path.isfile(os.path.join(media_dir, f)) for f in os.listdir(media_dir)
    )
    if not has_files:
        meta_path = os.path.join(entry_dir, "meta.json")
        if not os.path.isfile(meta_path):
            return jsonify({"error": "Not found"}), 404
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            return jsonify({"error": "Cache metadata unreadable"}), 500
        url = meta.get("url", "")
        if not url:
            return jsonify({"error": "Cache entry has no URL"}), 400
        os.makedirs(media_dir, exist_ok=True)
        try:
            media_extractor.fetch_images(
                url,
                media_dir,
                cookies=cfg.get("gallery_dl_cookies") or None,
                cookies_from_browser=cfg.get("gallery_dl_browser") or None,
                timeout=180,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(media_dir)):
            p = os.path.join(media_dir, name)
            if os.path.isfile(p):
                zf.write(p, arcname=name)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{entry_hash[:10]}.zip",
    )


def _video_info(url):
    """Fetch and normalize yt-dlp metadata for the web card and CLI display."""
    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Timed out fetching video info") from e
    if result.returncode != 0:
        raise RuntimeError(_last_stderr_line(result))

    # yt-dlp may return multiple JSON objects (one per line) for multi-video pages.
    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not entries:
        raise RuntimeError("No video info returned")

    # Use the first entry with video formats, or fall back to the first entry.
    info = entries[0]
    for entry in entries:
        if any(f.get("height") for f in entry.get("formats", [])):
            info = entry
            break

    # Build quality options: retain the best format for each resolution.
    best_by_height = {}
    for f in info.get("formats", []):
        height = f.get("height")
        if height and f.get("vcodec", "none") != "none":
            tbr = f.get("tbr") or 0
            if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                best_by_height[height] = f

    formats = [
        {"id": f["format_id"], "label": f"{height}p", "height": height}
        for height, f in best_by_height.items()
    ]
    formats.sort(key=lambda x: x["height"], reverse=True)
    return {
        "title": info.get("title", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration"),
        "uploader": info.get("uploader", ""),
        "formats": formats,
    }


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Route image-heavy social hosts (Instagram, Threads, etc.) through
    # gallery-dl rather than yt-dlp. Carousels, posts, and Reels-with-poster
    # all return as kind=images for the frontend grid view.
    if classify_url(url) == "images":
        return _info_images(url)

    try:
        return jsonify(_video_info(url))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "type": job.get("type", "media"),
        "text": job.get("text"),
    })


@app.route("/api/stream/<job_id>")
def stream_status(job_id):
    """SSE endpoint — holds connection open until job completes."""
    def generate():
        last_status = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return
            status = job["status"]
            if status != last_status:
                last_status = status
                payload = {
                    "status": status,
                    "type": job.get("type", "media"),
                    "error": job.get("error"),
                    "filename": job.get("filename"),
                    "text": job.get("text"),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                if status in ("done", "error"):
                    return
            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


@app.route("/api/cache/stats")
def cache_stats():
    return jsonify(cache.stats())


@app.route("/api/cache/entries")
def cache_entries():
    """List all cache entries, most recent first.

    Used by the frontend to render the "Recent" cards on page load. Each entry
    includes presence flags (has_audio, has_transcript, etc.) so the UI can
    show only the action buttons relevant to what's actually cached.
    """
    return jsonify({"entries": cache.list_entries()})


@app.route("/api/cache/entry/<entry_hash>", methods=["DELETE"])
def cache_delete_entry(entry_hash):
    if not cache._is_safe_hash(entry_hash):
        return jsonify({"error": "Bad hash"}), 400
    ok = cache.delete_entry_by_hash(entry_hash)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/cache/entry/<entry_hash>/pin", methods=["POST"])
def cache_pin_entry(entry_hash):
    if not cache._is_safe_hash(entry_hash):
        return jsonify({"error": "Bad hash"}), 400
    body = request.get_json(silent=True) or {}
    pinned = bool(body.get("pinned", True))
    # Look up the URL from the existing entry's meta.json
    entry_dir = os.path.join(cache.cache_dir, entry_hash)
    meta_path = os.path.join(entry_dir, "meta.json")
    if not os.path.isfile(meta_path):
        return jsonify({"error": "Not found"}), 404
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "Cache metadata unreadable"}), 500
    url = meta.get("url", "")
    if not url:
        return jsonify({"error": "Cache entry has no URL"}), 400
    cache.set_pinned(url, pinned)
    return jsonify({"ok": True, "pinned": pinned})


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    body = request.get_json(silent=True) or {}
    keep_pinned = bool(body.get("keep_pinned", False))
    removed = cache.clear_all(keep_pinned=keep_pinned)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/cache/entry/<entry_hash>/reveal", methods=["POST"])
def cache_reveal_entry(entry_hash):
    """Open the cache entry's directory in the OS file manager. Loopback-only —
    we're invoking a GUI on the host machine, which doesn't make sense over
    Tailscale and is also a small safety win (only the local user can spawn
    `open`/`xdg-open` against arbitrary cache hashes)."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: reveal is only available via loopback"}), 403
    if not cache._is_safe_hash(entry_hash):
        return jsonify({"error": "Bad hash"}), 400
    entry_dir = os.path.join(cache.cache_dir, entry_hash)
    if not os.path.isdir(entry_dir):
        return jsonify({"error": "Not found"}), 404
    if sys.platform == "darwin":
        opener = "open"
    elif sys.platform.startswith("linux"):
        opener = "xdg-open"
    else:
        return jsonify({"error": f"Reveal not supported on {sys.platform}"}), 400
    try:
        subprocess.run([opener, entry_dir], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/config")
def get_config():
    if not _is_loopback(request):
        return jsonify({}), 403

    from urllib.parse import urlparse
    stats = cache.stats()
    stt_parsed = urlparse(cfg["stt_url"])
    llm_parsed = urlparse(cfg["summarize_url"])

    return jsonify({
        "cache": {
            "location": stats["location"],
            "used_mb": stats["used_mb"],
            "max_mb": stats["max_mb"],
            "entry_count": stats["entry_count"],
        },
        "stt_host": f"{stt_parsed.hostname}:{stt_parsed.port}",
        "llm_host": f"{llm_parsed.hostname}:{llm_parsed.port}",
    })


@app.route("/api/settings", methods=["GET", "POST"])
def settings_endpoint():
    """Read or write the raw config.ini file. Loopback-only."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: settings editing is only available via loopback"}), 403

    if request.method == "GET":
        return jsonify({
            "content": cfg.read_file(),
            "path": cfg.config_path,
        })

    data = request.json or {}
    content = data.get("content")
    if content is None:
        return jsonify({"error": "Missing 'content' field"}), 400

    try:
        cfg.write_file(content)
    except OSError as e:
        return jsonify({"error": f"Failed to write config: {e}"}), 500

    return jsonify({"ok": True, "path": cfg.config_path})


@app.route("/api/service", methods=["GET"])
def service_status():
    """Return service install/run status. Loopback-only."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: service management is only available via loopback"}), 403
    mgr = ServiceManager()
    return jsonify(mgr.status())


@app.route("/api/service/install", methods=["POST"])
def service_install():
    """Install and start the service. Loopback-only. Idempotent."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: service management is only available via loopback"}), 403
    mgr = ServiceManager()
    if not mgr.supported():
        return jsonify({"error": f"Not supported on {mgr.platform}"}), 400
    try:
        mgr.install()
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"launchctl/systemctl failed: {(e.stderr or e.stdout or str(e)).strip()}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, **mgr.status()})


@app.route("/api/service/uninstall", methods=["POST"])
def service_uninstall():
    """Stop and remove the service. Loopback-only. Idempotent."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: service management is only available via loopback"}), 403
    mgr = ServiceManager()
    if not mgr.supported():
        return jsonify({"error": f"Not supported on {mgr.platform}"}), 400
    try:
        mgr.uninstall()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, **mgr.status()})


def _schedule_self_exit(delay):
    """Spawn a daemon thread that calls os._exit after `delay` seconds.

    Werkzeug's dev server has no clean shutdown API and SSE connections block
    sys.exit, so os._exit is correct here. launchd/systemd treat it as a
    normal exit and respawn.
    """
    def _do_exit():
        time.sleep(delay)
        os._exit(0)
    t = threading.Thread(target=_do_exit, daemon=True)
    t.start()


@app.route("/api/restart", methods=["POST"])
def restart_server():
    """Schedule a clean exit. Under a supervisor (launchd/systemd) the
    process respawns; under foreground it stays dead. Loopback-only."""
    if not _is_loopback(request):
        return jsonify({"error": "Forbidden: restart is only available via loopback"}), 403
    _schedule_self_exit(0.5)
    return jsonify({
        "ok": True,
        "is_running_as_service": is_running_as_service(),
        "delay_s": 0.5,
    }), 202


@app.route("/api/playlist", methods=["POST"])
def playlist():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "-j", "--flat-playlist", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                entries.append({
                    "url": item.get("url", item.get("webpage_url", "")),
                    "title": item.get("title", ""),
                    "duration": item.get("duration"),
                    "thumbnail": item.get("thumbnail", ""),
                })
            except json.JSONDecodeError:
                continue

        urls = [entry["url"] for entry in entries if entry.get("url")]
        return jsonify({"entries": entries, "urls": urls})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/transcribe", methods=["POST"])
def transcribe_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Check cache first
    cached_text = cache.read_text(url, "transcript.txt")
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_transcribe, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/actions")
def list_actions_endpoint():
    """Registry listing for the dynamic UI: id/name/params per action, plus
    last_error so a broken actions.json edit surfaces as a banner instead of
    silently keeping the last good config."""
    actions_registry.maybe_reload()
    return jsonify({
        "actions": [
            {
                "id": a.id,
                "name": a.name,
                "source": a.source,
                "params": [
                    {"name": p.name, "type": p.type, "required": p.required,
                     "label": p.label or p.name}
                    for p in a.params
                ],
            }
            for a in actions_registry.list()
        ],
        "last_error": actions_registry.last_error,
    })


@app.route("/api/action/<action_id>", methods=["POST"])
def run_action_endpoint(action_id):
    """Generic action runner — the registry-driven replacement for the
    per-kind summarize/translate/counterargue routes."""
    actions_registry.maybe_reload()
    action = actions_registry.get(action_id)
    if action is None:
        return jsonify({"error": f"Unknown action: {action_id}"}), 404

    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    params = data.get("params") or {}
    for p in action.params:
        if p.required and not str(params.get(p.name, "")).strip():
            return jsonify({"error": f"Missing required parameter: {p.name}"}), 400

    source_variant = _action_source_variant(url, action)
    filename = _action_output_filename(action, params, source_variant)
    cached_text = cache.read_text(url, filename)
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text, "filename": filename})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}
    thread = threading.Thread(
        target=_run_action_job, args=(job_id, url, action, params, source_variant)
    )
    thread.daemon = True
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/diarize", methods=["POST"])
def diarize_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Check cache first
    cached_text = cache.read_text(url, "transcript_diarized.txt")
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_diarize, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


def _clean_text_for_tts(text):
    """Strip markdown that TTS reads literally.

    The chat models we use for summaries emit markdown; Qwen3-TTS will read
    the asterisks aloud as "asterisk" or pause oddly. Strip bold markers and
    convert leading-bullet asterisks to hyphens (which Qwen3-TTS handles
    cleanly as a list item beat).
    """
    import re
    # Remove all double-asterisks (bold/strong markers)
    text = text.replace("**", "")
    # Convert bullet-style single-asterisks at start-of-line (with optional
    # leading whitespace) to hyphens. Single asterisks inside text (italics
    # like *foo*) are left alone since they don't usually disrupt TTS as
    # much and risk false positives if we strip them globally.
    text = re.sub(r'(?m)^(\s*)\*(\s)', r'\1-\2', text)
    return text


def _chunk_text_for_tts(text, max_chars=300):
    """Split text into TTS-friendly chunks at paragraph/sentence boundaries.

    Qwen3-TTS drifts in voice consistency on long text (>300 chars is shaky,
    >500 very bad). This splits at paragraph breaks first, then sentence
    boundaries, then word boundaries as a last resort.
    """
    # Strip metadata header if present
    if "=== Transcript ===" in text:
        text = text.split("=== Transcript ===", 1)[1].strip()
    # Clean markdown (asterisks etc.) before chunking
    text = _clean_text_for_tts(text)

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        # Split paragraph on sentence boundaries
        import re
        sentences = re.split(r'(?<=[.!?])\s+', para)
        current = ""
        for sent in sentences:
            if len(current) + len(sent) + 1 <= max_chars:
                current = (current + " " + sent).strip() if current else sent
            else:
                if current:
                    chunks.append(current)
                if len(sent) <= max_chars:
                    current = sent
                else:
                    # Sentence itself too long — split on word boundaries
                    words = sent.split()
                    w_current = ""
                    for w in words:
                        if len(w_current) + len(w) + 1 <= max_chars:
                            w_current = (w_current + " " + w).strip() if w_current else w
                        else:
                            if w_current:
                                chunks.append(w_current)
                            w_current = w
                    current = w_current
        if current:
            chunks.append(current)
    return chunks if chunks else [text]


def _concat_wavs(wav_bytes_list, out_path):
    """Concatenate multiple WAV byte blobs into one file, preserving format params."""
    import wave
    if len(wav_bytes_list) == 1:
        with open(out_path, "wb") as f:
            f.write(wav_bytes_list[0])
        return

    # Collect frames from each blob
    all_frames = []
    params = None
    for blob in wav_bytes_list:
        with wave.open(io.BytesIO(blob), "rb") as w:
            if params is None:
                params = w.getparams()
            all_frames.append(w.readframes(w.getnframes()))

    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        out.writeframes(b"".join(all_frames))


def _tts_cache_filename(text, voice):
    """Generate a cache filename based on content + voice hash."""
    key_material = f"{text}|{voice}"
    h = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16]
    return f"tts-{h}.wav"


def _resolve_speak_inputs(url, voice_override=""):
    """Return (ref_audio_path, ref_text, voice_str) for the speak pipeline.

    Implements the priority order described in _run_speak. Caller passes
    one of (ref_audio + ref_text) OR voice_str to text_to_speech; the
    other will be empty. Both can be empty if no voice config + no audio.
    """
    candidate = voice_override or cfg.get("tts_voice", "") or ""

    if candidate:
        # Distinguish between a path-on-disk and a description string
        if os.path.isfile(candidate):
            # User-supplied ref_audio clip. We need a transcript for ref_text.
            # If the user also set RECLIP_TTS_VOICE_TEXT, use it; otherwise
            # transcribe the clip via STT.
            override_text = cfg.get("tts_voice_text", "") or ""
            if override_text:
                return (candidate, override_text, "")
            try:
                stt_result = llm_transcribe(
                    audio_path=candidate,
                    url=cfg["stt_url"],
                    model=cfg["stt_model"],
                    api_key=cfg["stt_api_key"],
                    prompt=cfg["stt_prompt"],
                    api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
                )
                return (candidate, (stt_result.get("text") or "").strip(), "")
            except Exception:
                # If STT fails, fall through to using as voice description
                return ("", "", candidate)
        # Plain string — voice name or description for description-driven models
        return ("", "", candidate)

    # No explicit voice config — auto-clone from the video's cached audio
    rp, rt = _resolve_voice_reference(url)
    return (rp, rt, "")


def _resolve_voice_reference(url):
    """Produce a (ref_audio_path, ref_text) tuple for voice cloning.

    Extracts a 7-second clip from the middle of the cached audio (avoiding
    intro/outro and matching Qwen3-TTS-Base's recommended ~10s ceiling),
    then transcribes it via STT to produce the ref_text that voice-cloning
    models require alongside ref_audio. Both are cached so subsequent
    speak calls reuse them.

    Returns (path, text) or ("", "") if a reference can't be produced.
    """
    if not cache.has_file(url, "audio.mp3"):
        return ("", "")

    voice_clip = cache.entry_path(url, "voice_sample.wav")
    voice_clip_text_path = cache.entry_path(url, "voice_sample.txt")

    # Extract clip if missing
    if not os.path.isfile(voice_clip):
        audio_path = cache.entry_path(url, "audio.mp3")
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(probe.stdout.strip()) if probe.returncode == 0 else 60
            # 7-second clip from the middle. Qwen3-TTS docs recommend ~10s
            # max; longer references increase voice variance.
            start = max(0, (duration / 2) - 3.5)
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path, "-ss", str(start),
                 "-t", "7", "-ar", "24000", "-ac", "1", voice_clip],
                capture_output=True, timeout=30,
            )
        except Exception:
            return ("", "")
        if not os.path.isfile(voice_clip):
            return ("", "")

    # Transcribe the clip if we don't have a transcript yet
    ref_text = cache.read_text(url, "voice_sample.txt")
    if not ref_text:
        try:
            stt_result = llm_transcribe(
                audio_path=voice_clip,
                url=cfg["stt_url"],
                model=cfg["stt_model"],
                api_key=cfg["stt_api_key"],
                prompt=_stt_bias_prompt(_fetch_and_cache_metadata(url),
                                        cfg["stt_prompt"], cfg["stt_metadata_prompt"]),
                api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
                word_timestamps=cfg["stt_word_timestamps"],
            )
            ref_text = (stt_result.get("text") or "").strip()
            cache.write_text(url, "voice_sample.txt", ref_text)
        except Exception:
            ref_text = ""

    # If STT failed, we can still try voice-cloning with a placeholder; some
    # mlx-audio variants are lenient. Empty ref_text disables cloning entirely
    # in our llm_client (both fields must be set).
    return (voice_clip, ref_text or "")


def _speak_source_filename(source):
    """Map a speak 'source' token to its cache filename, or None when it
    doesn't denote a known text artifact (also the path-traversal guard:
    only whitelisted names and safe generated prefixes resolve)."""
    fixed = {
        "transcript": "transcript.txt",
        "summary": "summary.txt",
        "counterargue": "counterargue.txt",
        "counterargue-diarized": "counterargue-diarized.txt",
        "diarized": "transcript_diarized.txt",
    }
    if source in fixed:
        return fixed[source]
    safe = re.fullmatch(r"(translation|summary|action)-[a-z0-9][a-z0-9-]*", source or "")
    if safe:
        return source + ".txt"
    return None


def _run_speak(job_id, url, source, voice_override):
    job = jobs[job_id]
    _log("speak", "start job=%s model=%s source=%s url=%s",
         job_id, cfg["tts_model"], source, url)
    try:
        source_file = _speak_source_filename(source)
        if not source_file:
            raise RuntimeError(f"Unknown source: {source}")

        text = cache.read_text(url, source_file)
        if text is None:
            raise RuntimeError(f"No {source} text found — run that operation first")

        # Voice resolution priority:
        #   1. voice_override (per-request body) — highest
        #   2. RECLIP_TTS_VOICE (config) — could be a preset voice name, a
        #      voice-description prompt for description-driven models, or a
        #      filesystem path to a reference audio clip
        #   3. Auto-clone from the video's own audio (middle 7s + STT)
        #
        # File paths route to ref_audio (with ref_text from STT of the clip).
        # Plain strings route to the OpenAI-compatible `voice` field, which
        # oMLX maps to mlx-audio's voice= kwarg if the model has one
        # (Kokoro, CustomVoice), or to the `instructions` field for
        # VoiceDesign models (Qwen3-TTS-VoiceDesign).
        ref_path, ref_text, voice_str = _resolve_speak_inputs(url, voice_override)
        cache_key_voice = voice_str or ref_path
        tts_filename = _tts_cache_filename(text, cache_key_voice)

        if cache.has_file(url, tts_filename):
            job["status"] = "done"
            job["file"] = cache.entry_path(url, tts_filename)
            job["filename"] = f"{source}.wav"
            _log("speak", "cache hit — returning %s", tts_filename)
            return

        chunks = _chunk_text_for_tts(text, max_chars=300)
        _log("speak", "synthesizing %d chunks (text=%d chars)", len(chunks), len(text))
        t0 = time.time()
        chunk_wavs = []
        for i, chunk in enumerate(chunks):
            job["progress"] = f"chunk {i+1}/{len(chunks)}"
            chunk_t0 = time.time()
            chunk_wav = text_to_speech(
                url=cfg["tts_url"],
                model=cfg["tts_model"],
                text=chunk,
                api_key=cfg["tts_api_key"],
                voice=voice_str,
                ref_audio_path=ref_path,
                ref_text=ref_text,
                api_key_hint="RECLIP_TTS_API_KEY or RECLIP_API_KEY",
            )
            chunk_wavs.append(chunk_wav)
            _log("speak", "chunk %d/%d done in %.1fs", i + 1, len(chunks), time.time() - chunk_t0)

        # Concatenate WAV chunks into single output
        out_path = cache.entry_path(url, tts_filename)
        _concat_wavs(chunk_wavs, out_path)

        # Also produce an MP3 for compact downloads
        mp3_path = out_path.replace(".wav", ".mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", out_path, "-codec:a", "libmp3lame",
                 "-qscale:a", "4", mp3_path],
                capture_output=True, timeout=60,
            )
        except Exception:
            pass  # MP3 is a bonus; WAV is the authoritative format

        job["status"] = "done"
        job["file"] = out_path
        job["filename"] = f"{source}.wav"
        _log("speak", "done in %.1fs", time.time() - t0)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log("speak", "ERROR: %s", e)


def _run_media_job_sync(worker, *args):
    """Run an existing media worker synchronously for non-HTTP callers.

    The web layer schedules these workers in background threads. The CLI needs
    their identical download, cache, and TTS behavior without starting Flask,
    so it creates a private in-process job and returns its completed artifact.
    """
    job_id = f"cli-{uuid.uuid4().hex}"
    jobs[job_id] = {"status": "processing", "type": "media", "url": args[0]}
    try:
        worker(job_id, *args)
        job = jobs[job_id]
        if job.get("status") != "done":
            raise RuntimeError(job.get("error") or "Media operation failed")
        if not job.get("file"):
            raise RuntimeError("Media operation completed without an output file")
        return {"file": job["file"], "filename": job.get("filename", "")}
    finally:
        jobs.pop(job_id, None)


def download_url(url, format_choice="video", format_id=None):
    """Download a URL through the same yt-dlp and cache path as the web UI.

    This preserves the video-source-then-ffmpeg workaround for audio-only
    YouTube 403s instead of allowing command-line callers to use a divergent
    direct-audio downloader.
    """
    if format_choice not in ("audio", "video"):
        raise RuntimeError("format must be 'audio' or 'video'")
    return _run_media_job_sync(run_download, url, format_choice, format_id)


def info_url(url):
    """Return video metadata through the same yt-dlp normalization as the UI."""
    if classify_url(url) == "images":
        raise RuntimeError("Image-host metadata is available in the web UI")
    return _video_info(url)


def transcribe_url(url):
    """Return the cached-or-new transcript using the web STT configuration."""
    return _ensure_transcript(url)


def diarize_url(url):
    """Return the cached-or-new speaker-labelled transcript from the web pipeline."""
    return _diarize_sync(url)


def list_actions():
    """Return the current hot-reloaded action registry for non-HTTP callers."""
    actions_registry.maybe_reload()
    return actions_registry.list()


def action_url(url, action_id, params=None):
    """Run one configured action using the same source chaining and cache as the UI."""
    return _run_action_sync(url, _action_or_builtin(action_id), params or {})


def speak_url(url, source="summary", voice_override="", output_format="wav"):
    """Synthesize a cached text artifact with the web TTS pipeline synchronously."""
    if output_format not in ("wav", "mp3"):
        raise RuntimeError("format must be 'wav' or 'mp3'")
    result = _run_media_job_sync(_run_speak, url, source, voice_override)
    if output_format == "mp3":
        mp3_path = result["file"].replace(".wav", ".mp3")
        if os.path.isfile(mp3_path):
            result["file"] = mp3_path
            result["filename"] = result["filename"].replace(".wav", ".mp3")
    return result


@app.route("/api/speak", methods=["POST"])
def speak_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    source = data.get("source", "summary").strip()
    voice_override = data.get("voice", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Quick cache check — match the cache key formula used in _run_speak
    if voice_override:
        cache_key_voice = voice_override
    else:
        ref_path, _ = _resolve_voice_reference(url)
        cache_key_voice = ref_path
    source_file = _speak_source_filename(source) or (source + ".txt")

    text = cache.read_text(url, source_file)
    if text:
        tts_filename = _tts_cache_filename(text, cache_key_voice)
        if cache.has_file(url, tts_filename):
            return send_file(
                cache.entry_path(url, tts_filename),
                mimetype="audio/wav",
                as_attachment=False,
                download_name=f"{source}.wav",
            )

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "media", "url": url}

    thread = threading.Thread(target=_run_speak, args=(job_id, url, source, voice_override))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/speak/download", methods=["POST"])
def speak_download():
    """Download cached TTS audio as WAV or MP3."""
    data = request.json
    url = data.get("url", "").strip()
    source = data.get("source", "summary").strip()
    fmt = data.get("format", "mp3").strip().lower()
    voice_override = data.get("voice", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if voice_override:
        cache_key_voice = voice_override
    else:
        ref_path, _ = _resolve_voice_reference(url)
        cache_key_voice = ref_path
    source_file = _speak_source_filename(source) or (source + ".txt")

    text = cache.read_text(url, source_file)
    if not text:
        return jsonify({"error": "No text to speak"}), 404

    tts_filename = _tts_cache_filename(text, cache_key_voice)
    wav_path = cache.entry_path(url, tts_filename)

    if fmt == "mp3":
        mp3_path = wav_path.replace(".wav", ".mp3")
        if not os.path.isfile(mp3_path) and os.path.isfile(wav_path):
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame",
                     "-qscale:a", "4", mp3_path],
                    capture_output=True, timeout=60,
                )
            except Exception:
                pass
        if os.path.isfile(mp3_path):
            return send_file(mp3_path, mimetype="audio/mpeg",
                             as_attachment=True, download_name=f"{source}.mp3")

    if os.path.isfile(wav_path):
        return send_file(wav_path, mimetype="audio/wav",
                         as_attachment=True, download_name=f"{source}.wav")
    return jsonify({"error": "Audio not found"}), 404


@app.route("/api/text/<job_id>")
def text_download(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done" or job.get("type") != "text":
        return jsonify({"error": "Text not ready"}), 404
    text = job.get("text", "")
    buf = io.BytesIO(text.encode("utf-8"))
    filename = job.get("filename", "text.txt")
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="text/plain")


if __name__ == "__main__":
    # Env vars (HOST/PORT) win for ad-hoc launches; otherwise pull from config.ini.
    host = os.environ.get("HOST") or cfg.get("host", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT") or cfg.get("port", 8899))
    except (TypeError, ValueError):
        port = 8899
    if host not in ("127.0.0.1", "::1", "localhost"):
        sys.stderr.write(
            f"\x1b[33m[reclip+] Binding to {host}:{port} — reachable from other "
            f"machines. Settings UI stays loopback-gated, but transcription/"
            f"summarization/TTS endpoints are exposed.\x1b[0m\n"
        )
        if cfg.get("gallery_dl_browser"):
            sys.stderr.write(
                f"\x1b[31m[reclip+] WARNING: gallery-dl is set to extract cookies "
                f"from {cfg.get('gallery_dl_browser')}. Anyone who can reach "
                f"{host}:{port} can trigger image fetches that ride your session "
                f"cookies. Set RECLIP_GALLERY_DL_BROWSER='' in config.ini to "
                f"disable, or pin RECLIP_HOST back to 127.0.0.1.\x1b[0m\n"
            )
    app.run(host=host, port=port)
