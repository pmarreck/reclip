import json
import pytest
import responses


class TestTranscribe:
	@responses.activate
	def test_transcribe_posts_audio_file(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"fake audio data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "Hello world", "language": "en", "duration": 10.5},
			status=200,
		)

		result = transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper-large-v3-turbo",
			api_key="test-key",
			prompt="",
		)
		assert result["text"] == "Hello world"
		assert result["language"] == "en"
		req = responses.calls[0].request
		assert "Bearer test-key" in req.headers.get("Authorization", "")
		assert b"fake audio data" in req.body

	@responses.activate
	def test_transcribe_returns_segments(self, tmp_path):
		"""oMLX includes per-segment timestamps; we must not discard them —
		the diarization merge step aligns speaker turns against these."""
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		segments = [
			{"start": 0.0, "end": 4.2, "text": "Hello there."},
			{"start": 4.8, "end": 9.1, "text": "And welcome back."},
		]
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "Hello there. And welcome back.", "language": "en",
			      "duration": 9.1, "segments": segments},
			status=200,
		)

		result = transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper",
			api_key="",
			prompt="",
		)
		assert result["segments"] == segments

	@responses.activate
	def test_transcribe_segments_absent_is_none(self, tmp_path):
		"""Older/other servers may omit segments — surface as None, not KeyError."""
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "ok"},
			status=200,
		)
		result = transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper", api_key="", prompt="",
		)
		assert result["segments"] is None

	@responses.activate
	def test_transcribe_no_api_key(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:9999/v1/audio/transcriptions",
			json={"text": "ok"},
			status=200,
		)

		transcribe(
			audio_path=str(audio_file),
			url="http://localhost:9999/v1/audio/transcriptions",
			model="whisper",
			api_key="",
			prompt="",
		)
		req = responses.calls[0].request
		assert "Authorization" not in req.headers

	@responses.activate
	def test_transcribe_error_raises(self, tmp_path):
		from llm_client import transcribe, LLMError
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"error": {"message": "model not loaded"}},
			status=400,
		)

		with pytest.raises(LLMError, match="model not loaded"):
			transcribe(
				audio_path=str(audio_file),
				url="http://localhost:8000/v1/audio/transcriptions",
				model="whisper",
				api_key="",
				prompt="",
			)

	@responses.activate
	def test_processor_not_found_auto_recovery(self, tmp_path):
		"""oMLX 'Processor not found' triggers unload + retry, then succeeds."""
		from llm_client import transcribe
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"audio data")

		# First call: 500 with the recoverable error
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"error": {"message": "Processor not found. Make sure the model was loaded with a HuggingFace processor."}},
			status=500,
		)
		# Unload call
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/models/whisper/unload",
			json={"status": "ok"},
			status=200,
		)
		# Retry call: succeeds
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "Hello after retry", "language": "en"},
			status=200,
		)

		result = transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper",
			api_key="key",
			prompt="",
		)
		assert result["text"] == "Hello after retry"
		urls = [c.request.url for c in responses.calls]
		assert any("/v1/models/whisper/unload" in u for u in urls)

	@responses.activate
	def test_non_recoverable_error_no_retry(self, tmp_path):
		"""Other errors should not trigger the unload-and-retry path."""
		from llm_client import transcribe, LLMError
		audio_file = tmp_path / "audio.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"error": {"message": "Some other error"}},
			status=500,
		)

		with pytest.raises(LLMError, match="Some other error"):
			transcribe(
				audio_path=str(audio_file),
				url="http://localhost:8000/v1/audio/transcriptions",
				model="whisper",
				api_key="",
				prompt="",
			)
		assert len(responses.calls) == 1


