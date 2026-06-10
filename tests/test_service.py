"""Tests for service file generation. Does not actually install."""
import os
import sys
import pytest


class TestPlatformDetection:
	def test_detect_platform_returns_string(self):
		from service import detect_platform
		assert detect_platform() in ("darwin", "linux", "unsupported")


class TestServiceModeDetection:
	"""is_running_as_service() distinguishes a launchd/systemd-spawned process
	from a user-launched foreground `python app.py`. The launchd plist /
	systemd unit sets RECLIP_RUNNING_AS_SERVICE=1; the user's foreground
	shell does not."""

	def test_returns_false_when_env_unset(self, monkeypatch):
		monkeypatch.delenv("RECLIP_RUNNING_AS_SERVICE", raising=False)
		from service import is_running_as_service
		assert is_running_as_service() is False

	def test_returns_true_when_env_set_to_one(self, monkeypatch):
		monkeypatch.setenv("RECLIP_RUNNING_AS_SERVICE", "1")
		from service import is_running_as_service
		assert is_running_as_service() is True

	def test_captured_env_injects_service_marker(self, monkeypatch):
		"""So when the service spawns the child, the child sees the marker."""
		monkeypatch.delenv("RECLIP_RUNNING_AS_SERVICE", raising=False)
		from service import _captured_env
		env = _captured_env()
		assert env.get("RECLIP_RUNNING_AS_SERVICE") == "1"


class TestCapturedEnv:
	def test_captures_reclip_vars(self, monkeypatch):
		monkeypatch.setenv("RECLIP_API_KEY", "sk-test-123")
		monkeypatch.setenv("RECLIP_STT_MODEL", "custom-model")
		from service import _captured_env
		env = _captured_env()
		assert env["RECLIP_API_KEY"] == "sk-test-123"
		assert env["RECLIP_STT_MODEL"] == "custom-model"

	def test_captures_path(self, monkeypatch):
		monkeypatch.setenv("PATH", "/nix/store/foo:/usr/bin")
		from service import _captured_env
		env = _captured_env()
		assert env["PATH"] == "/nix/store/foo:/usr/bin"

	def test_excludes_unrelated_vars(self, monkeypatch):
		monkeypatch.setenv("SOMETHING_UNRELATED", "should-not-leak")
		from service import _captured_env
		env = _captured_env()
		assert "SOMETHING_UNRELATED" not in env

	def test_defaults_host_to_loopback(self, monkeypatch):
		monkeypatch.delenv("HOST", raising=False)
		from service import _captured_env
		env = _captured_env()
		assert env.get("HOST") == "127.0.0.1"


class TestPlistGeneration:
	def test_plist_contains_label(self):
		from service import build_plist, SERVICE_LABEL
		plist = build_plist(
			python_path="/nix/store/foo/bin/python",
			app_py="/home/u/reclip/app.py",
			project_dir="/home/u/reclip",
			env={"RECLIP_API_KEY": "sk-test"},
			log_dir="/tmp/reclip-logs",
		)
		assert SERVICE_LABEL in plist
		assert "ProgramArguments" in plist
		assert "/nix/store/foo/bin/python" in plist
		assert "/home/u/reclip/app.py" in plist

	def test_plist_includes_env_vars(self):
		from service import build_plist
		plist = build_plist(
			python_path="/bin/python",
			app_py="/app.py",
			project_dir="/",
			env={"RECLIP_API_KEY": "sk-mykey", "PATH": "/usr/bin"},
			log_dir="/tmp",
		)
		assert "<key>RECLIP_API_KEY</key><string>sk-mykey</string>" in plist
		assert "<key>PATH</key><string>/usr/bin</string>" in plist

	def test_plist_escapes_xml_special_chars(self):
		from service import build_plist
		plist = build_plist(
			python_path="/bin/python",
			app_py="/app.py",
			project_dir="/",
			env={"RECLIP_API_KEY": "key<with>&special\"chars'"},
			log_dir="/tmp",
		)
		# Raw special chars must not appear in the value
		assert ">key<with>" not in plist
		# They should be escaped
		assert "&lt;with&gt;" in plist or "&#60;with&#62;" in plist
		assert "&amp;special" in plist

	def test_plist_has_loopback_keep_alive(self):
		from service import build_plist
		plist = build_plist(
			python_path="/bin/python", app_py="/app.py", project_dir="/",
			env={}, log_dir="/tmp",
		)
		assert "<key>KeepAlive</key>" in plist
		assert "<key>RunAtLoad</key>" in plist


