#!/usr/bin/env bash
# Install logrotate config for Scottbott

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)"
    exit 1
fi

cp "$SCRIPT_DIR/logrotate-scottbott" /etc/logrotate.d/scottbott
echo "Installed /etc/logrotate.d/scottbott"

# Test the config
logrotate -d /etc/logrotate.d/scottbott > /dev/null 2>&1 && echo "Logrotate config OK" || {
    echo "Logrotate config test failed"
    exit 1
}
