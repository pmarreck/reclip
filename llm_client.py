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

_UNAVAILABLE_MODEL_PATTERNS = (
	re.compile(r"\bmodel\b.*\b(?:not loaded|not found|unavailable|unknown|does not exist)\b", re.IGNORECASE),
	re.compile(r"\b(?:not loaded|not found|unavailable|unknown)\b.*\bmodel\b", re.IGNORECASE),
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


def _response_error_message(resp, default):
	"""Extract an OpenAI-compatible error field without discarding plain text."""
	error_msg = default
	try:
		body = resp.json()
		if "error" in body:
			error_msg = body["error"].get("message", str(body["error"]))
	except (ValueError, KeyError):
		error_msg = resp.text
	return error_msg or default


def _has_unavailable_model_error(error_msg):
	return any(p.search(error_msg) for p in _UNAVAILABLE_MODEL_PATTERNS)


def _configured_http_error(resp, default, model, model_hint, api_key_hint):
	"""Add actionable local-config guidance to OpenAI-compatible HTTP errors."""
	error_msg = _response_error_message(resp, default)
	if _has_unavailable_model_error(error_msg):
		error_msg += (
			f" The configured model {model!r} is unavailable from this server; "
			f"load it there or set {model_hint} to a model the endpoint exposes."
		)
	if resp.status_code in (401, 403) and api_key_hint:
		error_msg += f" (set {api_key_hint})"
	return error_msg


def _unreachable_service_error(service_name, url, url_hint, error):
	"""Turn low-level connection failures into a precise backend setup action."""
	return LLMError(
		f"Cannot reach the {service_name} at {url}: {error}. "
		f"Start the server or set {url_hint} to its OpenAI-compatible endpoint."
	)


def _do_transcribe(audio_path, url, model, api_key, prompt, word_timestamps=False):
	"""Single transcription request. Returns (status_code, json_or_text_dict)."""
	headers = {}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	with open(audio_path, "rb") as f:
		files = {"file": (audio_path.split("/")[-1], f, "application/octet-stream")}
		data = {"model": model}
		if prompt:
			data["prompt"] = prompt
		if word_timestamps:
			# oMLX extension (Whisper models): segments gain a words array of
			# {word, start, end, probability} for word-level alignment.
			data["word_timestamps"] = "true"
		resp = requests.post(url, headers=headers, files=files, data=data, timeout=600)
	return resp


def transcribe(audio_path, url, model, api_key="", prompt="", api_key_hint="", word_timestamps=False,
			   model_hint="RECLIP_STT_MODEL", url_hint="RECLIP_STT_URL"):
	"""Post an audio file for transcription via multipart form upload.

	Sends file + model as multipart/form-data to any OpenAI-compatible
	/audio/transcriptions endpoint. Returns dict with text/language/duration.

	Auto-recovers from oMLX's "Processor not found" stale-load bug by
	unloading the model and retrying once.
	"""
	try:
		resp = _do_transcribe(audio_path, url, model, api_key, prompt, word_timestamps)
	except requests.RequestException as e:
		raise _unreachable_service_error("speech-to-text service", url, url_hint, e) from e

	if resp.status_code >= 400:
		error_msg = _response_error_message(resp, "Transcription failed")

		# Auto-recover: oMLX caches "no processor" state until model is reloaded.
		# Unload it and try once more.
		if _is_recoverable(error_msg):
			_logger.warning(
				"STT returned %r — applying oMLX workaround: unloading %s and retrying",
				error_msg, model,
			)
			_try_unload_omlx_model(url, model, api_key)
			try:
				resp = _do_transcribe(audio_path, url, model, api_key, prompt, word_timestamps)
			except requests.RequestException as e:
				raise _unreachable_service_error("speech-to-text service", url, url_hint, e) from e
			if resp.status_code < 400:
				_logger.info("STT auto-recovery succeeded after model reload")
				result = resp.json()
				return {
					"text": result.get("text", ""),
					"language": result.get("language"),
					"duration": result.get("duration"),
					"segments": result.get("segments"),
				}
			# Still failing — re-extract the error message
		raise LLMError(_configured_http_error(
			resp, "Transcription failed", model, model_hint, api_key_hint,
		))

	result = resp.json()
	return {
		"text": result.get("text", ""),
		"language": result.get("language"),
		"duration": result.get("duration"),
		"segments": result.get("segments"),
	}


def chat_completion(url, model, api_key="", system_prompt="", user_content="", api_key_hint="",
					model_hint="RECLIP_SUMMARIZE_MODEL", url_hint="RECLIP_SUMMARIZE_URL"):
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

	try:
		resp = requests.post(url, headers=headers, json=payload, timeout=600)
	except requests.RequestException as e:
		raise _unreachable_service_error("chat service", url, url_hint, e) from e

	if resp.status_code >= 400:
		raise LLMError(_configured_http_error(
			resp, "Chat completion failed", model, model_hint, api_key_hint,
		))

	result = resp.json()
	return result["choices"][0]["message"]["content"]


def text_to_speech(url, model, text, api_key="", voice="", speed=1.0,
				   response_format="wav", api_key_hint="",
				   ref_audio_path="", ref_text="", instructions="",
				   model_hint="RECLIP_TTS_MODEL", url_hint="RECLIP_TTS_URL"):
	"""Generate speech audio from text via OpenAI-compatible TTS endpoint.

	For voice cloning (Qwen3-TTS-Base, F5-TTS, etc.) pass both:
	  - `ref_audio_path`: filesystem path to a short reference clip (~5-10s
	    of clean speech in the target voice)
	  - `ref_text`: the transcript of that reference clip

	The function reads the file, base64-encodes it, and posts it as
	`ref_audio` (oMLX's schema). Both fields are required together.

	For preset-voice models (Kokoro, CustomVoice, OpenAI), pass `voice`
	instead — a string identifier.

	For description-driven models (Qwen3-TTS-VoiceDesign), oMLX requires the
	wire field `instructions` (plural, matching OpenAI's gpt-4o-mini-tts
	API) rather than `voice`. To keep callers simple, `voice=` is auto-routed
	to `instructions=` when the model name contains "VoiceDesign". An explicit
	`instructions=` kwarg always wins.

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
	# Route description string to the correct field per model.
	is_voicedesign = "VoiceDesign" in (model or "")
	effective_instructions = instructions or (voice if is_voicedesign else "")
	effective_voice = "" if is_voicedesign else voice

	if effective_voice:
		payload["voice"] = effective_voice
	if effective_instructions:
		payload["instructions"] = effective_instructions
	if ref_audio_path and ref_text:
		with open(ref_audio_path, "rb") as f:
			payload["ref_audio"] = base64.b64encode(f.read()).decode("ascii")
		payload["ref_text"] = ref_text

	try:
		resp = requests.post(url, headers=headers, json=payload, timeout=600)
	except requests.RequestException as e:
		raise _unreachable_service_error("text-to-speech service", url, url_hint, e) from e

	if resp.status_code >= 400:
		raise LLMError(_configured_http_error(
			resp, "Text-to-speech failed", model, model_hint, api_key_hint,
		))

	return resp.content
