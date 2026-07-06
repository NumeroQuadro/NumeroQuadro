#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
LOG_DIR="${HOME}/Library/Logs/NumeroQuadro"
LOCK_DIR="${TMPDIR:-/tmp}/numeroquadro-vibecoding-dashboard.lock"

mkdir -p "$LOG_DIR"
exec >> "${LOG_DIR}/vibecoding-dashboard.log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Starting vibecoding dashboard refresh"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another refresh is already running; exiting."
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$REPO_DIR"

git pull --rebase --autostash origin main

python3 scripts/generate_vibecoding_dashboard.py

if rg -n "/Users/|numero_quadro|dimonlimon|Library/Mobile Documents|\\.codex|\\.claude|\\.gemini" \
  README.md vibecoding-heatmap-2026.svg vibecoding-heatmap-interactive.html >/dev/null; then
  echo "Privacy check failed: generated public artifacts contain local/private path fragments."
  exit 1
fi

git add README.md vibecoding-heatmap-2026.svg vibecoding-heatmap-interactive.html

if git diff --cached --quiet; then
  echo "No public dashboard changes to commit."
  exit 0
fi

git commit -m "Refresh vibecoding dashboard"
git push origin main

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Finished vibecoding dashboard refresh"
