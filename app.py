import io
import os
import uuid
import glob
import json
import subprocess
import threading
import time
from flask import Flask, request, jsonify, send_file, render_template, Response

from config import load_config
from cache import Cache
import hashlib
from llm_client import transcribe as llm_transcribe, chat_completion, text_to_speech, LLMError
from service import ServiceManager

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

cfg = load_config()
cache = Cache(cfg["cache_dir"], cfg["cache_max_mb"])

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


def _ensure_audio(url):
    """Ensure audio.mp3 exists in cache. Downloads via yt-dlp if needed."""
    if cache.has_file(url, "audio.mp3"):
        return cache.entry_path(url, "audio.mp3")
    # Fetch metadata before downloading (cheap, and useful later)
    _fetch_and_cache_metadata(url)
    # Download using yt-dlp with audio extraction
    cmd = [
        "yt-dlp", "--no-playlist", "-x", "--audio-format", "mp3",
        "-o", cache.entry_path(url, "audio.%(ext)s"), url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().split("\n")[-1])
    audio_path = cache.entry_path(url, "audio.mp3")
    if not os.path.isfile(audio_path):
        # yt-dlp may have named it differently
        entry_dir = os.path.dirname(audio_path)
        for f in os.listdir(entry_dir):
            if f.startswith("audio."):
                os.rename(os.path.join(entry_dir, f), audio_path)
                break
    if not os.path.isfile(audio_path):
        raise RuntimeError("Audio download completed but no file found")
    return audio_path


def _save_transcript(url, raw_text):
    """Prepend metadata header and cache the transcript. Returns full text."""
    full = _metadata_header(url) + raw_text
    cache.write_text(url, "transcript.txt", full)
    return full


def _translate_filename(source, language):
    lang = language.lower().strip().replace(" ", "-")
    if source == "summary":
        return f"summary-{lang}.txt"
    return f"translation-{lang}.txt"


def _run_summarize_sync(url):
    """Synchronous summarize for use within translate pipeline."""
    transcript = cache.read_text(url, "transcript.txt")
    if transcript is None:
        audio_path = _ensure_audio(url)
        result = llm_transcribe(
            audio_path=audio_path,
            url=cfg["stt_url"],
            model=cfg["stt_model"],
            api_key=cfg["stt_api_key"],
            prompt=cfg["stt_prompt"],
            api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
        )
        transcript = _save_transcript(url, result["text"])
    summary = chat_completion(
        url=cfg["summarize_url"],
        model=cfg["summarize_model"],
        api_key=cfg["summarize_api_key"],
        system_prompt=cfg["summarize_prompt"],
        user_content=transcript,
        api_key_hint="RECLIP_SUMMARIZE_API_KEY or RECLIP_API_KEY",
    )
    cache.write_text(url, "summary.txt", summary)
    return summary


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "-o", out_template]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            job["status"] = "error"
            job["error"] = result.stderr.strip().split("\n")[-1]
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
    try:
        audio_path = _ensure_audio(url)
        result = llm_transcribe(
            audio_path=audio_path,
            url=cfg["stt_url"],
            model=cfg["stt_model"],
            api_key=cfg["stt_api_key"],
            prompt=cfg["stt_prompt"],
            api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
        )
        transcript = _save_transcript(url, result["text"])
        job["status"] = "done"
        job["text"] = transcript
        job["filename"] = "transcript.txt"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def _run_summarize(job_id, url):
    job = jobs[job_id]
    try:
        # Ensure transcript exists first
        transcript = cache.read_text(url, "transcript.txt")
        if transcript is None:
            audio_path = _ensure_audio(url)
            result = llm_transcribe(
                audio_path=audio_path,
                url=cfg["stt_url"],
                model=cfg["stt_model"],
                api_key=cfg["stt_api_key"],
                prompt=cfg["stt_prompt"],
                api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
            )
            transcript = result["text"]
            cache.write_text(url, "transcript.txt", transcript)
        summary = chat_completion(
            url=cfg["summarize_url"],
            model=cfg["summarize_model"],
            api_key=cfg["summarize_api_key"],
            system_prompt=cfg["summarize_prompt"],
            user_content=transcript,
            api_key_hint="RECLIP_SUMMARIZE_API_KEY or RECLIP_API_KEY",
        )
        cache.write_text(url, "summary.txt", summary)
        job["status"] = "done"
        job["text"] = summary
        job["filename"] = "summary.txt"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def _run_counterargue(job_id, url):
    job = jobs[job_id]
    try:
        transcript = cache.read_text(url, "transcript.txt")
        if transcript is None:
            audio_path = _ensure_audio(url)
            result = llm_transcribe(
                audio_path=audio_path,
                url=cfg["stt_url"],
                model=cfg["stt_model"],
                api_key=cfg["stt_api_key"],
                prompt=cfg["stt_prompt"],
                api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
            )
            transcript = _save_transcript(url, result["text"])
        counterargument = chat_completion(
            url=cfg["counterargue_url"],
            model=cfg["counterargue_model"],
            api_key=cfg["counterargue_api_key"],
            system_prompt=cfg["counterargue_prompt"],
            user_content=transcript,
            api_key_hint="RECLIP_COUNTERARGUE_API_KEY or RECLIP_API_KEY",
        )
        cache.write_text(url, "counterargue.txt", counterargument)
        job["status"] = "done"
        job["text"] = counterargument
        job["filename"] = "counterargue.txt"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