class TestChatCompletion:
	@responses.activate
	def test_chat_completion_returns_text(self):
		from llm_client import chat_completion
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"choices": [{"message": {"content": "Summary here"}}]},
			status=200,
		)

		result = chat_completion(
			url="http://localhost:8000/v1/chat/completions",
			model="gemma4",
			api_key="key123",
			system_prompt="Summarize this",
			user_content="Long transcript...",
		)
		assert result == "Summary here"
		req = responses.calls[0].request
		body = json.loads(req.body)
		assert body["model"] == "gemma4"
		assert body["messages"][0]["role"] == "system"
		assert body["messages"][1]["role"] == "user"
		assert "Bearer key123" in req.headers["Authorization"]

	@responses.activate
	def test_chat_completion_no_api_key(self):
		from llm_client import chat_completion
		responses.add(
			responses.POST,
			"http://localhost:11434/v1/chat/completions",
			json={"choices": [{"message": {"content": "ok"}}]},
			status=200,
		)

		chat_completion(
			url="http://localhost:11434/v1/chat/completions",
			model="llama3",
			api_key="",
			system_prompt="prompt",
			user_content="content",
		)
		req = responses.calls[0].request
		assert "Authorization" not in req.headers

	@responses.activate
	def test_chat_completion_error_raises(self):
		from llm_client import chat_completion, LLMError
		responses.add(
			responses.POST,
			"http://localhost:8000/v1/chat/completions",
			json={"error": {"message": "context too long"}},
			status=400,
		)

		with pytest.raises(LLMError, match="context too long"):
			chat_completion(
				url="http://localhost:8000/v1/chat/completions",
				model="gemma4",
				api_key="",
				system_prompt="p",
				user_content="c",
			)


class TestTextToSpeech:
	"""text_to_speech routes voice/instructions fields based on the model so
	oMLX's per-model expectations are met. VoiceDesign needs `instructions`
	(matches OpenAI's gpt-4o-mini-tts wire field name); others use `voice`."""

	@responses.activate
	def test_voicedesign_routes_to_instructions_field(self):
		from llm_client import text_to_speech

		captured = {}

		def callback(req):
			captured.update(json.loads(req.body))
			return (200, {}, b"\x00" * 16)

		responses.add_callback(
			responses.POST,
			"http://localhost:8000/v1/audio/speech",
			callback=callback,
			content_type="audio/wav",
		)

		text_to_speech(
			url="http://localhost:8000/v1/audio/speech",
			model="Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
			text="hello world",
			voice="warm feminine voice with a soft sultry tone",
			api_key="",
		)
		assert captured.get("instructions") == "warm feminine voice with a soft sultry tone"
		# voice field should NOT be set (oMLX rejects with conflicting fields)
		assert "voice" not in captured
		# Old field name must not leak through
		assert "instruct" not in captured

	@responses.activate
	def test_non_voicedesign_uses_voice_field(self):
		from llm_client import text_to_speech

		captured = {}

		def callback(req):
			captured.update(json.loads(req.body))
			return (200, {}, b"\x00" * 16)

		responses.add_callback(
			responses.POST,
			"http://localhost:8000/v1/audio/speech",
			callback=callback,
			content_type="audio/wav",
		)

		text_to_speech(
			url="http://localhost:8000/v1/audio/speech",
			model="Kokoro-82M-bf16",
			text="hello",
			voice="af_bella",
			api_key="",
		)
		assert captured.get("voice") == "af_bella"
		assert "instructions" not in captured
		assert "instruct" not in captured

	@responses.activate
	def test_explicit_instructions_kwarg_wins(self):
		"""If caller passes instructions=, it goes through unchanged regardless of model."""
		from llm_client import text_to_speech

		captured = {}

		def callback(req):
			captured.update(json.loads(req.body))
			return (200, {}, b"\x00" * 16)

		responses.add_callback(
			responses.POST,
			"http://localhost:8000/v1/audio/speech",
			callback=callback,
			content_type="audio/wav",
		)

		text_to_speech(
			url="http://localhost:8000/v1/audio/speech",
			model="some-model",
			text="hi",
			instructions="explicit instructions value",
			api_key="",
		)
		assert captured.get("instructions") == "explicit instructions value"


class TestWordTimestamps:
	"""word_timestamps is an oMLX extension form field (Whisper models only).
	When requested, segments carry words: [{word,start,end,probability}]."""

	@responses.activate
	def test_word_timestamps_sent_as_form_field(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "a.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "ok"},
			status=200,
		)
		transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper", api_key="", prompt="",
			word_timestamps=True,
		)
		body = responses.calls[0].request.body
		# multipart form body — the field name and value appear in the payload
		assert b'name="word_timestamps"' in body
		assert b"true" in body.lower()

	@responses.activate
	def test_word_timestamps_default_off_not_sent(self, tmp_path):
		from llm_client import transcribe
		audio_file = tmp_path / "a.mp3"
		audio_file.write_bytes(b"data")

		responses.add(
			responses.POST,
			"http://localhost:8000/v1/audio/transcriptions",
			json={"text": "ok"},
			status=200,
		)
		transcribe(
			audio_path=str(audio_file),
			url="http://localhost:8000/v1/audio/transcriptions",
			model="whisper", api_key="", prompt="",
		)
		assert b'name="word_timestamps"' not in responses.calls[0].request.body
