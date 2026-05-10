#!/usr/bin/env bash
# Deploy / update the Plan B Signet Quiz app on the VPS.
#
# Usage (run from the VPS, e.g. /opt/planb-signet-playground/quiz/deploy):
#   ./deploy.sh           # first-time install — prompts for .env.prod values
#   ./deploy.sh update    # rebuild + restart after `git pull`
#   ./deploy.sh status    # docker compose ps
#   ./deploy.sh logs      # docker compose logs -f
#
# Prereqs on the VPS: docker, docker compose plugin, caddy (already running
# system-wide and serving the other -signet.planb.academy subdomains).

set -euo pipefail

DOMAIN="quiz-signet.planb.academy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.prod"
CADDY_SNIPPET="$SCRIPT_DIR/caddy.snippet"
CADDYFILE="/etc/caddy/Caddyfile"

cd "$SCRIPT_DIR"

cmd="${1:-init}"

case "$cmd" in
  init)
    if [[ -f "$ENV_FILE" ]]; then
      echo "$ENV_FILE already exists — refusing to overwrite."
      echo "Run './deploy.sh update' to redeploy, or delete .env.prod first."
      exit 1
    fi

    echo "First-time deploy — collecting LNbits credentials."
    read -rp "LNBITS_URL (e.g. https://lnbits-signet.planb.academy): " lnbits_url
    read -rsp "LNBITS_ADMIN_KEY: " lnbits_key
    echo

    if [[ -z "$lnbits_url" || -z "$lnbits_key" ]]; then
      echo "Both LNBITS_URL and LNBITS_ADMIN_KEY are required." >&2
      exit 1
    fi

    umask 077
    cat > "$ENV_FILE" <<EOF
LNBITS_URL=$lnbits_url
LNBITS_ADMIN_KEY=$lnbits_key
EOF
    umask 022
    echo "wrote $ENV_FILE"

    mkdir -p data

    docker compose up -d --build

    if [[ ! -f "$CADDYFILE" ]]; then
      echo "WARNING: $CADDYFILE not found — install Caddy or add the snippet manually:" >&2
      cat "$CADDY_SNIPPET" >&2
    elif ! grep -qF "$DOMAIN" "$CADDYFILE"; then
      echo "Appending Caddy block for $DOMAIN to $CADDYFILE..."
      sudo tee -a "$CADDYFILE" < "$CADDY_SNIPPET" > /dev/null
      sudo systemctl reload caddy
    else
      echo "$DOMAIN already in $CADDYFILE — skipping Caddy update."
    fi

    echo
    echo "Quiz app deployed."
    echo "  Local:  curl http://127.0.0.1:3000/api/logs"
    echo "  Public: https://$DOMAIN/api/logs (once DNS A record points here)"
    ;;

  update)
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "$ENV_FILE missing — run './deploy.sh' (no args) first." >&2
      exit 1
    fi
    docker compose up -d --build
    echo "Updated."
    ;;

  status)
    docker compose ps
    ;;

  logs)
    docker compose logs -f
    ;;

  *)
    echo "Usage: $0 [init|update|status|logs]" >&2
    exit 1
    ;;
esac
