"""Tests for actions.py — configurable LLM actions (Summarize / Translate /
Counterargue and any user-added) backed by ~/.config/reclip+/actions.json.

Schema:
  { "version": 1, "actions": [Action, ...] }

Action:
  { id, name, source, system_prompt, model?, params? }

source is either "transcript" or another action's id. params is a list of
{name, type, required, label}.
"""
import json
import os
import time
import pytest


@pytest.fixture(autouse=True)
def isolated_config_dir(monkeypatch, tmp_path):
	"""Every test gets a fresh empty config dir so defaults apply cleanly."""
	cfg_dir = tmp_path / "reclip-config"
	monkeypatch.setenv("RECLIP_CONFIG_DIR", str(cfg_dir))
	return cfg_dir


def _write_actions(cfg_dir, data):
	"""Helper: write actions.json into the isolated config dir."""
	cfg_dir.mkdir(parents=True, exist_ok=True)
	(cfg_dir / "actions.json").write_text(json.dumps(data))


class TestDefaults:
	def test_load_returns_three_builtins_when_no_user_file(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		ids = [x.id for x in a.list()]
		assert ids == ["summarize", "translate", "translate_transcript", "counterargue"]

	def test_first_run_seeds_user_file(self, isolated_config_dir):
		"""On first load with no user file, actions.json is created from defaults."""
		from actions import Actions
		Actions()
		assert (isolated_config_dir / "actions.json").is_file()

	def test_summarize_default_matches_config_default(self, isolated_config_dir):
		"""The migrated summarize prompt is byte-identical to config.DEFAULT_SUMMARIZE_PROMPT."""
		from actions import Actions
		from config import DEFAULT_SUMMARIZE_PROMPT
		a = Actions()
		assert a.get("summarize").system_prompt == DEFAULT_SUMMARIZE_PROMPT

	def test_translate_default_matches_config_default(self, isolated_config_dir):
		from actions import Actions
		from config import DEFAULT_TRANSLATE_PROMPT
		a = Actions()
		assert a.get("translate").system_prompt == DEFAULT_TRANSLATE_PROMPT

	def test_counterargue_default_matches_config_default(self, isolated_config_dir):
		from actions import Actions
		from config import DEFAULT_COUNTERARGUE_PROMPT
		a = Actions()
		assert a.get("counterargue").system_prompt == DEFAULT_COUNTERARGUE_PROMPT

	def test_translate_has_language_param(self, isolated_config_dir):
		"""Translate's prompt has `{language}` placeholder so the param must be named `language`."""
		from actions import Actions
		a = Actions()
		t = a.get("translate")
		assert len(t.params) == 1
		assert t.params[0].name == "language"
		assert t.params[0].required is True

	def test_summarize_no_params(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		assert a.get("summarize").params == []

	def test_get_unknown_id_returns_none(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		assert a.get("nonexistent") is None


class TestSourceChain:
	def test_summarize_sources_transcript(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		assert a.get("summarize").source == "transcript"

	def test_translate_sources_summarize(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		assert a.get("translate").source == "summarize"

	def test_resolve_chain_summarize(self, isolated_config_dir):
		"""Chain for a transcript-rooted action is just that action."""
		from actions import Actions
		a = Actions()
		assert a.resolve_chain("summarize") == ["summarize"]

	def test_resolve_chain_translate(self, isolated_config_dir):
		"""Translate sources summarize → chain is [summarize, translate]."""
		from actions import Actions
		a = Actions()
		assert a.resolve_chain("translate") == ["summarize", "translate"]

	def test_resolve_chain_unknown_id_raises(self, isolated_config_dir):
		from actions import Actions, ActionError
		a = Actions()
		with pytest.raises(ActionError, match="unknown"):
			a.resolve_chain("nonexistent")


class TestUserOverrides:
	def test_user_file_replaces_defaults(self, isolated_config_dir):
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "summarize", "name": "Sum", "source": "transcript",
				 "system_prompt": "custom prompt"},
			],
		})
		from actions import Actions
		a = Actions()
		assert [x.id for x in a.list()] == ["summarize"]
		assert a.get("summarize").system_prompt == "custom prompt"

	def test_user_can_add_new_action(self, isolated_config_dir):
		"""User-defined custom action with source pointing at a built-in id."""
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "summarize", "name": "Summarize", "source": "transcript",
				 "system_prompt": "sum"},
				{"id": "tldr", "name": "TL;DR", "source": "summarize",
				 "system_prompt": "compress to 2 sentences"},
			],
		})
		from actions import Actions
		a = Actions()
		assert a.resolve_chain("tldr") == ["summarize", "tldr"]


