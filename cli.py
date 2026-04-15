#!/usr/bin/env python3
"""ReClip CLI — transcribe, summarize, translate, and download media from URLs."""

import argparse
import json
import os
import subprocess
import sys
import time

from config import load_config
from cache import Cache
from llm_client import transcribe as llm_transcribe, chat_completion, LLMError


cfg = load_config()
cache = Cache(cfg["cache_dir"], cfg["cache_max_mb"])


def _format_duration(seconds):
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fetch_and_cache_metadata(url):
    meta = cache.read_meta(url)
    if meta.get("title"):
        return meta
    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    meta = {
                        "title": info.get("title", "Unknown"),
                        "uploader": info.get("uploader", info.get("channel", "Unknown")),
                        "upload_date": info.get("upload_date", "Unknown"),
                        "duration": info.get("duration", 0),
                        "url": url,
                    }
                    cache._write_meta(url, meta)
                    return meta
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return {"title": "Unknown", "uploader": "Unknown", "upload_date": "Unknown", "duration": 0, "url": url}


def _metadata_header(url):
    meta = _fetch_and_cache_metadata(url)
    return (
        f"=== Video Metadata ===\n"
        f"Title: {meta.get('title', 'Unknown')}\n"
        f"Channel: {meta.get('uploader', 'Unknown')}\n"
        f"Upload Date: {meta.get('upload_date', 'Unknown')}\n"
        f"Duration: {_format_duration(meta.get('duration'))}\n"
        f"URL: {meta.get('url', url)}\n"
        f"=== Transcript ===\n\n"
    )


def _ensure_audio(url):
    """Ensure audio.mp3 exists in cache. Downloads via yt-dlp if needed."""
    if cache.has_file(url, "audio.mp3"):
        print(f"  audio: cached", file=sys.stderr)
        return cache.entry_path(url, "audio.mp3")

    print(f"  downloading audio...", file=sys.stderr)
    cmd = [
        "yt-dlp", "--no-playlist", "-x", "--audio-format", "mp3",
        "-o", cache.entry_path(url, "audio.%(ext)s"), url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().split("\n")[-1])

    audio_path = cache.entry_path(url, "audio.mp3")
    if not os.path.isfile(audio_path):
        entry_dir = os.path.dirname(audio_path)
        for f in os.listdir(entry_dir):
            if f.startswith("audio."):
                os.rename(os.path.join(entry_dir, f), audio_path)
                break

    if not os.path.isfile(audio_path):
        raise RuntimeError("Audio download completed but no file found")
    return audio_path


def _ensure_transcript(url):
    """Ensure transcript.txt exists in cache. Transcribes if needed."""
    cached = cache.read_text(url, "transcript.txt")
    if cached is not None:
        print(f"  transcript: cached", file=sys.stderr)
        return cached

    audio_path = _ensure_audio(url)
    print(f"  transcribing...", file=sys.stderr)
    result = llm_transcribe(
        audio_path=audio_path,
        url=cfg["stt_url"],
        model=cfg["stt_model"],
        api_key=cfg["stt_api_key"],
        prompt=cfg["stt_prompt"],
        api_key_hint="RECLIP_STT_API_KEY or RECLIP_API_KEY",
    )
    text = result["text"]
    full = _metadata_header(url) + text
    cache.write_text(url, "transcript.txt", full)
    return full


def _ensure_summary(url):
    """Ensure summary.txt exists in cache. Summarizes if needed."""
    cached = cache.read_text(url, "summary.txt")
    if cached is not None:
        print(f"  summary: cached", file=sys.stderr)
        return cached

    transcript = _ensure_transcript(url)
    print(f"  summarizing...", file=sys.stderr)
    summary = chat_completion(
        url=cfg["summarize_url"],
        model=cfg["summarize_model"],
        api_key=cfg["summarize_api_key"],
        system_prompt=cfg["summarize_prompt"],
        user_content=transcript,
        api_key_hint="RECLIP_SUMMARIZE_API_KEY or RECLIP_API_KEY",
    )
    cache.write_text(url, "summary.txt", summary)
    return summary


