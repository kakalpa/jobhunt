#!/usr/bin/env bash
# scout.sh - 1-Click Automated Job Scout for Finland & EU Remote
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"

echo "🔎 Running Automated IT Job Scout..."

if command -v uv >/dev/null 2>&1; then
    exec uv run --python 3.12 --with python-jobspy python3 "$SCRIPT_DIR/job_scout.py" "$@"
else
    exec python3 "$SCRIPT_DIR/job_scout.py" "$@"
fi

