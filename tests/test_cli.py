"""Public CLI contract tests.

These tests execute cli.py as a user would, while later workflow tests inject
the network/media adapters so they remain hermetic.
"""
import subprocess
import sys
import os
import inspect
from pathlib import Path

import cli


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
	return subprocess.run(
		[sys.executable, "cli.py", *args],
		cwd=ROOT,
		capture_output=True,
		text=True,
	)


def test_help_lists_every_web_media_workflow():
	result = run_cli("--help")

	assert result.returncode == 0
	for command in (
		"download", "transcribe", "speakers", "actions", "action", "speak",
		"summarize", "translate",
	):
		assert command in result.stdout


def test_path_entrypoint_exposes_cli_from_outside_the_checkout():
	entrypoint = ROOT / "bin" / "reclip"

	assert entrypoint.is_file()
	assert os.access(entrypoint, os.X_OK)
	result = subprocess.run(
		[str(entrypoint), "--help"],
		cwd=ROOT.parent,
		capture_output=True,
		text=True,
	)

	assert result.returncode == 0
	assert "ReClip CLI" in result.stdout


class FakeWorkflows:
	def __init__(self):
		self.calls = []

	def info_url(self, url):
		self.calls.append(("info", url))
		return {
			"title": "Example video",
			"uploader": "Example channel",
			"duration": 90,
			"formats": [{"height": 1080}, {"height": 720}],
		}

	def download_url(self, url, format_choice, format_id=None):
		self.calls.append(("download", url, format_choice, format_id))
		return {"file": "/cache/media.mp3", "filename": "media.mp3"}

	def transcribe_url(self, url):
		self.calls.append(("transcribe", url))
		return "Transcript"

	def diarize_url(self, url):
		self.calls.append(("speakers", url))
		return "Speaker 1: Hello"

	def list_actions(self):
		self.calls.append(("actions",))
		return [{"id": "custom", "name": "Custom", "source": "diarized", "params": ["style"]}]

	def action_url(self, url, action_id, params):
		self.calls.append(("action", url, action_id, params))
		return "Action output"

	def speak_url(self, url, source, voice_override="", output_format="wav"):
		self.calls.append(("speak", url, source, voice_override, output_format))
		return {"file": f"/cache/{source}.{output_format}", "filename": f"{source}.{output_format}"}


def test_media_commands_delegate_to_shared_web_workflows(monkeypatch, capsys):
	workflows = FakeWorkflows()
	monkeypatch.setattr(cli, "_workflows", lambda: workflows)

	assert cli.main(["info", "https://example.com/a"]) == 0
	assert cli.main([
		"download", "https://example.com/a", "--format", "audio", "--format-id", "140",
	]) == 0
	assert cli.main(["transcribe", "https://example.com/a"]) == 0
	assert cli.main(["speakers", "https://example.com/a"]) == 0
	assert cli.main(["actions"]) == 0
	assert cli.main(["action", "https://example.com/a", "custom", "--param", "style=brief"]) == 0
	assert cli.main(["summarize", "https://example.com/a"]) == 0
	assert cli.main(["translate", "https://example.com/a", "Spanish"]) == 0
	assert cli.main([
		"translate", "https://example.com/a", "French", "--source", "summary",
	]) == 0
	assert cli.main([
		"speak", "https://example.com/a", "--source", "diarized",
		"--voice", "calm", "--format", "mp3",
	]) == 0

	assert workflows.calls == [
		("info", "https://example.com/a"),
		("download", "https://example.com/a", "audio", "140"),
		("transcribe", "https://example.com/a"),
		("speakers", "https://example.com/a"),
		("actions",),
		("action", "https://example.com/a", "custom", {"style": "brief"}),
		("action", "https://example.com/a", "summarize", {}),
		("action", "https://example.com/a", "translate_transcript", {"language": "Spanish"}),
		("action", "https://example.com/a", "translate", {"language": "French"}),
		("speak", "https://example.com/a", "diarized", "calm", "mp3"),
	]
	assert "subprocess.run" not in inspect.getsource(cli.cmd_info)
	assert "Transcript" in capsys.readouterr().out


def test_action_rejects_malformed_parameter(monkeypatch, capsys):
	monkeypatch.setattr(cli, "_workflows", FakeWorkflows)

	assert cli.main(["action", "https://example.com/a", "custom", "--param", "style"]) == 2
	assert "NAME=VALUE" in capsys.readouterr().err
