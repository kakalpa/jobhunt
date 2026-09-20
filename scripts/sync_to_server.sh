#!/usr/bin/env bash
# ==============================================================================
# sync_to_server.sh - 1-Click Sync of Local Applications & Data to Public Server
# ==============================================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
cd "$DIR"

SERVER_IP="${SERVER_IP:-82.70.59.174}"
SERVER_USER="${SERVER_USER:-opc}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ssh-key-2026-09-18.key}"

echo "🔄 Syncing Job Hunt workspace to ${SERVER_USER}@${SERVER_IP}..."

if [ ! -f "$SSH_KEY" ]; then
    echo "⚠️ Warning: SSH key not found at $SSH_KEY. Checking default ~/.ssh/id_rsa..."
    if [ -f "$HOME/.ssh/id_rsa" ]; then
        SSH_KEY="$HOME/.ssh/id_rsa"
    fi
fi

# Ensure correct key permissions
chmod 600 "$SSH_KEY" 2>/dev/null || true

# Fix any container root-owned folders on server before rsync
ssh -i "$SSH_KEY" "${SERVER_USER}@${SERVER_IP}" "sudo chown -R ${SERVER_USER}:${SERVER_USER} /home/${SERVER_USER}/jobhunt" 2>/dev/null || true

# Sync all folders, applications, and tracking data
rsync -avz \
    --exclude=".git" \
    --exclude=".env" \
    --exclude="venv" \
    --exclude="__pycache__" \
    --exclude=".pytest_cache" \
    --exclude="caddy_data" \
    --exclude="caddy_config" \
    -e "ssh -i $SSH_KEY" \
    "$DIR/" "${SERVER_USER}@${SERVER_IP}:/home/${SERVER_USER}/jobhunt/"

# Restart container to refresh state
ssh -i "$SSH_KEY" "${SERVER_USER}@${SERVER_IP}" "cd /home/${SERVER_USER}/jobhunt && docker compose restart"

echo "✅ Successfully synced all application packages and tracking data to the public instance!"
echo "🌐 Dashboard live at: http://${SERVER_IP}/"
