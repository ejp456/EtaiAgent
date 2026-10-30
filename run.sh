#!/usr/bin/env bash
# EtaiAgent Universal Runner
# Works on macOS, Linux, Raspberry Pi, and WSL.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 1. Resolve Python interpreter (prefer project virtualenv if it exists)
if [ -f "$DIR/.venv/bin/python" ]; then
    PYTHON="$DIR/.venv/bin/python"
elif [ -f "$DIR/venv/bin/python" ]; then
    PYTHON="$DIR/venv/bin/python"
elif [ -f "$DIR/.venv/Scripts/python.exe" ]; then
    PYTHON="$DIR/.venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "Error: Python 3 is not installed or not in PATH." >&2
    exit 1
fi

# 2. Check if Anthropic SRT sandbox is available & requested
if [ "$USE_SRT" = "1" ] && [ -f "$DIR/node_modules/.bin/srt" ]; then
    exec "$DIR/node_modules/.bin/srt" --settings "$DIR/srt-settings.json" -- "$PYTHON" "$DIR/main.py" "$@"
else
    exec "$PYTHON" "$DIR/main.py" "$@"
fi
