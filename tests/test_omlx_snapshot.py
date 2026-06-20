"""Tests for scripts/omlx_snapshot.py — captures oMLX local-only state
(model overrides, installed models, version, applied patches) into a tracked,
diffable artifact so changes outside the repo (the oMLX app + ~/.omlx/) leave
a record. Secrets must never reach the tracked file."""
import importlib.util
import json
import os

_SPEC = importlib.util.spec_from_file_location(
    "omlx_snapshot",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "omlx_snapshot.py"),
)
omlx_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(omlx_snapshot)


class TestRedactSettings:
    SETTINGS = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "auth": {
            "api_key": "oMLX_SUPERSECRET",
            "secret_key": "deadbeefcafe",
            "skip_api_key_verification": False,
            "sub_keys": ["sk-a", "sk-b"],
        },
        "model": {"model_dir": "/Users/x/.omlx/models"},
    }

    def test_api_key_redacted(self):
        out = omlx_snapshot.redact_settings(self.SETTINGS)
        assert out["auth"]["api_key"] == "<redacted>"

    def test_secret_key_redacted(self):
        out = omlx_snapshot.redact_settings(self.SETTINGS)
        assert out["auth"]["secret_key"] == "<redacted>"

    def test_sub_keys_redacted(self):
        out = omlx_snapshot.redact_settings(self.SETTINGS)
        assert out["auth"]["sub_keys"] == "<redacted>"

    def test_nonsecret_fields_preserved(self):
        out = omlx_snapshot.redact_settings(self.SETTINGS)
        assert out["server"]["host"] == "0.0.0.0"
        assert out["auth"]["skip_api_key_verification"] is False
        assert out["model"]["model_dir"] == "/Users/x/.omlx/models"

    def test_input_not_mutated(self):
        """Redaction must deep-copy — the live settings dict stays intact."""
        omlx_snapshot.redact_settings(self.SETTINGS)
        assert self.SETTINGS["auth"]["api_key"] == "oMLX_SUPERSECRET"

    def test_no_secret_string_survives_serialization(self):
        """Classifier-over-the-whole-object: serialize the redacted result and
        assert NO known secret value appears anywhere (catches a secret nested
        in an unexpected key, not just the ones we explicitly null)."""
        out = omlx_snapshot.redact_settings(self.SETTINGS)
        blob = json.dumps(out)
        for secret in ("oMLX_SUPERSECRET", "deadbeefcafe", "sk-a", "sk-b"):
            assert secret not in blob, f"secret {secret!r} leaked into snapshot"

    def test_missing_auth_section_ok(self):
        out = omlx_snapshot.redact_settings({"server": {"host": "x"}})
        assert out["server"]["host"] == "x"


class TestBuildSnapshot:
    def test_snapshot_has_meta_and_sections(self, tmp_path):
        model_settings = {"version": 1, "models": {
            "parakeet-tdt-0.6b-v3": {"model_type_override": "audio_stt"}}}
        settings = {"auth": {"api_key": "SECRET"}, "server": {"port": 8000}}
        models = [{"id": "whisper-large-v3-fp16", "type": "audio_stt"}]

        snap = omlx_snapshot.build_snapshot(
            version="0.3.8rc1",
            model_settings=model_settings,
            settings=settings,
            installed_models=models,
        )
        assert "_meta" in snap  # self-describing per the data-file convention
        assert snap["omlx_version"] == "0.3.8rc1"
        assert snap["model_settings"]["models"]["parakeet-tdt-0.6b-v3"][
            "model_type_override"] == "audio_stt"
        assert snap["settings_redacted"]["auth"]["api_key"] == "<redacted>"
        assert snap["installed_models"] == models

    def test_snapshot_is_deterministic(self):
        """Same inputs → identical bytes (sorted keys), so the tracked file
        only changes when the actual state does — clean diffs."""
        args = dict(version="0.4.4",
                    model_settings={"models": {}},
                    settings={"server": {"port": 8000}},
                    installed_models=[])
        a = omlx_snapshot.serialize(omlx_snapshot.build_snapshot(**args))
        b = omlx_snapshot.serialize(omlx_snapshot.build_snapshot(**args))
        assert a == b
