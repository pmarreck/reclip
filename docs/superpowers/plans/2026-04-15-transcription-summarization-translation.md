# Transcription, Summarization & Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transcription (via Whisper/STT), summarization, and translation capabilities to ReClip's web UI, backed by configurable OpenAI-compatible LLM endpoints, with a flat-file cache to avoid redundant work, and playlist support for batch operations.

**Architecture:** Flask backend gains 4 new modules (config, cache, llm_client, url_normalize) and 5 new API endpoints. All LLM calls use the OpenAI wire format. A flat-file cache keyed by SHA-256 of normalized URLs stores artifacts at each pipeline stage. The frontend adds action buttons per video card and playlist batch operations.

**Tech Stack:** Python 3.12, Flask, yt-dlp, ffmpeg, OpenAI-compatible API (oMLX/Ollama/LM Studio), pytest

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `config.py` | Load all env vars with defaults, expose as a config dict |
| `cache.py` | URL normalization, cache key computation, read/write/evict, size accounting |
| `llm_client.py` | OpenAI-compatible HTTP calls: transcribe (multipart), chat/completions (summarize, translate) |
| `tests/test_config.py` | Unit tests for config loading |
| `tests/test_cache.py` | Unit tests for cache (normalize, lookup, write, eviction, size) |
| `tests/test_llm_client.py` | Unit tests for LLM client with mocked HTTP |
| `tests/test_api.py` | Integration tests for new API endpoints using Flask test client |
| `tests/conftest.py` | Shared pytest fixtures (temp cache dir, mock LLM server, Flask test client) |
| `test` | Bash runner script: `pytest` wrapper that runs all tests |

### Modified files

| File | Changes |
|------|---------|
| `app.py` | Import new modules, add 5 new endpoints, modify `run_download()` for caching, add `type` to job status, add config footer route |
| `templates/index.html` | Add Transcribe/Summarize/Translate buttons per card, translate language input, playlist header bar, config footer |
| `flake.nix` | Add `pytest` and `responses` (HTTP mocking) to dev shell |
| `requirements.txt` | Add `requests` (for LLM calls) and `pytest`+`responses` (dev) |

---

## Task 1: Test infrastructure and config module

**Files:**
- Create: `test`
- Create: `config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`
- Modify: `flake.nix`
- Modify: `requirements.txt`

- [ ] **Step 1: Add test deps to flake.nix and requirements.txt**

In `flake.nix`, change the `pythonEnv` to include test deps:
```nix
pythonEnv = python.withPackages (ps: with ps; [
  flask
  requests
  pytest
  responses
]);
```

In `requirements.txt`, add:
```
requests
```

- [ ] **Step 2: Create the test runner script**

Create `test` (executable, no extension):
```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
exec nix develop -c python -m pytest tests/ -v "$@"
```

Run: `chmod +x test`

- [ ] **Step 3: Create tests/__init__.py and tests/conftest.py**

`tests/__init__.py`: empty file.

`tests/conftest.py`:
```python
import os
import pytest
from app import app as flask_app


@pytest.fixture
def tmp_cache(tmp_path):
	"""Provide a temporary cache directory and set the env var."""
	cache_dir = tmp_path / "reclip-cache"
	cache_dir.mkdir()
	os.environ["RECLIP_CACHE_DIR"] = str(cache_dir)
	os.environ["RECLIP_CACHE_MAX_MB"] = "10"
	yield cache_dir
	os.environ.pop("RECLIP_CACHE_DIR", None)
	os.environ.pop("RECLIP_CACHE_MAX_MB", None)


@pytest.fixture
def client():
	"""Flask test client."""
	flask_app.config["TESTING"] = True
	with flask_app.test_client() as c:
		yield c
```

- [ ] **Step 4: Write failing tests for config module**

`tests/test_config.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `./test tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 6: Implement config.py**

```python
import os

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


def _default_cache_dir():
	xdg = os.environ.get("XDG_CACHE_HOME")
	if xdg:
		return os.path.join(xdg, "reclip")
	return os.path.join(os.path.expanduser("~"), ".cache", "reclip")


def load_config():
	return {
		"cache_dir": os.environ.get("RECLIP_CACHE_DIR", _default_cache_dir()),
		"cache_max_mb": int(os.environ.get("RECLIP_CACHE_MAX_MB", "1024")),
		"stt_url": os.environ.get("RECLIP_STT_URL", "http://localhost:8000/v1/audio/transcriptions"),
		"stt_api_key": os.environ.get("RECLIP_STT_API_KEY", ""),
		"stt_model": os.environ.get("RECLIP_STT_MODEL", "mlx-community/whisper-large-v3-turbo"),
		"stt_prompt": os.environ.get("RECLIP_STT_PROMPT", ""),
		"summarize_url": os.environ.get("RECLIP_SUMMARIZE_URL", "http://localhost:8000/v1/chat/completions"),
		"summarize_api_key": os.environ.get("RECLIP_SUMMARIZE_API_KEY", ""),
		"summarize_model": os.environ.get("RECLIP_SUMMARIZE_MODEL", "gemma4-heretical-mlx-8bit"),
		"summarize_prompt": os.environ.get("RECLIP_SUMMARIZE_PROMPT", DEFAULT_SUMMARIZE_PROMPT),
		"translate_url": os.environ.get("RECLIP_TRANSLATE_URL", "http://localhost:8000/v1/chat/completions"),
		"translate_api_key": os.environ.get("RECLIP_TRANSLATE_API_KEY", ""),
		"translate_model": os.environ.get("RECLIP_TRANSLATE_MODEL", "gemma4-heretical-mlx-8bit"),
		"translate_prompt": os.environ.get("RECLIP_TRANSLATE_PROMPT", DEFAULT_TRANSLATE_PROMPT),
	}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./test tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add test tests/ config.py flake.nix requirements.txt
git commit -m "feat: add config module and test infrastructure"
```

---

## Task 2: URL normalization and cache key computation

