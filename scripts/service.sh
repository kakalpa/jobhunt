#!/usr/bin/env bash
# service.sh - Manage Job Hunt Command Center & Scout Background Services
set -e

SERVICE_NAME="job-hunt-dashboard.service"
TIMER_NAME="job-hunt-scout.timer"
SCOUT_SERVICE="job-hunt-scout.service"

case "${1:-status}" in
  start)
    echo "🚀 Starting Job Hunt Dashboard and Scout Timer..."
    systemctl --user daemon-reload
    systemctl --user start "$SERVICE_NAME"
    systemctl --user start "$TIMER_NAME"
    echo "✅ Dashboard: http://localhost:5500"
    systemctl --user status "$SERVICE_NAME" --no-pager -n 5
    ;;
  stop)
    echo "🛑 Stopping Job Hunt Dashboard and Scout Timer..."
    systemctl --user stop "$SERVICE_NAME"
    systemctl --user stop "$TIMER_NAME"
    echo "✅ Stopped."
    ;;
  restart)
    echo "🔄 Restarting Job Hunt Dashboard..."
    systemctl --user daemon-reload
    systemctl --user restart "$SERVICE_NAME"
    echo "✅ Dashboard restarted: http://localhost:5500"
    systemctl --user status "$SERVICE_NAME" --no-pager -n 5
    ;;
  enable)
    echo "🔌 Enabling services to start on boot/login..."
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user enable "$TIMER_NAME"
    loginctl enable-linger "$USER" 2>/dev/null || true
    echo "✅ Enabled on boot!"
    ;;
  disable)
    echo "🔌 Disabling services from auto-start..."
    systemctl --user disable "$SERVICE_NAME"
    systemctl --user disable "$TIMER_NAME"
    echo "✅ Disabled."
    ;;
  status)
    echo "📊 --- Dashboard Service Status ---"
    systemctl --user status "$SERVICE_NAME" --no-pager -n 5 || true
    echo ""
    echo "⏱️ --- Job Scout Timer Status ---"
    systemctl --user status "$TIMER_NAME" --no-pager -n 5 || true
    ;;
  logs)
    echo "📜 Streaming Dashboard service logs (Ctrl+C to exit)..."
    journalctl --user -u "$SERVICE_NAME" -f -n 50
    ;;
  scout-logs)
    echo "📜 Streaming Job Scout logs (Ctrl+C to exit)..."
    journalctl --user -u "$SCOUT_SERVICE" -f -n 50
    ;;
  scout-now)
    echo "🔎 Triggering immediate Job Scout scan..."
    systemctl --user start "$SCOUT_SERVICE"
    journalctl --user -u "$SCOUT_SERVICE" -n 30 --no-pager
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|enable|disable|status|logs|scout-logs|scout-now}"
    exit 1
    ;;
esac
