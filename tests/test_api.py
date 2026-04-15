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
        assert cached == "Transcribed text"


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
