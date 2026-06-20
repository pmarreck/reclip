#!/usr/bin/env python3
"""Snapshot oMLX's local-only state into a tracked, diffable artifact.

reclip depends on state that lives OUTSIDE this repo: the oMLX app and its
`~/.omlx/` config (model-type overrides, installed models, the app version,
and any local patches like fix-omlx-stt.sh). git can't see those, so a wiped
machine — or a handoff — loses the knowledge of *what was changed and why*.

This script materializes that state into `omlx-state.json` at the repo root.
Because the file is version-controlled, `jj diff omlx-state.json` becomes the
durable record of every local model change. Secrets (api_key / secret_key /
sub_keys) are redacted so the tracked file is safe to commit.

Usage:
    scripts/omlx_snapshot.py            # write/refresh omlx-state.json
    scripts/omlx_snapshot.py --check    # exit 1 if live state drifted from it
"""
import copy
import json
import os
import plistlib
import subprocess
import sys
import urllib.request


OMLX_DIR = os.path.expanduser("~/.omlx")
OMLX_APP_PLIST = "/Applications/oMLX.app/Contents/Info.plist"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "omlx-state.json")

# Keys under settings that must never be written to the tracked file.
_SECRET_KEYS = ("api_key", "secret_key", "sub_keys")
_REDACTED = "<redacted>"


def redact_settings(settings):
    """Return a deep copy of oMLX settings with secret auth fields replaced by
    a placeholder. Deep-copies so the caller's live dict is untouched."""
    out = copy.deepcopy(settings or {})
    auth = out.get("auth")
    if isinstance(auth, dict):
        for k in _SECRET_KEYS:
            if k in auth:
                auth[k] = _REDACTED
    return out


def build_snapshot(version, model_settings, settings, installed_models):
    """Assemble the snapshot dict. Self-describing via `_meta` per the fleet
    data-file provenance convention (the record explains itself when copied
    away from this repo)."""
    return {
        "_meta": (
            "oMLX local-only state for reclip — model overrides, installed "
            "models, app version. Regenerate with scripts/omlx_snapshot.py; "
            "secrets redacted; diff this file to see local model changes."
        ),
        "schema": 1,
        "omlx_version": version,
        "model_settings": model_settings or {},
        "settings_redacted": redact_settings(settings),
        "installed_models": installed_models or [],
    }


def serialize(snapshot):
    """Deterministic JSON: sorted keys + trailing newline, so the tracked file
    changes only when the actual state changes."""
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


# --- live-state collectors (I/O; not exercised by unit tests) -------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _omlx_app_version():
    try:
        with open(OMLX_APP_PLIST, "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "unknown")
    except OSError:
        return "unknown"


def _installed_models():
    """List served models (id/type/engine/size) via the local oMLX API. Falls
    back to the on-disk model dir names if the server isn't reachable."""
    api_key = (_read_json(os.path.join(OMLX_DIR, "settings.json"))
               .get("auth", {}).get("api_key", ""))
    try:
        req = urllib.request.Request("http://localhost:8000/v1/models")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r).get("data", [])
        # /v1/models returns ids; enrich with type/engine if oMLX provides them
        return sorted(
            ({k: m[k] for k in ("id", "type", "engine", "size") if k in m}
             for m in data),
            key=lambda m: m["id"],
        )
    except Exception:
        model_dir = os.path.join(OMLX_DIR, "models")
        try:
            return sorted({"id": n} for n in os.listdir(model_dir)
                          if not n.startswith("."))
        except OSError:
            return []


def collect_live_snapshot():
    return build_snapshot(
        version=_omlx_app_version(),
        model_settings=_read_json(os.path.join(OMLX_DIR, "model_settings.json")),
        settings=_read_json(os.path.join(OMLX_DIR, "settings.json")),
        installed_models=_installed_models(),
    )


def main(argv):
    live = serialize(collect_live_snapshot())
    if "--check" in argv:
        try:
            with open(SNAPSHOT_PATH, encoding="utf-8") as f:
                tracked = f.read()
        except OSError:
            print(f"omlx-state.json missing — run scripts/omlx_snapshot.py", file=sys.stderr)
            return 1
        if live != tracked:
            print("DRIFT: live oMLX state differs from omlx-state.json — "
                  "run scripts/omlx_snapshot.py and commit, or revert the change.",
                  file=sys.stderr)
            return 1
        print("omlx-state.json is current.")
        return 0
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        f.write(live)
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
