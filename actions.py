"""Configurable LLM actions backed by ~/.config/reclip+/actions.json.

Each action is a (name, source, system_prompt, params?) tuple. `source` is
either "transcript" (the raw STT output) or the id of another action — letting
actions chain (e.g. Translate sources Summarize). The engine in app.py walks
the source chain lazily, running missing upstream actions on demand.

Defaults capture today's hardcoded summarize/translate/counterargue prompts
verbatim from config.py, so the migration is byte-identical. User edits to
~/.config/reclip+/actions.json override the defaults; the file is hot-reloaded
on mtime change. Malformed/invalid edits keep the last good config in memory
and surface a `last_error` string for the UI to display.
"""
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field, asdict


SOURCE_TRANSCRIPT = "transcript"
SOURCE_DIARIZED = "diarized"
TERMINAL_SOURCES = (SOURCE_TRANSCRIPT, SOURCE_DIARIZED)


class ActionError(Exception):
	pass


@dataclass
class ActionParam:
	name: str
	type: str = "string"
	required: bool = False
	label: str = ""


@dataclass
class Action:
	id: str
	name: str
	source: str
	system_prompt: str
	model: object = None  # None = inherit global default. Reserved for per-action override.
	params: list = field(default_factory=list)


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


def _builtin_actions():
	"""The seed actions; prompts now live HERE (the legacy RECLIP_*_PROMPT
	config knobs were removed — edit ~/.config/reclip+/actions.json instead)."""
	return [
		Action(
			id="summarize", name="Summarize", source=SOURCE_TRANSCRIPT,
			system_prompt=DEFAULT_SUMMARIZE_PROMPT, params=[],
		),
		Action(
			id="translate", name="Translate Summary", source="summarize",
			system_prompt=DEFAULT_TRANSLATE_PROMPT,
			params=[ActionParam(
				name="language", type="string", required=True, label="Language",
			)],
		),
		Action(
			id="translate_transcript", name="Translate Transcript", source=SOURCE_TRANSCRIPT,
			system_prompt=DEFAULT_TRANSLATE_PROMPT,
			params=[ActionParam(
				name="language", type="string", required=True, label="Language",
			)],
		),
		Action(
			id="counterargue", name="Counterargue", source="transcript",
			system_prompt=DEFAULT_COUNTERARGUE_PROMPT, params=[],
		),
	]


def _serialize_actions(actions):
	"""Convert an Action list to the on-disk JSON shape."""
	out = []
	for a in actions:
		out.append({
			"id": a.id,
			"name": a.name,
			"source": a.source,
			"system_prompt": a.system_prompt,
			"model": a.model,
			"params": [asdict(p) for p in a.params],
		})
	return {"version": 1, "actions": out}


def _parse_actions(data):
	"""Convert parsed JSON dict to Action list. Raises ActionError on shape problems."""
	if not isinstance(data, dict):
		raise ActionError("top-level must be an object")
	raw = data.get("actions")
	if not isinstance(raw, list):
		raise ActionError("'actions' must be a list")
	out = []
	for i, entry in enumerate(raw):
		if not isinstance(entry, dict):
			raise ActionError(f"action #{i} is not an object")
		try:
			out.append(Action(
				id=entry["id"],
				name=entry["name"],
				source=entry["source"],
				system_prompt=entry["system_prompt"],
				model=entry.get("model"),
				params=[ActionParam(
					name=p["name"],
					type=p.get("type", "string"),
					required=bool(p.get("required", False)),
					label=p.get("label", ""),
				) for p in entry.get("params", [])],
			))
		except KeyError as e:
			raise ActionError(f"action #{i} missing required field: {e}")
	return out


def _validate(actions):
	"""Check ids unique, sources resolvable, no cycles. Raises ActionError on first problem."""
	ids = [a.id for a in actions]
	seen_ids = set()
	for x in ids:
		if x in seen_ids:
			raise ActionError(f"duplicate action id: {x!r}")
		seen_ids.add(x)
	known = set(ids)
	for a in actions:
		if a.source not in TERMINAL_SOURCES and a.source not in known:
			raise ActionError(f"action {a.id!r} has unknown source: {a.source!r}")
	# Cycle detection: walk source chain from each action; loop = revisit.
	by_id = {a.id: a for a in actions}
	for a in actions:
		visited = set()
		cur = a.id
		while cur not in TERMINAL_SOURCES:
			if cur in visited:
				raise ActionError(f"cyclic source chain involving action {a.id!r}")
			visited.add(cur)
			cur = by_id[cur].source


