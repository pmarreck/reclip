"""Live end-to-end integration tests against a real running stack.

Hits real yt-dlp (downloads audio) and a real LLM/STT/TTS server (oMLX or
compatible). Skipped by default — opt in with:

    ./test --integration

Each test is keyed off a small set of env-controlled toggles so a partial
stack still gets useful coverage:

    RECLIP_INTEGRATION=1            enable the suite (required)
    RECLIP_INTEGRATION_URL          override test video (default: classic 19s clip)
    RECLIP_INTEGRATION_TIMEOUT      seconds to wait per step (default: 180)
    RECLIP_API_KEY                  passed through to STT/LLM/TTS

The default URL is the same one yt-transcriber uses: "Me at the zoo",
~19 seconds, public domain feel, very small download.
"""
import os
import shutil
import socket
import subprocess
import tempfile
from urllib.parse import urlparse

import pytest


INTEGRATION_URL = os.environ.get(
	"RECLIP_INTEGRATION_URL",
	"https://www.youtube.com/watch?v=jNQXAC9IVRw",
)
TIMEOUT = int(os.environ.get("RECLIP_INTEGRATION_TIMEOUT", "180"))


def _enabled():
	return os.environ.get("RECLIP_INTEGRATION", "").strip() not in ("", "0", "false", "False")


def _can_reach(url):
	try:
		parsed = urlparse(url)
		host = parsed.hostname or "localhost"
		port = parsed.port or (443 if parsed.scheme == "https" else 80)
		with socket.create_connection((host, port), timeout=2):
			return True
	except OSError:
		return False


def _have(cmd):
	return shutil.which(cmd) is not None


pytestmark = pytest.mark.skipif(
	not _enabled(),
	reason="set RECLIP_INTEGRATION=1 to enable live integration tests",
)


@pytest.fixture(scope="module")
def integration_cache(tmp_path_factory):
	"""Isolated cache dir per integration session so we don't pollute the user's."""
	d = tmp_path_factory.mktemp("reclip-integration")
	prev_cache = os.environ.get("RECLIP_CACHE_DIR")
	prev_config = os.environ.get("RECLIP_CONFIG_DIR")
	os.environ["RECLIP_CACHE_DIR"] = str(d)
	os.environ["RECLIP_CONFIG_DIR"] = str(tmp_path_factory.mktemp("reclip-integration-cfg"))
	yield d
	if prev_cache is not None:
		os.environ["RECLIP_CACHE_DIR"] = prev_cache
	else:
		os.environ.pop("RECLIP_CACHE_DIR", None)
	if prev_config is not None:
		os.environ["RECLIP_CONFIG_DIR"] = prev_config
	else:
		os.environ.pop("RECLIP_CONFIG_DIR", None)


@pytest.fixture(scope="module")
def cfg_and_cache(integration_cache):
	"""Fresh Config + Cache anchored to the temp dirs."""
	# Force-reimport so the singleton sees the new env vars.
	import importlib
	import config as config_mod
	import cache as cache_mod
	importlib.reload(config_mod)
	cfg = config_mod.load_config()
	cache_obj = cache_mod.Cache(cfg["cache_dir"], cfg["cache_max_mb"])
	return cfg, cache_obj


@pytest.fixture(scope="module")
def yt_dlp_available():
	if not _have("yt-dlp") or not _have("ffmpeg"):
		pytest.skip("yt-dlp and ffmpeg are required for download/transcribe tests")


@pytest.fixture(scope="module")
def stt_reachable(cfg_and_cache):
	cfg, _ = cfg_and_cache
	if not _can_reach(cfg["stt_url"]):
		pytest.skip(f"STT endpoint unreachable: {cfg['stt_url']}")


@pytest.fixture(scope="module")
def chat_reachable(cfg_and_cache):
	cfg, _ = cfg_and_cache
	if not _can_reach(cfg["summarize_url"]):
		pytest.skip(f"Chat endpoint unreachable: {cfg['summarize_url']}")


@pytest.fixture(scope="module")
def tts_reachable(cfg_and_cache):
	cfg, _ = cfg_and_cache
	if not _can_reach(cfg["tts_url"]):
		pytest.skip(f"TTS endpoint unreachable: {cfg['tts_url']}")


# --- Tests ---


def test_info(yt_dlp_available):
	"""yt-dlp can fetch metadata for the test video."""
	import json
	result = subprocess.run(
		["yt-dlp", "--no-playlist", "-j", INTEGRATION_URL],
		capture_output=True, text=True, timeout=TIMEOUT,
	)
	assert result.returncode == 0, f"yt-dlp failed: {result.stderr}"
	info = json.loads(result.stdout.strip().split("\n")[0])
	assert info.get("title")
	assert (info.get("duration") or 0) > 0


