"""ReClip+ configuration loader with shell-style INI file and hot-reload.

Values are loaded from $RECLIP_CONFIG_DIR/config.ini (or XDG default),
which uses bash-style KEY=VALUE syntax with ${VAR} and ${VAR:-default}
interpolation. The file is reloaded on-demand when its mtime changes,
throttled to once per 5 seconds.
"""
import os
import re
import threading
import time


DEFAULT_SUMMARIZE_PROMPT = (
	"Please summarize the most pertinent elements of the following transcript "
	"or narrative. If it (or any part of it) presents a list of things "
	"(questions, points, tasks, steps, sequential events, etc.), please list "
	"those out without collapsing them further. If there is an issue with the "
	"content (such as it appearing to be missing), mention that prefixed with "
	"'Problem: '. Don't comment on the summary itself. If there is a metadata "
	"section, output it verbatim at the top of the summary."
)

DEFAULT_TRANSLATE_PROMPT = (
	"You are an expert translator. Please translate the following into "
	"{language}. For idioms, words, or expressions that do not translate "
	"perfectly: (1) Make your best translation attempt (2) Add footnotes with "
	"explanations in both the source and target languages. Do not output "
	"anything but the translation and footnotes. If there is an unresolvable "
	"issue, mention it prefixed with 'Problem: ' in both languages. Preserve "
	"all formatting."
)

DEFAULT_COUNTERARGUE_PROMPT = (
	"You are a critical thinker and skilled debater. Analyze the following "
	"transcript and identify claims, arguments, or assertions that can be "
	"challenged. For each counterarguable point: (1) State the original claim "
	"(2) Present the strongest counterargument with evidence or reasoning "
	"(3) Note the strength of the counterargument (strong, moderate, weak). "
	"Be fair and intellectually honest — distinguish between factual errors, "
	"logical fallacies, unsupported claims, and matters of legitimate debate. "
	"If the content is purely factual reporting, a tutorial, music, or "
	"otherwise not appropriate to counterargue, state that clearly and explain "
	"why. Do not manufacture controversy where none exists."
)


