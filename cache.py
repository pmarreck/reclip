import hashlib
import json
import os
import shutil
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

TRACKING_PARAMS = {
	"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
	"utm_id", "utm_cid", "smid", "si", "feature", "unlocked_article_code",
	"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
}


def normalize_url(url):
	"""Normalize a URL for stable cache-key computation.

	Lowercases scheme/host, strips known tracking params (UTM, fbclid, smid,
	etc.), sorts remaining query params, and strips a trailing slash from the
	full URL (before fragment) so semantically identical URLs collapse to the
	same string.
	"""
	parsed = urlparse(url.rstrip("/"))
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
	"""Return a 64-char SHA-256 hex digest of the normalized URL.

	Used as the directory name for each cache entry so the filesystem layout
	is URL-independent and collision-resistant.
	"""
	normalized = normalize_url(url)
	return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Cache:
	"""Filesystem cache keyed by normalized URL hash.

	Stores arbitrary named files under per-URL subdirectories, maintains
	a meta.json with last_accessed timestamps, and evicts the least-recently-
	accessed entries when the total size exceeds max_mb.
	"""

	def __init__(self, cache_dir, max_mb=1024):
		self.cache_dir = cache_dir
		self.max_mb = max_mb
		os.makedirs(cache_dir, exist_ok=True)

	def _entry_dir(self, url):
		"""Return (and create) the directory for the given URL's cache entry."""
		key = cache_key(url)
		d = os.path.join(self.cache_dir, key)
		os.makedirs(d, exist_ok=True)
		return d

	def entry_path(self, url, filename):
		"""Return the full filesystem path for a named file within a URL's cache entry."""
		return os.path.join(self._entry_dir(url), filename)

	def has_file(self, url, filename):
		"""Return True if the named file exists in the cache entry for url."""
		return os.path.isfile(self.entry_path(url, filename))

	def read_text(self, url, filename):
		"""Read a cached text file; returns None if absent. Updates last_accessed."""
		path = self.entry_path(url, filename)
		if not os.path.isfile(path):
			return None
		self._touch_access(url)
		with open(path, "r", encoding="utf-8") as f:
			return f.read()

	def write_text(self, url, filename, content, meta=None):
		"""Write text content to the cache entry for url, then evict if over limit."""
		path = self.entry_path(url, filename)
		with open(path, "w", encoding="utf-8") as f:
			f.write(content)
		if meta:
			self._write_meta(url, meta)
		self._touch_access(url)
		self._evict_if_needed()

	def write_file(self, url, filename, src_path):
		"""Copy a file into the cache entry directory, then evict if over limit."""
		dst = self.entry_path(url, filename)
		shutil.copy2(src_path, dst)
		self._touch_access(url)
		self._evict_if_needed()

	def read_meta(self, url):
		"""Read the meta.json for a cache entry; returns {} if absent."""
		path = self.entry_path(url, "meta.json")
		if not os.path.isfile(path):
			return {}
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)

	def _write_meta(self, url, meta):
		"""Merge provided metadata dict into meta.json, setting last_accessed."""
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
		"""Update last_accessed in meta.json without altering other fields."""
		path = self.entry_path(url, "meta.json")
		meta = {}
		if os.path.isfile(path):
			with open(path, "r", encoding="utf-8") as f:
				meta = json.load(f)
		meta["last_accessed"] = time.time()
		with open(path, "w", encoding="utf-8") as f:
			json.dump(meta, f)

	def _total_size_bytes(self):
		"""Walk the cache directory and sum all file sizes in bytes."""
		total = 0
		for dirpath, _, filenames in os.walk(self.cache_dir):
			for f in filenames:
				total += os.path.getsize(os.path.join(dirpath, f))
		return total

	def _evict_if_needed(self):
		"""Remove least-recently-accessed UNPINNED entries until total size is
		within max_mb. Pinned entries are exempt — better to overflow the
		budget than to betray a pin."""
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
			pinned = False
			if os.path.isfile(meta_path):
				with open(meta_path, "r") as f:
					meta = json.load(f)
				accessed = meta.get("last_accessed", 0)
				pinned = bool(meta.get("pinned"))
			if pinned:
				continue
			entries.append((accessed, entry_dir))
		entries.sort()
		for _, entry_dir in entries:
			if self._total_size_bytes() <= max_bytes:
				break
			shutil.rmtree(entry_dir)

	def stats(self):
		"""Return a dict with entry_count, used_mb, max_mb, and location."""
		entry_count = sum(
			1 for name in os.listdir(self.cache_dir)
			if os.path.isdir(os.path.join(self.cache_dir, name))
		)
		return {
			"location": self.cache_dir,
			"max_mb": self.max_mb,
			"used_mb": round(self._total_size_bytes() / (1024 * 1024), 2),
			"entry_count": entry_count,
		}

	def _is_safe_hash(self, h):
		"""Validate a hash is exactly the SHA-256 hex format we generate.

		Defense against path traversal: only accept 64 lowercase hex chars
		so '..', '/', '\\', or absolute paths are rejected before disk ops.
		"""
		if not isinstance(h, str) or len(h) != 64:
			return False
		return all(c in "0123456789abcdef" for c in h.lower())

	def list_entries(self):
		"""Return a list of cache-entry summaries sorted by last_accessed desc.

		Each entry includes meta.json fields plus presence flags for the
		well-known cached files (audio/transcript/summary/translations/
		counterargue/tts/media), the on-disk size in MB, and a pinned flag.
		Used by /api/cache/entries to render the recents list.
		"""
		results = []
		if not os.path.isdir(self.cache_dir):
			return results
		for name in os.listdir(self.cache_dir):
			entry_dir = os.path.join(self.cache_dir, name)
			if not os.path.isdir(entry_dir):
				continue
			meta_path = os.path.join(entry_dir, "meta.json")
			meta = {}
			if os.path.isfile(meta_path):
				try:
					with open(meta_path, "r", encoding="utf-8") as f:
						meta = json.load(f)
				except (OSError, json.JSONDecodeError):
					pass

			files = set()
			try:
				files = set(os.listdir(entry_dir))
			except OSError:
				continue

			has_translation = any(
				f.startswith("translation-") or f.startswith("summary-")
				for f in files
			)
			translations = sorted(
				f for f in files
				if f.startswith("translation-") and f.endswith(".txt")
			)
			media_dir = os.path.join(entry_dir, "media")
			media_files = []
			if os.path.isdir(media_dir):
				media_files = sorted(
					f for f in os.listdir(media_dir)
					if os.path.isfile(os.path.join(media_dir, f))
				)
			has_media = bool(media_files)
			first_media = media_files[0] if media_files else ""

			size_bytes = 0
			for dp, _, fns in os.walk(entry_dir):
				for fn in fns:
					try:
						size_bytes += os.path.getsize(os.path.join(dp, fn))
					except OSError:
						pass

			results.append({
				"entry_hash": name,
				"url": meta.get("url", ""),
				"title": meta.get("title", ""),
				"uploader": meta.get("uploader", ""),
				"upload_date": meta.get("upload_date", ""),
				"duration": meta.get("duration", 0),
				"thumbnail": meta.get("thumbnail", ""),
				"kind": meta.get("kind", "video"),
				"item_count": meta.get("item_count", 0),
				"last_accessed": meta.get("last_accessed", 0),
				"pinned": bool(meta.get("pinned", False)),
				"size_mb": round(size_bytes / (1024 * 1024), 3),
				"has_audio": "audio.mp3" in files,
				"has_transcript": "transcript.txt" in files,
				"has_summary": "summary.txt" in files,
				"has_translation": has_translation,
				"translations": translations,
				"has_counterargue": "counterargue.txt" in files,
				"has_tts": "tts.mp3" in files or "tts.wav" in files,
				"has_media": has_media,
				"first_media": first_media,
				"media_count": len(media_files),
				"media_files": media_files[:100],  # capped to keep payload small
			})

		results.sort(key=lambda e: e["last_accessed"], reverse=True)
		return results

	def delete_entry_by_hash(self, entry_hash):
		"""Remove a single cache entry directory. Returns True on success,
		False on bad input or missing dir. Path-traversal-safe."""
		if not self._is_safe_hash(entry_hash):
			return False
		entry_dir = os.path.join(self.cache_dir, entry_hash)
		if not os.path.isdir(entry_dir):
			return False
		shutil.rmtree(entry_dir)
		return True

	def clear_all(self, keep_pinned=False):
		"""Remove every cache entry (or every unpinned one). Returns count."""
		removed = 0
		if not os.path.isdir(self.cache_dir):
			return 0
		for name in os.listdir(self.cache_dir):
			entry_dir = os.path.join(self.cache_dir, name)
			if not os.path.isdir(entry_dir):
				continue
			if keep_pinned:
				meta_path = os.path.join(entry_dir, "meta.json")
				if os.path.isfile(meta_path):
					try:
						with open(meta_path, "r", encoding="utf-8") as f:
							if json.load(f).get("pinned"):
								continue
					except (OSError, json.JSONDecodeError):
						pass
			shutil.rmtree(entry_dir)
			removed += 1
		return removed

	def set_pinned(self, url, pinned):
		"""Toggle the pinned flag on a cache entry; creates meta.json if
		the entry doesn't exist yet. Pinned entries skip LRU eviction."""
		path = self.entry_path(url, "meta.json")
		meta = {"url": url}
		if os.path.isfile(path):
			try:
				with open(path, "r", encoding="utf-8") as f:
					meta = json.load(f)
			except (OSError, json.JSONDecodeError):
				pass
		meta["pinned"] = bool(pinned)
		meta.setdefault("last_accessed", time.time())
		with open(path, "w", encoding="utf-8") as f:
			json.dump(meta, f)
