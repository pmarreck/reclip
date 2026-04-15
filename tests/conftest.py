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
