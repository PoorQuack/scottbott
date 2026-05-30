#!/usr/bin/env bash
# Scottbott database backup script
# Backs up the database with a timestamp

set -euo pipefail

REPO_DIR="/home/ubuntu/scottbott"
DB_FILE="$REPO_DIR/scott_memory.db"
BACKUP_DIR="$REPO_DIR/backups"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Back up the persistent database
if [[ -f "$DB_FILE" ]]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/scott_memory.db.$TIMESTAMP"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "Database backed up to $BACKUP_FILE"
    
    # Clean up backups older than 30 days
    find "$BACKUP_DIR" -name "scott_memory.db.*" -mtime +30 -delete
    echo "Cleaned up backups older than 30 days"
else
    echo "No database found at $DB_FILE, skipping backup"
    exit 1
fi