DEFAULT_CONFIG_CONTENT = """# ReClip+ configuration
# Syntax: KEY=value. Quotes (' or ") optional. Lines starting with # are comments,
# and trailing " # comment" is also stripped (unless inside quotes).
# Bash-style interpolation: ${VAR} expands from environment, ${VAR:-default}
# falls back to default when VAR is unset or empty. Nesting is supported, e.g.
# ${SPECIFIC_KEY:-${COMMON_KEY}}.

# --- Server bind (requires restart to take effect) ---
# RECLIP_HOST controls which interface the web UI binds to.
#   127.0.0.1 (default): loopback only — safest, only accessible from this machine
#   0.0.0.0:             all interfaces — reachable from LAN, Tailscale, anything
#                        that can route to this machine. Settings UI stays
#                        loopback-gated, but transcription/summarization/TTS
#                        endpoints become reachable. Only enable on trusted
#                        networks (e.g. Tailscale-only LAN).
#   <specific IP>:       bind to one interface (e.g. your Tailscale IP from
#                        `tailscale ip -4`) to expose only over that network.
RECLIP_HOST=${RECLIP_HOST:-127.0.0.1}
RECLIP_PORT=${RECLIP_PORT:-8899}

# --- Cache (requires restart to take effect) ---
RECLIP_CACHE_DIR=${RECLIP_CACHE_DIR}
RECLIP_CACHE_MAX_MB=${RECLIP_CACHE_MAX_MB:-1024}

# --- Image scrapers (gallery-dl auth) ---
# Many image hosts (Instagram, Threads, Reddit, X/Twitter) reject anonymous
# requests and bounce them to a login page. gallery-dl can either:
#   a) read a cookies.txt (Netscape format) — set RECLIP_GALLERY_DL_COOKIES
#   b) extract cookies live from a browser profile — set RECLIP_GALLERY_DL_BROWSER
#      to one of: firefox, chromium, chrome, safari, edge, vivaldi, opera, brave
# (a) wins if both are set. The default below picks a sensible browser per OS.
# SECURITY: when RECLIP_HOST is non-loopback (e.g. 0.0.0.0 for Tailscale),
# anyone who can reach this server can trigger fetches that ride your session
# cookies to scrape on your behalf. They cannot read the cookies, but they
# can ride them. Set RECLIP_GALLERY_DL_BROWSER="" to disable when exposed.
RECLIP_GALLERY_DL_COOKIES=${RECLIP_GALLERY_DL_COOKIES}
RECLIP_GALLERY_DL_BROWSER=${RECLIP_GALLERY_DL_BROWSER}

# --- Common API key: used as fallback for all services below ---
RECLIP_API_KEY=${RECLIP_API_KEY}

# --- Speech-to-text (transcription) ---
# whisper-large-v3-fp16 is the recommended default for oMLX Server. Other
# tested options:
#   whisper-large-v3-fp16          -- ~3GB, full precision, HF processor included
#   whisper-large-v3-8bit          -- ~860MB, quantized but HF processor included
#   whisper-large-v3-turbo-8bit    -- AVOID with current oMLX (0.3.8.dev1+):
#                                     missing/unrecognized HuggingFace processor
#                                     files cause "Processor not found" errors
#                                     even after manually adding the upstream
#                                     preprocessor_config.json/tokenizer.json.
#   whisper-large-v3-mlx (.npz)    -- AVOID: oMLX needs safetensors, not .npz
RECLIP_STT_URL=${RECLIP_STT_URL:-http://localhost:8000/v1/audio/transcriptions}
RECLIP_STT_API_KEY=${RECLIP_STT_API_KEY:-${RECLIP_API_KEY}}
RECLIP_STT_MODEL=${RECLIP_STT_MODEL:-whisper-large-v3-fp16}
RECLIP_STT_PROMPT=${RECLIP_STT_PROMPT}
# Word-level timestamps (oMLX extension, Whisper models only): enables
# word-granularity speaker attribution in diarization. Default true.
RECLIP_STT_WORD_TIMESTAMPS=${RECLIP_STT_WORD_TIMESTAMPS:-true}
# When RECLIP_STT_PROMPT is unset, bias Whisper with the video's own
# title/uploader/description so proper nouns transcribe correctly
# (e.g. guest names from the description). Default true.
RECLIP_STT_METADATA_PROMPT=${RECLIP_STT_METADATA_PROMPT:-true}

# --- Summarization ---
RECLIP_SUMMARIZE_URL=${RECLIP_SUMMARIZE_URL:-http://localhost:8000/v1/chat/completions}
RECLIP_SUMMARIZE_API_KEY=${RECLIP_SUMMARIZE_API_KEY:-${RECLIP_API_KEY}}
RECLIP_SUMMARIZE_MODEL=${RECLIP_SUMMARIZE_MODEL:-gemma4-heretical-mlx-8bit}
# Leave unset to use the built-in default prompt, or override here:
# RECLIP_SUMMARIZE_PROMPT="your custom prompt"

# --- Translation ---
RECLIP_TRANSLATE_URL=${RECLIP_TRANSLATE_URL:-http://localhost:8000/v1/chat/completions}
RECLIP_TRANSLATE_API_KEY=${RECLIP_TRANSLATE_API_KEY:-${RECLIP_API_KEY}}
RECLIP_TRANSLATE_MODEL=${RECLIP_TRANSLATE_MODEL:-gemma4-heretical-mlx-8bit}
# RECLIP_TRANSLATE_PROMPT="must contain {language} placeholder"

# --- Counterargue ---
RECLIP_COUNTERARGUE_URL=${RECLIP_COUNTERARGUE_URL:-http://localhost:8000/v1/chat/completions}
RECLIP_COUNTERARGUE_API_KEY=${RECLIP_COUNTERARGUE_API_KEY:-${RECLIP_API_KEY}}
RECLIP_COUNTERARGUE_MODEL=${RECLIP_COUNTERARGUE_MODEL:-gemma4-heretical-mlx-8bit}
# RECLIP_COUNTERARGUE_PROMPT="your custom counterargue prompt"

# --- Text-to-speech ---
RECLIP_TTS_URL=${RECLIP_TTS_URL:-http://localhost:8000/v1/audio/speech}
RECLIP_TTS_API_KEY=${RECLIP_TTS_API_KEY:-${RECLIP_API_KEY}}
RECLIP_TTS_MODEL=${RECLIP_TTS_MODEL:-Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit}
# RECLIP_TTS_VOICE may be:
#   - empty: auto-clone from each video's own audio (middle 7s + STT)
#   - a voice description string: routed via the OpenAI `voice` field. With
#     Qwen3-TTS-VoiceDesign this becomes the `instructions` prompt; with Kokoro
#     or Qwen3-TTS-CustomVoice it selects a preset voice by name.
#   - a filesystem path to a reference audio file: used as ref_audio for
#     voice cloning across all videos. RECLIP_TTS_VOICE_TEXT (the transcript
#     of that clip) is recommended; if absent, ReClip+ will run STT on the
#     clip once and cache the transcript.
RECLIP_TTS_VOICE=${RECLIP_TTS_VOICE:-warm feminine voice with a soft sultry tone, gentle and engaging}
# Optional: transcript of the RECLIP_TTS_VOICE clip (only used when VOICE is
# a file path). Skip auto-STT if you provide it here.
RECLIP_TTS_VOICE_TEXT=${RECLIP_TTS_VOICE_TEXT}
"""