**Files:**
- Create: `cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write failing tests for URL normalization**

`tests/test_cache.py`:
```python
import pytest


class TestNormalizeUrl:
	def test_strips_utm_params(self):
		from cache import normalize_url
		url = "https://youtube.com/watch?v=abc123&utm_source=twitter&utm_medium=social"
		assert normalize_url(url) == "https://youtube.com/watch?v=abc123"

	def test_strips_tracking_params(self):
		from cache import normalize_url
		url = "https://nytimes.com/article?unlocked_article_code=xyz&smid=foo&si=bar"
		assert normalize_url(url) == "https://nytimes.com/article"

	def test_lowercases_scheme_and_host(self):
		from cache import normalize_url
		url = "HTTPS://YouTube.COM/watch?v=abc123"
		assert normalize_url(url) == "https://youtube.com/watch?v=abc123"

	def test_strips_trailing_slash(self):
		from cache import normalize_url
		url = "https://youtube.com/watch?v=abc123/"
		assert normalize_url(url) == "https://youtube.com/watch?v=abc123"

	def test_preserves_meaningful_params(self):
		from cache import normalize_url
		url = "https://youtube.com/watch?v=abc123&list=PLabc"
		result = normalize_url(url)
		assert "v=abc123" in result
		assert "list=PLabc" in result

	def test_sorts_params_for_consistency(self):
		from cache import normalize_url
		url_a = "https://youtube.com/watch?list=PLabc&v=abc123"
		url_b = "https://youtube.com/watch?v=abc123&list=PLabc"
		assert normalize_url(url_a) == normalize_url(url_b)

	def test_same_video_different_tracking_same_key(self):
		from cache import normalize_url, cache_key
		url_a = "https://youtube.com/watch?v=abc123&utm_source=twitter"
		url_b = "https://youtube.com/watch?v=abc123&smid=nytcore"
		assert cache_key(url_a) == cache_key(url_b)

	def test_different_videos_different_keys(self):
		from cache import cache_key
		assert cache_key("https://youtube.com/watch?v=abc") != cache_key("https://youtube.com/watch?v=xyz")

	def test_cache_key_is_hex_string(self):
		from cache import cache_key
		key = cache_key("https://youtube.com/watch?v=abc123")
		assert all(c in "0123456789abcdef" for c in key)
		assert len(key) == 64  # SHA-256 hex
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test tests/test_cache.py::TestNormalizeUrl -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cache'`

- [ ] **Step 3: Implement URL normalization and cache key in cache.py**

```python
import hashlib
import json
import os
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

TRACKING_PARAMS = {
	"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
	"utm_id", "utm_cid", "smid", "si", "feature", "unlocked_article_code",
	"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
}


def normalize_url(url):
	parsed = urlparse(url)
	scheme = parsed.scheme.lower()
	host = parsed.netloc.lower()
	path = parsed.path.rstrip("/")
	params = parse_qs(parsed.query, keep_blank_values=False)
	filtered = {
		k: v for k, v in params.items()
		if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
	}
	sorted_query = urlencode(sorted(filtered.items()), doseq=True)
	return urlunparse((scheme, host, path, "", sorted_query, ""))


def cache_key(url):
	normalized = normalize_url(url)
	return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test tests/test_cache.py::TestNormalizeUrl -v`
Expected: all PASS

- [ ] **Step 5: Write failing tests for cache read/write**