def _default_actions_path():
	from config import _default_config_dir
	return os.path.join(_default_config_dir(), "actions.json")


class Actions:
	"""Hot-reloadable, file-backed registry of LLM actions."""

	RELOAD_THROTTLE_SECONDS = 5

	def __init__(self, config_dir=None):
		if config_dir is not None:
			self._actions_path = os.path.join(config_dir, "actions.json")
		else:
			self._actions_path = _default_actions_path()
		self._lock = threading.Lock()
		self._last_check = 0.0
		self._last_mtime = 0.0
		self._actions = []
		self.last_error = None
		self._ensure_file()
		self._load(initial=True)

	def _ensure_file(self):
		"""Seed actions.json with defaults if missing."""
		dirpath = os.path.dirname(self._actions_path)
		os.makedirs(dirpath, exist_ok=True)
		if not os.path.isfile(self._actions_path):
			defaults = _builtin_actions()
			with open(self._actions_path, "w", encoding="utf-8") as f:
				json.dump(_serialize_actions(defaults), f, indent=2)

	def _read_validated(self):
		"""Read the file, parse, validate. Returns Action list or raises ActionError."""
		with open(self._actions_path, "r", encoding="utf-8") as f:
			content = f.read()
		try:
			data = json.loads(content)
		except json.JSONDecodeError as e:
			raise ActionError(f"invalid JSON ({e.msg} at line {e.lineno})")
		actions = _parse_actions(data)
		_validate(actions)
		return actions

	def _load(self, initial=False):
		"""Initial load. On error, fall back to built-ins so the UI stays functional."""
		try:
			actions = self._read_validated()
			self._last_mtime = os.path.getmtime(self._actions_path)
			self._actions = actions
			self.last_error = None
		except (OSError, ActionError) as e:
			err = f"actions.json: {e}"
			print(f"[reclip] WARNING: {err} — using built-in defaults", file=sys.stderr)
			self._actions = _builtin_actions()
			self.last_error = err

	def _reload(self):
		"""Hot-reload: swap actions only if new file is valid; otherwise keep
		the last good list and surface the error in last_error for the UI."""
		try:
			actions = self._read_validated()
			self._last_mtime = os.path.getmtime(self._actions_path)
			self._actions = actions
			self.last_error = None
		except (OSError, ActionError) as e:
			err = f"actions.json: {e}"
			print(f"[reclip] WARNING: {err} — keeping previous actions", file=sys.stderr)
			self.last_error = err

	def maybe_reload(self):
		"""Check mtime; reload if changed. Throttled."""
		now = time.time()
		if now - self._last_check < self.RELOAD_THROTTLE_SECONDS:
			return
		with self._lock:
			if now - self._last_check < self.RELOAD_THROTTLE_SECONDS:
				return
			self._last_check = now
			try:
				mtime = os.path.getmtime(self._actions_path)
			except OSError:
				return
			if mtime > self._last_mtime:
				self._reload()

	def list(self):
		return list(self._actions)

	def get(self, action_id):
		for a in self._actions:
			if a.id == action_id:
				return a
		return None

	def resolve_chain(self, action_id):
		"""Return action ids from transcript-root to action_id, e.g. translate
		(sources summarize, sources transcript) → ["summarize", "translate"].
		Raises ActionError on unknown id or cycle."""
		if self.get(action_id) is None:
			raise ActionError(f"unknown action: {action_id!r}")
		chain = []
		cur = action_id
		visited = set()
		while cur not in TERMINAL_SOURCES:
			if cur in visited:
				raise ActionError(f"cyclic source chain at {cur!r}")
			visited.add(cur)
			chain.append(cur)
			a = self.get(cur)
			if a is None:
				raise ActionError(f"unknown source: {cur!r}")
			cur = a.source
		return list(reversed(chain))
