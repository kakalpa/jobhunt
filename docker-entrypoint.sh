#!/usr/bin/env bash
# ==============================================================================
# docker-entrypoint.sh - Container Startup for Job Hunt Command Center
# Launches background autonomous scout daemon and Flask GUI dashboard
# ==============================================================================

set -e

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
mkdir -p "$WORKSPACE_DIR"

# Populate workspace with base documents from /app if not already present
if [ ! -f "$WORKSPACE_DIR/Base_CV.md" ]; then
    if [ -f "/app/Base_CV.md" ]; then
        cp -n /app/Base_CV.md "$WORKSPACE_DIR/Base_CV.md" 2>/dev/null || true
    elif [ -f "/app/Base_CV.template.md" ]; then
        echo "📋 Initializing workspace with Base_CV.template.md..."
        cp -n /app/Base_CV.template.md "$WORKSPACE_DIR/Base_CV.md" 2>/dev/null || true
    fi
fi

if [ ! -f "$WORKSPACE_DIR/pipeline_data.json" ]; then
    if [ -f "/app/pipeline_data.json" ]; then
        cp -n /app/pipeline_data.json "$WORKSPACE_DIR/pipeline_data.json" 2>/dev/null || true
    elif [ -f "/app/pipeline_data.template.json" ]; then
        cp -n /app/pipeline_data.template.json "$WORKSPACE_DIR/pipeline_data.json" 2>/dev/null || true
    fi
fi

# Export critical paths
export WORKSPACE_DIR="$WORKSPACE_DIR"
export PORT="${PORT:-5500}"
export CHROMIUM_PATH="${CHROMIUM_PATH:-/usr/bin/chromium}"
export PYTHONPATH="${WORKSPACE_DIR}:${WORKSPACE_DIR}/scripts:/app:/app/scripts:$PYTHONPATH"

# If custom command was passed, execute it directly
if [ $# -gt 0 ]; then
    exec "$@"
fi

echo "========================================================"
echo "🎯 Job Hunt Command Center Container Initializing"
echo "📁 Workspace: $WORKSPACE_DIR"
echo "🌐 Dashboard Port: $PORT"
echo "🤖 Gemini AI Key: $([ -n "$GEMINI_API_KEY" ] && echo 'Configured ✅' || echo 'Not Set (Template Fallback)')"
echo "📱 Telegram Alerts: $([ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] && echo 'Active ✅' || echo 'Disabled (Unconfigured)')"
echo "========================================================"

SCOUT_PID=""

cleanup() {
    echo "🛑 Stopping services..."
    if [ -n "$SCOUT_PID" ]; then
        kill "$SCOUT_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGINT SIGTERM

# Optional background scout loop
if [ "${SCOUT_ENABLED:-true}" = "true" ]; then
    if [ -n "$SCOUT_INTERVAL_MINUTES" ]; then
        INTERVAL_SEC=$(( SCOUT_INTERVAL_MINUTES * 60 ))
        SCHEDULE_DESC="${SCOUT_INTERVAL_MINUTES} minute(s)"
    else
        INTERVAL_HOURS="${SCOUT_INTERVAL_HOURS:-2}"
        INTERVAL_SEC=$(( INTERVAL_HOURS * 3600 ))
        SCHEDULE_DESC="${INTERVAL_HOURS} hour(s)"
    fi
    echo "⏰ Autonomous scout daemon scheduled to run every ${SCHEDULE_DESC}"

    (
        get_scout_script() {
            if [ -f "${WORKSPACE_DIR}/scripts/job_scout.py" ]; then
                echo "${WORKSPACE_DIR}/scripts/job_scout.py"
            else
                echo "/app/scripts/job_scout.py"
            fi
        }

        # Run on startup if enabled (default: true so user doesn't have to wait 2h for first run)
        if [ "${SCOUT_RUN_ON_START:-true}" = "true" ]; then
            echo "📡 Running initial scout discovery on startup at $(date '+%Y-%m-%d %H:%M:%S')..."
            SCRIPT_TO_RUN=$(get_scout_script)
            python3 -u "$SCRIPT_TO_RUN" || echo "⚠️ Startup scout completed with notice"
        fi

        while true; do
            NEXT_TIME=$(date -d "@$(( $(date +%s) + INTERVAL_SEC ))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "in ${SCHEDULE_DESC}")
            echo "⏰ Next autonomous IT scout scan scheduled for: ${NEXT_TIME}"
            sleep "$INTERVAL_SEC"
            echo "=================================================================="
            echo "📡 Running autonomous background IT scout scan at $(date '+%Y-%m-%d %H:%M:%S')..."
            SCRIPT_TO_RUN=$(get_scout_script)
            python3 -u "$SCRIPT_TO_RUN" || echo "⚠️ Autonomous scout cycle notice"
            echo "=================================================================="
        done
    ) &
    SCOUT_PID=$!
fi

# Run Gunicorn in production if available, else fallback to python3
if command -v gunicorn >/dev/null 2>&1; then
    WORKERS="${GUNICORN_WORKERS:-4}"
    echo "🚀 Starting Production Gunicorn Server on 0.0.0.0:${PORT} (${WORKERS} workers)..."
    exec gunicorn --workers "$WORKERS" --bind "0.0.0.0:${PORT}" --timeout 120 dashboard.app:app
else
    echo "🚀 Starting Dashboard on 0.0.0.0:${PORT}..."
    exec python3 -u /app/dashboard/app.py
fi
