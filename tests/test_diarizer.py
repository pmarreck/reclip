"""Tests for diarizer.py — ctypes binding to libspeakrs_ffi.

Unit tests inject a fake loader so no dylib, models, or network are needed.
The fake returns REAL C pointers (via ctypes buffers kept alive) so the
binding's cast/free pointer handling is exercised for real.

A small "real lib" tier runs error paths against the actual dylib when
RECLIP_SPEAKRS_LIB is set (dev shell) — still no models/network.
"""
import ctypes
import json
import os
import pytest


class FakeSpeakrsLib:
	"""Mimics ctypes.CDLL(libspeakrs_ffi) closely enough for the binding.

	Returns genuine C pointers (string buffers kept alive on self) so that
	ctypes.cast() in the code under test operates on real memory.
	"""

	def __init__(self, response=None, version=b"9.9.9"):
		self._response = response  # bytes JSON the diarize call returns
		self._keepalive = []
		self.calls = []
		self.freed = []
		self._version_buf = ctypes.create_string_buffer(version)

		# The binding assigns .restype/.argtypes; plain attrs absorb that.
		def version_fn():
			return ctypes.cast(self._version_buf, ctypes.c_void_p).value

		def diarize_fn(samples, num_samples, opts):
			opts_bytes = ctypes.cast(opts, ctypes.c_char_p).value if opts else None
			self.calls.append({"num_samples": num_samples, "opts": opts_bytes})
			buf = ctypes.create_string_buffer(self._response)
			self._keepalive.append(buf)
			return ctypes.cast(buf, ctypes.c_void_p).value

		def free_fn(ptr):
			self.freed.append(ptr)

		self.speakrs_ffi_version = _FakeFn(version_fn)
		self.speakrs_ffi_diarize = _FakeFn(diarize_fn)
		self.speakrs_ffi_free = _FakeFn(free_fn)


class _FakeFn:
	"""Callable that tolerates .restype / .argtypes assignment like a ctypes fn."""

	def __init__(self, fn):
		self._fn = fn
		self.restype = None
		self.argtypes = None

	def __call__(self, *args):
		return self._fn(*args)


SUCCESS_JSON = json.dumps({
	"ok": True,
	"segments": [
		{"start": 0.5, "end": 4.0, "speaker": "SPEAKER_00"},
		{"start": 5.0, "end": 9.5, "speaker": "SPEAKER_01"},
	],
	"speakers": ["SPEAKER_00", "SPEAKER_01"],
}).encode()

ERROR_JSON = json.dumps({"ok": False, "error": "failed to load models from /nope"}).encode()


def make_diarizer(response=SUCCESS_JSON, **kw):
	from diarizer import Diarizer
	fake = FakeSpeakrsLib(response=response)
	d = Diarizer(lib_path="/fake/libspeakrs_ffi.dylib", loader=lambda path: fake, **kw)
	return d, fake


class TestDiarizeSamples:
	def test_returns_parsed_turns(self):
		d, fake = make_diarizer()
		pcm = b"\x00" * (16000 * 4)  # 1s of f32 zeros
		result = d.diarize_samples(pcm)
		assert result["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
		assert result["segments"][0]["speaker"] == "SPEAKER_00"
		assert fake.calls[0]["num_samples"] == 16000

	def test_frees_result_pointer_exactly_once(self):
		d, fake = make_diarizer()
		d.diarize_samples(b"\x00" * 4)
		assert len(fake.freed) == 1
		assert fake.freed[0] is not None

	def test_frees_even_on_error_result(self):
		d, fake = make_diarizer(response=ERROR_JSON)
		from diarizer import DiarizerError
		with pytest.raises(DiarizerError, match="failed to load models"):
			d.diarize_samples(b"\x00" * 4)
		assert len(fake.freed) == 1

	def test_opts_carry_mode_and_models_dir(self):
		d, fake = make_diarizer()
		d.diarize_samples(b"\x00" * 4, mode="cpu", models_dir="/m dir/with spaces")
		opts = json.loads(fake.calls[0]["opts"])
		assert opts["mode"] == "cpu"
		assert opts["models_dir"] == "/m dir/with spaces"

	def test_no_opts_passes_null(self):
		d, fake = make_diarizer()
		d.diarize_samples(b"\x00" * 4)
		assert fake.calls[0]["opts"] is None

	def test_pcm_not_multiple_of_4_raises(self):
		d, fake = make_diarizer()
		from diarizer import DiarizerError
		with pytest.raises(DiarizerError, match="multiple of 4"):
			d.diarize_samples(b"\x00" * 5)
		assert fake.calls == []  # never reached the lib

	def test_empty_pcm_raises(self):
		d, fake = make_diarizer()
		from diarizer import DiarizerError
		with pytest.raises(DiarizerError, match="empty"):
			d.diarize_samples(b"")

	def test_version(self):
		d, fake = make_diarizer()
		assert d.version() == "9.9.9"


class TestLibDiscovery:
	def test_missing_lib_path_raises_helpful_error(self, monkeypatch):
		monkeypatch.delenv("RECLIP_SPEAKRS_LIB", raising=False)
		from diarizer import Diarizer, DiarizerError
		with pytest.raises(DiarizerError, match="RECLIP_SPEAKRS_LIB"):
			Diarizer()

	def test_env_var_used_when_no_explicit_path(self, monkeypatch):
		monkeypatch.setenv("RECLIP_SPEAKRS_LIB", "/from/env.dylib")
		from diarizer import Diarizer
		seen = {}
		fake = FakeSpeakrsLib(response=SUCCESS_JSON)

		def loader(path):
			seen["path"] = path
			return fake

		Diarizer(loader=loader)
		assert seen["path"] == "/from/env.dylib"

	def test_available_reflects_env(self, monkeypatch, tmp_path):
		from diarizer import available
		monkeypatch.delenv("RECLIP_SPEAKRS_LIB", raising=False)
		assert available() is False
		lib = tmp_path / "lib.dylib"
		lib.write_bytes(b"")
		monkeypatch.setenv("RECLIP_SPEAKRS_LIB", str(lib))
		assert available() is True


# --- real-lib tier: ABI/pointer handling against the actual dylib ---------
# No models, no network — exercises load, version(), and the error path.

_REAL_LIB = os.environ.get("RECLIP_SPEAKRS_LIB", "")
needs_real_lib = pytest.mark.skipif(
	not (_REAL_LIB and os.path.isfile(_REAL_LIB)),
	reason="RECLIP_SPEAKRS_LIB not set (enter nix develop)",
)


@needs_real_lib
class TestRealLib:
	def test_version_is_semverish(self):
		from diarizer import Diarizer
		v = Diarizer().version()
		assert v and v[0].isdigit()

	def test_bad_models_dir_raises_not_crashes(self):
		from diarizer import Diarizer, DiarizerError
		d = Diarizer()
		with pytest.raises(DiarizerError, match="models_dir"):
			d.diarize_samples(b"\x00" * (16000 * 4), mode="cpu",
			                  models_dir="/nonexistent/speakrs/models")