def _run_translate(job_id, url, language, source):
    job = jobs[job_id]
    try:
        # Ensure source text exists
        if source == "summary":
            source_text = cache.read_text(url, "summary.txt")
            if source_text is None:
                _run_summarize_sync(url)
                source_text = cache.read_text(url, "summary.txt")
        else:
            source_text = cache.read_text(url, "transcript.txt")
            if source_text is None:
                audio_path = _ensure_audio(url)
                result = llm_transcribe(
                    audio_path=audio_path,
                    url=cfg["stt_url"],
                    model=cfg["stt_model"],
                    api_key=cfg["stt_api_key"],
                    prompt=cfg["stt_prompt"],
                    api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
                )
                source_text = _save_transcript(url, result["text"])

        if source_text is None:
            raise RuntimeError(f"Could not obtain {source} text")

        prompt_template = cfg["translate_prompt"]
        system_prompt = prompt_template.replace("{language}", language)

        translation = chat_completion(
            url=cfg["translate_url"],
            model=cfg["translate_model"],
            api_key=cfg["translate_api_key"],
            system_prompt=system_prompt,
            user_content=source_text,
            api_key_hint="RECLIP_TRANSLATE_API_KEY or RECLIP_API_KEY",
        )
        filename = _translate_filename(source, language)
        cache.write_text(url, filename, translation)
        job["status"] = "done"
        job["text"] = translation
        job["filename"] = filename
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        # yt-dlp may return multiple JSON objects (one per line) for multi-video pages
        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not entries:
            return jsonify({"error": "No video info returned"}), 400

        # Use the first entry with video formats, or fall back to the first entry
        info = entries[0]
        for entry in entries:
            if any(f.get("height") for f in entry.get("formats", [])):
                info = entry
                break

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/playlist", methods=["POST"])
def get_playlist_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--flat-playlist", "-J", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries", [])
        urls = [entry.get("url") for entry in entries if entry.get("url")]
        return jsonify({"urls": urls})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except Exception as e:
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

        return jsonify({"entries": entries})
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


@app.route("/api/summarize", methods=["POST"])
def summarize_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Check cache first
    cached_text = cache.read_text(url, "summary.txt")
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_summarize, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/counterargue", methods=["POST"])
def counterargue_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Check cache first
    cached_text = cache.read_text(url, "counterargue.txt")
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_counterargue, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


def _chunk_text_for_tts(text, max_chars=300):
    """Split text into TTS-friendly chunks at paragraph/sentence boundaries.

    Qwen3-TTS drifts in voice consistency on long text (>300 chars is shaky,
    >500 very bad). This splits at paragraph breaks first, then sentence
    boundaries, then word boundaries as a last resort.
    """
    # Strip metadata header if present
    if "=== Transcript ===" in text:
        text = text.split("=== Transcript ===", 1)[1].strip()

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


