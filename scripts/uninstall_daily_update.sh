#!/bin/zsh
set -euo pipefail

LABEL="com.numeroquadro.vibecoding-dashboard"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"

echo "Uninstalled ${LABEL}"
