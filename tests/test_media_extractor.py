"""Tests for media_extractor — URL classification + gallery-dl invocation.

The module is a pure helper: classify_url() looks at the hostname only,
fetch_images() accepts an injectable subprocess runner so we don't have
to actually call gallery-dl in tests.
"""
import json
import os
import pytest


class TestClassifyUrl:
    """classify_url returns 'images' for known image-heavy social hosts,
    'video' for known video hosts, and 'unknown' for anything else.

    Hostname matching must be case-insensitive and `www.` prefix-tolerant.
    """

    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/p/ABC123/",
        "https://instagram.com/p/ABC123/",
        "https://www.threads.net/@someone/post/abc",
        "https://threads.net/@someone/post/abc",
        "https://www.threads.com/@someone/post/abc",
        "https://threads.com/@someone/post/abc",
        "https://www.reddit.com/r/pics/comments/abc/title/",
        "https://reddit.com/r/pics/comments/abc/title/",
        "https://old.reddit.com/r/pics/comments/abc/title/",
        "https://x.com/user/status/123",
        "https://twitter.com/user/status/123",
        "https://www.pinterest.com/pin/123/",
        "https://www.tumblr.com/blog/post",
        "https://imgur.com/a/abc",
        "https://www.imgur.com/gallery/xyz",
        "https://www.flickr.com/photos/user/12345/",
        "https://www.deviantart.com/artist/art/title-123",
    ])
    def test_image_hosts_classify_as_images(self, url):
        from media_extractor import classify_url
        assert classify_url(url) == "images", f"{url} should classify as images"

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://vimeo.com/12345",
        "https://soundcloud.com/user/track",
    ])
    def test_video_hosts_classify_as_video_or_unknown(self, url):
        # We let yt-dlp handle these; either "video" or "unknown" is acceptable
        # as long as it's NOT "images".
        from media_extractor import classify_url
        result = classify_url(url)
        assert result != "images", f"{url} must not route to gallery-dl"

    def test_uppercase_host_classifies(self):
        from media_extractor import classify_url
        assert classify_url("https://WWW.INSTAGRAM.COM/p/abc/") == "images"

    def test_unknown_host_returns_unknown_or_video(self):
        from media_extractor import classify_url
        assert classify_url("https://example.com/whatever") in ("unknown", "video")

    def test_invalid_url_returns_unknown(self):
        from media_extractor import classify_url
        # No scheme/host — shouldn't crash; returns "unknown".
        assert classify_url("not a url") == "unknown"


