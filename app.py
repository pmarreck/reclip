import io
import os
import uuid
import glob
import json
import subprocess
import threading
from flask import Flask, request, jsonify, send_file, render_template

from config import load_config
from cache import Cache
from llm_client import transcribe as llm_transcribe, chat_completion, LLMError

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


def _is_loopback(req):
    addr = req.remote_addr or ""
    return addr in ("127.0.0.1", "::1", "localhost")


def _ensure_audio(url):
    """Ensure audio.mp3 exists in cache. Downloads via yt-dlp if needed."""
    if cache.has_file(url, "audio.mp3"):
        return cache.entry_path(url, "audio.mp3")
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
        )
        transcript = result["text"]
        cache.write_text(url, "transcript.txt", transcript)
    summary = chat_completion(
        url=cfg["summarize_url"],
        model=cfg["summarize_model"],
        api_key=cfg["summarize_api_key"],
        system_prompt=cfg["summarize_prompt"],
        user_content=transcript,
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
        )
        transcript = result["text"]
        cache.write_text(url, "transcript.txt", transcript)
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
            )
            transcript = result["text"]
            cache.write_text(url, "transcript.txt", transcript)
        summary = chat_completion(
            url=cfg["summarize_url"],
            model=cfg["summarize_model"],
            api_key=cfg["summarize_api_key"],
            system_prompt=cfg["summarize_prompt"],
            user_content=transcript,
        )
        cache.write_text(url, "summary.txt", summary)
        job["status"] = "done"
        job["text"] = summary
        job["filename"] = "summary.txt"
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
                )
                source_text = result["text"]
                cache.write_text(url, "transcript.txt", source_text)

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

        info = parse_ytdlp_json(result.stdout)

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