class TestValidation:
	def test_cycle_detected(self, isolated_config_dir, capsys):
		"""A → B → A → falls back to defaults, logs error."""
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "a", "name": "A", "source": "b", "system_prompt": ""},
				{"id": "b", "name": "B", "source": "a", "system_prompt": ""},
			],
		})
		from actions import Actions
		a = Actions()
		# Fell back to built-ins
		assert [x.id for x in a.list()] == ["summarize", "translate", "translate_transcript", "counterargue"]
		err = capsys.readouterr().err
		assert "cycle" in err.lower() or "cyclic" in err.lower()

	def test_unknown_source_rejected(self, isolated_config_dir, capsys):
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "x", "name": "X", "source": "ghost", "system_prompt": ""},
			],
		})
		from actions import Actions
		a = Actions()
		assert [x.id for x in a.list()] == ["summarize", "translate", "translate_transcript", "counterargue"]
		err = capsys.readouterr().err
		assert "ghost" in err or "unknown" in err.lower()

	def test_duplicate_ids_rejected(self, isolated_config_dir, capsys):
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "dup", "name": "A", "source": "transcript", "system_prompt": ""},
				{"id": "dup", "name": "B", "source": "transcript", "system_prompt": ""},
			],
		})
		from actions import Actions
		a = Actions()
		assert [x.id for x in a.list()] == ["summarize", "translate", "translate_transcript", "counterargue"]

	def test_malformed_json_falls_back_to_defaults(self, isolated_config_dir, capsys):
		isolated_config_dir.mkdir(parents=True, exist_ok=True)
		(isolated_config_dir / "actions.json").write_text("{ not valid json")
		from actions import Actions
		a = Actions()
		assert [x.id for x in a.list()] == ["summarize", "translate", "translate_transcript", "counterargue"]
		err = capsys.readouterr().err
		assert "actions.json" in err or "parse" in err.lower() or "json" in err.lower()

	def test_self_loop_detected(self, isolated_config_dir, capsys):
		"""An action with source pointing at itself is a degenerate cycle."""
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "self", "name": "S", "source": "self", "system_prompt": ""},
			],
		})
		from actions import Actions
		a = Actions()
		assert [x.id for x in a.list()] == ["summarize", "translate", "translate_transcript", "counterargue"]


class TestHotReload:
	def test_reload_picks_up_file_change(self, isolated_config_dir):
		from actions import Actions
		a = Actions()
		assert a.get("summarize").system_prompt != "v2"

		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "summarize", "name": "Sum", "source": "transcript",
				 "system_prompt": "v2"},
			],
		})
		a._last_check = 0
		new_mtime = time.time() + 10
		os.utime(str(isolated_config_dir / "actions.json"), (new_mtime, new_mtime))
		a.maybe_reload()
		assert a.get("summarize").system_prompt == "v2"

	def test_reload_with_invalid_keeps_last_good(self, isolated_config_dir, capsys):
		"""Per spec: hot-reload with malformed JSON keeps the last good in memory."""
		from actions import Actions
		# Start with a valid custom config
		_write_actions(isolated_config_dir, {
			"version": 1,
			"actions": [
				{"id": "summarize", "name": "Sum", "source": "transcript",
				 "system_prompt": "good"},
			],
		})
		a = Actions()
		assert a.get("summarize").system_prompt == "good"

		# Break it
		(isolated_config_dir / "actions.json").write_text("{ broken")
		a._last_check = 0
		new_mtime = time.time() + 10
		os.utime(str(isolated_config_dir / "actions.json"), (new_mtime, new_mtime))
		a.maybe_reload()
		# Last good still in memory
		assert a.get("summarize").system_prompt == "good"
		# Error surfaces for the UI to display
		assert a.last_error is not None


class TestCounterargueSource:
	def test_counterargue_sources_transcript(self, isolated_config_dir):
		"""Legacy _run_counterargue reads the transcript, not the summary —
		the registry default must match for byte-identical migration."""
		from actions import Actions
		a = Actions()
		assert a.get("counterargue").source == "transcript"
		assert a.resolve_chain("counterargue") == ["counterargue"]
