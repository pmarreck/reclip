"""ctypes binding to libspeakrs_ffi — local speaker diarization.

Wraps the C FFI from github:pmarreck/speakrs_ffi (samples in, JSON out).
The library path comes from RECLIP_SPEAKRS_LIB (wired by the nix flake's
devShell and the packaged wrapper). Audio decoding stays out here in
Python-land: decode_to_pcm() shells to ffmpeg for f32le mono 16 kHz, the
same contract every other consumer of the FFI uses.

Memory contract: the FFI returns a heap C string we must free via
speakrs_ffi_free. restype is c_void_p (NOT c_char_p — ctypes would copy
to bytes and drop the pointer, leaking the original).
"""
import ctypes
import json
import os
import subprocess


SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4  # f32le


class DiarizerError(Exception):
	pass


def _lib_path_from_env():
	return os.environ.get("RECLIP_SPEAKRS_LIB", "")


def available():
	"""True when the speakrs FFI library is present (diarization possible)."""
	path = _lib_path_from_env()
	return bool(path) and os.path.isfile(path)


class Diarizer:
	"""Thin, testable wrapper over libspeakrs_ffi.

	`loader` is injectable (defaults to ctypes.CDLL) so unit tests can
	substitute a fake lib without any dylib on disk.
	"""

	def __init__(self, lib_path=None, loader=None):
		path = lib_path or _lib_path_from_env()
		if not path:
			raise DiarizerError(
				"speakrs FFI library not found — set RECLIP_SPEAKRS_LIB to "
				"libspeakrs_ffi.{dylib,so} (the nix dev shell sets this for you)"
			)
		loader = loader or ctypes.CDLL
		try:
			self._lib = loader(path)
		except OSError as e:
			raise DiarizerError(f"failed to load {path}: {e}")

		self._lib.speakrs_ffi_version.restype = ctypes.c_void_p
		self._lib.speakrs_ffi_version.argtypes = []
		self._lib.speakrs_ffi_diarize.restype = ctypes.c_void_p
		self._lib.speakrs_ffi_diarize.argtypes = [
			ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_char_p,
		]
		self._lib.speakrs_ffi_free.restype = None
		self._lib.speakrs_ffi_free.argtypes = [ctypes.c_void_p]

	def version(self):
		ptr = self._lib.speakrs_ffi_version()
		return ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")

	def diarize_samples(self, pcm_f32le, mode=None, models_dir=None):
		"""Diarize raw f32le mono 16 kHz PCM bytes.

		Returns {"segments": [{"start", "end", "speaker"}, ...], "speakers": [...]}.
		Raises DiarizerError on any failure (the FFI reports all errors as
		{"ok": false, "error": ...} — including panics, which never unwind
		across the boundary).
		"""
		if not pcm_f32le:
			raise DiarizerError("empty PCM buffer — nothing to diarize")
		if len(pcm_f32le) % BYTES_PER_SAMPLE != 0:
			raise DiarizerError(
				f"PCM byte length {len(pcm_f32le)} is not a multiple of 4 "
				"(expected raw f32le samples)"
			)

		num_samples = len(pcm_f32le) // BYTES_PER_SAMPLE
		buf = (ctypes.c_float * num_samples).from_buffer_copy(pcm_f32le)

		opts = None
		if mode or models_dir:
			o = {}
			if mode:
				o["mode"] = mode
			if models_dir:
				o["models_dir"] = models_dir
			opts = json.dumps(o).encode("utf-8")

		ptr = self._lib.speakrs_ffi_diarize(buf, num_samples, opts)
		if not ptr:
			raise DiarizerError("speakrs_ffi_diarize returned NULL (contract violation)")
		try:
			raw = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
		finally:
			self._lib.speakrs_ffi_free(ptr)

		result = json.loads(raw)
		if not result.get("ok"):
			raise DiarizerError(result.get("error", "unknown diarization error"))
		return {
			"segments": result.get("segments", []),
			"speakers": result.get("speakers", []),
		}


def decode_to_pcm(media_path, ffmpeg_bin="ffmpeg"):
	"""Decode any ffmpeg-readable media to raw f32le mono 16 kHz PCM bytes."""
	proc = subprocess.run(
		[ffmpeg_bin, "-v", "error", "-i", media_path,
		 "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
		capture_output=True, timeout=600,
	)
	if proc.returncode != 0:
		raise DiarizerError(
			f"ffmpeg failed to decode {media_path}: "
			f"{proc.stderr.decode('utf-8', 'replace').strip()}"
		)
	return proc.stdout


def diarize_file(media_path, mode=None, models_dir=None, diarizer=None):
	"""Convenience: decode a media file and diarize it."""
	d = diarizer or Diarizer()
	return d.diarize_samples(decode_to_pcm(media_path), mode=mode, models_dir=models_dir)
