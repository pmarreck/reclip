import logging
import re
import sys
import requests
from urllib.parse import urlparse


_logger = logging.getLogger("reclip.llm_client")
if not _logger.handlers:
	_h = logging.StreamHandler(sys.stderr)
	_h.setFormatter(logging.Formatter("[reclip] %(message)s"))
	_logger.addHandler(_h)
	_logger.setLevel(logging.INFO)


class LLMError(Exception):
	pass


# oMLX-specific transient errors that resolve after unloading the model
_OMLX_RECOVERABLE_PATTERNS = (
	re.compile(r"processor not found", re.IGNORECASE),
)


def _try_unload_omlx_model(api_url, model, api_key):
	"""Best-effort POST to oMLX's /v1/models/{model}/unload to clear stale state.

	Derived from any /v1/* endpoint URL. Silently no-ops on non-oMLX servers
	or if the request fails — this is a recovery hook, not a hard requirement.
	"""
	try:
		parsed = urlparse(api_url)
		base = f"{parsed.scheme}://{parsed.netloc}"
		unload_url = f"{base}/v1/models/{model}/unload"
		headers = {}
		if api_key:
			headers["Authorization"] = f"Bearer {api_key}"
		requests.post(unload_url, headers=headers, timeout=10)
	except Exception:
		pass


def _is_recoverable(error_msg):
	return any(p.search(error_msg) for p in _OMLX_RECOVERABLE_PATTERNS)


def _do_transcribe(audio_path, url, model, api_key, prompt):
	"""Single transcription request. Returns (status_code, json_or_text_dict)."""
	headers = {}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	with open(audio_path, "rb") as f:
		files = {"file": (audio_path.split("/")[-1], f, "application/octet-stream")}
		data = {"model": model}
		if prompt:
			data["prompt"] = prompt
		resp = requests.post(url, headers=headers, files=files, data=data, timeout=600)
	return resp


def transcribe(audio_path, url, model, api_key="", prompt="", api_key_hint=""):
	"""Post an audio file for transcription via multipart form upload.

	Sends file + model as multipart/form-data to any OpenAI-compatible
	/audio/transcriptions endpoint. Returns dict with text/language/duration.

	Auto-recovers from oMLX's "Processor not found" stale-load bug by
	unloading the model and retrying once.
	"""
	resp = _do_transcribe(audio_path, url, model, api_key, prompt)

	if resp.status_code >= 400:
		error_msg = "Transcription failed"
		try:
			body = resp.json()
			if "error" in body:
				error_msg = body["error"].get("message", str(body["error"]))
		except (ValueError, KeyError):
			error_msg = resp.text

		# Auto-recover: oMLX caches "no processor" state until model is reloaded.
		# Unload it and try once more.
		if _is_recoverable(error_msg):
			_logger.warning(
				"STT returned %r — applying oMLX workaround: unloading %s and retrying",
				error_msg, model,
			)
			_try_unload_omlx_model(url, model, api_key)
			resp = _do_transcribe(audio_path, url, model, api_key, prompt)
			if resp.status_code < 400:
				_logger.info("STT auto-recovery succeeded after model reload")
				result = resp.json()
				return {
					"text": result.get("text", ""),
					"language": result.get("language"),
					"duration": result.get("duration"),
				}
			# Still failing — re-extract the error message
			try:
				body = resp.json()
				if "error" in body:
					error_msg = body["error"].get("message", str(body["error"]))
			except (ValueError, KeyError):
				error_msg = resp.text

		if resp.status_code in (401, 403) and api_key_hint:
			error_msg += f" (set {api_key_hint})"
		raise LLMError(error_msg)

	result = resp.json()
	return {
		"text": result.get("text", ""),
		"language": result.get("language"),
		"duration": result.get("duration"),
	}


def chat_completion(url, model, api_key="", system_prompt="", user_content="", api_key_hint=""):
	"""Send a chat completion request to any OpenAI-compatible endpoint.

	Builds a two-message conversation (system + user) as JSON and returns
	the assistant's text content from choices[0].message.content.
	"""
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
		if resp.status_code in (401, 403) and api_key_hint:
			error_msg += f" (set {api_key_hint})"
		raise LLMError(error_msg)

	result = resp.json()
	return result["choices"][0]["message"]["content"]


def text_to_speech(url, model, text, api_key="", voice="", speed=1.0,
                   response_format="wav", api_key_hint="",
                   ref_audio_path="", ref_text=""):
	"""Generate speech audio from text via OpenAI-compatible TTS endpoint.

	For voice cloning (Qwen3-TTS-Base, F5-TTS, etc.) pass both:
	  - `ref_audio_path`: filesystem path to a short reference clip (~5-10s
	    of clean speech in the target voice)
	  - `ref_text`: the transcript of that reference clip

	The function reads the file, base64-encodes it, and posts it as
	`ref_audio` (oMLX's schema). Both fields are required together.

	For preset-voice models (Kokoro, CustomVoice, OpenAI), pass `voice`
	instead — a string identifier.

	Returns raw audio bytes.
	"""
	import base64

	headers = {"Content-Type": "application/json"}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	payload = {
		"model": model,
		"input": text,
		"speed": speed,
		"response_format": response_format,
	}
	if voice:
		payload["voice"] = voice
	if ref_audio_path and ref_text:
		with open(ref_audio_path, "rb") as f:
			payload["ref_audio"] = base64.b64encode(f.read()).decode("ascii")
		payload["ref_text"] = ref_text

	resp = requests.post(url, headers=headers, json=payload, timeout=600)

	if resp.status_code >= 400:
		error_msg = "Text-to-speech failed"
		try:
			body = resp.json()
			if "error" in body:
				error_msg = body["error"].get("message", str(body["error"]))
		except (ValueError, KeyError):
			error_msg = resp.text
		if resp.status_code in (401, 403) and api_key_hint:
			error_msg += f" (set {api_key_hint})"
		raise LLMError(error_msg)

	return resp.content
