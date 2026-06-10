import os
import time
import pytest


@pytest.fixture(autouse=True)
def isolated_config_dir(monkeypatch, tmp_path):
	"""Every test gets a fresh empty config dir so defaults apply cleanly."""
	cfg_dir = tmp_path / "reclip-config"
	monkeypatch.setenv("RECLIP_CONFIG_DIR", str(cfg_dir))
	return cfg_dir


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
	assert cfg["stt_model"] == "whisper-large-v3-fp16"
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


# --- New tests for file-based config and hot-reload ---


class TestIniParser:
	def test_simple_key_value(self):
		from config import _parse_file
		assert _parse_file("FOO=bar") == {"FOO": "bar"}

	def test_double_quoted_value(self):
		from config import _parse_file
		assert _parse_file('FOO="hello world"') == {"FOO": "hello world"}

	def test_single_quoted_value(self):
		from config import _parse_file
		assert _parse_file("FOO='hello world'") == {"FOO": "hello world"}

	def test_quoted_preserves_hash(self):
		from config import _parse_file
		assert _parse_file('FOO="a # b"') == {"FOO": "a # b"}

	def test_leading_comment_skipped(self):
		from config import _parse_file
		assert _parse_file("# this is a comment\nFOO=bar") == {"FOO": "bar"}

	def test_trailing_comment_stripped(self):
		from config import _parse_file
		assert _parse_file("FOO=bar # trailing comment") == {"FOO": "bar"}

	def test_blank_lines_skipped(self):
		from config import _parse_file
		assert _parse_file("\n\nFOO=bar\n\n") == {"FOO": "bar"}

	def test_interpolation_from_env(self, monkeypatch):
		from config import _parse_file
		monkeypatch.setenv("SOME_VAR", "interpolated")
		assert _parse_file("FOO=${SOME_VAR}") == {"FOO": "interpolated"}

	def test_interpolation_with_default(self, monkeypatch):
		from config import _parse_file
		monkeypatch.delenv("SOME_VAR", raising=False)
		assert _parse_file("FOO=${SOME_VAR:-defaultval}") == {"FOO": "defaultval"}

	def test_interpolation_env_overrides_default(self, monkeypatch):
		from config import _parse_file
		monkeypatch.setenv("SOME_VAR", "envval")
		assert _parse_file("FOO=${SOME_VAR:-defaultval}") == {"FOO": "envval"}

	def test_nested_interpolation(self, monkeypatch):
		from config import _parse_file
		monkeypatch.delenv("OUTER", raising=False)
		monkeypatch.setenv("INNER", "innerval")
		# ${OUTER:-${INNER}} → OUTER unset, falls back to INNER
		assert _parse_file("FOO=${OUTER:-${INNER}}") == {"FOO": "innerval"}

	def test_empty_env_uses_default(self, monkeypatch):
		from config import _parse_file
		monkeypatch.setenv("SOME_VAR", "")
		assert _parse_file("FOO=${SOME_VAR:-defaultval}") == {"FOO": "defaultval"}