class TestSystemdUnitGeneration:
	def test_unit_has_required_sections(self):
		from service import build_systemd_unit
		unit = build_systemd_unit(
			python_path="/nix/store/foo/bin/python",
			app_py="/home/u/reclip/app.py",
			project_dir="/home/u/reclip",
			env={"RECLIP_API_KEY": "sk-test"},
		)
		assert "[Unit]" in unit
		assert "[Service]" in unit
		assert "[Install]" in unit

	def test_unit_execstart(self):
		from service import build_systemd_unit
		unit = build_systemd_unit(
			python_path="/bin/python", app_py="/app.py", project_dir="/",
			env={},
		)
		assert "ExecStart=/bin/python /app.py" in unit
		assert "WorkingDirectory=/" in unit

	def test_unit_environment_lines(self):
		from service import build_systemd_unit
		unit = build_systemd_unit(
			python_path="/bin/python", app_py="/app.py", project_dir="/",
			env={"RECLIP_API_KEY": "sk-test", "PATH": "/usr/bin"},
		)
		assert 'Environment=RECLIP_API_KEY="sk-test"' in unit
		assert 'Environment=PATH="/usr/bin"' in unit

	def test_unit_escapes_quotes_in_values(self):
		from service import build_systemd_unit
		unit = build_systemd_unit(
			python_path="/bin/python", app_py="/app.py", project_dir="/",
			env={"WEIRD": 'has"quote'},
		)
		assert 'Environment=WEIRD="has\\"quote"' in unit

	def test_unit_restart_on_failure(self):
		from service import build_systemd_unit
		unit = build_systemd_unit(
			python_path="/bin/python", app_py="/app.py", project_dir="/",
			env={},
		)
		assert "Restart=on-failure" in unit


class TestServiceManager:
	def test_manager_default_paths_darwin(self, monkeypatch):
		monkeypatch.setattr(sys, "platform", "darwin")
		from service import ServiceManager, SERVICE_LABEL
		mgr = ServiceManager(project_dir="/home/u/reclip", python_path="/bin/python")
		assert mgr.platform == "darwin"
		assert mgr.supported() is True
		assert mgr.service_path.endswith(f"Library/LaunchAgents/{SERVICE_LABEL}.plist")

	def test_manager_default_paths_linux(self, monkeypatch):
		monkeypatch.setattr(sys, "platform", "linux")
		monkeypatch.setenv("XDG_CONFIG_HOME", "/home/u/.config")
		from service import ServiceManager, LINUX_UNIT_NAME
		mgr = ServiceManager(project_dir="/home/u/reclip", python_path="/bin/python")
		assert mgr.platform == "linux"
		assert mgr.supported() is True
		assert mgr.service_path == f"/home/u/.config/systemd/user/{LINUX_UNIT_NAME}"

	def test_manager_unsupported(self, monkeypatch):
		monkeypatch.setattr(sys, "platform", "win32")
		from service import ServiceManager
		mgr = ServiceManager()
		assert mgr.platform == "unsupported"
		assert mgr.supported() is False
		assert mgr.service_path is None

	def test_install_unsupported_raises(self, monkeypatch):
		monkeypatch.setattr(sys, "platform", "win32")
		from service import ServiceManager
		mgr = ServiceManager()
		with pytest.raises(RuntimeError, match="not supported"):
			mgr.install()

	def test_render_darwin_produces_plist(self, monkeypatch, tmp_path):
		monkeypatch.setattr(sys, "platform", "darwin")
		from service import ServiceManager
		mgr = ServiceManager(
			project_dir=str(tmp_path),
			python_path="/bin/python",
			env={"RECLIP_API_KEY": "sk-test"},
		)
		mgr.log_dir = str(tmp_path / "logs")  # avoid writing to ~/Library
		content = mgr._render()
		assert "<?xml version=" in content
		assert "<plist" in content
		assert "sk-test" in content

	def test_render_linux_produces_unit(self, monkeypatch, tmp_path):
		monkeypatch.setattr(sys, "platform", "linux")
		from service import ServiceManager
		mgr = ServiceManager(
			project_dir=str(tmp_path),
			python_path="/bin/python",
			env={"RECLIP_API_KEY": "sk-test"},
		)
		content = mgr._render()
		assert "[Unit]" in content
		assert 'Environment=RECLIP_API_KEY="sk-test"' in content

	def test_status_shape(self, monkeypatch):
		# Don't actually run launchctl/systemctl
		monkeypatch.setattr(sys, "platform", "darwin")
		import subprocess as sp
		def _mock_run(*a, **kw):
			class R:
				stdout = ""
				stderr = ""
				returncode = 0
			return R()
		monkeypatch.setattr(sp, "run", _mock_run)
		from service import ServiceManager
		mgr = ServiceManager(project_dir="/tmp/x", python_path="/bin/python")
		status = mgr.status()
		assert set(status.keys()) == {
			"platform", "supported", "installed", "running",
			"service_path", "is_running_as_service",
		}


class TestOrtDylibPathCaptured:
	def test_ort_dylib_path_in_captured_env(self, monkeypatch):
		"""Diarization's cpu/cuda modes dlopen ONNX Runtime via ORT_DYLIB_PATH;
		the service file must carry it since launchd has no shell env."""
		from service import _captured_env
		monkeypatch.setenv("ORT_DYLIB_PATH", "/nix/store/x/libonnxruntime.dylib")
		env = _captured_env()
		assert env.get("ORT_DYLIB_PATH") == "/nix/store/x/libonnxruntime.dylib"
