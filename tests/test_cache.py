import os
import time
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

	def test_pinned_entries_survive_eviction(self, tmp_path):
		"""Pinned entries are exempt from LRU eviction even when over budget."""
		from cache import Cache
		c = Cache(str(tmp_path / "pin-evict"), max_mb=0.001)
		c.write_text("https://pinned.com", "data.txt", "P" * 600, meta={"url": "https://pinned.com"})
		c.set_pinned("https://pinned.com", True)
		time.sleep(0.01)
		# Write a new entry that pushes total over budget
		c.write_text("https://transient.com", "data.txt", "T" * 600, meta={"url": "https://transient.com"})
		# Pinned entry must still be there
		assert c.read_text("https://pinned.com", "data.txt") == "P" * 600
		# Eviction may or may not have purged transient (depends on order),
		# but pinned must survive regardless.

	def test_pinned_evicts_when_only_pinned_exceeds_budget(self, tmp_path):
		"""If even pinned entries alone exceed the budget, eviction is a no-op
		rather than purging pinned. Better to over-fill than betray the pin."""
		from cache import Cache
		c = Cache(str(tmp_path / "all-pinned"), max_mb=0.001)
		# Pin BEFORE writing so the eviction pass that fires inside write_text
		# already sees the pin marker.
		c.set_pinned("https://a.com", True)
		c.write_text("https://a.com", "data.txt", "A" * 1200, meta={"url": "https://a.com"})
		# Pinned, exceeds budget — eviction must skip it rather than purge.
		assert c.read_text("https://a.com", "data.txt") is not None


class TestListEntries:
	"""list_entries() returns rich entry summaries sorted by last_accessed
	descending so the UI can render most-recent-first."""

	def test_returns_empty_for_empty_cache(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		assert c.list_entries() == []

	def test_returns_entries_most_recent_first(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		c.write_text("https://oldest.com", "transcript.txt", "old", meta={"url": "https://oldest.com", "title": "Oldest"})
		time.sleep(0.01)
		c.write_text("https://middle.com", "transcript.txt", "mid", meta={"url": "https://middle.com", "title": "Middle"})
		time.sleep(0.01)
		c.write_text("https://newest.com", "transcript.txt", "new", meta={"url": "https://newest.com", "title": "Newest"})

		entries = c.list_entries()
		assert len(entries) == 3
		titles = [e["title"] for e in entries]
		assert titles == ["Newest", "Middle", "Oldest"]

	@pytest.mark.parametrize("audio_filename", ["audio.m4a", "audio.mp3"])
	def test_entry_includes_what_is_cached(self, tmp_cache, audio_filename):
		"""Each entry exposes presence flags so the UI knows which buttons to show."""
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://example.com/v"
		c.write_text(url, "transcript.txt", "T", meta={"url": url, "title": "Vid"})
		c.write_text(url, "summary.txt", "S")
		c.write_text(url, audio_filename, "AAA")

		entries = c.list_entries()
		assert len(entries) == 1
		e = entries[0]
		assert e["has_transcript"] is True
		assert e["has_summary"] is True
		assert e["has_audio"] is True
		assert e["has_translation"] is False
		assert e["has_counterargue"] is False

	def test_media_flags_classify_a_mixed_entry_set(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		cases = [
			("https://example.com/internal-audio", "audio.m4a", True, False),
			("https://example.com/download-audio", "audio-192k.mp3", True, False),
			("https://example.com/video", "video.mp4", False, True),
			("https://example.com/text", "transcript.txt", False, False),
		]
		for url, filename, _, _ in cases:
			c.write_text(url, filename, "x", meta={"url": url})

		entries = {entry["url"]: entry for entry in c.list_entries()}
		assert [
			(entries[url]["has_audio"], entries[url]["has_video"])
			for url, _, _, _ in cases
		] == [(has_audio, has_video) for _, _, has_audio, has_video in cases]

	def test_entry_includes_pinned_flag(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://example.com/p"
		c.write_text(url, "transcript.txt", "x", meta={"url": url})
		c.set_pinned(url, True)
		[e] = c.list_entries()
		assert e["pinned"] is True


class TestDeleteAndClear:
	def test_delete_entry_removes_dir(self, tmp_cache):
		from cache import Cache, cache_key
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://to-delete.com"
		c.write_text(url, "x.txt", "x", meta={"url": url})
		entry_dir = os.path.join(str(tmp_cache), cache_key(url))
		assert os.path.isdir(entry_dir)
		assert c.delete_entry_by_hash(cache_key(url)) is True
		assert not os.path.isdir(entry_dir)

	def test_delete_unknown_entry_returns_false(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		assert c.delete_entry_by_hash("0" * 64) is False

	def test_delete_rejects_path_traversal(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		# .. / and / must be rejected before they reach disk operations
		for bad in ("..", "..\\", "../foo", "foo/bar", "/abs/path", ""):
			assert c.delete_entry_by_hash(bad) is False

	def test_clear_all_removes_all_entries(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		c.write_text("https://a.com", "x.txt", "a", meta={"url": "https://a.com"})
		c.write_text("https://b.com", "x.txt", "b", meta={"url": "https://b.com"})
		assert c.clear_all() == 2
		assert c.list_entries() == []

	def test_clear_all_respects_pin_when_keep_pinned(self, tmp_cache):
		"""clear_all(keep_pinned=True) keeps pinned entries."""
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		c.write_text("https://pin.com", "x.txt", "p", meta={"url": "https://pin.com"})
		c.set_pinned("https://pin.com", True)
		c.write_text("https://not.com", "x.txt", "n", meta={"url": "https://not.com"})
		removed = c.clear_all(keep_pinned=True)
		assert removed == 1
		entries = c.list_entries()
		assert len(entries) == 1
		assert entries[0]["pinned"] is True


class TestPinning:
	def test_set_pinned_true_then_false(self, tmp_cache):
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		url = "https://pin-toggle.com"
		c.write_text(url, "x.txt", "y", meta={"url": url})
		c.set_pinned(url, True)
		assert c.read_meta(url).get("pinned") is True
		c.set_pinned(url, False)
		assert c.read_meta(url).get("pinned") is False

	def test_pin_unknown_url_creates_meta(self, tmp_cache):
		"""Pinning before any content is cached should work — meta.json gets created."""
		from cache import Cache
		c = Cache(str(tmp_cache), max_mb=10)
		c.set_pinned("https://no-content.com", True)
		assert c.read_meta("https://no-content.com").get("pinned") is True