class TestFetchImages:
    """fetch_images runs gallery-dl twice (dump-json then download) and
    returns a list of {filename, url, width, height} item dicts.

    The subprocess runner is injectable so tests don't need a real binary.
    """

    def test_fetch_images_returns_items(self, tmp_path):
        from media_extractor import fetch_images

        # Two simulated posts in a carousel
        dump_payload = [
            ["version", 1, {}],
            ["url", "https://cdn.example.com/img1.jpg",
             {"filename": "img1", "extension": "jpg", "width": 1080, "height": 1080}],
            ["url", "https://cdn.example.com/img2.jpg",
             {"filename": "img2", "extension": "jpg", "width": 1080, "height": 1350}],
        ]

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            r = R()
            if "--dump-json" in cmd:
                r.stdout = json.dumps(dump_payload)
            else:
                # Simulate gallery-dl writing files into the dest dir
                # (parsed from -d <dir>)
                d_idx = cmd.index("-d")
                dest = cmd[d_idx + 1]
                os.makedirs(dest, exist_ok=True)
                for name in ("img1.jpg", "img2.jpg"):
                    p = os.path.join(dest, name)
                    with open(p, "wb") as f:
                        f.write(b"\xff\xd8\xff\xe0fake-jpeg")
            return r

        items = fetch_images(
            "https://www.instagram.com/p/abc/",
            str(tmp_path),
            runner=fake_runner,
        )

        assert len(items) == 2
        assert items[0]["filename"] == "img1.jpg"
        assert items[0]["width"] == 1080
        assert items[0]["height"] == 1080
        assert items[1]["filename"] == "img2.jpg"
        assert items[1]["height"] == 1350
        # Each item must include a URL we can serve locally (or the source URL)
        assert "url" in items[0]

        # We expect two subprocess invocations: --dump-json then a download
        assert any("--dump-json" in c for c in calls)
        assert any("-d" in c for c in calls)

    def test_fetch_images_handles_dump_failure(self, tmp_path):
        from media_extractor import fetch_images

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "ERROR: not authorized"
            return R()

        with pytest.raises(RuntimeError) as exc:
            fetch_images(
                "https://www.instagram.com/p/abc/",
                str(tmp_path),
                runner=fake_runner,
            )
        assert "not authorized" in str(exc.value).lower() or "gallery-dl" in str(exc.value).lower()

    def test_fetch_images_uses_no_shell(self, tmp_path):
        """gallery-dl must be invoked as an arg list, not via shell."""
        from media_extractor import fetch_images

        seen_kwargs = []

        def fake_runner(cmd, **kwargs):
            seen_kwargs.append(kwargs)
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        try:
            fetch_images("https://imgur.com/a/x", str(tmp_path), runner=fake_runner)
        except Exception:
            pass  # Empty dump may raise; we only care about the runner kwargs

        for kw in seen_kwargs:
            assert kw.get("shell") is not True, "shell=True is forbidden"

    def test_fetch_images_passes_timeout(self, tmp_path):
        from media_extractor import fetch_images

        seen = []

        def fake_runner(cmd, **kwargs):
            seen.append(kwargs.get("timeout"))
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        try:
            fetch_images("https://imgur.com/a/x", str(tmp_path), runner=fake_runner, timeout=42)
        except Exception:
            pass

        # Both invocations should pass the timeout through
        assert all(t == 42 for t in seen)

    def test_fetch_images_passes_cookies_file(self, tmp_path):
        """When cookies=<path> is provided, gallery-dl is invoked with --cookies <path>."""
        from media_extractor import fetch_images

        cookies_path = str(tmp_path / "ig_cookies.txt")
        with open(cookies_path, "w") as f:
            f.write("# Netscape\n")

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        fetch_images(
            "https://www.instagram.com/p/abc/",
            str(tmp_path / "out"),
            runner=fake_runner,
            cookies=cookies_path,
        )

        assert len(calls) >= 1
        for cmd in calls:
            assert "--cookies" in cmd, f"--cookies missing from {cmd}"
            i = cmd.index("--cookies")
            assert cmd[i + 1] == cookies_path

    @pytest.mark.parametrize("browser", ["firefox", "chrome", "safari"])
    def test_fetch_images_passes_cookies_from_browser(self, tmp_path, browser):
        """When cookies_from_browser is provided, gallery-dl is invoked with
        --cookies-from-browser <name>."""
        from media_extractor import fetch_images

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        fetch_images(
            "https://www.instagram.com/p/abc/",
            str(tmp_path / "out"),
            runner=fake_runner,
            cookies_from_browser=browser,
        )

        assert len(calls) >= 1
        for cmd in calls:
            assert "--cookies-from-browser" in cmd
            i = cmd.index("--cookies-from-browser")
            assert cmd[i + 1] == browser

    def test_fetch_images_no_auth_args_when_unset(self, tmp_path):
        """No auth flags should appear if neither cookies nor browser is set."""
        from media_extractor import fetch_images

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        fetch_images("https://imgur.com/a/x", str(tmp_path / "out"), runner=fake_runner)

        assert len(calls) >= 1
        for cmd in calls:
            assert "--cookies" not in cmd
            assert "--cookies-from-browser" not in cmd

    def test_dump_images_metadata_only(self, tmp_path):
        """dump_images runs ONE gallery-dl invocation (--dump-json --no-download)
        and returns CDN-pointing items without writing any files. This is what
        /api/info should call so the frontend isn't blocked on full download."""
        from media_extractor import dump_images

        dump_payload = [
            [1, 1, {}],
            [2, {"category": "instagram"}],
            [3, "https://cdn.example.com/img1.jpg",
             {"filename": "ig1", "extension": "jpg", "width": 1080, "height": 1080}],
            [3, "https://cdn.example.com/img2.jpg",
             {"filename": "ig2", "extension": "jpg", "width": 1080, "height": 1350}],
        ]

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = json.dumps(dump_payload) if "--dump-json" in cmd else ""
            return R()

        items = dump_images(
            "https://www.instagram.com/p/abc/",
            runner=fake_runner,
            cookies_from_browser="firefox",
        )

        assert len(items) == 2
        assert items[0]["url"] == "https://cdn.example.com/img1.jpg"
        assert items[0]["width"] == 1080
        assert items[1]["height"] == 1350
        # Exactly ONE invocation: dump-json. No download command.
        assert len(calls) == 1
        assert "--dump-json" in calls[0]
        assert "--no-download" in calls[0]
        # Cookie auth must propagate
        assert "--cookies-from-browser" in calls[0]

    def test_dump_images_login_redirect_raises_friendly_error(self, tmp_path):
        from media_extractor import dump_images

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "[instagram][error] HTTP redirect to login page"
            return R()

        with pytest.raises(RuntimeError) as exc:
            dump_images("https://www.instagram.com/p/abc/", runner=fake_runner)
        msg = str(exc.value)
        assert msg.startswith("Gallery authentication required:")
        assert "instagram.com" in msg
        assert "Firefox, Chrome, or Safari" in msg
        assert "RECLIP_GALLERY_DL_BROWSER" in msg

    def test_auth_error_names_configured_browser_without_usable_login(self):
        from media_extractor import dump_images

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "[instagram][error] HTTP Error 403: Forbidden"
            return R()

        with pytest.raises(RuntimeError) as exc:
            dump_images(
                "https://www.instagram.com/p/abc/",
                runner=fake_runner,
                cookies_from_browser="chrome",
            )

        msg = str(exc.value)
        assert msg.startswith("Gallery authentication required:")
        assert "Chrome" in msg
        assert "not logged in" in msg

    def test_browser_cookie_database_error_explains_configuration(self):
        from media_extractor import dump_images

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "[cookies][error] Unable to find Firefox cookies database"
            return R()

        with pytest.raises(RuntimeError) as exc:
            dump_images(
                "https://www.instagram.com/p/abc/",
                runner=fake_runner,
                cookies_from_browser="firefox",
            )

        msg = str(exc.value)
        assert msg.startswith("Gallery authentication required:")
        assert "Firefox" in msg
        assert "profile" in msg

    def test_fetch_images_flattens_subdirs(self, tmp_path):
        """gallery-dl writes to <dest>/<extractor>/<owner>/ by default. We pass
        -o extractor.directory=[] to force flat output. Both dump and download
        invocations must include this flag."""
        from media_extractor import fetch_images

        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]" if "--dump-json" in cmd else ""
            return R()

        fetch_images(
            "https://www.instagram.com/p/abc/",
            str(tmp_path / "out"),
            runner=fake_runner,
        )

        assert len(calls) >= 2
        for cmd in calls:
            assert "-o" in cmd, f"missing -o in {cmd}"
            i = cmd.index("-o")
            assert cmd[i + 1] == "extractor.directory=[]"

    def test_fetch_images_reconciles_disk_names_to_dump_metadata(self, tmp_path):
        """gallery-dl's on-disk filenames don't match --dump-json's filename
        field for IG (disk: <post_id>_<media_id>.jpg; dump: CDN filename).
        Reconcile by index when counts match: keep disk names, attach width/
        height from dump."""
        from media_extractor import fetch_images

        dump_payload = [
            [1, 1, {}],
            [2, {"category": "instagram"}],
            [3, "https://cdn.example.com/cdn1.jpg",
             {"filename": "ig-cdn-name-1", "extension": "jpg", "width": 1080, "height": 1080}],
            [3, "https://cdn.example.com/cdn2.jpg",
             {"filename": "ig-cdn-name-2", "extension": "jpg", "width": 1080, "height": 1350}],
        ]
        out_dir = tmp_path / "out"

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            r = R()
            if "--dump-json" in cmd:
                r.stdout = json.dumps(dump_payload)
            else:
                # gallery-dl renames on disk: post_id_media_id.jpg
                os.makedirs(out_dir, exist_ok=True)
                for n in ("999_1.jpg", "999_2.jpg"):
                    with open(out_dir / n, "wb") as f:
                        f.write(b"\xff\xd8\xff\xe0fake")
            return r

        items = fetch_images(
            "https://www.instagram.com/p/abc/",
            str(out_dir),
            runner=fake_runner,
        )

        assert len(items) == 2
        # Filenames must match what's actually on disk, not the CDN metadata
        assert items[0]["filename"] == "999_1.jpg"
        assert items[1]["filename"] == "999_2.jpg"
        # But width/height are preserved from the dump (by positional match)
        assert items[0]["width"] == 1080
        assert items[1]["height"] == 1350

    def test_fetch_images_parses_numeric_kind(self, tmp_path):
        """gallery-dl --dump-json emits numeric type codes (3 = downloadable
        URL, 2 = directory, 5 = queue, 1 = version). Must keep type==3 rows."""
        from media_extractor import fetch_images

        # Realistic shape from gallery-dl 1.31.x against an IG carousel.
        dump_payload = [
            [1, 1, {}],  # version
            [2, {"category": "instagram", "count": 2}],  # directory (only 2 elems!)
            [3, "https://cdn.example.com/img1.jpg",
             {"filename": "img1", "extension": "jpg", "width": 1080, "height": 1080}],
            [3, "https://cdn.example.com/img2.jpg",
             {"filename": "img2", "extension": "jpg", "width": 1080, "height": 1350}],
        ]

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 0
                stderr = ""
                stdout = json.dumps(dump_payload) if "--dump-json" in cmd else ""
            return R()

        items = fetch_images(
            "https://www.instagram.com/p/abc/",
            str(tmp_path),
            runner=fake_runner,
        )
        assert len(items) == 2, f"expected 2 items, got {items}"
        assert items[0]["filename"] == "img1.jpg"
        assert items[1]["height"] == 1350

    def test_fetch_images_login_redirect_raises_friendly_error(self, tmp_path):
        """gallery-dl typically prints 'Redirect to login page' or similar when
        auth is required. We surface that with an actionable message."""
        from media_extractor import fetch_images

        def fake_runner(cmd, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "[instagram][error] HTTP redirect to login page"
            return R()

        with pytest.raises(RuntimeError) as exc:
            fetch_images(
                "https://www.instagram.com/p/abc/",
                str(tmp_path / "out"),
                runner=fake_runner,
            )
        msg = str(exc.value).lower()
        assert "login" in msg or "auth" in msg
        # Should hint at the config knob the user needs to flip
        assert "cookie" in msg or "RECLIP_GALLERY_DL" in str(exc.value)


class TestUnsupportedHostMessaging:
    """Threads has no gallery-dl/yt-dlp extractor (June 2026). It must classify
    consistently AND fail with a clear, host-specific message — not a cryptic
    bare 'Unsupported URL'."""

    def test_both_threads_domains_classify_as_images(self):
        from media_extractor import classify_url
        # set-level: both domains route the same so behavior is consistent
        assert classify_url("https://www.threads.com/@a/post/x") == "images"
        assert classify_url("https://www.threads.net/@a/post/x") == "images"

    def test_unsupported_threads_dump_gives_friendly_error(self):
        from media_extractor import dump_images
        def fake_runner(cmd, **kw):
            class R:
                returncode = 1
                stdout = ""
                stderr = "[gallery-dl][error] Unsupported URL 'https://www.threads.com/@a/post/x'"
            return R()
        import pytest
        with pytest.raises(RuntimeError) as ei:
            dump_images("https://www.threads.com/@a/post/x", runner=fake_runner)
        msg = str(ei.value)
        assert "Threads" in msg
        assert "extractor" in msg.lower()  # explains the real reason
        assert "4281" in msg               # points at the tracking issue

    def test_unsupported_hint_only_for_unsupported_errors(self):
        # A non-"unsupported" error on a threads URL passes through unchanged
        from media_extractor import _unsupported_hint
        assert _unsupported_hint("https://www.threads.com/@a/post/x",
                                 "some transient network error") is None

    def test_unsupported_hint_none_for_supported_hosts(self):
        from media_extractor import _unsupported_hint
        # An "unsupported url" on a supported host (hypothetical) isn't masked
        assert _unsupported_hint("https://www.instagram.com/p/abc/",
                                 "Unsupported URL 'x'") is None
