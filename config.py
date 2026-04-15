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


def _default_cache_dir():
	xdg = os.environ.get("XDG_CACHE_HOME")
	if xdg:
		return os.path.join(xdg, "reclip")
	return os.path.join(os.path.expanduser("~"), ".cache", "reclip")


def load_config():
	return {
		"cache_dir": os.environ.get("RECLIP_CACHE_DIR", _default_cache_dir()),
		"cache_max_mb": int(os.environ.get("RECLIP_CACHE_MAX_MB", "1024")),
		"stt_url": os.environ.get("RECLIP_STT_URL", "http://localhost:8000/v1/audio/transcriptions"),
		"stt_api_key": os.environ.get("RECLIP_STT_API_KEY", ""),
		"stt_model": os.environ.get("RECLIP_STT_MODEL", "mlx-community/whisper-large-v3-turbo"),
		"stt_prompt": os.environ.get("RECLIP_STT_PROMPT", ""),
		"summarize_url": os.environ.get("RECLIP_SUMMARIZE_URL", "http://localhost:8000/v1/chat/completions"),
		"summarize_api_key": os.environ.get("RECLIP_SUMMARIZE_API_KEY", ""),
		"summarize_model": os.environ.get("RECLIP_SUMMARIZE_MODEL", "gemma4-heretical-mlx-8bit"),
		"summarize_prompt": os.environ.get("RECLIP_SUMMARIZE_PROMPT", DEFAULT_SUMMARIZE_PROMPT),
		"translate_url": os.environ.get("RECLIP_TRANSLATE_URL", "http://localhost:8000/v1/chat/completions"),
		"translate_api_key": os.environ.get("RECLIP_TRANSLATE_API_KEY", ""),
		"translate_model": os.environ.get("RECLIP_TRANSLATE_MODEL", "gemma4-heretical-mlx-8bit"),
		"translate_prompt": os.environ.get("RECLIP_TRANSLATE_PROMPT", DEFAULT_TRANSLATE_PROMPT),
	}
