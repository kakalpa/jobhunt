#!/usr/bin/env bash
# ==============================================================================
# cron_scout.sh - Host Cron Runner for Job Hunt Command Center
# Usage in crontab (e.g. run every 2 hours):
#   0 */2 * * * /home/opc/jobhunt/scripts/cron_scout.sh
# ==============================================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Ensure common paths are available in cron environment
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

LOG_FILE="$DIR/scout_cron.log"

# Prevent overlapping runs with file lock
LOCK_FILE="/tmp/jobhunt_scout_cron.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Another scout process is already in progress. Skipping duplicate cron cycle." >> "$LOG_FILE"
    exit 0
fi

echo "==================================================================" >> "$LOG_FILE"
echo "⏰ [$(date '+%Y-%m-%d %H:%M:%S')] Triggering Scheduled IT Scout Scan" >> "$LOG_FILE"
echo "==================================================================" >> "$LOG_FILE"

# If the container is currently running, execute directly inside it
if docker ps --format '{{.Names}}' | grep -q "^job-hunt-command-center$"; then
    echo "🐳 Running scout inside active container 'job-hunt-command-center'..." >> "$LOG_FILE"
    docker exec job-hunt-command-center python3 -u /workspace/scripts/job_scout.py >> "$LOG_FILE" 2>&1
else
    echo "🚀 Container not running. Launching ephemeral scout run via docker compose..." >> "$LOG_FILE"
    docker compose run --rm job-hunt-command-center python3 -u /workspace/scripts/job_scout.py >> "$LOG_FILE" 2>&1
fi

echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Scheduled IT Scout Scan Complete" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