class TestConfigFile:
	def test_first_run_creates_file(self, isolated_config_dir):
		from config import Config
		cfg = Config()
		assert os.path.isfile(cfg.config_path)
		assert cfg.config_path == str(isolated_config_dir / "config.ini")

	def test_first_run_uses_defaults(self, isolated_config_dir):
		from config import Config
		cfg = Config()
		assert cfg["stt_model"] == "whisper-large-v3-fp16"

	def test_file_override_takes_precedence(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_MODEL", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "config.ini").write_text("RECLIP_STT_MODEL=my-custom-model\n")
		from config import Config
		cfg = Config()
		assert cfg["stt_model"] == "my-custom-model"


class TestHotReload:
	def test_reload_picks_up_file_change(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_MODEL", raising=False)
		from config import Config
		cfg = Config()
		assert cfg["stt_model"] == "whisper-large-v3-fp16"

		# Write a new value and bump mtime far past the throttle window
		(isolated_config_dir / "config.ini").write_text("RECLIP_STT_MODEL=updated-model\n")
		# Force the "last check" to be in the past so maybe_reload actually checks
		cfg._last_check = 0
		# Ensure mtime differs (filesystem resolution)
		new_mtime = time.time() + 10
		os.utime(str(isolated_config_dir / "config.ini"), (new_mtime, new_mtime))
		cfg.maybe_reload()
		assert cfg["stt_model"] == "updated-model"

	def test_reload_throttled(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_MODEL", raising=False)
		from config import Config
		cfg = Config()
		cfg._last_check = time.time()  # just checked
		(isolated_config_dir / "config.ini").write_text("RECLIP_STT_MODEL=should-not-load\n")
		cfg.maybe_reload()  # should be skipped
		assert cfg["stt_model"] == "whisper-large-v3-fp16"

	def test_write_file_reloads_immediately(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_MODEL", raising=False)
		from config import Config
		cfg = Config()
		cfg.write_file("RECLIP_STT_MODEL=written-via-api\n")
		assert cfg["stt_model"] == "written-via-api"


class TestConfigDirOverride:
	def test_reclip_config_dir_respected(self, monkeypatch, tmp_path):
		custom = tmp_path / "custom-config"
		monkeypatch.setenv("RECLIP_CONFIG_DIR", str(custom))
		from config import Config
		cfg = Config()
		assert cfg.config_dir == str(custom)
		assert os.path.isfile(str(custom / "config.ini"))


class TestSecretsFile:
	"""secrets.ini lives next to config.ini and supplies values that interpolation
	in config.ini can reference, plus serves as a fallback for keys not in
	config.ini. Designed so a launchd/systemd-user service that doesn't inherit
	the interactive shell env can still pick up API keys from a file.

	Precedence (highest wins): config.ini literal > real os.environ > secrets.ini
	> default. (config.ini wins over env to match today's behavior; secrets fills
	in below env so an ad-hoc shell override still takes effect.)
	"""

	def test_missing_secrets_file_is_ok(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_API_KEY", raising=False)
		monkeypatch.delenv("RECLIP_API_KEY", raising=False)
		from config import Config
		cfg = Config()
		assert cfg["stt_api_key"] == ""

	def test_secrets_feeds_interpolation_when_env_unset(self, isolated_config_dir, monkeypatch):
		"""config.ini interpolates ${RECLIP_API_KEY}; env is empty; secrets.ini
		supplies the value → it gets interpolated in."""
		monkeypatch.delenv("RECLIP_API_KEY", raising=False)
		monkeypatch.delenv("RECLIP_STT_API_KEY", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "secrets.ini").write_text("RECLIP_API_KEY=sk-from-secrets\n")
		(isolated_config_dir / "config.ini").write_text(
			"RECLIP_STT_API_KEY=${RECLIP_STT_API_KEY:-${RECLIP_API_KEY}}\n"
		)
		from config import Config
		cfg = Config()
		assert cfg["stt_api_key"] == "sk-from-secrets"

	def test_real_env_beats_secrets_in_interpolation(self, isolated_config_dir, monkeypatch):
		monkeypatch.setenv("RECLIP_API_KEY", "sk-from-env")
		monkeypatch.delenv("RECLIP_STT_API_KEY", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "secrets.ini").write_text("RECLIP_API_KEY=sk-from-secrets\n")
		(isolated_config_dir / "config.ini").write_text(
			"RECLIP_STT_API_KEY=${RECLIP_STT_API_KEY:-${RECLIP_API_KEY}}\n"
		)
		from config import Config
		cfg = Config()
		assert cfg["stt_api_key"] == "sk-from-env"

	def test_secrets_surfaces_when_config_ini_omits_key(self, isolated_config_dir, monkeypatch):
		"""No literal in config.ini; secrets.ini has the key → cfg returns it."""
		monkeypatch.delenv("RECLIP_STT_API_KEY", raising=False)
		monkeypatch.delenv("RECLIP_API_KEY", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "secrets.ini").write_text("RECLIP_STT_API_KEY=sk-direct\n")
		from config import Config
		cfg = Config()
		assert cfg["stt_api_key"] == "sk-direct"

	def test_config_ini_literal_beats_secrets(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_MODEL", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "secrets.ini").write_text("RECLIP_STT_MODEL=secret-model\n")
		(isolated_config_dir / "config.ini").write_text("RECLIP_STT_MODEL=config-model\n")
		from config import Config
		cfg = Config()
		assert cfg["stt_model"] == "config-model"

	def test_secrets_hot_reloads(self, isolated_config_dir, monkeypatch):
		monkeypatch.delenv("RECLIP_STT_API_KEY", raising=False)
		monkeypatch.delenv("RECLIP_API_KEY", raising=False)
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "secrets.ini").write_text("RECLIP_STT_API_KEY=sk-v1\n")
		from config import Config
		cfg = Config()
		assert cfg["stt_api_key"] == "sk-v1"

		(isolated_config_dir / "secrets.ini").write_text("RECLIP_STT_API_KEY=sk-v2\n")
		cfg._last_check = 0
		new_mtime = time.time() + 10
		os.utime(str(isolated_config_dir / "secrets.ini"), (new_mtime, new_mtime))
		cfg.maybe_reload()
		assert cfg["stt_api_key"] == "sk-v2"