def test_audio_download_and_metadata(yt_dlp_available, cfg_and_cache):
	"""Audio downloads to cache, metadata is captured."""
	from app import _ensure_audio, _fetch_and_cache_metadata
	audio_path = _ensure_audio(INTEGRATION_URL)
	assert os.path.isfile(audio_path)
	assert os.path.getsize(audio_path) > 1000

	meta = _fetch_and_cache_metadata(INTEGRATION_URL)
	assert meta.get("title")
	assert meta.get("duration", 0) > 0


def test_transcribe(yt_dlp_available, stt_reachable, cfg_and_cache):
	"""STT endpoint returns non-empty transcript text for the test audio."""
	cfg, cache_obj = cfg_and_cache
	from app import _ensure_audio, _save_transcript
	from llm_client import transcribe as llm_transcribe

	audio_path = _ensure_audio(INTEGRATION_URL)
	result = llm_transcribe(
		audio_path=audio_path,
		url=cfg["stt_url"],
		model=cfg["stt_model"],
		api_key=cfg["stt_api_key"],
		prompt=cfg["stt_prompt"],
		api_key_hint="RECLIP_STT_API_KEY",
	)
	assert isinstance(result.get("text"), str)
	assert len(result["text"].strip()) > 0
	# Persist for the rest of the suite
	_save_transcript(INTEGRATION_URL, result["text"])
	cached = cache_obj.read_text(INTEGRATION_URL, "transcript.txt")
	assert "=== Video Metadata ===" in cached
	assert result["text"].strip() in cached


def test_summarize(stt_reachable, chat_reachable, cfg_and_cache):
	"""Summarize over the cached transcript returns non-empty output."""
	cfg, cache_obj = cfg_and_cache
	transcript = cache_obj.read_text(INTEGRATION_URL, "transcript.txt")
	if not transcript:
		pytest.skip("transcript not available; transcribe step likely failed")

	from llm_client import chat_completion
	summary = chat_completion(
		url=cfg["summarize_url"],
		model=cfg["summarize_model"],
		api_key=cfg["summarize_api_key"],
		system_prompt=cfg["summarize_prompt"],
		user_content=transcript,
		api_key_hint="RECLIP_SUMMARIZE_API_KEY",
	)
	assert isinstance(summary, str)
	assert len(summary.strip()) > 0
	cache_obj.write_text(INTEGRATION_URL, "summary.txt", summary)


def test_translate_transcript(stt_reachable, chat_reachable, cfg_and_cache):
	"""Translate transcript to Spanish — a different non-empty output."""
	cfg, cache_obj = cfg_and_cache
	transcript = cache_obj.read_text(INTEGRATION_URL, "transcript.txt")
	if not transcript:
		pytest.skip("transcript not available")

	from llm_client import chat_completion
	prompt = cfg["translate_prompt"].replace("{language}", "Spanish")
	translation = chat_completion(
		url=cfg["translate_url"],
		model=cfg["translate_model"],
		api_key=cfg["translate_api_key"],
		system_prompt=prompt,
		user_content=transcript,
		api_key_hint="RECLIP_TRANSLATE_API_KEY",
	)
	assert isinstance(translation, str)
	assert len(translation.strip()) > 0


def test_counterargue(stt_reachable, chat_reachable, cfg_and_cache):
	"""Counterargue over the transcript returns non-empty output."""
	cfg, cache_obj = cfg_and_cache
	transcript = cache_obj.read_text(INTEGRATION_URL, "transcript.txt")
	if not transcript:
		pytest.skip("transcript not available")

	from llm_client import chat_completion
	out = chat_completion(
		url=cfg["counterargue_url"],
		model=cfg["counterargue_model"],
		api_key=cfg["counterargue_api_key"],
		system_prompt=cfg["counterargue_prompt"],
		user_content=transcript,
		api_key_hint="RECLIP_COUNTERARGUE_API_KEY",
	)
	assert isinstance(out, str)
	assert len(out.strip()) > 0


def test_tts(tts_reachable, cfg_and_cache):
	"""TTS endpoint returns audio bytes for a short prompt."""
	cfg, _ = cfg_and_cache
	from llm_client import text_to_speech
	audio = text_to_speech(
		url=cfg["tts_url"],
		model=cfg["tts_model"],
		text="This is a short integration test.",
		api_key=cfg["tts_api_key"],
		voice=cfg["tts_voice"],
		api_key_hint="RECLIP_TTS_API_KEY",
	)
	assert isinstance(audio, (bytes, bytearray))
	# A plausible WAV/MP3/etc payload should be at least a few KB
	assert len(audio) > 1024