def cmd_info(args):
    """Fetch video metadata."""
    cmd = ["yt-dlp", "--no-playlist", "-j", args.url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 1

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        info = json.loads(line)
        dur = info.get("duration", 0) or 0
        print(f"Title:    {info.get('title', 'N/A')}")
        print(f"Uploader: {info.get('uploader', 'N/A')}")
        print(f"Duration: {dur/60:.1f} min ({dur}s)")
        fmts = [f for f in info.get("formats", []) if f.get("height")]
        heights = sorted(set(f["height"] for f in fmts), reverse=True)
        if heights:
            print(f"Quality:  {', '.join(str(h) + 'p' for h in heights)}")
        print()
    return 0


def cmd_transcribe(args):
    """Transcribe audio from URL."""
    print(f"Transcribing: {args.url}", file=sys.stderr)
    text = _ensure_transcript(args.url)
    print(text)
    return 0


def cmd_summarize(args):
    """Summarize a URL's content."""
    print(f"Summarizing: {args.url}", file=sys.stderr)
    summary = _ensure_summary(args.url)
    print(summary)
    return 0


def cmd_translate(args):
    """Translate transcript or summary."""
    source = args.source
    language = args.language
    lang_slug = language.lower().strip().replace(" ", "-")
    filename = f"summary-{lang_slug}.txt" if source == "summary" else f"translation-{lang_slug}.txt"

    cached = cache.read_text(args.url, filename)
    if cached is not None:
        print(f"  translation: cached", file=sys.stderr)
        print(cached)
        return 0

    print(f"Translating {source} to {language}: {args.url}", file=sys.stderr)

    if source == "summary":
        source_text = _ensure_summary(args.url)
    else:
        source_text = _ensure_transcript(args.url)

    print(f"  translating...", file=sys.stderr)
    prompt = cfg["translate_prompt"].replace("{language}", language)
    translated = chat_completion(
        url=cfg["translate_url"],
        model=cfg["translate_model"],
        api_key=cfg["translate_api_key"],
        system_prompt=prompt,
        user_content=source_text,
        api_key_hint="RECLIP_TRANSLATE_API_KEY or RECLIP_API_KEY",
    )
    cache.write_text(args.url, filename, translated)
    print(translated)
    return 0


def cmd_cache(args):
    """Show cache stats."""
    stats = cache.stats()
    print(f"Location:  {stats['location']}")
    print(f"Used:      {stats['used_mb']} MB / {stats['max_mb']} MB")
    print(f"Entries:   {stats['entry_count']}")
    return 0


def cmd_config(args):
    """Show current configuration."""
    print(f"Cache dir:       {cfg['cache_dir']}")
    print(f"Cache max MB:    {cfg['cache_max_mb']}")
    print(f"STT URL:         {cfg['stt_url']}")
    print(f"STT model:       {cfg['stt_model']}")
    print(f"Summarize URL:   {cfg['summarize_url']}")
    print(f"Summarize model: {cfg['summarize_model']}")
    print(f"Translate URL:   {cfg['translate_url']}")
    print(f"Translate model: {cfg['translate_model']}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="ReClip CLI — transcribe, summarize, translate media from URLs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # info
    p_info = sub.add_parser("info", help="Fetch video metadata")
    p_info.add_argument("url", help="Video URL")

    # transcribe
    p_trans = sub.add_parser("transcribe", help="Transcribe audio from URL")
    p_trans.add_argument("url", help="Video URL")

    # summarize
    p_sum = sub.add_parser("summarize", help="Summarize a video's content")
    p_sum.add_argument("url", help="Video URL")

    # translate
    p_tl = sub.add_parser("translate", help="Translate transcript or summary")
    p_tl.add_argument("url", help="Video URL")
    p_tl.add_argument("language", help="Target language (e.g. Spanish, French)")
    p_tl.add_argument(
        "--source", choices=["transcript", "summary"], default="transcript",
        help="What to translate (default: transcript)",
    )

    # cache
    sub.add_parser("cache", help="Show cache stats")

    # config
    sub.add_parser("config", help="Show current configuration")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "info": cmd_info,
        "transcribe": cmd_transcribe,
        "summarize": cmd_summarize,
        "translate": cmd_translate,
        "cache": cmd_cache,
        "config": cmd_config,
    }

    try:
        return commands[args.command](args)
    except (LLMError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
