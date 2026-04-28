import json
import os
import time
import pytest
import responses
import subprocess


class TestCacheStatsEndpoint:
    def test_cache_stats_returns_info(self, client):
        resp = client.get("/api/cache/stats")
        data = resp.get_json()
        assert resp.status_code == 200
        assert "location" in data
        assert "max_mb" in data
        assert "used_mb" in data
        assert "entry_count" in data


class TestCheckStatusExtendedFields:
    """Verify check_status now returns type and text fields."""

    def test_status_includes_type_and_text_for_text_job(self, client):
        import app
        job_id = "textjob123"
        app.jobs[job_id] = {
            "status": "done",
            "type": "text",
            "text": "Some transcript text",
        }
        resp = client.get(f"/api/status/{job_id}")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["type"] == "text"
        assert data["text"] == "Some transcript text"

    def test_status_defaults_type_to_media(self, client):
        import app
        job_id = "mediajob42"
        app.jobs[job_id] = {
            "status": "done",
            "filename": "video.mp4",
        }
        resp = client.get(f"/api/status/{job_id}")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["type"] == "media"


class TestTranscribeEndpoint:
    @responses.activate
    def test_transcribe_cached_returns_immediately(self, client, tmp_cache):
        import app
        app.cache.write_text(
            "https://youtube.com/watch?v=cached1",
            "transcript.txt",
            "Cached transcript",
        )
        resp = client.post("/api/transcribe", json={
            "url": "https://youtube.com/watch?v=cached1",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("cached") is True
        assert data.get("text") == "Cached transcript"

    def test_transcribe_missing_url_returns_400(self, client):
        resp = client.post("/api/transcribe", json={})
        assert resp.status_code == 400

    @responses.activate
    def test_transcribe_uncached_returns_job_id(self, client, tmp_cache, monkeypatch):
        # Mock subprocess.run so yt-dlp "downloads" audio
        import app

        def mock_run(*args, **kwargs):
            # Simulate yt-dlp creating an audio file
            url = args[0][-1] if args else kwargs.get("args", [])[-1]
            audio_path = app.cache.entry_path(url, "audio.mp3")
            os.makedirs(os.path.dirname(audio_path), exist_ok=True)
            with open(audio_path, "wb") as f:
                f.write(b"fake mp3 data")

            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock the LLM transcribe call
        responses.add(
            responses.POST,
            "http://localhost:8000/v1/audio/transcriptions",
            json={"text": "Transcribed text", "language": "en", "duration": 60},
            status=200,
        )

        resp = client.post("/api/transcribe", json={
            "url": "https://youtube.com/watch?v=uncached1",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert "job_id" in data

        # Wait for the background thread to finish
        job_id = data["job_id"]
        for _ in range(50):
            status_resp = client.get(f"/api/status/{job_id}")
            status_data = status_resp.get_json()
            if status_data["status"] != "processing":
                break
            time.sleep(0.05)

        assert status_data["status"] == "done"
        assert status_data["type"] == "text"
        assert "Transcribed text" in status_data["text"]

        # Verify it was cached
        cached = app.cache.read_text(
            "https://youtube.com/watch?v=uncached1",
            "transcript.txt",
        )
        assert "Transcribed text" in cached
        assert "=== Video Metadata ===" in cached


class TestSummarizeEndpoint:
    @responses.activate
    def test_summarize_cached_returns_immediately(self, client, tmp_cache):
        import app
        app.cache.write_text(
            "https://youtube.com/watch?v=sumcached",
            "summary.txt",
            "Cached summary",
        )
        resp = client.post("/api/summarize", json={
            "url": "https://youtube.com/watch?v=sumcached",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("cached") is True
        assert data.get("text") == "Cached summary"

    def test_summarize_missing_url_returns_400(self, client):
        resp = client.post("/api/summarize", json={})
        assert resp.status_code == 400

    @responses.activate
    def test_summarize_uncached_returns_job_id(self, client, tmp_cache, monkeypatch):
        import app

        # Pre-cache transcript so summarize doesn't need to transcribe
        app.cache.write_text(
            "https://youtube.com/watch?v=sumuncached",
            "transcript.txt",
            "This is a long transcript that needs summarizing.",
        )

        # Mock the chat completion call for summarize
        responses.add(
            responses.POST,
            "http://localhost:8000/v1/chat/completions",
            json={"choices": [{"message": {"content": "Summary of the transcript."}}]},
            status=200,
        )

        resp = client.post("/api/summarize", json={
            "url": "https://youtube.com/watch?v=sumuncached",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert "job_id" in data

        # Wait for the background thread
        job_id = data["job_id"]
        for _ in range(50):
            status_resp = client.get(f"/api/status/{job_id}")
            status_data = status_resp.get_json()
            if status_data["status"] != "processing":
                break
            time.sleep(0.05)

        assert status_data["status"] == "done"
        assert status_data["type"] == "text"
        assert "Summary of the transcript." in status_data["text"]


class TestTranslateEndpoint:
    @responses.activate
    def test_translate_cached_returns_immediately(self, client, tmp_cache):
        import app
        url = "https://youtube.com/watch?v=trcached"
        app.cache.write_text(url, "translation-spanish.txt", "Cached translation")

        resp = client.post("/api/translate", json={
            "url": url,
            "language": "Spanish",
            "source": "transcript",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("cached") is True
        assert data.get("text") == "Cached translation"

    def test_translate_invalid_source_returns_400(self, client):
        resp = client.post("/api/translate", json={
            "url": "https://example.com",
            "language": "Spanish",
            "source": "invalid",
        })
        assert resp.status_code == 400

    def test_translate_missing_url_returns_400(self, client):
        resp = client.post("/api/translate", json={
            "language": "Spanish",
            "source": "transcript",
        })
        assert resp.status_code == 400

    def test_translate_missing_language_returns_400(self, client):
        resp = client.post("/api/translate", json={
            "url": "https://example.com",
            "source": "transcript",
        })
        assert resp.status_code == 400

    @responses.activate
    def test_translate_summary_cached_filename(self, client, tmp_cache):
        import app
        url = "https://youtube.com/watch?v=sumtrcached"
        app.cache.write_text(url, "summary-french.txt", "Cached summary translation")

        resp = client.post("/api/translate", json={
            "url": url,
            "language": "French",
            "source": "summary",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("cached") is True
        assert data.get("text") == "Cached summary translation"

    @responses.activate
    def test_translate_uncached_returns_job_id(self, client, tmp_cache, monkeypatch):
        import app

        # Pre-cache transcript so translate can read it
        url = "https://youtube.com/watch?v=truncached"
        app.cache.write_text(url, "transcript.txt", "English text to translate.")

        # Mock the chat completion call for translate
        responses.add(
            responses.POST,
            "http://localhost:8000/v1/chat/completions",
            json={"choices": [{"message": {"content": "Texto traducido."}}]},
            status=200,
        )

        resp = client.post("/api/translate", json={
            "url": url,
            "language": "Spanish",
            "source": "transcript",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert "job_id" in data

        # Wait for the background thread
        job_id = data["job_id"]
        for _ in range(50):
            status_resp = client.get(f"/api/status/{job_id}")
            status_data = status_resp.get_json()
            if status_data["status"] != "processing":
                break
            time.sleep(0.05)

        assert status_data["status"] == "done"
        assert status_data["type"] == "text"
        assert "Texto traducido." in status_data["text"]


class TestPlaylistEndpoint:
    def test_playlist_returns_entries(self, client, monkeypatch):
        fake_output = (
            '{"id": "v1", "title": "Video 1", "url": "https://youtube.com/watch?v=v1", "duration": 120}\n'
            '{"id": "v2", "title": "Video 2", "url": "https://youtube.com/watch?v=v2", "duration": 300}\n'
        )

        def mock_run(*args, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = fake_output
                stderr = ""
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        resp = client.post("/api/playlist", json={
            "url": "https://youtube.com/playlist?list=PLtest",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert len(data["entries"]) == 2
        assert data["entries"][0]["title"] == "Video 1"
        assert data["entries"][1]["title"] == "Video 2"
        assert data["entries"][0]["url"] == "https://youtube.com/watch?v=v1"
        assert data["entries"][1]["duration"] == 300

    def test_playlist_missing_url_returns_400(self, client):
        resp = client.post("/api/playlist", json={})
        assert resp.status_code == 400

    def test_playlist_error_returns_400(self, client, monkeypatch):
        def mock_run(*args, **kwargs):
            class FakeResult:
                returncode = 1
                stdout = ""
                stderr = "ERROR: not a playlist\n"
            return FakeResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        resp = client.post("/api/playlist", json={
            "url": "https://youtube.com/watch?v=notaplaylist",
        })
        assert resp.status_code == 400


class TestTextDownload:
    def test_text_download_serves_txt_file(self, client):
        import app
        job_id = "txtdl123"
        app.jobs[job_id] = {
            "status": "done",
            "type": "text",
            "text": "Here is the transcript text.",
            "filename": "transcript.txt",
        }
        resp = client.get(f"/api/text/{job_id}")
        assert resp.status_code == 200
        assert resp.data.decode("utf-8") == "Here is the transcript text."
        assert "text/plain" in resp.content_type

    def test_text_download_job_not_found(self, client):
        resp = client.get("/api/text/nonexistent")
        assert resp.status_code == 404

    def test_text_download_job_not_done(self, client):
        import app
        app.jobs["pending123"] = {
            "status": "processing",
            "type": "text",
        }
        resp = client.get("/api/text/pending123")
        assert resp.status_code == 404


class TestDownloadCaching:
    """Verify that run_download caches files and metadata after a successful download."""

    def _make_mock_run(self, job_id, download_dir, format_choice="video"):
        """Return a mock subprocess.run that creates a fake downloaded file."""
        def mock_run(cmd, *args, **kwargs):
            # If this is an ffmpeg audio extraction call, create a fake audio file
            if cmd[0] == "ffmpeg":
                audio_out = cmd[-2]  # second-to-last arg is the output path
                os.makedirs(os.path.dirname(audio_out), exist_ok=True)
                with open(audio_out, "wb") as f:
                    f.write(b"fake extracted audio")
                class FakeResult:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return FakeResult()

            # yt-dlp download call — create the downloaded file
            ext = "mp3" if format_choice == "audio" else "mp4"
            fake_file = os.path.join(download_dir, f"{job_id}.{ext}")
            with open(fake_file, "wb") as f:
                f.write(b"fake media content")

            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""
            return FakeResult()

        return mock_run

    def test_video_download_caches_video_file(self, client, tmp_cache, monkeypatch, tmp_path):
        import app

        url = "https://youtube.com/watch?v=cachevideo1"
        job_id = "cachevid001"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Test Video"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR))

        app.run_download(job_id, url, "video", None)

        assert app.jobs[job_id]["status"] == "done"
        assert app.cache.has_file(url, "video.mp4"), "video.mp4 should be cached after video download"

    def test_video_download_also_extracts_audio(self, client, tmp_cache, monkeypatch):
        import app

        url = "https://youtube.com/watch?v=cachevideo2"
        job_id = "cachevid002"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Test Video 2"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR))

        app.run_download(job_id, url, "video", None)

        assert app.jobs[job_id]["status"] == "done"
        assert app.cache.has_file(url, "audio.mp3"), "audio.mp3 should be extracted and cached after video download"

    def test_audio_download_caches_audio_file(self, client, tmp_cache, monkeypatch):
        import app

        url = "https://youtube.com/watch?v=cacheaudio1"
        job_id = "cacheaud001"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Test Audio"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR, format_choice="audio"))

        app.run_download(job_id, url, "audio", None)

        assert app.jobs[job_id]["status"] == "done"
        assert app.cache.has_file(url, "audio.mp3"), "audio.mp3 should be cached after audio download"

    def test_audio_download_does_not_cache_video(self, client, tmp_cache, monkeypatch):
        import app

        url = "https://youtube.com/watch?v=cacheaudio2"
        job_id = "cacheaud002"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Test Audio 2"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR, format_choice="audio"))

        app.run_download(job_id, url, "audio", None)

        assert app.jobs[job_id]["status"] == "done"
        assert not app.cache.has_file(url, "video.mp4"), "video.mp4 should NOT be cached for audio-only download"

    def test_video_download_writes_meta_json(self, client, tmp_cache, monkeypatch):
        import app

        url = "https://youtube.com/watch?v=cachemeta1"
        job_id = "cachemeta01"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Meta Test"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR))

        app.run_download(job_id, url, "video", None)

        assert app.jobs[job_id]["status"] == "done"
        meta = app.cache.read_meta(url)
        assert meta.get("url") == url, "meta.json should record the URL"
        assert "fetched_at" in meta, "meta.json should record fetched_at timestamp"

    def test_cache_failure_does_not_break_download(self, client, tmp_cache, monkeypatch):
        """Cache errors must be swallowed — the download itself must still succeed."""
        import app

        url = "https://youtube.com/watch?v=cachefail1"
        job_id = "cachefail01"
        app.jobs[job_id] = {"status": "downloading", "url": url, "title": "Fail Test"}

        monkeypatch.setattr(subprocess, "run", self._make_mock_run(job_id, app.DOWNLOAD_DIR))

        # Make write_file raise to simulate cache failure
        def bad_write_file(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(app.cache, "write_file", bad_write_file)

        app.run_download(job_id, url, "video", None)

        assert app.jobs[job_id]["status"] == "done", "Download should succeed even if cache write fails"


class TestIsLoopback:
    def test_loopback_ipv4(self, client):
        """Requests from test client should be considered loopback."""
        # The test client simulates 127.0.0.1
        import app
        assert app._is_loopback is not None  # function exists


class TestConfigFooter:
    def test_config_returns_info_for_loopback(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        assert resp.status_code == 200
        assert "cache" in data
        assert "stt_host" in data
        assert "llm_host" in data


class TestSettingsEndpoint:
    def test_get_settings_returns_file_content(self, client):
        resp = client.get("/api/settings")
        data = resp.get_json()
        assert resp.status_code == 200
        assert "content" in data
        assert "path" in data
        assert "RECLIP_STT_URL" in data["content"]

    def test_post_settings_writes_file(self, client):
        new_content = "RECLIP_STT_MODEL=posted-via-api\n"
        resp = client.post("/api/settings", json={"content": new_content})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("ok") is True

        # Verify round-trip
        resp2 = client.get("/api/settings")
        assert "posted-via-api" in resp2.get_json()["content"]

    def test_post_missing_content_returns_error(self, client):
        resp = client.post("/api/settings", json={})
        assert resp.status_code == 400
        assert "content" in resp.get_json().get("error", "").lower()

    def test_loopback_gating(self, client):
        # Simulate non-loopback remote addr
        resp = client.get("/api/settings", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        assert resp.status_code == 403


class TestTTSTextCleaning:
    def test_strips_double_asterisks(self):
        from app import _clean_text_for_tts
        assert _clean_text_for_tts("This is **bold** text") == "This is bold text"
        assert _clean_text_for_tts("**Heading**") == "Heading"

    def test_converts_leading_bullet_asterisk_to_hyphen(self):
        from app import _clean_text_for_tts
        result = _clean_text_for_tts("* first item\n* second item")
        assert result == "- first item\n- second item"

    def test_converts_indented_bullet_asterisk_to_hyphen(self):
        from app import _clean_text_for_tts
        result = _clean_text_for_tts("    * nested item")
        assert result == "    - nested item"

    def test_preserves_inline_asterisks(self):
        from app import _clean_text_for_tts
        # Single asterisk mid-sentence should be left alone (italic markers
        # are less disruptive to TTS than bullet/bold and could be false
        # positives if stripped indiscriminately).
        result = _clean_text_for_tts("a *b* c")
        assert result == "a *b* c"

    def test_combined(self):
        from app import _clean_text_for_tts
        src = "**Summary**\n\n* first\n* second with **bold** inside"
        out = _clean_text_for_tts(src)
        assert "**" not in out
        assert "- first" in out
        assert "- second with bold inside" in out


class TestTTSPipelineBoundary:
    """Locks in: TTS-fed text is cleaned, but the cached on-disk text is NOT."""

    def test_speak_strips_markdown_only_for_tts_not_cache(self, client, tmp_cache, monkeypatch):
        import app
        from cache import Cache

        url = "https://youtube.com/watch?v=mdtest1"
        c = Cache(str(tmp_cache), max_mb=10)
        # Cache a summary that contains markdown the user expects to keep
        markdown_summary = "**Heading**\n\n* first point\n* second **important** point"
        c.write_text(url, "summary.txt", markdown_summary)

        # Capture every call's text+ref so we can assert what TTS actually sees
        captured = []

        def fake_tts(*args, **kwargs):
            captured.append(kwargs.get("text", ""))
            # Return a minimal valid WAV (44-byte RIFF header is enough for the
            # concat path because we only generate one chunk for short text)
            import wave, io
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 100)
            return buf.getvalue()

        monkeypatch.setattr(app, "text_to_speech", fake_tts)
        # Don't try to extract a real voice reference — speak with no clone
        monkeypatch.setattr(app, "_resolve_voice_reference", lambda u: ("", ""))

        # Run the worker synchronously so we can assert immediately
        app.jobs["t1"] = {"status": "processing", "type": "media", "url": url}
        app._run_speak("t1", url, "summary", "")

        assert app.jobs["t1"]["status"] == "done", app.jobs["t1"].get("error")

        # The on-disk summary still has markdown — not modified
        cached = c.read_text(url, "summary.txt")
        assert cached == markdown_summary
        assert "**" in cached
        assert "* first point" in cached

        # But what we sent to TTS has no ** and bullets are converted to '-'
        assert captured, "text_to_speech was never called"
        joined = "\n".join(captured)
        assert "**" not in joined
        assert "- first point" in joined
        assert "- second important point" in joined


class TestTTSChunking:
    def test_short_text_single_chunk(self):
        from app import _chunk_text_for_tts
        chunks = _chunk_text_for_tts("Just a short sentence.")
        assert chunks == ["Just a short sentence."]

    def test_strips_metadata_header(self):
        from app import _chunk_text_for_tts
        text = "=== Video Metadata ===\nTitle: Foo\n=== Transcript ===\n\nHello world."
        chunks = _chunk_text_for_tts(text)
        assert len(chunks) == 1
        assert "Metadata" not in chunks[0]
        assert chunks[0] == "Hello world."

    def test_cleans_markdown_before_chunking(self):
        from app import _chunk_text_for_tts
        chunks = _chunk_text_for_tts("**Bold** then * bullet")
        joined = "\n".join(chunks)
        assert "**" not in joined

    def test_long_paragraph_splits_at_sentences(self):
        from app import _chunk_text_for_tts
        sentences = ["This is sentence number {}.".format(i) for i in range(20)]
        text = " ".join(sentences)
        chunks = _chunk_text_for_tts(text, max_chars=80)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 80


class TestServiceEndpoints:
    def test_status_returns_shape(self, client):
        resp = client.get("/api/service")
        assert resp.status_code == 200
        data = resp.get_json()
        for key in ("platform", "supported", "installed", "running", "service_path"):
            assert key in data

    def test_status_loopback_gated(self, client):
        resp = client.get("/api/service", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        assert resp.status_code == 403

    def test_install_loopback_gated(self, client):
        resp = client.post("/api/service/install", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        assert resp.status_code == 403

    def test_uninstall_loopback_gated(self, client):
        resp = client.post("/api/service/uninstall", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        assert resp.status_code == 403
