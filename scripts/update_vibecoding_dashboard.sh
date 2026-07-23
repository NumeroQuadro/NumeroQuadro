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

GIT_ATTEMPTS=3
GIT_RETRY_DELAY_SECONDS=15

git_retry() {
  local operation="$1"
  shift
  local attempt=1

  while (( attempt <= GIT_ATTEMPTS )); do
    echo "Git ${operation} attempt ${attempt}/${GIT_ATTEMPTS}"
    if git -c http.version=HTTP/1.1 \
      -c http.lowSpeedLimit=1000 \
      -c http.lowSpeedTime=30 \
      "$@"; then
      return 0
    fi
    if (( attempt < GIT_ATTEMPTS )); then
      sleep "$GIT_RETRY_DELAY_SECONDS"
    fi
    (( attempt++ ))
  done

  return 1
}

if ! git_retry "pull" pull --rebase --autostash origin main; then
  echo "Unable to sync with origin/main after ${GIT_ATTEMPTS} attempts; leaving the working tree unchanged."
  exit 1
fi

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

if ! git_retry "push" push origin main; then
  echo "Push failed after ${GIT_ATTEMPTS} attempts; the generated commit remains local for the next refresh."
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Finished vibecoding dashboard refresh"
