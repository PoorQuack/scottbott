#!/usr/bin/env bash
# Scottbott deploy script
# Backs up the DB, pulls latest code, restarts the service, and verifies health.

set -euo pipefail

REPO_DIR="/home/ubuntu/scottbott"
DB_FILE="$REPO_DIR/scott_memory.db"
BACKUP_DIR="$REPO_DIR/backups"
SERVICE_NAME="scottbott"

echo "=== Scottbott Deploy ==="

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Back up the persistent database before touching anything
if [[ -f "$DB_FILE" ]]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/scott_memory.db.$TIMESTAMP"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "✅ Database backed up to $BACKUP_FILE"
else
    echo "⚠️  No database found at $DB_FILE — skipping backup"
fi

# Pull latest code
cd "$REPO_DIR"
echo "⬇️  Pulling latest code..."
git pull origin main

# Restart the service
echo "🔄 Restarting $SERVICE_NAME..."
sudo systemctl restart "$SERVICE_NAME"

# Wait for the service to come back up
sleep 3

# Verify health
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ $SERVICE_NAME is running"
else
    echo "❌ $SERVICE_NAME failed to start. Check logs with:"
    echo "   sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
    exit 1
fi

# Show recent logs
echo ""
echo "--- Recent logs ---"
sudo journalctl -u "$SERVICE_NAME" -n 10 --no-pager

echo ""
echo "=== Deploy complete ==="
