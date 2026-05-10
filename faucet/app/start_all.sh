#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ ! -f .env ]]; then
  echo "Missing $APP_DIR/.env. Copy .env.example, fill secrets, then rerun." >&2
  exit 1
fi

if ! python3 -c 'import flask, bitcoinrpc, dotenv' >/dev/null 2>&1; then
  echo "Python dependencies missing. Install with: python3 -m pip install -r requirements.txt" >&2
  exit 1
fi

exec "$APP_DIR/run_faucet.sh"
