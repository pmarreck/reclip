import os
import tempfile
import pytest

# Isolate test config dir before importing app (which loads config at import time).
# Individual tests can still override via monkeypatch + app.cfg = Config() if needed.
_test_config_dir = tempfile.mkdtemp(prefix="reclip-test-config-")
os.environ.setdefault("RECLIP_CONFIG_DIR", _test_config_dir)

from app import app as flask_app


@pytest.fixture
def tmp_cache(tmp_path):
    """Provide a temporary cache directory and set the env var.

    Also reinitializes the app-level cache object so that routes
    use the temp directory instead of the default one.
    """
    import app
    from cache import Cache

    cache_dir = tmp_path / "reclip-cache"
    cache_dir.mkdir()
    os.environ["RECLIP_CACHE_DIR"] = str(cache_dir)
    os.environ["RECLIP_CACHE_MAX_MB"] = "10"

    # Reinitialize the module-level cache so routes use temp dir
    app.cache = Cache(str(cache_dir), max_mb=10)

    yield cache_dir

    os.environ.pop("RECLIP_CACHE_DIR", None)
    os.environ.pop("RECLIP_CACHE_MAX_MB", None)


@pytest.fixture
def client():
    """Flask test client."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
