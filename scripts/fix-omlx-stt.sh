#!/usr/bin/env bash
# Idempotently patch the bundled oMLX.app so its STT engine can load Whisper.
#
# Root cause:
#   oMLX 0.3.x bundles transformers 5.x against mistral_common 1.9.x, but
#   transformers/tokenization_mistral_common.py imports `ReasoningEffort`
#   which was only added in mistral_common 1.10. The import fails at module
#   load. WhisperProcessor.from_pretrained() walks the transformers module
#   map during processor discovery, hits the broken import, and falls back
#   to "no processor available". oMLX then reports a misleading "missing
#   preprocessor_config.json" error to the client.
#
# This patch makes the `ReasoningEffort` import optional with a stub class
# so non-Mistral processors (Whisper, Voxtral, etc.) load cleanly. Safe to
# re-run; idempotent. Reverted automatically if oMLX auto-updates the file.
#
# Tracked upstream: https://github.com/jundot/omlx/issues/<TODO>
#
# Usage:
#   ./scripts/fix-omlx-stt.sh              # patch /Applications/oMLX.app
#   OMLX_APP=~/oMLX.app ./scripts/...      # patch a custom location
#   ./scripts/fix-omlx-stt.sh --check      # report patched/unpatched, no edit
#   ./scripts/fix-omlx-stt.sh --revert     # restore original from .reclip-backup
set -uo pipefail

OMLX_APP="${OMLX_APP:-/Applications/oMLX.app}"
TARGET="$OMLX_APP/Contents/Python/framework-mlx-framework/lib/python3.11/site-packages/transformers/tokenization_mistral_common.py"
BACKUP="$TARGET.reclip-backup"
MARKER="ReasoningEffort was added in mistral_common 1.10"

mode="patch"
case "${1:-}" in
	--check)  mode="check" ;;
	--revert) mode="revert" ;;
	--help|-h)
		sed -n '2,/^set/p' "$0" | sed 's/^# \?//' | head -n -1
		exit 0 ;;
esac

if [ ! -f "$TARGET" ]; then
	echo "fix-omlx-stt: target not found: $TARGET" >&2
	echo "  set OMLX_APP=/path/to/oMLX.app if installed elsewhere" >&2
	exit 1
fi

is_patched() {
	grep -q "$MARKER" "$TARGET"
}

case "$mode" in
	check)
		if is_patched; then
			echo "patched: $TARGET"
			exit 0
		else
			echo "not patched: $TARGET"
			exit 2
		fi
		;;
	revert)
		if [ ! -f "$BACKUP" ]; then
			echo "fix-omlx-stt: no backup at $BACKUP — cannot revert" >&2
			exit 1
		fi
		cp "$BACKUP" "$TARGET"
		echo "reverted: $TARGET (backup kept at $BACKUP)"
		exit 0
		;;
esac

if is_patched; then
	echo "fix-omlx-stt: already patched, no changes."
	exit 0
fi

# Save backup once. Don't overwrite if it already exists (preserves true original).
if [ ! -f "$BACKUP" ]; then
	cp "$TARGET" "$BACKUP"
	echo "backed up original to: $BACKUP"
fi

# Replace the brittle one-line import with a try/except + stub class.
# Use a small Python helper so we don't have to fight sed escaping.
python3 - "$TARGET" <<'PY'
import io
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
src = target.read_text()

old = (
    "if is_mistral_common_available():\n"
    "    from mistral_common.protocol.instruct.request import ChatCompletionRequest, ReasoningEffort\n"
    "    from mistral_common.protocol.instruct.validator import ValidationMode\n"
)
new = (
    "if is_mistral_common_available():\n"
    "    from mistral_common.protocol.instruct.request import ChatCompletionRequest\n"
    "    try:\n"
    "        # ReasoningEffort was added in mistral_common 1.10. The oMLX bundle pins\n"
    "        # mistral_common 1.9.x against transformers 5.x which expects it, so\n"
    "        # importing this module fails with a misleading \"missing processor\"\n"
    "        # error for any HF processor (e.g. WhisperProcessor) whose load path\n"
    "        # walks the transformers module map. Stub it so the import succeeds.\n"
    "        from mistral_common.protocol.instruct.request import ReasoningEffort\n"
    "    except ImportError:\n"
    "        class ReasoningEffort:  # type: ignore[no-redef]\n"
    "            \"\"\"Stub for older mistral_common. Touching attributes raises clearly.\"\"\"\n"
    "            pass\n"
    "    from mistral_common.protocol.instruct.validator import ValidationMode\n"
)

if old not in src:
    print("fix-omlx-stt: ERROR — expected import block not found.", file=sys.stderr)
    print("  oMLX may have changed the file. Aborting without writing.", file=sys.stderr)
    sys.exit(3)

target.write_text(src.replace(old, new, 1))
print(f"patched: {target}")
PY

echo ""
echo "Restart oMLX (quit from menu bar, relaunch) for the change to take effect."