Append to `tests/test_cache.py`:
```python
import os
import time


class TestCacheReadWrite:
	def test_cache_dir_created_on_init(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache / "sub"), max_mb=10)
		assert os.path.isdir(str(tmp_cache / "sub"))

	def test_write_and_read_text(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=test1"
		c.write_text(url, "transcript.txt", "Hello world")
		assert c.read_text(url, "transcript.txt") == "Hello world"

	def test_read_missing_returns_none(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		assert c.read_text("https://example.com", "transcript.txt") is None

	def test_write_updates_meta(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=test2"
		c.write_text(url, "transcript.txt", "content", meta={"title": "Test", "url": url})
		meta = c.read_meta(url)
		assert meta["title"] == "Test"
		assert "last_accessed" in meta

	def test_read_updates_last_accessed(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=test3"
		c.write_text(url, "transcript.txt", "content")
		meta_before = c.read_meta(url)
		time.sleep(0.01)
		c.read_text(url, "transcript.txt")
		meta_after = c.read_meta(url)
		assert meta_after["last_accessed"] >= meta_before["last_accessed"]

	def test_has_file(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=test4"
		assert not c.has_file(url, "audio.mp3")
		c.write_text(url, "audio.mp3", "fake audio")
		assert c.has_file(url, "audio.mp3")

	def test_entry_path(self, tmp_cache):
		from cache import Cache, cache_key
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=test5"
		path = c.entry_path(url, "audio.mp3")
		assert path.endswith("audio.mp3")
		assert cache_key(url) in path

	def test_stats(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		c.write_text("https://a.com", "t.txt", "aaa")
		c.write_text("https://b.com", "t.txt", "bbb")
		stats = c.stats()
		assert stats["entry_count"] == 2
		assert stats["used_mb"] >= 0
		assert stats["max_mb"] == 10
		assert stats["location"] == str(tmp_cache)
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `./test tests/test_cache.py::TestCacheReadWrite -v`
Expected: FAIL — `ImportError: cannot import name 'Cache'`

- [ ] **Step 7: Implement Cache class**

Append to `cache.py`:
```python
class Cache:
	def __init__(self, cache_dir, max_mb=1024):
		self.cache_dir = cache_dir
		self.max_mb = max_mb
		os.makedirs(cache_dir, exist_ok=True)

	def _entry_dir(self, url):
		key = cache_key(url)
		d = os.path.join(self.cache_dir, key)
		os.makedirs(d, exist_ok=True)
		return d

	def entry_path(self, url, filename):
		return os.path.join(self._entry_dir(url), filename)

	def has_file(self, url, filename):
		return os.path.isfile(self.entry_path(url, filename))

	def read_text(self, url, filename):
		path = self.entry_path(url, filename)
		if not os.path.isfile(path):
			return None
		self._touch_access(url)
		with open(path, "r", encoding="utf-8") as f:
			return f.read()

	def write_text(self, url, filename, content, meta=None):
		path = self.entry_path(url, filename)
		with open(path, "w", encoding="utf-8") as f:
			f.write(content)
		if meta:
			self._write_meta(url, meta)
		self._touch_access(url)
		self._evict_if_needed()

	def write_file(self, url, filename, src_path):
		"""Copy a file into the cache entry directory."""
		import shutil
		dst = self.entry_path(url, filename)
		shutil.copy2(src_path, dst)
		self._touch_access(url)
		self._evict_if_needed()

	def read_meta(self, url):
		path = self.entry_path(url, "meta.json")
		if not os.path.isfile(path):
			return {}
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)

	def _write_meta(self, url, meta):
		path = self.entry_path(url, "meta.json")
		existing = {}
		if os.path.isfile(path):
			with open(path, "r", encoding="utf-8") as f:
				existing = json.load(f)
		existing.update(meta)
		existing["last_accessed"] = time.time()
		with open(path, "w", encoding="utf-8") as f:
			json.dump(existing, f)

	def _touch_access(self, url):
		path = self.entry_path(url, "meta.json")
		meta = {}
		if os.path.isfile(path):
			with open(path, "r", encoding="utf-8") as f:
				meta = json.load(f)
		meta["last_accessed"] = time.time()
		with open(path, "w", encoding="utf-8") as f:
			json.dump(meta, f)

	def _total_size_bytes(self):
		total = 0
		for dirpath, _, filenames in os.walk(self.cache_dir):
			for f in filenames:
				total += os.path.getsize(os.path.join(dirpath, f))
		return total

	def _evict_if_needed(self):
		max_bytes = self.max_mb * 1024 * 1024
		if self._total_size_bytes() <= max_bytes:
			return
		entries = []
		for name in os.listdir(self.cache_dir):
			entry_dir = os.path.join(self.cache_dir, name)
			if not os.path.isdir(entry_dir):
				continue
			meta_path = os.path.join(entry_dir, "meta.json")
			accessed = 0
			if os.path.isfile(meta_path):
				with open(meta_path, "r") as f:
					accessed = json.load(f).get("last_accessed", 0)
			entries.append((accessed, entry_dir))
		entries.sort()
		import shutil
		for _, entry_dir in entries:
			if self._total_size_bytes() <= max_bytes:
				break
			shutil.rmtree(entry_dir)

	def stats(self):
		entry_count = 0
		for name in os.listdir(self.cache_dir):
			if os.path.isdir(os.path.join(self.cache_dir, name)):
				entry_count += 1
		return {
			"location": self.cache_dir,
			"max_mb": self.max_mb,
			"used_mb": round(self._total_size_bytes() / (1024 * 1024), 2),
			"entry_count": entry_count,
		}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `./test tests/test_cache.py -v`
Expected: all PASS

- [ ] **Step 9: Write failing test for cache eviction**

Append to `tests/test_cache.py`:
```python
class TestCacheEviction:
	def test_evicts_oldest_when_over_limit(self, tmp_path):
		from cache import Cache
		# 1 KB limit
		c = Cache(str(tmp_path / "evict"), max_mb=0.001)
		# Write entries that exceed 1 KB total
		c.write_text("https://a.com", "data.txt", "A" * 600, meta={"url": "https://a.com"})
		time.sleep(0.01)
		c.write_text("https://b.com", "data.txt", "B" * 600, meta={"url": "https://b.com"})
		# The oldest entry (a.com) should have been evicted
		assert c.read_text("https://a.com", "data.txt") is None
		assert c.read_text("https://b.com", "data.txt") is not None
```

- [ ] **Step 10: Run eviction test to verify it passes (should pass with existing impl)**

Run: `./test tests/test_cache.py::TestCacheEviction -v`
Expected: PASS (eviction logic already in `write_text` -> `_evict_if_needed`)

- [ ] **Step 11: Commit**

```bash
git add cache.py tests/test_cache.py
git commit -m "feat: add cache module with URL normalization, read/write, and LRU eviction"
```

---

## Task 3: LLM client module

**Files:**
- Create: `llm_client.py`
- Create: `tests/test_llm_client.py`

- [ ] **Step 1: Write failing tests for LLM client**

