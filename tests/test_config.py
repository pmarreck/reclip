import os
import pytest


def test_default_cache_dir_uses_xdg(monkeypatch, tmp_path):
	monkeypatch.delenv("RECLIP_CACHE_DIR", raising=False)
	monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
	from config import load_config
	cfg = load_config()
	assert cfg["cache_dir"] == str(tmp_path / "reclip")


def test_default_cache_dir_falls_back_to_home(monkeypatch, tmp_path):
	monkeypatch.delenv("RECLIP_CACHE_DIR", raising=False)
	monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
	monkeypatch.setenv("HOME", str(tmp_path))
	from config import load_config
	cfg = load_config()
	assert cfg["cache_dir"] == str(tmp_path / ".cache" / "reclip")


def test_explicit_cache_dir(monkeypatch, tmp_path):
	monkeypatch.setenv("RECLIP_CACHE_DIR", str(tmp_path / "custom"))
	from config import load_config
	cfg = load_config()
	assert cfg["cache_dir"] == str(tmp_path / "custom")


def test_default_cache_max_mb():
	from config import load_config
	cfg = load_config()
	assert cfg["cache_max_mb"] == 1024


def test_explicit_cache_max_mb(monkeypatch):
	monkeypatch.setenv("RECLIP_CACHE_MAX_MB", "2048")
	from config import load_config
	cfg = load_config()
	assert cfg["cache_max_mb"] == 2048


def test_stt_defaults():
	from config import load_config
	cfg = load_config()
	assert cfg["stt_url"] == "http://localhost:8000/v1/audio/transcriptions"
	assert cfg["stt_api_key"] == ""
	assert cfg["stt_model"] == "mlx-community/whisper-large-v3-turbo"
	assert cfg["stt_prompt"] == ""


def test_summarize_defaults():
	from config import load_config
	cfg = load_config()
	assert cfg["summarize_url"] == "http://localhost:8000/v1/chat/completions"
	assert cfg["summarize_model"] == "gemma4-heretical-mlx-8bit"
	assert "summarize" in cfg["summarize_prompt"].lower() or "summary" in cfg["summarize_prompt"].lower()


def test_translate_defaults():
	from config import load_config
	cfg = load_config()
	assert cfg["translate_url"] == "http://localhost:8000/v1/chat/completions"
	assert cfg["translate_model"] == "gemma4-heretical-mlx-8bit"
	assert "{language}" in cfg["translate_prompt"]


def test_env_overrides_all(monkeypatch):
	monkeypatch.setenv("RECLIP_STT_URL", "http://myserver:9999/v1/audio/transcriptions")
	monkeypatch.setenv("RECLIP_STT_API_KEY", "sk-test")
	monkeypatch.setenv("RECLIP_SUMMARIZE_PROMPT", "Custom summary prompt")
	from config import load_config
	cfg = load_config()
	assert cfg["stt_url"] == "http://myserver:9999/v1/audio/transcriptions"
	assert cfg["stt_api_key"] == "sk-test"
	assert cfg["summarize_prompt"] == "Custom summary prompt"
