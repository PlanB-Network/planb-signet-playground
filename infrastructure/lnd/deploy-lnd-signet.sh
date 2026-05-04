#!/usr/bin/env bash
#
# One-click LND install for the PlanB signet playground.
# Run as alice / bob / charlie AFTER deploy-signet-peer.sh has succeeded.
#
#   bash deploy-lnd-signet.sh
#
# Wallet is auto-created (password "hello") and auto-unlocks on every start.
# Playground-grade: do not use for real funds.
#
# Idempotent guard: aborts if ~/.lnd-signet/lnd.conf already exists.

set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: refuse to run as root." >&2
  exit 1
fi

# user => BTC_RPC  LND_P2P  LND_GRPC  LND_REST  ZMQ_BLOCK  ZMQ_TX
declare -A PORTS=(
  [alice]="38342 9746 10020 8091 28342 28343"
  [bob]="38352 9756 10030 8101 28352 28353"
  [charlie]="38362 9766 10040 8111 28362 28363"
)

declare -A COLORS=(
  [alice]="#7f5af0"
  [bob]="#2cb67d"
  [charlie]="#f25f4c"
)

if [[ -z "${PORTS[$USER]:-}" ]]; then
  echo "ERROR: unsupported user '$USER'. Supported: ${!PORTS[*]}" >&2
  exit 1
fi

read -r BTC_RPC LND_P2P LND_GRPC LND_REST ZMQ_BLOCK ZMQ_TX <<<"${PORTS[$USER]}"
COLOR="${COLORS[$USER]}"

LND_DIR="$HOME/.lnd-signet"
LND_CONF="$LND_DIR/lnd.conf"
TLS_CERT="$LND_DIR/tls.cert"
MACAROON="$LND_DIR/data/chain/bitcoin/signet/admin.macaroon"

if [[ -e "$LND_CONF" ]]; then
  echo "ERROR: $LND_CONF already exists. Stop LND and rm -rf ~/.lnd-signet for a fresh install." >&2
  exit 1
fi

if [[ ! -e "$HOME/.bitcoin/bitcoin.conf" ]]; then
  echo "ERROR: ~/.bitcoin/bitcoin.conf missing. Run deploy-signet-peer.sh first." >&2
  exit 1
fi
if ! bitcoin-cli -signet getblockchaininfo >/dev/null 2>&1; then
  echo "ERROR: bitcoin-cli -signet failed. Is bitcoind running?" >&2
  exit 1
fi

if ! command -v lnd >/dev/null 2>&1; then
  echo "ERROR: lnd not on PATH." >&2
  exit 1
fi

VER=$(lnd --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [[ ! "$VER" =~ ^0\.20\. ]]; then
  echo "ERROR: expected lnd 0.20.x, got $VER." >&2
  exit 1
fi

echo ">> [$USER] writing $LND_CONF (gRPC=$LND_GRPC REST=$LND_REST P2P=$LND_P2P)"
mkdir -p "$LND_DIR"

cat >"$LND_CONF" <<EOF
[Application Options]
lnddir=$LND_DIR
listen=0.0.0.0:$LND_P2P
rpclisten=127.0.0.1:$LND_GRPC
restlisten=127.0.0.1:$LND_REST
alias=$USER-planb
color=$COLOR
noseedbackup=true

[Bitcoin]
bitcoin.active=true
bitcoin.signet=true
bitcoin.node=bitcoind

[Bitcoind]
bitcoind.rpchost=127.0.0.1:$BTC_RPC
bitcoind.rpcuser=devuser
bitcoind.rpcpass=devpass123
bitcoind.zmqpubrawblock=tcp://127.0.0.1:$ZMQ_BLOCK
bitcoind.zmqpubrawtx=tcp://127.0.0.1:$ZMQ_TX

[protocol]
protocol.wumbo-channels=true
EOF
chmod 600 "$LND_CONF"

echo ">> [$USER] starting lnd (nohup, detached)"
nohup /usr/local/bin/lnd --lnddir="$LND_DIR" > "$LND_DIR/lnd.log" 2>&1 &
disown

echo ">> [$USER] adding @reboot crontab entry (idempotent)"
CRON_LINE="@reboot sleep 10 && /usr/local/bin/lnd --lnddir=$LND_DIR > $LND_DIR/lnd.log 2>&1"
if ! ( crontab -l 2>/dev/null | grep -Fxq "$CRON_LINE" ); then
  ( crontab -l 2>/dev/null || true; echo "$CRON_LINE" ) | crontab -
fi

echo ">> [$USER] waiting for LND to bootstrap wallet + macaroons (≤60s)..."
LNCLI="lncli --network=signet --rpcserver=127.0.0.1:$LND_GRPC --lnddir=$LND_DIR --tlscertpath=$TLS_CERT --macaroonpath=$MACAROON"
for _ in $(seq 1 60); do
  if [[ -e "$MACAROON" ]] && $LNCLI getinfo >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! $LNCLI getinfo >/dev/null 2>&1; then
  echo "ERROR: LND didn't come up within 60s. Check $LND_DIR/lnd.log" >&2
  exit 1
fi

echo
echo "=== lnd status ==="
$LNCLI getinfo | grep -E '"(identity_pubkey|alias|synced_to_chain|synced_to_graph|num_active_channels|block_height)"' | sed 's/^/  /'
echo
echo "Done. Wallet password is 'hello' (playground-grade — do not use for real funds)."
echo "  logs:  $LND_DIR/lnd.log"
echo "  stop:  pkill -u \$USER -x lnd"
echo
echo "Add this alias to your ~/.bashrc to skip the long flag list:"
echo "  alias lncli='lncli --network=signet --rpcserver=127.0.0.1:$LND_GRPC --lnddir=$LND_DIR --tlscertpath=$TLS_CERT --macaroonpath=$MACAROON'"