`tests/test_llm_client.py`:
```python
import json
import pytest
import responses


class TestTranscribe:
	@responses.activate
	def test_transcribe_posts_audio_file(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"fake audio data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "Hello world", "language": "en", "duration": 10.5},
			status=200,
		)

		result = transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper-large-v3-turbo",
			api_key="test-key",
			prompt="",
		)
		assert result["text"] == "Hello world"
		assert result["language"] == "en"
		req = responses.calls[0].request
		assert "Bearer test-key" in req.headers.get("Authorization", "")
		assert b"fake audio data" in req.body

	@responses.activate
	def test_transcribe_no_api_key(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:9999/v1/audio/transcriptions",
			json={"text": "ok"},
			status=200,
		)

		transcribe(
			audio_path=str(audio_file),
			url="http://localhost:9999/v1/audio/transcriptions",
			model="whisper",
			api_key="",
			prompt="",
		)
		req = responses.calls[0].request
		assert "Authorization" not in req.headers

	@responses.activate
	def test_transcribe_error_raises(self, tmp_path):
		from llm_client import transcribe, LLMError
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"error": {"message": "model not loaded"}},
			status=400,
		)

		with pytest.raises(LLMError, match="model not loaded"):
			transcribe(
				audio_path=str(audio_file),
				url="http://localhost:8000/v1/audio/transcriptions",
				model="whisper",
				api_key="",
				prompt="",
			)


class TestChatCompletion:
	@responses.activate
	def test_chat_completion_returns_text(self):
		from llm_client import chat_completion
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"choices": [{"message": {"content": "Summary here"}}]},
			status=200,
		)

		result = chat_completion(
			url="http://localhost:8000/v1/chat/completions",
			model="gemma4",
			api_key="key123",
			system_prompt="Summarize this",
			user_content="Long transcript...",
		)
		assert result == "Summary here"
		req = responses.calls[0].request
		body = json.loads(req.body)
		assert body["model"] == "gemma4"
		assert body["messages"][0]["role"] == "system"
		assert body["messages"][1]["role"] == "user"
		assert "Bearer key123" in req.headers["Authorization"]

	@responses.activate
	def test_chat_completion_no_api_key(self):
		from llm_client import chat_completion
		responses.add(
			responses.POST,
			"http://localhost:11434/v1/chat/completions",
			json={"choices": [{"message": {"content": "ok"}}]},
			status=200,
		)

		chat_completion(
			url="http://localhost:11434/v1/chat/completions",
			model="llama3",
			api_key="",
			system_prompt="prompt",
			user_content="content",
		)
		req = responses.calls[0].request
		assert "Authorization" not in req.headers

	@responses.activate
	def test_chat_completion_error_raises(self):
		from llm_client import chat_completion, LLMError
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"error": {"message": "context too long"}},
			status=400,
		)

		with pytest.raises(LLMError, match="context too long"):
			chat_completion(
				url="http://localhost:8000/v1/chat/completions",
				model="gemma4",
				api_key="",
				system_prompt="p",
				user_content="c",
			)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_client'`

- [ ] **Step 3: Implement llm_client.py**

```python
import json
import requests


class LLMError(Exception):
	pass


def transcribe(audio_path, url, model, api_key="", prompt=""):
	headers = {}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	with open(audio_path, "rb") as f:
		files = {"file": (audio_path.split("/")[-1], f, "application/octet-stream")}
		data = {"model": model}
		if prompt:
			data["prompt"] = prompt

		resp = requests.post(url, headers=headers, files=files, data=data, timeout=600)

	if resp.status_code >= 400:
		error_msg = "Transcription failed"
		try:
			body = resp.json()
			if "error" in body:
				error_msg = body["error"].get("message", str(body["error"]))
		except (ValueError, KeyError):
			error_msg = resp.text
		raise LLMError(error_msg)

	result = resp.json()
	return {
		"text": result.get("text", ""),
		"language": result.get("language"),
		"duration": result.get("duration"),
	}


def chat_completion(url, model, api_key="", system_prompt="", user_content=""):
	headers = {"Content-Type": "application/json"}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	payload = {
		"model": model,
		"messages": [
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": user_content},
		],
	}

	resp = requests.post(url, headers=headers, json=payload, timeout=600)

	if resp.status_code >= 400:
		error_msg = "Chat completion failed"
		try:
			body = resp.json()
			if "error" in body:
				error_msg = body["error"].get("message", str(body["error"]))
		except (ValueError, KeyError):
			error_msg = resp.text
		raise LLMError(error_msg)

	result = resp.json()
	return result["choices"][0]["message"]["content"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test tests/test_llm_client.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_llm_client.py
git commit -m "feat: add LLM client module for transcription and chat completions"
```

---

## Task 4: Backend API endpoints (transcribe, summarize, translate, playlist, cache stats)

**Files:**
- Create: `tests/test_api.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing tests for the new API endpoints**

`tests/test_api.py`:
```python
import json
import os
import time
import pytest
import responses


@pytest.fixture(autouse=True)
def setup_cache(tmp_cache):
	"""All API tests use a temp cache dir."""
	pass


class TestCacheStats:
	def test_cache_stats_returns_info(self, client):
		resp = client.get("/api/cache/stats")
		data = resp.get_json()
		assert resp.status_code == 200
		assert "location" in data
		assert "max_mb" in data
		assert "used_mb" in data
		assert "entry_count" in data


