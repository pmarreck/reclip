"""Contracts for non-diarized transcript paragraph formatting."""

from paragraphize import paragraphize_transcript


def test_timestamp_pause_creates_a_paragraph_at_a_sentence_boundary():
	segments = [
		{"start": 0.0, "end": 1.0, "text": "First point."},
		{"start": 1.1, "end": 2.0, "text": "Still the same thought."},
		{"start": 5.0, "end": 6.0, "text": "A new topic begins."},
	]

	formatted = paragraphize_transcript(
		"First point. Still the same thought. A new topic begins.", segments,
		min_words=4, max_words=40, pause_seconds=2.0,
	)

	assert formatted == "First point. Still the same thought.\n\nA new topic begins."


def test_paragraphization_preserves_every_transcribed_word():
	segments = [
		{"start": 0.0, "end": 1.0, "text": "Alice said this."},
		{"start": 3.5, "end": 4.5, "text": "Bob replied with that."},
	]

	formatted = paragraphize_transcript(
		"Alice said this. Bob replied with that.", segments,
		min_words=1, max_words=40, pause_seconds=2.0,
	)

	assert formatted.split() == "Alice said this. Bob replied with that.".split()
	assert "\n\n" in formatted


def test_timestamp_free_transcript_uses_sentence_length_fallback():
	formatted = paragraphize_transcript(
		"One short sentence. Two short sentence. Three short sentence.",
		None, min_words=4, max_words=6,
	)

	assert formatted == "One short sentence. Two short sentence.\n\nThree short sentence."
