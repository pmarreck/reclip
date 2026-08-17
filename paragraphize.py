"""Lossless paragraph layout for non-diarized speech-to-text output."""

import re


DEFAULT_MIN_WORDS = 60
DEFAULT_MAX_WORDS = 160
DEFAULT_PAUSE_SECONDS = 1.8
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.DOTALL)


def _word_count(text):
	return len(text.split())


def _ends_sentence(text):
	return text.rstrip().endswith((".", "!", "?"))


def _paragraphize_units(units, min_words, max_words, pause_seconds):
	paragraphs = []
	current = []
	current_words = 0

	for index, (text, start, end) in enumerate(units):
		current.append(text)
		current_words += _word_count(text)
		if index == len(units) - 1 or not _ends_sentence(text):
			continue

		next_start = units[index + 1][1]
		pause = (next_start - end) if start is not None and next_start is not None else 0
		if current_words >= max_words or (current_words >= min_words and pause >= pause_seconds):
			paragraphs.append(" ".join(current))
			current = []
			current_words = 0

	if current:
		paragraphs.append(" ".join(current))
	return "\n\n".join(paragraphs)


def _sentence_units(text):
	return [(match.group(0).strip(), None, None) for match in _SENTENCE_RE.finditer(text)
			if match.group(0).strip()]


def _segment_units(segments):
	units = []
	for segment in segments or []:
		text = str(segment.get("text") or "").strip()
		if not text:
			continue
		start = segment.get("start")
		end = segment.get("end")
		if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
			start = end = None
		units.append((text, start, end))
	return units


def paragraphize_transcript(text, segments=None, *, min_words=DEFAULT_MIN_WORDS,
							max_words=DEFAULT_MAX_WORDS,
							pause_seconds=DEFAULT_PAUSE_SECONDS):
	"""Add paragraph whitespace without changing transcript words.

	Whisper segment pauses are a language-neutral proxy for discourse changes.
	When timestamps are absent, sentence-length breaks retain readability. A
	timestamp-derived candidate is accepted only when its words exactly match
	the original STT text, avoiding accidental segment/text drift.
	"""
	if not text or not text.strip():
		return text

	units = _segment_units(segments)
	if units:
		candidate = _paragraphize_units(units, min_words, max_words, pause_seconds)
		if candidate.split() == text.split():
			return candidate

	return _paragraphize_units(
		_sentence_units(text), min_words, max_words, pause_seconds,
	)
