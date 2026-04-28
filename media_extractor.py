"""Image-heavy social-site extractor (gallery-dl bridge).

Pure helper module — no Flask, no global state, no I/O at import time.
classify_url() inspects only the hostname so the routing decision in
app.py is cheap and deterministic. fetch_images() invokes gallery-dl
through an injectable subprocess runner so tests can fake it.
"""
import json
import os
import subprocess
from urllib.parse import urlparse


# Hosts where the primary content is photos/carousels — these route to
# gallery-dl, not yt-dlp. Reels/stories on Instagram are still images-first
# (a poster frame is good enough for v1).
IMAGE_HOSTS = frozenset({
	"instagram.com",
	"threads.net",
	"reddit.com",
	"redd.it",
	"x.com",
	"twitter.com",
	"pinterest.com",
	"pinimg.com",
	"tumblr.com",
	"imgur.com",
	"flickr.com",
	"deviantart.com",
})

# Hosts where yt-dlp is the canonical extractor. Listed for clarity; anything
# not in IMAGE_HOSTS already routes here, but keeping this explicit lets us
# return "video" rather than "unknown" for friendlier diagnostics later.
VIDEO_HOSTS = frozenset({
	"youtube.com",
	"youtu.be",
	"vimeo.com",
	"soundcloud.com",
	"tiktok.com",
	"twitch.tv",
	"dailymotion.com",
	"facebook.com",
	"loom.com",
	"streamable.com",
})


def _canonical_host(url):
	"""Return lowercase host with a leading 'www.' / 'm.' / 'old.' stripped.

	Used so 'www.instagram.com' and 'instagram.com' classify the same. We
	deliberately strip a small allowlist of subdomain prefixes rather than
	just the eTLD+1 to avoid pulling in tldextract.
	"""
	try:
		host = (urlparse(url).hostname or "").lower()
	except Exception:
		return ""
	if not host:
		return ""
	for prefix in ("www.", "m.", "mobile.", "old.", "new."):
		if host.startswith(prefix):
			host = host[len(prefix):]
			break
	return host


def classify_url(url):
	"""Return 'images', 'video', or 'unknown' based on the URL's hostname."""
	host = _canonical_host(url)
	if not host:
		return "unknown"
	if host in IMAGE_HOSTS:
		return "images"
	# Match a registered domain at any depth (e.g. 'i.imgur.com' -> 'imgur.com')
	parts = host.split(".")
	for i in range(len(parts) - 1):
		suffix = ".".join(parts[i:])
		if suffix in IMAGE_HOSTS:
			return "images"
		if suffix in VIDEO_HOSTS:
			return "video"
	if host in VIDEO_HOSTS:
		return "video"
	return "unknown"


def _default_runner(cmd, **kwargs):
	return subprocess.run(cmd, **kwargs)


def _parse_dump(stdout):
	"""Parse gallery-dl --dump-json output into a list of item dicts.

	gallery-dl emits a JSON array of [type, ...] tuples where type is an int:
	1=Version, 2=Directory, 3=Url, 5=Queue, 7=Urllist. Only type==3 rows are
	downloadable items (shape: [3, url, metadata]). We also accept the string
	'url' for forward/backward compat with other gallery-dl versions.
	"""
	if not stdout.strip():
		return []
	try:
		data = json.loads(stdout)
	except json.JSONDecodeError:
		# Some configurations emit one JSON object per line instead — try that.
		items = []
		for line in stdout.splitlines():
			line = line.strip()
			if not line:
				continue
			try:
				items.append(json.loads(line))
			except json.JSONDecodeError:
				continue
		data = items

	results = []
	for row in data:
		if not isinstance(row, list) or len(row) < 3:
			continue
		kind, item_url, meta = row[0], row[1], row[2]
		if kind != 3 and kind != "url":
			continue
		if not isinstance(meta, dict):
			meta = {}
		fname = meta.get("filename") or ""
		ext = meta.get("extension") or ""
		if fname and ext and not fname.endswith("." + ext):
			fname = f"{fname}.{ext}"
		elif not fname and item_url:
			fname = os.path.basename(urlparse(item_url).path) or ""
		results.append({
			"filename": fname,
			"url": item_url,
			"width": meta.get("width"),
			"height": meta.get("height"),
		})
	return results


_LOGIN_REDIRECT_PATTERNS = (
	"redirect to login",
	"login required",
	"authentication required",
	"login page",
	"401 unauthorized",
	"403 forbidden",
)


def _looks_like_auth_failure(stderr):
	low = (stderr or "").lower()
	return any(pat in low for pat in _LOGIN_REDIRECT_PATTERNS)


