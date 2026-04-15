import requests


class LLMError(Exception):
	pass


def transcribe(audio_path, url, model, api_key="", prompt="", api_key_hint=""):
	"""Post an audio file for transcription via multipart form upload.

	Sends file + model as multipart/form-data to any OpenAI-compatible
	/audio/transcriptions endpoint. Returns dict with text/language/duration.
	"""
	headers = {}
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	with open(audio_path, "rb") as f:
		files = {"file": (audio_path.split("/")[-1], f, "application/octet-stream")}
		data = {"model": model}
		if prompt:
			data["prompt"] = prompt

		resp = requests.post(url, headers=headers, files=files, data=data, timeout=600)

	if resp.status_code >= 400:
		error_msg = "Transcription failed"
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
