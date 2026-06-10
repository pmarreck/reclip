"""Pure speaker-pipeline functions: merge transcript segments with diarization
turns, render a diarized transcript, and name speakers via an LLM.

Everything here is deterministic and I/O-free (the LLM call itself lives in
the caller; we only build prompts and parse responses) — directly unit-testable
per the project's hexagonal discipline.

Pipeline shape:
    merged  = merge_speakers(transcript_segments, speaker_turns)
    text    = format_diarized(merged)                      # "Speaker N:" labels
    labels  = speaker_label_map(merged)                    # {"Speaker 1": "SPEAKER_07"}
    sys,usr = build_naming_prompt(text, video_metadata)
    naming  = parse_naming_response(llm(sys, usr))         # keyed by display label
    names   = apply_names(labels, naming)                  # {"SPEAKER_07": "Alice"}
    final   = format_diarized(merged, names=names)
"""
import json
import re


UNKNOWN_LABEL = "Unknown"


def _overlap(a_start, a_end, b_start, b_end):
	return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def merge_speakers(transcript_segments, speaker_turns):
	"""Assign each transcript segment the speaker with the greatest total
	temporal overlap (accumulated across that speaker's turns, so a speaker
	briefly interrupted mid-segment still wins). Speaker is None when no
	turn overlaps the segment at all (e.g. music, captions over silence).
	"""
	merged = []
	for seg in transcript_segments:
		totals = {}
		for turn in speaker_turns:
			ov = _overlap(seg["start"], seg["end"], turn["start"], turn["end"])
			if ov > 0:
				totals[turn["speaker"]] = totals.get(turn["speaker"], 0.0) + ov
		speaker = max(totals, key=totals.get) if totals else None
		merged.append({
			"start": seg["start"],
			"end": seg["end"],
			"text": seg["text"],
			"speaker": speaker,
		})
	return merged


def _ordered_speakers(merged):
	"""Raw speaker ids in order of first appearance, Nones excluded."""
	seen = []
	for m in merged:
		sp = m["speaker"]
		if sp is not None and sp not in seen:
			seen.append(sp)
	return seen


def speaker_label_map(merged):
	"""{"Speaker 1": raw_id, ...} using the same first-appearance numbering
	as format_diarized — the contract that lets LLM answers (keyed by display
	label) map back to raw diarization ids."""
	return {f"Speaker {i + 1}": sp for i, sp in enumerate(_ordered_speakers(merged))}


def format_diarized(merged, names=None):
	"""Render merged segments as a diarized transcript, collapsing consecutive
	same-speaker segments into one block:

	    Speaker 1: Hello there. Nice to meet you.
	    Speaker 2: Likewise!

	`names` ({raw_id: display_name}) overrides the default "Speaker N" labels.
	"""
	names = names or {}
	default_labels = {sp: f"Speaker {i + 1}" for i, sp in enumerate(_ordered_speakers(merged))}

	def label_for(sp):
		if sp is None:
			return UNKNOWN_LABEL
		return names.get(sp) or default_labels.get(sp, UNKNOWN_LABEL)

	lines = []
	cur_speaker = object()  # sentinel that never equals a real value
	cur_texts = []
	for m in merged:
		if m["speaker"] == cur_speaker:
			cur_texts.append(m["text"].strip())
		else:
			if cur_texts:
				lines.append(f"{label_for(cur_speaker)}: {' '.join(cur_texts)}")
			cur_speaker = m["speaker"]
			cur_texts = [m["text"].strip()]
	if cur_texts:
		lines.append(f"{label_for(cur_speaker)}: {' '.join(cur_texts)}")
	return "\n".join(lines)


NAMING_SYSTEM_PROMPT = (
	"You identify speakers in a diarized transcript using context cues: "
	"self-introductions ('I'm Joe'), how speakers address each other "
	"('thanks, Jane'), and the video metadata (title, description, uploader). "
	"Respond with ONLY a JSON object mapping each speaker label to "
	'{"name": string or null, "confidence": number 0-1, "evidence": string}. '
	"Use null for name when there is no real evidence — NEVER guess or "
	"manufacture names. A role is acceptable when a proper name is absent "
	"(e.g. 'Interviewer', 'Narrator') but lower the confidence accordingly."
)


def build_naming_prompt(diarized_text, metadata):
	"""Returns (system_prompt, user_content) for the naming chat call."""
	meta_lines = []
	for key in ("title", "uploader", "description"):
		val = (metadata or {}).get(key)
		if val:
			meta_lines.append(f"{key}: {val}")
	user = (
		"Video metadata:\n" + ("\n".join(meta_lines) or "(none)") +
		"\n\nDiarized transcript:\n" + diarized_text
	)
	return NAMING_SYSTEM_PROMPT, user


def parse_naming_response(text):
	"""Extract the JSON object from an LLM response, tolerating ```json fences
	and surrounding prose. Raises ValueError when no parseable object exists
	or the shape is not {label: {...}}."""
	candidate = None
	fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
	if fence:
		candidate = fence.group(1)
	else:
		brace = re.search(r"\{.*\}", text, re.DOTALL)
		if brace:
			candidate = brace.group(0)
	if candidate is None:
		raise ValueError("no JSON object found in naming response")
	try:
		parsed = json.loads(candidate)
	except json.JSONDecodeError as e:
		raise ValueError(f"naming response JSON invalid: {e}")
	if not isinstance(parsed, dict) or not all(isinstance(v, dict) for v in parsed.values()):
		raise ValueError("naming response is not a {label: {name,...}} object")
	return parsed


_UNKNOWNISH = {"unknown", "unidentified", "n/a", "none", "speaker", ""}


def apply_names(label_map, naming, min_confidence=0.6):
	"""Convert the LLM's {display_label: {name, confidence, ...}} into
	{raw_speaker_id: display_name}, dropping low-confidence and non-answers.
	Dropped speakers simply keep their 'Speaker N' fallback in the formatter.
	"""
	out = {}
	for label, raw_id in label_map.items():
		entry = naming.get(label)
		if not entry:
			continue
		name = entry.get("name")
		if not name or str(name).strip().lower() in _UNKNOWNISH:
			continue
		try:
			confidence = float(entry.get("confidence", 0))
		except (TypeError, ValueError):
			continue
		if confidence < min_confidence:
			continue
		out[raw_id] = str(name).strip()
	return out
