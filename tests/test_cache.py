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
