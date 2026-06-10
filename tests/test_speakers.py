"""Tests for speakers.py — pure functions: transcript×turns merge, diarized
formatting, and LLM speaker-naming prompt/parse. No I/O, no network."""
import json
import pytest


# --- merge_speakers --------------------------------------------------------

class TestMergeSpeakers:
	def test_basic_assignment_by_overlap(self):
		from speakers import merge_speakers
		transcript = [
			{"start": 0.0, "end": 4.0, "text": "Hello, I am Alice."},
			{"start": 5.0, "end": 9.0, "text": "And I am Bob."},
		]
		turns = [
			{"start": 0.2, "end": 4.2, "speaker": "SPEAKER_00"},
			{"start": 4.9, "end": 9.5, "speaker": "SPEAKER_01"},
		]
		merged = merge_speakers(transcript, turns)
		assert [m["speaker"] for m in merged] == ["SPEAKER_00", "SPEAKER_01"]
		assert merged[0]["text"] == "Hello, I am Alice."

	def test_straddling_segment_goes_to_bigger_overlap(self):
		from speakers import merge_speakers
		transcript = [{"start": 0.0, "end": 10.0, "text": "long segment"}]
		turns = [
			{"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},   # 3s overlap
			{"start": 3.0, "end": 10.0, "speaker": "SPEAKER_01"},  # 7s overlap
		]
		merged = merge_speakers(transcript, turns)
		assert merged[0]["speaker"] == "SPEAKER_01"

	def test_same_speaker_split_turns_accumulate(self):
		"""Two short turns of one speaker must beat one longer turn of another.
		Each individual SPEAKER_00 turn is SHORTER than SPEAKER_01's single
		turn, so only correct accumulation (3+4=7 > 5.5) picks SPEAKER_00 —
		a last-turn-wins or max-single-turn bug picks SPEAKER_01."""
		from speakers import merge_speakers
		transcript = [{"start": 0.0, "end": 13.0, "text": "x"}]
		turns = [
			{"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},    # 3.0s
			{"start": 3.0, "end": 8.5, "speaker": "SPEAKER_01"},    # 5.5s
			{"start": 9.0, "end": 13.0, "speaker": "SPEAKER_00"},   # 4.0s → 7.0 total
		]
		merged = merge_speakers(transcript, turns)
		assert merged[0]["speaker"] == "SPEAKER_00"

	def test_no_overlap_yields_none(self):
		from speakers import merge_speakers
		transcript = [{"start": 0.0, "end": 2.0, "text": "intro music lyrics"}]
		turns = [{"start": 10.0, "end": 20.0, "speaker": "SPEAKER_00"}]
		merged = merge_speakers(transcript, turns)
		assert merged[0]["speaker"] is None

	def test_empty_inputs(self):
		from speakers import merge_speakers
		assert merge_speakers([], []) == []
		assert merge_speakers([], [{"start": 0, "end": 1, "speaker": "S"}]) == []
		merged = merge_speakers([{"start": 0, "end": 1, "text": "t"}], [])
		assert merged[0]["speaker"] is None


# --- format_diarized -------------------------------------------------------

class TestFormatDiarized:
	MERGED = [
		{"start": 0.0, "end": 4.0, "text": "Hello there.", "speaker": "SPEAKER_07"},
		{"start": 4.5, "end": 8.0, "text": "Nice to meet you.", "speaker": "SPEAKER_07"},
		{"start": 9.0, "end": 12.0, "text": "Likewise!", "speaker": "SPEAKER_02"},
		{"start": 13.0, "end": 15.0, "text": "Bye.", "speaker": "SPEAKER_07"},
	]

	def test_consecutive_same_speaker_collapsed(self):
		from speakers import format_diarized
		out = format_diarized(self.MERGED)
		# 3 speaker blocks: 07, 02, 07
		assert out.count("Speaker 1:") == 2
		assert out.count("Speaker 2:") == 1
		assert "Hello there. Nice to meet you." in out

	def test_numbering_by_first_appearance(self):
		"""SPEAKER_07 appears first → 'Speaker 1', regardless of label sort order."""
		from speakers import format_diarized
		out = format_diarized(self.MERGED)
		first_line = out.splitlines()[0]
		assert first_line.startswith("Speaker 1:")
		assert "Hello there" in first_line

	def test_names_map_applied(self):
		from speakers import format_diarized
		out = format_diarized(self.MERGED, names={"SPEAKER_07": "Alice", "SPEAKER_02": "Bob"})
		assert "Alice:" in out
		assert "Bob:" in out
		assert "Speaker 1" not in out

	def test_unknown_speaker_labeled(self):
		from speakers import format_diarized
		merged = [{"start": 0, "end": 1, "text": "hm", "speaker": None}]
		out = format_diarized(merged)
		assert "Unknown:" in out


class TestSpeakerLabelMap:
	def test_labels_match_format_numbering(self):
		"""speaker_label_map must use the same first-appearance numbering as
		format_diarized, or the LLM's answers won't map back correctly."""
		from speakers import speaker_label_map
		merged = TestFormatDiarized.MERGED
		assert speaker_label_map(merged) == {
			"Speaker 1": "SPEAKER_07",
			"Speaker 2": "SPEAKER_02",
		}

	def test_none_speaker_excluded(self):
		from speakers import speaker_label_map
		merged = [{"start": 0, "end": 1, "text": "x", "speaker": None}]
		assert speaker_label_map(merged) == {}


# --- naming prompt + response parsing -------------------------------------

class TestNaming:
	def test_prompt_includes_transcript_and_metadata(self):
		from speakers import build_naming_prompt
		system, user = build_naming_prompt(
			"Speaker 1: I'm Joe, welcome to the show.",
			{"title": "The Joe Show #42", "uploader": "JoeShowOfficial",
			 "description": "Joe talks to Jane Doe about llamas"},
		)
		assert "JSON" in system
		assert "The Joe Show #42" in user
		assert "I'm Joe, welcome to the show." in user

	def test_parse_clean_json(self):
		from speakers import parse_naming_response
		resp = json.dumps({"Speaker 1": {"name": "Joe", "confidence": 0.95,
		                                 "evidence": "introduces himself"}})
		out = parse_naming_response(resp)
		assert out["Speaker 1"]["name"] == "Joe"

	def test_parse_fenced_json(self):
		from speakers import parse_naming_response
		resp = 'Sure! Here you go:\n```json\n{"Speaker 1": {"name": "Joe", "confidence": 0.9, "evidence": "e"}}\n```\nLet me know!'
		out = parse_naming_response(resp)
		assert out["Speaker 1"]["name"] == "Joe"

	def test_parse_garbage_raises(self):
		from speakers import parse_naming_response
		with pytest.raises(ValueError):
			parse_naming_response("I have no idea who these people are.")

	def test_apply_names_threshold(self):
		from speakers import apply_names
		naming = {
			"Speaker 1": {"name": "Joe", "confidence": 0.95, "evidence": "intro"},
			"Speaker 2": {"name": "Maybe Jane", "confidence": 0.3, "evidence": "guess"},
		}
		display = apply_names({"Speaker 1": "SPEAKER_00", "Speaker 2": "SPEAKER_01"},
		                      naming, min_confidence=0.6)
		assert display["SPEAKER_00"] == "Joe"
		# Low confidence → no entry (formatter falls back to Speaker N)
		assert "SPEAKER_01" not in display

	def test_apply_names_skips_null_and_unknownish(self):
		from speakers import apply_names
		naming = {
			"Speaker 1": {"name": None, "confidence": 0.99, "evidence": ""},
			"Speaker 2": {"name": "Unknown", "confidence": 0.99, "evidence": ""},
		}
		display = apply_names({"Speaker 1": "SPEAKER_00", "Speaker 2": "SPEAKER_01"}, naming)
		assert display == {}
