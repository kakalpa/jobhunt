#!/usr/bin/env bash
# ==============================================================================
# start_dashboard.sh - Launcher for Job Hunter Command Center GUI
# ==============================================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

PORT=5500

# Check if port 5500 is in use
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "⚠️ Port $PORT is already in use, trying 5501..."
    PORT=5501
fi

echo ""
echo "🚀 Starting Job Hunter Command Center..."
echo "📍 Workspace: $DIR"
echo "🌐 URL: http://localhost:$PORT"
echo ""
echo "Press Ctrl+C to stop the dashboard."
echo ""

# Try opening default browser in background if xdg-open exists
if command -v xdg-open > /dev/null 2>&1; then
    (sleep 1 && xdg-open "http://localhost:$PORT" > /dev/null 2>&1) &
fi

export PORT=$PORT
exec uv run --python 3.12 --with flask python3 dashboard/app.py