_INTERP_RE = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)(?::-([^${}]*))?\}')


def _interpolate(s, extra=None):
	"""Expand ${VAR} and ${VAR:-default}.

	Lookup order: os.environ → extra (e.g. secrets.ini) → default. Real env wins
	so ad-hoc shell overrides still work; the extra dict fills in below env,
	which is the path that lets a launchd/systemd-user service (with no inherited
	shell env) still resolve secrets-file values during interpolation.
	"""
	def sub(m):
		var = m.group(1)
		default = m.group(2)
		val = os.environ.get(var)
		if val:
			return val
		if extra:
			fb = extra.get(var)
			if fb:
				return fb
		return default if default is not None else ""

	# Iterate for nested ${A:-${B}} by expanding innermost first
	for _ in range(10):
		new_s = _INTERP_RE.sub(sub, s)
		if new_s == s:
			break
		s = new_s
	return s


def _parse_value(raw, extra=None):
	"""Parse the right-hand side of KEY=... handling quotes, interpolation, trailing comments."""
	s = raw.lstrip()
	if not s:
		return ""

	# Quoted string (single or double): value is everything until matching close quote
	if s[0] in ('"', "'"):
		quote = s[0]
		try:
			end = s.index(quote, 1)
			return _interpolate(s[1:end], extra=extra)
		except ValueError:
			# Unclosed quote — treat as literal
			return _interpolate(s[1:], extra=extra)

	# Unquoted: read until a " #" trailing comment, respecting ${...} braces
	i = 0
	depth = 0
	n = len(s)
	while i < n:
		c = s[i]
		if c == '$' and i + 1 < n and s[i + 1] == '{':
			depth += 1
			i += 2
			continue
		if c == '}' and depth > 0:
			depth -= 1
			i += 1
			continue
		if depth == 0 and c == '#' and i > 0 and s[i - 1].isspace():
			break
		i += 1
	return _interpolate(s[:i].rstrip(), extra=extra)


def _parse_file(content, extra=None):
	"""Parse shell-style INI content into a dict of RECLIP_* values.

	`extra` is an optional dict of fallback values used during interpolation
	(below os.environ, above the literal default). Used to thread secrets.ini
	values into config.ini's `${VAR}` expansion.
	"""
	values = {}
	for raw_line in content.splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#"):
			continue
		if "=" not in line:
			continue
		key, _, rest = line.partition("=")
		key = key.strip()
		if not key or not key.isidentifier():
			continue
		values[key] = _parse_value(rest, extra=extra)
	return values


def _default_cache_dir():
	xdg = os.environ.get("XDG_CACHE_HOME")
	if xdg:
		return os.path.join(xdg, "reclip")
	return os.path.join(os.path.expanduser("~"), ".cache", "reclip")


def _default_browser_for_cookies():
	"""Default browser for gallery-dl cookie extraction.

	Firefox across all platforms — works without OS-level permission prompts
	(Safari needs Full Disk Access; Chrome on Linux uses kwallet/secret-service).
	gallery-dl accepts 'chromium:/path/to/profile' for Chromium-derivatives like
	ChatGPT Atlas; users wanting that should override RECLIP_GALLERY_DL_BROWSER.
	Returns the gallery-dl browser-name string or "" to mean "no auth".
	"""
	return "firefox"


