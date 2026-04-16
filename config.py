import os

DEFAULT_SUMMARIZE_PROMPT = (
	"Please summarize the most pertinent elements of the following transcript "
	"or narrative. If it (or any part of it) presents a list of things "
	"(questions, points, tasks, steps, sequential events, etc.), please list "
	"those out without collapsing them further. If there is an issue with the "
	"content (such as it appearing to be missing), mention that prefixed with "
	"'Problem: '. Don't comment on the summary itself. If there is a metadata "
	"section, output it verbatim at the top of the summary."
)

DEFAULT_TRANSLATE_PROMPT = (
	"You are an expert translator. Please translate the following into "
	"{language}. For idioms, words, or expressions that do not translate "
	"perfectly: (1) Make your best translation attempt (2) Add footnotes with "
	"explanations in both the source and target languages. Do not output "
	"anything but the translation and footnotes. If there is an unresolvable "
	"issue, mention it prefixed with 'Problem: ' in both languages. Preserve "
	"all formatting."
)

DEFAULT_COUNTERARGUE_PROMPT = (
	"You are a critical thinker and skilled debater. Analyze the following "
	"transcript and identify claims, arguments, or assertions that can be "
	"challenged. For each counterarguable point: (1) State the original claim "
	"(2) Present the strongest counterargument with evidence or reasoning "
	"(3) Note the strength of the counterargument (strong, moderate, weak). "
	"Be fair and intellectually honest — distinguish between factual errors, "
	"logical fallacies, unsupported claims, and matters of legitimate debate. "
	"If the content is purely factual reporting, a tutorial, music, or "
	"otherwise not appropriate to counterargue, state that clearly and explain "
	"why. Do not manufacture controversy where none exists."
)


def _default_cache_dir():
	xdg = os.environ.get("XDG_CACHE_HOME")
	if xdg:
		return os.path.join(xdg, "reclip")
	return os.path.join(os.path.expanduser("~"), ".cache", "reclip")


def load_config():
	# Common API key fallback — set once, used by all backends unless overridden
	common_key = os.environ.get("RECLIP_API_KEY", "")

	return {
		"cache_dir": os.environ.get("RECLIP_CACHE_DIR", _default_cache_dir()),
		"cache_max_mb": int(os.environ.get("RECLIP_CACHE_MAX_MB", "1024")),
		"stt_url": os.environ.get("RECLIP_STT_URL", "http://localhost:8000/v1/audio/transcriptions"),
		"stt_api_key": os.environ.get("RECLIP_STT_API_KEY", common_key),
		"stt_model": os.environ.get("RECLIP_STT_MODEL", "whisper-large-v3-turbo-8bit"),
		"stt_prompt": os.environ.get("RECLIP_STT_PROMPT", ""),
		"summarize_url": os.environ.get("RECLIP_SUMMARIZE_URL", "http://localhost:8000/v1/chat/completions"),
		"summarize_api_key": os.environ.get("RECLIP_SUMMARIZE_API_KEY", common_key),
		"summarize_model": os.environ.get("RECLIP_SUMMARIZE_MODEL", "gemma4-heretical-mlx-8bit"),
		"summarize_prompt": os.environ.get("RECLIP_SUMMARIZE_PROMPT", DEFAULT_SUMMARIZE_PROMPT),
		"translate_url": os.environ.get("RECLIP_TRANSLATE_URL", "http://localhost:8000/v1/chat/completions"),
		"translate_api_key": os.environ.get("RECLIP_TRANSLATE_API_KEY", common_key),
		"translate_model": os.environ.get("RECLIP_TRANSLATE_MODEL", "gemma4-heretical-mlx-8bit"),
		"translate_prompt": os.environ.get("RECLIP_TRANSLATE_PROMPT", DEFAULT_TRANSLATE_PROMPT),
		"counterargue_url": os.environ.get("RECLIP_COUNTERARGUE_URL", os.environ.get("RECLIP_SUMMARIZE_URL", "http://localhost:8000/v1/chat/completions")),
		"counterargue_api_key": os.environ.get("RECLIP_COUNTERARGUE_API_KEY", common_key),
		"counterargue_model": os.environ.get("RECLIP_COUNTERARGUE_MODEL", os.environ.get("RECLIP_SUMMARIZE_MODEL", "gemma4-heretical-mlx-8bit")),
		"counterargue_prompt": os.environ.get("RECLIP_COUNTERARGUE_PROMPT", DEFAULT_COUNTERARGUE_PROMPT),
		"tts_url": os.environ.get("RECLIP_TTS_URL", "http://localhost:8000/v1/audio/speech"),
		"tts_api_key": os.environ.get("RECLIP_TTS_API_KEY", common_key),
		"tts_model": os.environ.get("RECLIP_TTS_MODEL", "Qwen3-TTS-12Hz-1.7B-Base-8bit"),
		"tts_voice": os.environ.get("RECLIP_TTS_VOICE", ""),
		"tts_speed": float(os.environ.get("RECLIP_TTS_SPEED", "1.0")),
	}