def _augment_auth_error(stderr):
	"""Wrap a gallery-dl auth failure with an actionable hint."""
	return (
		f"{stderr.strip()}\n"
		"Site requires login. Set RECLIP_GALLERY_DL_BROWSER (e.g. firefox/safari/"
		"chrome) to extract session cookies live, or RECLIP_GALLERY_DL_COOKIES to "
		"point at a Netscape-format cookies.txt file."
	)


def _auth_args(cookies, cookies_from_browser):
	"""Render the gallery-dl auth flags. cookies (file path) wins over browser."""
	if cookies:
		return ["--cookies", cookies]
	if cookies_from_browser:
		return ["--cookies-from-browser", cookies_from_browser]
	return []


def dump_images(url, runner=None, timeout=60,
                cookies=None, cookies_from_browser=None):
	"""Run only gallery-dl --dump-json (no download). Fast — typically a few
	seconds even for IG carousels. Returns the parsed item list with CDN URLs.

	Use this for the /api/info path so the frontend can render <img> tags
	pointing at the CDN directly. fetch_images() runs both stages and is for
	the actual cached download.
	"""
	if runner is None:
		runner = _default_runner
	auth = _auth_args(cookies, cookies_from_browser)
	flatten = ["-o", "extractor.directory=[]"]

	dump_cmd = ["gallery-dl", *auth, *flatten, "--dump-json", "--no-download", url]
	r = runner(dump_cmd, capture_output=True, text=True, timeout=timeout)
	if getattr(r, "returncode", 1) != 0:
		err = (getattr(r, "stderr", "") or "").strip() or "gallery-dl --dump-json failed"
		if _looks_like_auth_failure(err):
			raise RuntimeError(_augment_auth_error(err))
		raise RuntimeError(err)

	return _parse_dump(getattr(r, "stdout", "") or "")


def fetch_images(url, dest_dir, runner=None, timeout=60,
                 cookies=None, cookies_from_browser=None):
	"""Run gallery-dl to enumerate and download images for `url` into dest_dir.

	Two-stage: first --dump-json to learn the item list (cheap, no payload),
	then a -d <dir> invocation to actually pull the bytes. Both go through
	`runner` so tests can substitute a fake.

	Auth: pass `cookies` (path to a Netscape cookies.txt) or
	`cookies_from_browser` (browser name like 'firefox'/'safari'/'chrome'). The
	file path wins if both are set. When auth is unavailable and the site
	bounces us to a login page, raises a RuntimeError with a hint about which
	config keys to set.

	Raises RuntimeError if either invocation fails. Returns a list of
	{filename, url, width, height} dicts.
	"""
	if runner is None:
		runner = _default_runner
	os.makedirs(dest_dir, exist_ok=True)
	auth = _auth_args(cookies, cookies_from_browser)
	# Force flat output: gallery-dl's per-extractor `directory` template would
	# otherwise nest files under e.g. instagram/<username>/.
	flatten = ["-o", "extractor.directory=[]"]

	dump_cmd = ["gallery-dl", *auth, *flatten, "--dump-json", "--no-download", url]
	r = runner(dump_cmd, capture_output=True, text=True, timeout=timeout)
	if getattr(r, "returncode", 1) != 0:
		err = (getattr(r, "stderr", "") or "").strip() or "gallery-dl --dump-json failed"
		if _looks_like_auth_failure(err):
			raise RuntimeError(_augment_auth_error(err))
		raise RuntimeError(err)

	items = _parse_dump(getattr(r, "stdout", "") or "")

	dl_cmd = ["gallery-dl", *auth, *flatten, "-d", dest_dir, "--no-mtime", url]
	r2 = runner(dl_cmd, capture_output=True, text=True, timeout=timeout)
	if getattr(r2, "returncode", 1) != 0:
		err = (getattr(r2, "stderr", "") or "").strip() or "gallery-dl download failed"
		if _looks_like_auth_failure(err):
			raise RuntimeError(_augment_auth_error(err))
		raise RuntimeError(err)

	# Reconcile dump-json metadata against what actually landed on disk.
	# gallery-dl's filename format for Instagram (e.g. "{sidecar_media_id}_
	# {media_id}") does not match dump-json's `filename` field (which is the
	# CDN's source filename), so our /media/<hash>/<filename> URLs would 404
	# without this. We trust on-disk order and zip with dump items.
	on_disk = sorted(
		f for f in os.listdir(dest_dir)
		if os.path.isfile(os.path.join(dest_dir, f))
	)
	if items and len(items) == len(on_disk):
		for it, fname in zip(items, on_disk):
			it["filename"] = fname
	elif on_disk:
		items = [
			{"filename": n, "url": "", "width": None, "height": None}
			for n in on_disk
		]
	return items
