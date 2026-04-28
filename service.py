"""Install/uninstall ReClip+ as a user-level system service.

macOS: launchd user agent in ~/Library/LaunchAgents/
Linux: systemd user unit in ~/.config/systemd/user/
"""
import os
import re
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape as xml_escape


SERVICE_LABEL = "com.reclip.server"
LINUX_UNIT_NAME = "reclip.service"

# Only these env vars get baked into the service file.
# PATH is included so the service can find ffmpeg and yt-dlp from nix store.
ENV_ALLOWLIST_PREFIXES = ("RECLIP_",)
ENV_ALLOWLIST_EXACT = ("PATH", "HOME", "LANG", "LC_ALL", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")


def detect_platform():
	if sys.platform == "darwin":
		return "darwin"
	if sys.platform.startswith("linux"):
		return "linux"
	return "unsupported"


def is_running_as_service():
	"""True when this process was spawned by launchd/systemd via the unit
	we generate. Used so the UI can show 'Restart server' (under a supervisor
	that will respawn) vs 'Stop server' (foreground, manual relaunch needed).
	The marker env var is set unconditionally in _captured_env() so it
	always appears in the service file we render.
	"""
	return os.environ.get("RECLIP_RUNNING_AS_SERVICE") == "1"


def _captured_env():
	"""Snapshot relevant env vars from the current process for the service."""
	env = {}
	for k, v in os.environ.items():
		if k in ENV_ALLOWLIST_EXACT or any(k.startswith(p) for p in ENV_ALLOWLIST_PREFIXES):
			env[k] = v
	# Ensure HOST defaults to loopback for safety
	env.setdefault("HOST", "127.0.0.1")
	# Marker so the spawned process can tell it's running under a supervisor.
	env["RECLIP_RUNNING_AS_SERVICE"] = "1"
	return env


def _plist_xml_escape(s):
	"""Escape a string for safe inclusion in plist XML."""
	return xml_escape(s, {'"': "&quot;", "'": "&apos;"})


def _systemd_escape(s):
	r"""Escape a value for a systemd Environment= line.

	Systemd uses shell-like parsing: wrap in double quotes and escape " and \ inside.
	"""
	return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_plist(python_path, app_py, project_dir, env, log_dir):
	"""Generate macOS launchd plist content."""
	env_items = "".join(
		f"        <key>{_plist_xml_escape(k)}</key><string>{_plist_xml_escape(v)}</string>\n"
		for k, v in sorted(env.items())
	)
	return (
		'<?xml version="1.0" encoding="UTF-8"?>\n'
		'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
		'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
		'<plist version="1.0">\n'
		'<dict>\n'
		f'    <key>Label</key><string>{SERVICE_LABEL}</string>\n'
		'    <key>ProgramArguments</key>\n'
		'    <array>\n'
		f'        <string>{_plist_xml_escape(python_path)}</string>\n'
		f'        <string>{_plist_xml_escape(app_py)}</string>\n'
		'    </array>\n'
		f'    <key>WorkingDirectory</key><string>{_plist_xml_escape(project_dir)}</string>\n'
		'    <key>EnvironmentVariables</key>\n'
		'    <dict>\n'
		f'{env_items}'
		'    </dict>\n'
		'    <key>RunAtLoad</key><true/>\n'
		'    <key>KeepAlive</key><true/>\n'
		'    <key>ProcessType</key><string>Background</string>\n'
		f'    <key>StandardOutPath</key><string>{_plist_xml_escape(os.path.join(log_dir, "reclip.log"))}</string>\n'
		f'    <key>StandardErrorPath</key><string>{_plist_xml_escape(os.path.join(log_dir, "reclip.err.log"))}</string>\n'
		'</dict>\n'
		'</plist>\n'
	)


def build_systemd_unit(python_path, app_py, project_dir, env):
	"""Generate systemd user unit file content."""
	env_lines = "\n".join(
		f"Environment={k}={_systemd_escape(v)}" for k, v in sorted(env.items())
	)
	return (
		"[Unit]\n"
		"Description=ReClip+ self-hosted media downloader and AI assistant\n"
		"After=network.target\n"
		"\n"
		"[Service]\n"
		"Type=simple\n"
		f"WorkingDirectory={project_dir}\n"
		f"ExecStart={python_path} {app_py}\n"
		f"{env_lines}\n"
		"Restart=on-failure\n"
		"RestartSec=5\n"
		"StandardOutput=journal\n"
		"StandardError=journal\n"
		"\n"
		"[Install]\n"
		"WantedBy=default.target\n"
	)


class ServiceManager:
	"""Install/uninstall ReClip+ as a user-level service on macOS or Linux."""

	def __init__(self, project_dir=None, python_path=None, env=None):
		self.platform = detect_platform()
		self.project_dir = project_dir or os.path.dirname(os.path.abspath(__file__))
		self.python_path = python_path or sys.executable
		self.app_py = os.path.join(self.project_dir, "app.py")
		self.env = env if env is not None else _captured_env()

		if self.platform == "darwin":
			self.service_path = os.path.expanduser(
				f"~/Library/LaunchAgents/{SERVICE_LABEL}.plist"
			)
			self.log_dir = os.path.expanduser("~/Library/Logs/reclip")
		elif self.platform == "linux":
			xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
			self.service_path = os.path.join(xdg, "systemd", "user", LINUX_UNIT_NAME)
			self.log_dir = None  # systemd journals
		else:
			self.service_path = None
			self.log_dir = None

	def supported(self):
		return self.platform in ("darwin", "linux")

	def _render(self):
		if self.platform == "darwin":
			os.makedirs(self.log_dir, exist_ok=True)
			return build_plist(
				self.python_path, self.app_py, self.project_dir, self.env, self.log_dir
			)
		if self.platform == "linux":
			return build_systemd_unit(
				self.python_path, self.app_py, self.project_dir, self.env
			)
		raise RuntimeError(f"Unsupported platform: {self.platform}")

	def status(self):
		"""Return dict: platform, supported, installed, running, service_path,
		is_running_as_service."""
		info = {
			"platform": self.platform,
			"supported": self.supported(),
			"installed": False,
			"running": False,
			"service_path": self.service_path,
			"is_running_as_service": is_running_as_service(),
		}
		if not self.supported() or not self.service_path:
			return info

		info["installed"] = os.path.isfile(self.service_path)

		if self.platform == "darwin":
			try:
				result = subprocess.run(
					["launchctl", "list"], capture_output=True, text=True, timeout=10
				)
				info["running"] = SERVICE_LABEL in result.stdout
			except (subprocess.TimeoutExpired, FileNotFoundError):
				pass
		elif self.platform == "linux":
			try:
				result = subprocess.run(
					["systemctl", "--user", "is-active", LINUX_UNIT_NAME],
					capture_output=True, text=True, timeout=10,
				)
				info["running"] = result.stdout.strip() == "active"
			except (subprocess.TimeoutExpired, FileNotFoundError):
				pass

		return info

	def install(self):
		"""Write service file and start the service. Idempotent (replaces existing)."""
		if not self.supported():
			raise RuntimeError(f"Service management not supported on {self.platform}")

		# Stop existing first so we don't have two processes fighting for the port
		if os.path.isfile(self.service_path):
			try:
				self.uninstall()
			except Exception:
				pass

		content = self._render()
		os.makedirs(os.path.dirname(self.service_path), exist_ok=True)
		with open(self.service_path, "w", encoding="utf-8") as f:
			f.write(content)

		if self.platform == "darwin":
			# Use bootstrap which is the modern replacement for load
			uid = os.getuid()
			subprocess.run(
				["launchctl", "bootstrap", f"gui/{uid}", self.service_path],
				capture_output=True, text=True, check=True, timeout=30,
			)
		elif self.platform == "linux":
			subprocess.run(
				["systemctl", "--user", "daemon-reload"],
				capture_output=True, text=True, timeout=10,
			)
			subprocess.run(
				["systemctl", "--user", "enable", "--now", LINUX_UNIT_NAME],
				capture_output=True, text=True, check=True, timeout=30,
			)

	def uninstall(self):
		"""Stop service and remove service file. Idempotent."""
		if not self.supported():
			return

		if self.platform == "darwin":
			uid = os.getuid()
			subprocess.run(
				["launchctl", "bootout", f"gui/{uid}/{SERVICE_LABEL}"],
				capture_output=True, text=True, timeout=30,
			)
		elif self.platform == "linux":
			subprocess.run(
				["systemctl", "--user", "disable", "--now", LINUX_UNIT_NAME],
				capture_output=True, text=True, timeout=30,
			)
			subprocess.run(
				["systemctl", "--user", "daemon-reload"],
				capture_output=True, text=True, timeout=10,
			)

		if self.service_path and os.path.isfile(self.service_path):
			os.remove(self.service_path)