class TestTranscribeEndpoint:
	@responses.activate
	def test_transcribe_returns_job_id(self, client):
		# Mock yt-dlp info call (used internally to download audio)
		# and the STT endpoint
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "Transcribed text here", "language": "en", "duration": 60.0},
			status=200,
		)

		resp = client.post("/api/transcribe", json={"url": "https://youtube.com/watch?v=test123"})
		data = resp.get_json()
		assert resp.status_code == 200
		assert "job_id" in data

	@responses.activate
	def test_transcribe_cached_returns_immediately(self, client, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=cached1"
		c.write_text(url, "transcript.txt", "Cached transcript")

		resp = client.post("/api/transcribe", json={"url": url})
		data = resp.get_json()
		assert resp.status_code == 200
		assert data.get("cached") is True
		assert data.get("text") == "Cached transcript"


class TestSummarizeEndpoint:
	@responses.activate
	def test_summarize_returns_job_id(self, client, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=sum1"
		c.write_text(url, "transcript.txt", "Long transcript content here")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"choices": [{"message": {"content": "Summary of content"}}]},
			status=200,
		)

		resp = client.post("/api/summarize", json={"url": url})
		data = resp.get_json()
		assert resp.status_code == 200
		assert "job_id" in data

	@responses.activate
	def test_summarize_cached_returns_immediately(self, client, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=sumcached"
		c.write_text(url, "summary.txt", "Cached summary")

		resp = client.post("/api/summarize", json={"url": url})
		data = resp.get_json()
		assert resp.status_code == 200
		assert data.get("cached") is True
		assert data.get("text") == "Cached summary"


class TestTranslateEndpoint:
	@responses.activate
	def test_translate_transcript(self, client, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=tr1"
		c.write_text(url, "transcript.txt", "English transcript")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"choices": [{"message": {"content": "Transcripcion en espanol"}}]},
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

	@responses.activate
	def test_translate_cached_returns_immediately(self, client, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://youtube.com/watch?v=trcached"
		c.write_text(url, "translation-spanish.txt", "Cached translation")

		resp = client.post("/api/translate", json={
			"url": url,
			"language": "Spanish",
			"source": "transcript",
		})
		data = resp.get_json()
		assert resp.status_code == 200
		assert data.get("cached") is True
		assert data.get("text") == "Cached translation"


class TestPlaylistEndpoint:
	def test_playlist_returns_entries(self, client, monkeypatch):
		import subprocess
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

		resp = client.post("/api/playlist", json={"url": "https://youtube.com/playlist?list=PLtest"})
		data = resp.get_json()
		assert resp.status_code == 200
		assert len(data["entries"]) == 2
		assert data["entries"][0]["title"] == "Video 1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test tests/test_api.py -v`
Expected: FAIL — endpoints don't exist yet

- [ ] **Step 3: Implement new endpoints in app.py**

Add imports at top of `app.py`:
```python
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
```

After `app = Flask(__name__)`, add config and cache init:
```python
cfg = load_config()
cache = Cache(cfg["cache_dir"], cfg["cache_max_mb"])
```

Add new endpoints (after existing ones, before `if __name__`):

```python
def _is_loopback(req):
    addr = req.remote_addr or ""
    return addr in ("127.0.0.1", "::1", "localhost")


@app.route("/api/cache/stats")
def cache_stats():
    return jsonify(cache.stats())


@app.route("/api/playlist", methods=["POST"])
def get_playlist():
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
            info = json.loads(line)
            entries.append({
                "url": info.get("url") or f"https://youtube.com/watch?v={info.get('id', '')}",
                "title": info.get("title", ""),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail", ""),
            })

        return jsonify({"entries": entries})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def _ensure_audio(url):
    """Ensure audio.mp3 exists in cache for this URL. Downloads if needed."""
    if cache.has_file(url, "audio.mp3"):
        return cache.entry_path(url, "audio.mp3")

    out_dir = cache.entry_path(url, "")
    out_template = os.path.join(os.path.dirname(out_dir), os.path.basename(os.path.dirname(out_dir)), "dl.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "-x", "--audio-format", "mp3", "-o", cache.entry_path(url, "audio.%(ext)s"), url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().split("\n")[-1])

    # yt-dlp names it audio.mp3 because of our template
    audio_path = cache.entry_path(url, "audio.mp3")
    if not os.path.isfile(audio_path):
        # Try to find any audio file that was downloaded
        entry_dir = os.path.dirname(audio_path)
        for f in os.listdir(entry_dir):
            if f.startswith("audio."):
                actual = os.path.join(entry_dir, f)
                if actual != audio_path:
                    os.rename(actual, audio_path)
                break

    if not os.path.isfile(audio_path):
        raise RuntimeError("Audio download completed but no file found")

    return audio_path


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
        text = result["text"]
        cache.write_text(url, "transcript.txt", text)
        job["status"] = "done"
        job["text"] = text
        job["filename"] = "transcript.txt"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/api/transcribe", methods=["POST"])
def start_transcribe():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cached = cache.read_text(url, "transcript.txt")
    if cached is not None:
        return jsonify({"cached": True, "text": cached})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_transcribe, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


def _run_summarize(job_id, url):
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


@app.route("/api/summarize", methods=["POST"])
def start_summarize():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cached = cache.read_text(url, "summary.txt")
    if cached is not None:
        return jsonify({"cached": True, "text": cached})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_summarize, args=(job_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


def _translate_filename(source, language):
    lang = language.lower().strip().replace(" ", "-")
    if source == "summary":
        return f"summary-{lang}.txt"
    return f"translation-{lang}.txt"


def _run_translate(job_id, url, language, source):
    job = jobs[job_id]
    try:
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

        prompt = cfg["translate_prompt"].replace("{language}", language)
        translated = chat_completion(
            url=cfg["translate_url"],
            model=cfg["translate_model"],
            api_key=cfg["translate_api_key"],
            system_prompt=prompt,
            user_content=source_text,
        )
        filename = _translate_filename(source, language)
        cache.write_text(url, filename, translated)
        job["status"] = "done"
        job["text"] = translated
        job["filename"] = filename
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


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


@app.route("/api/translate", methods=["POST"])
def start_translate():
    data = request.json
    url = data.get("url", "").strip()
    language = data.get("language", "English").strip()
    source = data.get("source", "transcript").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if source not in ("transcript", "summary"):
        return jsonify({"error": "source must be 'transcript' or 'summary'"}), 400

    filename = _translate_filename(source, language)
    cached = cache.read_text(url, filename)
    if cached is not None:
        return jsonify({"cached": True, "text": cached})

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "processing", "type": "text", "url": url}

    thread = threading.Thread(target=_run_translate, args=(job_id, url, language, source))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/text/<job_id>")
def download_text(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done" or "text" not in job:
        return jsonify({"error": "Text not ready"}), 404
    from io import BytesIO
    buf = BytesIO(job["text"].encode("utf-8"))
    return send_file(buf, as_attachment=True, download_name=job.get("filename", "result.txt"), mimetype="text/plain")
```

Also update `check_status` to include `type` and `text` fields:
```python
@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "type": job.get("type", "media"),
        "error": job.get("error"),
        "filename": job.get("filename"),
        "text": job.get("text"),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test tests/test_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: add transcribe, summarize, translate, playlist, and cache stats endpoints"
```

---

## Task 5: Modify existing download endpoint for caching

**Files:**
- Modify: `app.py`
- Add tests to: `tests/test_api.py`

- [ ] **Step 1: Write failing test for download caching**

Append to `tests/test_api.py`:
```python
class TestDownloadCaching:
	def test_download_caches_audio(self, client, tmp_cache, monkeypatch):
		"""After a download, audio.mp3 should exist in cache for the same URL."""
		import subprocess
		from cache import Cache

		url = "https://youtube.com/watch?v=dlcache1"
		c = Cache(str(tmp_cache), max_mb=10)

		# Create a fake downloaded file in the expected location
		def mock_run(cmd, *args, **kwargs):
			# Simulate yt-dlp creating a file
			for i, arg in enumerate(cmd):
				if arg == "-o":
					template = cmd[i + 1]
					# Create a fake mp4 file
					fake_path = template.replace("%(ext)s", "mp4")
					os.makedirs(os.path.dirname(fake_path), exist_ok=True)
					with open(fake_path, "wb") as f:
						f.write(b"fake video data")
					break

			class FakeResult:
				returncode = 0
				stderr = ""
			return FakeResult()

		monkeypatch.setattr(subprocess, "run", mock_run)

		resp = client.post("/api/download", json={
			"url": url,
			"format": "video",
			"title": "Test Video",
		})
		data = resp.get_json()
		assert "job_id" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test tests/test_api.py::TestDownloadCaching -v`
Expected: FAIL (download doesn't use cache yet)

- [ ] **Step 3: Modify run_download() to cache results**

Update `run_download()` in `app.py` to write to cache after successful download:

After the download succeeds and `chosen` is determined, add:
```python
        # Cache the downloaded file
        try:
            if format_choice == "audio":
                cache.write_file(url, "audio.mp3", chosen)
            else:
                cache.write_file(url, "video.mp4", chosen)
                # Also extract and cache audio for future transcription
                audio_cache_path = cache.entry_path(url, "audio.mp3")
                if not os.path.isfile(audio_cache_path):
                    extract_cmd = ["ffmpeg", "-i", chosen, "-vn", "-acodec", "libmp3lame", "-q:a", "2", audio_cache_path, "-y"]
                    subprocess.run(extract_cmd, capture_output=True, timeout=120)
            cache.write_text(url, "meta.json", "", meta={
                "url": url,
                "title": job.get("title", ""),
                "fetched_at": __import__("time").time(),
            })
        except Exception:
            pass  # Cache failure should not break the download
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test tests/test_api.py::TestDownloadCaching -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api.py
git commit -m "feat: cache downloaded media and extract audio for transcription pipeline"
```

---

## Task 6: Web UI — card action buttons (Transcribe, Summarize, Translate)

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add new action buttons to card rendering**

In `templates/index.html`, update the `renderCard()` function. Replace the ready-state `actionHtml` block with:

```javascript
      if (c.status === 'ready') {
        actionHtml = `<button class="card-dl-btn" onclick="dlCard(${idx})">Download</button>
          <button class="card-dl-btn card-btn-alt" onclick="transcribeCard(${idx})">Transcribe</button>
          <button class="card-dl-btn card-btn-alt" onclick="summarizeCard(${idx})">Summarize</button>
          <button class="card-dl-btn card-btn-alt" onclick="toggleTranslate(${idx})">Translate</button>
          ${qualityChips}`;
      } else if (c.status === 'downloading') {
```

Add per-operation status tracking to the card. After the `actionHtml` string, add a section for active operations:

```javascript
      // Per-operation status indicators
      let opsHtml = '';
      for (const op of ['transcribe', 'summarize', 'translate_transcript', 'translate_summary']) {
        const opState = c.ops?.[op];
        if (!opState) continue;
        const label = op.replace('_', ' ');
        if (opState.status === 'processing') {
          opsHtml += `<div class="card-status downloading"><span class="spin"></span> ${label}...</div>`;
        } else if (opState.status === 'done') {
          opsHtml += `<button class="card-dl-btn done" onclick="saveText(${idx}, '${op}')">${label} — Save</button>`;
        } else if (opState.status === 'error') {
          opsHtml += `<span class="card-status error">${label}: ${esc(opState.error || 'Failed')}</span>`;
        }
      }
```

Add a translate panel (hidden by default, toggled by the Translate button):

```javascript
      let translateHtml = '';
      if (c.showTranslate) {
        translateHtml = `
          <div class="translate-panel" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <input type="text" class="translate-lang" id="lang-${idx}" value="${esc(c.translateLang || 'English')}"
              placeholder="Language" style="padding:5px 10px;border:1.5px solid var(--card-border);border-radius:7px;
              font-family:'DM Mono',monospace;font-size:0.7rem;width:110px;background:var(--card);color:var(--fg);">
            <button class="card-dl-btn card-btn-alt" onclick="translateCard(${idx}, 'transcript')">Translate Transcript</button>
            <button class="card-dl-btn card-btn-alt" onclick="translateCard(${idx}, 'summary')">Translate Summary</button>
          </div>`;
      }
```

Update the card body innerHTML to include opsHtml and translateHtml:

```javascript
      el.innerHTML = `
        <div class="card-thumb">${thumbHtml}</div>
        <div class="card-body">
          <div class="card-title">${esc(c.title || 'Untitled')}</div>
          <div class="card-meta">${esc(c.uploader)}${c.duration ? ' · ' + fmtDur(c.duration) : ''}</div>
          <div class="card-actions">${actionHtml}</div>
          ${translateHtml}
          <div class="card-ops" style="margin-top:6px;display:flex;flex-direction:column;gap:4px;">${opsHtml}</div>
        </div>
      `;
```

- [ ] **Step 2: Add CSS for the new button variant**

In the `<style>` section, add:
```css
    .card-btn-alt {
      background: transparent;
      color: var(--fg);
      border: 1.5px solid var(--card-border);
    }
    .card-btn-alt:hover { background: rgba(26,26,24,0.04); border-color: var(--fg); }
```

- [ ] **Step 3: Add JavaScript functions for new operations**

Add before `</script>`:

```javascript
    function toggleTranslate(idx) {
      cardData[idx].showTranslate = !cardData[idx].showTranslate;
      if (!cardData[idx].translateLang) cardData[idx].translateLang = 'English';
      renderCard(idx);
    }

    function _initOps(idx) {
      if (!cardData[idx].ops) cardData[idx].ops = {};
    }

    async function transcribeCard(idx) {
      const c = cardData[idx];
      _initOps(idx);
      c.ops.transcribe = { status: 'processing' };
      renderCard(idx);

      try {
        const res = await fetch('/api/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: c.url, title: c.title || '' }),
        });
        const data = await res.json();
        if (data.error) {
          c.ops.transcribe = { status: 'error', error: data.error };
          renderCard(idx);
          return;
        }
        if (data.cached) {
          c.ops.transcribe = { status: 'done', text: data.text, jobId: null };
          renderCard(idx);
          return;
        }
        c.ops.transcribe.jobId = data.job_id;
        pollOp(idx, 'transcribe', data.job_id);
      } catch (err) {
        c.ops.transcribe = { status: 'error', error: err.message };
        renderCard(idx);
      }
    }

    async function summarizeCard(idx) {
      const c = cardData[idx];
      _initOps(idx);
      c.ops.summarize = { status: 'processing' };
      renderCard(idx);

      try {
        const res = await fetch('/api/summarize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: c.url, title: c.title || '' }),
        });
        const data = await res.json();
        if (data.error) {
          c.ops.summarize = { status: 'error', error: data.error };
          renderCard(idx);
          return;
        }
        if (data.cached) {
          c.ops.summarize = { status: 'done', text: data.text, jobId: null };
          renderCard(idx);
          return;
        }
        c.ops.summarize.jobId = data.job_id;
        pollOp(idx, 'summarize', data.job_id);
      } catch (err) {
        c.ops.summarize = { status: 'error', error: err.message };
        renderCard(idx);
      }
    }

    async function translateCard(idx, source) {
      const c = cardData[idx];
      const langInput = document.getElementById(`lang-${idx}`);
      const language = langInput ? langInput.value.trim() : 'English';
      c.translateLang = language;
      _initOps(idx);
      const opKey = source === 'summary' ? 'translate_summary' : 'translate_transcript';
      c.ops[opKey] = { status: 'processing' };
      renderCard(idx);

      try {
        const res = await fetch('/api/translate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: c.url, title: c.title || '', language, source }),
        });
        const data = await res.json();
        if (data.error) {
          c.ops[opKey] = { status: 'error', error: data.error };
          renderCard(idx);
          return;
        }
        if (data.cached) {
          c.ops[opKey] = { status: 'done', text: data.text, jobId: null };
          renderCard(idx);
          return;
        }
        c.ops[opKey].jobId = data.job_id;
        pollOp(idx, opKey, data.job_id);
      } catch (err) {
        c.ops[opKey] = { status: 'error', error: err.message };
        renderCard(idx);
      }
    }

    function pollOp(idx, opKey, jobId) {
      const c = cardData[idx];
      const iv = setInterval(async () => {
        try {
          const res = await fetch(`/api/status/${jobId}`);
          const data = await res.json();
          if (data.status === 'done') {
            clearInterval(iv);
            c.ops[opKey] = { status: 'done', text: data.text, jobId, filename: data.filename };
            renderCard(idx);
          } else if (data.status === 'error') {
            clearInterval(iv);
            c.ops[opKey] = { status: 'error', error: data.error };
            renderCard(idx);
          }
        } catch {
          clearInterval(iv);
          c.ops[opKey] = { status: 'error', error: 'Lost connection' };
          renderCard(idx);
        }
      }, 2000);
    }

    function saveText(idx, opKey) {
      const c = cardData[idx];
      const op = c.ops?.[opKey];
      if (!op) return;

      if (op.text) {
        // Direct download from cached text
        const blob = new Blob([op.text], { type: 'text/plain' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = op.filename || `${opKey}.txt`;
        a.click();
        URL.revokeObjectURL(a.href);
        return;
      }

      if (op.jobId) {
        const a = document.createElement('a');
        a.href = `/api/text/${op.jobId}`;
        a.download = op.filename || `${opKey}.txt`;
        a.click();
      }
    }
```

- [ ] **Step 4: Test manually in browser**

Run: `nix develop -c python app.py`
Open `http://localhost:8899`, paste a URL, Fetch, verify new buttons appear.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "feat: add Transcribe, Summarize, and Translate buttons to card UI"
```

---

## Task 7: Web UI — playlist support

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add playlist detection and batch UI**

In `templates/index.html`, modify the `go()` function to detect playlists. Replace it with:

```javascript
    async function go() {
      const rawText = document.getElementById('urls').value;
      const urls = parseUrls(rawText);
      if (!urls.length) return;

      const btn = document.getElementById('goBtn');
      const container = document.getElementById('cards');
      btn.disabled = true;
      btn.textContent = 'Loading...';
      container.innerHTML = '';
      cardData = [];

      for (let i = 0; i < urls.length; i++) {
        const url = urls[i];

        // Check if it's a playlist URL
        if (url.includes('playlist?list=') || url.includes('/sets/') || url.includes('&list=')) {
          await loadPlaylist(url, container);
          continue;
        }

        const idx = cardData.length;
        cardData.push({ url, status: 'loading' });
        renderCard(idx);

        try {
          const res = await fetch('/api/info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
          });
          const data = await res.json();
          if (data.error) {
            cardData[idx] = { ...cardData[idx], status: 'info-error', error: data.error };
          } else {
            cardData[idx] = {
              ...cardData[idx],
              status: 'ready',
              title: data.title || '',
              thumbnail: data.thumbnail || '',
              duration: data.duration,
              uploader: data.uploader || '',
              formats: data.formats || [],
              selectedFormatId: data.formats?.[0]?.id || null,
            };
          }
        } catch (err) {
          cardData[idx] = { ...cardData[idx], status: 'info-error', error: err.message };
        }
        renderCard(idx);
      }

      if (cardData.filter(c => c.status === 'ready').length > 1) {
        renderDownloadAll();
      }

      btn.disabled = false;
      btn.textContent = 'Fetch';
    }

    async function loadPlaylist(url, container) {
      // Show loading indicator
      const headerEl = document.createElement('div');
      headerEl.className = 'playlist-header';
      headerEl.innerHTML = '<span class="card-status downloading"><span class="spin"></span> Loading playlist...</span>';
      container.appendChild(headerEl);

      try {
        const res = await fetch('/api/playlist', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const data = await res.json();
        if (data.error) {
          headerEl.innerHTML = `<span class="card-status error">Playlist error: ${esc(data.error)}</span>`;
          return;
        }

        const entries = data.entries || [];
        headerEl.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <span style="font-family:'Instrument Serif',serif;font-size:1.1rem;">Playlist — ${entries.length} videos</span>
            <div style="display:flex;gap:6px;">
              <button class="card-dl-btn" onclick="batchOp('download')">Download All</button>
              <button class="card-dl-btn card-btn-alt" onclick="batchOp('transcribe')">Transcribe All</button>
              <button class="card-dl-btn card-btn-alt" onclick="batchOp('summarize')">Summarize All</button>
            </div>
          </div>`;

        // Fetch info for each entry
        for (const entry of entries) {
          const idx = cardData.length;
          cardData.push({
            url: entry.url,
            status: 'ready',
            title: entry.title || '',
            thumbnail: entry.thumbnail || '',
            duration: entry.duration,
            uploader: '',
            formats: [],
            selectedFormatId: null,
          });
          renderCard(idx);
        }
      } catch (err) {
        headerEl.innerHTML = `<span class="card-status error">${esc(err.message)}</span>`;
      }
    }

    async function batchOp(op) {
      for (let i = 0; i < cardData.length; i++) {
        if (cardData[i].status !== 'ready') continue;
        if (op === 'download') await dlCard(i);
        else if (op === 'transcribe') await transcribeCard(i);
        else if (op === 'summarize') await summarizeCard(i);
      }
    }
```

- [ ] **Step 2: Add CSS for playlist header**

```css
    .playlist-header {
      padding: 14px;
      border: 1.5px solid var(--card-border);
      border-radius: var(--radius);
      background: var(--card);
      margin-bottom: 10px;
    }
```

- [ ] **Step 3: Test manually with a YouTube playlist URL**

Run: `nix develop -c python app.py`
Open `http://localhost:8899`, paste a playlist URL, verify header and individual cards render.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: add playlist detection with batch operation buttons"
```

---

## Task 8: Web UI — config footer (loopback only)

**Files:**
- Modify: `app.py`
- Modify: `templates/index.html`
- Add test to: `tests/test_api.py`

- [ ] **Step 1: Write failing test for config endpoint**

Append to `tests/test_api.py`:
```python
class TestConfigFooter:
	def test_config_returns_info_for_loopback(self, client):
		resp = client.get("/api/config")
		data = resp.get_json()
		assert resp.status_code == 200
		assert "cache" in data
		assert "stt_host" in data
		assert "llm_host" in data
		# API keys must never be exposed
		assert "api_key" not in json.dumps(data).lower() or "sk-" not in json.dumps(data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test tests/test_api.py::TestConfigFooter -v`
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Add /api/config endpoint to app.py**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test tests/test_api.py::TestConfigFooter -v`
Expected: PASS

- [ ] **Step 5: Add footer to index.html**

At the end of the `.page` div, before the closing `</div>`, replace the existing footer with:
```html
    <div class="footer" id="configFooter"></div>

    <div class="footer">
      YouTube &middot; TikTok &middot; Instagram &middot; Twitter/X &middot; Reddit &middot; Facebook<br>
      Vimeo &middot; Twitch &middot; Dailymotion &middot; SoundCloud &middot; Loom &middot; Streamable<br>
      Pinterest &middot; Tumblr &middot; Threads &middot; LinkedIn &middot; 1000+ more
    </div>
```

Add JavaScript to load config footer on page load:
```javascript
    (async function loadConfigFooter() {
      try {
        const res = await fetch('/api/config');
        if (res.status !== 200) return;
        const data = await res.json();
        if (!data.cache) return;
        const el = document.getElementById('configFooter');
        el.textContent = `Cache: ${data.cache.location} (${data.cache.used_mb} MB / ${data.cache.max_mb} MB, ${data.cache.entry_count} entries)  \u2022  STT: ${data.stt_host}  \u2022  LLM: ${data.llm_host}`;
      } catch {}
    })();
```

- [ ] **Step 6: Test manually — verify footer appears on localhost, not on remote**

Run: `nix develop -c python app.py`
Open `http://localhost:8899`, verify footer shows config info.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/index.html tests/test_api.py
git commit -m "feat: add config footer visible only on loopback connections"
```

---

## Task 9: Update flake.nix and final integration test

**Files:**
- Modify: `flake.nix`
- Run full test suite

- [ ] **Step 1: Update flake.nix with all new deps**

Update the `pythonEnv` in `flake.nix`:
```nix
pythonEnv = python.withPackages (ps: with ps; [
  flask
  requests
  pytest
  responses
]);
```

Update the `packages.default` installPhase to copy new modules:
```nix
installPhase = ''
  mkdir -p $out/share/reclip $out/bin
  cp -r app.py config.py cache.py llm_client.py templates static assets $out/share/reclip/
  makeWrapper ${pythonEnv}/bin/python $out/bin/reclip \
    --add-flags "$out/share/reclip/app.py" \
    --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg pkgs.yt-dlp ]}
'';
```

- [ ] **Step 2: Run full test suite**

Run: `./test -v`
Expected: all tests PASS

- [ ] **Step 3: Run app and manually test full pipeline**

Run: `nix develop -c python app.py`
Open `http://localhost:8899`. Test:
1. Paste a YouTube URL, Fetch, verify card
2. Click Download, verify download works
3. Click Transcribe, verify transcript appears
4. Click Summarize, verify summary appears
5. Click Translate, type "Spanish", click Translate Transcript
6. Verify config footer shows at bottom

- [ ] **Step 4: Commit**

```bash
git add flake.nix
git commit -m "feat: update flake.nix with new Python deps and modules"
```