_FALSY = {"0", "false", "no", "off", ""}


def _parse_bool(raw, default):
	"""Lenient boolean: 0/false/no/off (any case) → False; unset → default."""
	if raw is None or raw == "":
		return default
	return raw.strip().lower() not in _FALSY


def _default_config_dir():
	override = os.environ.get("RECLIP_CONFIG_DIR")
	if override:
		return override
	xdg = os.environ.get("XDG_CONFIG_HOME")
	if xdg:
		return os.path.join(xdg, "reclip+")
	return os.path.join(os.path.expanduser("~"), ".config", "reclip+")


class Config:
	"""Config loader with file-backed values, env interpolation, and hot-reload."""

	RELOAD_THROTTLE_SECONDS = 5

	def __init__(self, config_dir=None):
		self.config_dir = config_dir or _default_config_dir()
		self.config_path = os.path.join(self.config_dir, "config.ini")
		self.secrets_path = os.path.join(self.config_dir, "secrets.ini")
		self._lock = threading.Lock()
		self._last_check = 0.0
		self._last_mtime = 0.0
		self._last_secrets_mtime = 0.0
		self._values = {}
		self._secrets_perm_warned = False
		self._ensure_file()
		self._load()

	def _ensure_file(self):
		"""Create config dir + file with defaults if they don't exist."""
		os.makedirs(self.config_dir, exist_ok=True)
		if not os.path.isfile(self.config_path):
			with open(self.config_path, "w", encoding="utf-8") as f:
				f.write(DEFAULT_CONFIG_CONTENT)

	def _read_secrets(self):
		"""Return (raw_dict, mtime). Empty dict + 0.0 mtime when file missing.

		Warns once on stderr if perms are broader than 0600 — file holds API keys.
		"""
		try:
			st = os.stat(self.secrets_path)
		except OSError:
			return {}, 0.0
		try:
			with open(self.secrets_path, "r", encoding="utf-8") as f:
				content = f.read()
		except OSError:
			return {}, 0.0
		# Permission hygiene: warn once if group/other can read.
		if not self._secrets_perm_warned and (st.st_mode & 0o077):
			import sys as _sys
			print(
				f"[reclip] WARNING: {self.secrets_path} has permissions "
				f"{oct(st.st_mode & 0o777)}; recommended 0600. Run: "
				f"chmod 600 {self.secrets_path}",
				file=_sys.stderr,
			)
			self._secrets_perm_warned = True
		return _parse_file(content), st.st_mtime

	def _load(self):
		"""Parse secrets.ini + config.ini and compute the effective config dict.

		Lookup order for each key: config.ini literal → real os.environ →
		secrets.ini → hardcoded default. secrets.ini values also flow into
		config.ini's `${VAR}` interpolation below os.environ.
		"""
		secrets_raw, secrets_mtime = self._read_secrets()
		self._last_secrets_mtime = secrets_mtime

		try:
			with open(self.config_path, "r", encoding="utf-8") as f:
				content = f.read()
			self._last_mtime = os.path.getmtime(self.config_path)
		except OSError:
			content = DEFAULT_CONFIG_CONTENT

		raw = _parse_file(content, extra=secrets_raw)

		def get(key, default=""):
			# config.ini wins, then env, then secrets, then default.
			v = raw.get(key)
			if v:
				return v
			v = os.environ.get(key, "")
			if v:
				return v
			v = secrets_raw.get(key, "")
			if v:
				return v
			return default

		cache_dir = get("RECLIP_CACHE_DIR") or _default_cache_dir()
		try:
			cache_max_mb = int(get("RECLIP_CACHE_MAX_MB", "1024"))
		except ValueError:
			cache_max_mb = 1024
		try:
			host_port = int(get("RECLIP_PORT", "8899"))
		except ValueError:
			host_port = 8899

		self._values = {
			"host": get("RECLIP_HOST", "127.0.0.1"),
			"port": host_port,
			"cache_dir": os.path.expanduser(cache_dir),
			"cache_max_mb": cache_max_mb,
			"gallery_dl_cookies": os.path.expanduser(get("RECLIP_GALLERY_DL_COOKIES")) if get("RECLIP_GALLERY_DL_COOKIES") else "",
			"gallery_dl_browser": get("RECLIP_GALLERY_DL_BROWSER", _default_browser_for_cookies()),
			"stt_url": get("RECLIP_STT_URL", "http://localhost:8000/v1/audio/transcriptions"),
			"stt_api_key": get("RECLIP_STT_API_KEY"),
			"stt_model": get("RECLIP_STT_MODEL", "whisper-large-v3-fp16"),
			"stt_prompt": get("RECLIP_STT_PROMPT"),
			"stt_word_timestamps": _parse_bool(get("RECLIP_STT_WORD_TIMESTAMPS"), True),
			"stt_metadata_prompt": _parse_bool(get("RECLIP_STT_METADATA_PROMPT"), True),
			"summarize_url": get("RECLIP_SUMMARIZE_URL", "http://localhost:8000/v1/chat/completions"),
			"summarize_api_key": get("RECLIP_SUMMARIZE_API_KEY"),
			"summarize_model": get("RECLIP_SUMMARIZE_MODEL", "gemma4-heretical-mlx-8bit"),
			"summarize_prompt": get("RECLIP_SUMMARIZE_PROMPT", DEFAULT_SUMMARIZE_PROMPT),
			"translate_url": get("RECLIP_TRANSLATE_URL", "http://localhost:8000/v1/chat/completions"),
			"translate_api_key": get("RECLIP_TRANSLATE_API_KEY"),
			"translate_model": get("RECLIP_TRANSLATE_MODEL", "gemma4-heretical-mlx-8bit"),
			"translate_prompt": get("RECLIP_TRANSLATE_PROMPT", DEFAULT_TRANSLATE_PROMPT),
			"counterargue_url": get("RECLIP_COUNTERARGUE_URL", "http://localhost:8000/v1/chat/completions"),
			"counterargue_api_key": get("RECLIP_COUNTERARGUE_API_KEY"),
			"counterargue_model": get("RECLIP_COUNTERARGUE_MODEL", "gemma4-heretical-mlx-8bit"),
			"counterargue_prompt": get("RECLIP_COUNTERARGUE_PROMPT", DEFAULT_COUNTERARGUE_PROMPT),
			"tts_url": get("RECLIP_TTS_URL", "http://localhost:8000/v1/audio/speech"),
			"tts_api_key": get("RECLIP_TTS_API_KEY"),
			"tts_model": get("RECLIP_TTS_MODEL", "Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"),
			"tts_voice": get("RECLIP_TTS_VOICE", "warm feminine voice with a soft sultry tone, gentle and engaging"),
			"tts_voice_text": get("RECLIP_TTS_VOICE_TEXT"),
		}

	def maybe_reload(self):
		"""Check mtime on config.ini and secrets.ini; reload if either changed. Throttled."""
		now = time.time()
		if now - self._last_check < self.RELOAD_THROTTLE_SECONDS:
			return
		with self._lock:
			if now - self._last_check < self.RELOAD_THROTTLE_SECONDS:
				return
			self._last_check = now
			cfg_mtime = 0.0
			try:
				cfg_mtime = os.path.getmtime(self.config_path)
			except OSError:
				pass
			sec_mtime = 0.0
			try:
				sec_mtime = os.path.getmtime(self.secrets_path)
			except OSError:
				pass
			if cfg_mtime > self._last_mtime or sec_mtime > self._last_secrets_mtime:
				self._load()

	def read_file(self):
		"""Return raw file contents, or default if missing."""
		try:
			with open(self.config_path, "r", encoding="utf-8") as f:
				return f.read()
		except OSError:
			return DEFAULT_CONFIG_CONTENT

	def write_file(self, content):
		"""Write new content to the file and reload immediately."""
		with self._lock:
			os.makedirs(self.config_dir, exist_ok=True)
			with open(self.config_path, "w", encoding="utf-8") as f:
				f.write(content)
			self._last_mtime = 0  # force reload
		self._load()

	# Dict-like API so existing cfg["key"] calls continue to work
	def __getitem__(self, key):
		return self._values[key]

	def get(self, key, default=None):
		return self._values.get(key, default)

	def __contains__(self, key):
		return key in self._values

	def as_dict(self):
		return dict(self._values)


def load_config():
	"""Legacy entrypoint — returns a Config instance (dict-compatible)."""
	return Config()
