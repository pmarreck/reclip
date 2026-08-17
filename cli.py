#!/usr/bin/env python3
"""ReClip command-line interface for the web application's media workflows."""

import argparse
import platform
import sys


def _workflows():
	"""Load web workflow adapters only for commands that need configured services."""
	import app
	return app


def _format_duration(seconds):
	if not seconds:
		return "Unknown"
	seconds = int(seconds)
	hours, remainder = divmod(seconds, 3600)
	minutes, secs = divmod(remainder, 60)
	if hours:
		return f"{hours}:{minutes:02d}:{secs:02d}"
	return f"{minutes}:{secs:02d}"


def _parse_params(raw_params):
	"""Turn repeatable NAME=VALUE flags into the action registry's parameter map."""
	params = {}
	for raw in raw_params:
		name, separator, value = raw.partition("=")
		if not separator or not name:
			raise ValueError("--param must be NAME=VALUE")
		if name in params:
			raise ValueError(f"--param was provided more than once: {name}")
		params[name] = value
	return params


def cmd_info(args):
	"""Fetch video metadata without downloading the media."""
	info = _workflows().info_url(args.url)
	duration = info.get("duration", 0) or 0
	print(f"Title:    {info.get('title', 'N/A')}")
	print(f"Uploader: {info.get('uploader', 'N/A')}")
	print(f"Duration: {_format_duration(duration)} ({duration}s)")
	heights = sorted({fmt["height"] for fmt in info.get("formats", []) if fmt.get("height")}, reverse=True)
	if heights:
		print(f"Quality:  {', '.join(f'{height}p' for height in heights)}")
	print()
	return 0


def cmd_download(args):
	"""Download through the web workflow, including its YouTube audio fallback."""
	result = _workflows().download_url(args.url, args.format, args.format_id)
	print(result["file"])
	return 0


def cmd_transcribe(args):
	"""Print the cached-or-new timestamped transcript."""
	print(_workflows().transcribe_url(args.url))
	return 0


def cmd_speakers(args):
	"""Print the opt-in diarized transcript with speaker labels."""
	print(_workflows().diarize_url(args.url))
	return 0


def _action_field(action, name, default=None):
	if isinstance(action, dict):
		return action.get(name, default)
	return getattr(action, name, default)


def cmd_actions(_args):
	"""List configured hot-reloadable actions and their accepted parameters."""
	for action in _workflows().list_actions():
		params = _action_field(action, "params", [])
		param_names = [
			_action_field(param, "name", str(param))
			for param in params
		]
		details = f" [params: {', '.join(param_names)}]" if param_names else ""
		print(
			f"{_action_field(action, 'id')}: "
			f"{_action_field(action, 'name')} "
			f"(source: {_action_field(action, 'source')}){details}"
		)
	return 0


def cmd_action(args):
	"""Run a configured action through the same source-chain resolver as the UI."""
	print(_workflows().action_url(args.url, args.action_id, _parse_params(args.param)))
	return 0


def cmd_summarize(args):
	"""Keep the established summary shortcut as a built-in action invocation."""
	print(_workflows().action_url(args.url, "summarize", {}))
	return 0


def cmd_translate(args):
	"""Keep the established translation shortcut while using the action registry."""
	action_id = "translate" if args.source == "summary" else "translate_transcript"
	print(_workflows().action_url(args.url, action_id, {"language": args.language}))
	return 0


def cmd_speak(args):
	"""Synthesize a cached text artifact using the web TTS workflow."""
	result = _workflows().speak_url(args.url, args.source, args.voice, args.format)
	print(result["file"])
	return 0


def cmd_cache(_args):
	"""Show the shared cache's current usage."""
	stats = _workflows().cache.stats()
	print(f"Location:  {stats['location']}")
	print(f"Used:      {stats['used_mb']} MB / {stats['max_mb']} MB")
	print(f"Entries:   {stats['entry_count']}")
	return 0


def cmd_config(_args):
	"""Show configured endpoint and model names without revealing API keys."""
	cfg = _workflows().cfg
	for name in ("cache_dir", "cache_max_mb", "stt_url", "stt_model", "summarize_url",
			"summarize_model", "tts_url", "tts_model"):
		print(f"{name}: {cfg[name]}")
	return 0


def build_parser():
	parser = argparse.ArgumentParser(
		description="ReClip CLI - run the web application's media workflows from a shell",
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	parser.add_argument(
		"--about", action="version",
		version=f"ReClip CLI 0.1.0 ({platform.system()} {platform.machine()})",
	)
	sub = parser.add_subparsers(dest="command", help="Available commands")

	p_info = sub.add_parser("info", help="Fetch video metadata")
	p_info.add_argument("url", help="Video URL")

	p_download = sub.add_parser("download", help="Download media via the shared web workflow")
	p_download.add_argument("url", help="Video URL")
	p_download.add_argument("--format", choices=("video", "audio"), default="video")
	p_download.add_argument("--format-id", help="Optional yt-dlp format id")

	p_transcribe = sub.add_parser("transcribe", help="Transcribe a video")
	p_transcribe.add_argument("url", help="Video URL")

	p_speakers = sub.add_parser("speakers", help="Diarize a video and label speakers")
	p_speakers.add_argument("url", help="Video URL")

	sub.add_parser("actions", help="List configured LLM actions")
	p_action = sub.add_parser("action", help="Run a configured LLM action")
	p_action.add_argument("url", help="Video URL")
	p_action.add_argument("action_id", help="Action id from `reclip actions`")
	p_action.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")

	p_summarize = sub.add_parser("summarize", help="Summarize a video's transcript")
	p_summarize.add_argument("url", help="Video URL")

	p_translate = sub.add_parser("translate", help="Translate a transcript or summary")
	p_translate.add_argument("url", help="Video URL")
	p_translate.add_argument("language", help="Target language (for example Spanish)")
	p_translate.add_argument("--source", choices=("transcript", "summary"), default="transcript")

	p_speak = sub.add_parser("speak", help="Synthesize a cached result as speech")
	p_speak.add_argument("url", help="Video URL")
	p_speak.add_argument("--source", default="summary", help="Cached source name (default: summary)")
	p_speak.add_argument("--voice", default="", help="Voice override")
	p_speak.add_argument("--format", choices=("wav", "mp3"), default="wav")

	sub.add_parser("cache", help="Show cache stats")
	sub.add_parser("config", help="Show backend configuration without API keys")
	return parser


def main(argv=None):
	parser = build_parser()
	args = parser.parse_args(argv)
	if not args.command:
		parser.print_help()
		return 1

	commands = {
		"info": cmd_info,
		"download": cmd_download,
		"transcribe": cmd_transcribe,
		"speakers": cmd_speakers,
		"actions": cmd_actions,
		"action": cmd_action,
		"summarize": cmd_summarize,
		"translate": cmd_translate,
		"speak": cmd_speak,
		"cache": cmd_cache,
		"config": cmd_config,
	}

	try:
		return commands[args.command](args)
	except ValueError as e:
		print(f"Error: {e}", file=sys.stderr)
		return 2
	except KeyboardInterrupt:
		print("\nInterrupted", file=sys.stderr)
		return 130
	except Exception as e:
		print(f"Error: {e}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
