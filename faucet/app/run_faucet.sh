#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"

mkdir -p "$LOG_DIR"
cd "$APP_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

exec python3 web_faucet.py >> "$LOG_DIR/faucet.log" 2>&1