def _resolve_voice(url):
    """Pick the best voice reference: middle 10s of video audio > global default > none.

    Extracts a 10-second clip from the middle of the audio for voice cloning,
    avoiding intro/outro music and getting the primary speaker.
    """
    if cache.has_file(url, "audio.mp3"):
        voice_clip = cache.entry_path(url, "voice_sample.wav")
        if not os.path.isfile(voice_clip):
            audio_path = cache.entry_path(url, "audio.mp3")
            try:
                # Get duration via ffprobe
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", audio_path],
                    capture_output=True, text=True, timeout=10,
                )
                duration = float(probe.stdout.strip()) if probe.returncode == 0 else 60
                # Extract 10 seconds from the middle
                start = max(0, (duration / 2) - 5)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", audio_path, "-ss", str(start),
                     "-t", "10", "-ar", "24000", "-ac", "1", voice_clip],
                    capture_output=True, timeout=30,
                )
            except Exception:
                return cfg["tts_voice"] or ""
        if os.path.isfile(voice_clip):
            return voice_clip
    # Fall back to globally configured voice
    if cfg["tts_voice"]:
        return cfg["tts_voice"]
    return ""


def _run_speak(job_id, url, source, voice_override):
    job = jobs[job_id]
    try:
        # Resolve which text to speak
        source_map = {
            "transcript": "transcript.txt",
            "summary": "summary.txt",
            "counterargue": "counterargue.txt",
        }
        if source.startswith("translation-") or source.startswith("summary-"):
            source_file = source + ".txt" if not source.endswith(".txt") else source
        else:
            source_file = source_map.get(source)
        if not source_file:
            raise RuntimeError(f"Unknown source: {source}")

        text = cache.read_text(url, source_file)
        if text is None:
            raise RuntimeError(f"No {source} text found — run that operation first")

        voice = voice_override or _resolve_voice(url)
        tts_filename = _tts_cache_filename(text, voice)

        # Check TTS cache
        if cache.has_file(url, tts_filename):
            job["status"] = "done"
            job["file"] = cache.entry_path(url, tts_filename)
            job["filename"] = f"{source}.wav"
            return

        # Chunk long text to avoid Qwen3-TTS voice drift on >300 chars
        # (see github.com/QwenLM/Qwen3-TTS issues #80, #239)
        chunks = _chunk_text_for_tts(text, max_chars=300)
        chunk_wavs = []
        for i, chunk in enumerate(chunks):
            job["progress"] = f"chunk {i+1}/{len(chunks)}"
            chunk_wav = text_to_speech(
                url=cfg["tts_url"],
                model=cfg["tts_model"],
                text=chunk,
                api_key=cfg["tts_api_key"],
                voice=voice,
                api_key_hint="RECLIP_TTS_API_KEY or RECLIP_API_KEY",
            )
            chunk_wavs.append(chunk_wav)

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
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/api/speak", methods=["POST"])
def speak_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    source = data.get("source", "summary").strip()
    voice_override = data.get("voice", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Quick cache check
    voice = voice_override or _resolve_voice(url)
    source_map = {
        "transcript": "transcript.txt",
        "summary": "summary.txt",
        "counterargue": "counterargue.txt",
    }
    if source.startswith("translation-") or source.startswith("summary-"):
        source_file = source + ".txt" if not source.endswith(".txt") else source
    else:
        source_file = source_map.get(source, source + ".txt")

    text = cache.read_text(url, source_file)
    if text:
        tts_filename = _tts_cache_filename(text, voice)
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

    voice = voice_override or _resolve_voice(url)
    source_map = {
        "transcript": "transcript.txt",
        "summary": "summary.txt",
        "counterargue": "counterargue.txt",
    }
    if source.startswith("translation-") or source.startswith("summary-"):
        source_file = source + ".txt" if not source.endswith(".txt") else source
    else:
        source_file = source_map.get(source, source + ".txt")

    text = cache.read_text(url, source_file)
    if not text:
        return jsonify({"error": "No text to speak"}), 404

    tts_filename = _tts_cache_filename(text, voice)
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


@app.route("/api/translate", methods=["POST"])
def translate_endpoint():
    data = request.json
    url = data.get("url", "").strip()
    language = data.get("language", "").strip()
    source = data.get("source", "transcript").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not language:
        return jsonify({"error": "No language provided"}), 400
    if source not in ("transcript", "summary"):
        return jsonify({"error": "source must be 'transcript' or 'summary'"}), 400

    filename = _translate_filename(source, language)

    # Check cache first
    cached_text = cache.read_text(url, filename)
    if cached_text is not None:
        return jsonify({"cached": True, "text": cached_text})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(
        target=_run_translate, args=(job_id, url, language, source),
    )
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


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
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
