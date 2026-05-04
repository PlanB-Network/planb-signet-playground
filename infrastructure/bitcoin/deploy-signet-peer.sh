#!/usr/bin/env bash
#
# One-click signet peer node install for the PlanB playground.
# Run as alice / bob / charlie from their own SSH session — never root.
#
#   bash deploy-signet-peer.sh
#
# Idempotent guard: aborts if ~/.bitcoin/bitcoin.conf already exists.
# To re-run, remove ~/.bitcoin and try again.

set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: refuse to run as root. Run this as alice/bob/charlie." >&2
  exit 1
fi

# user => RPC  P2P  ZMQ_BLOCK  ZMQ_TX  (offset +10/+20/+30 from miner)
declare -A PORTS=(
  [alice]="38342 38343 28342 28343"
  [bob]="38352 38353 28352 28353"
  [charlie]="38362 38363 28362 28363"
)

if [[ -z "${PORTS[$USER]:-}" ]]; then
  echo "ERROR: unsupported user '$USER'. Supported: ${!PORTS[*]}" >&2
  exit 1
fi

read -r RPC P2P ZMQ_BLOCK ZMQ_TX <<<"${PORTS[$USER]}"

CONF_DIR="$HOME/.bitcoin"
CONF="$CONF_DIR/bitcoin.conf"

if [[ -e "$CONF" ]]; then
  echo "ERROR: $CONF already exists. Remove ~/.bitcoin and re-run for a fresh install." >&2
  exit 1
fi

if ! command -v bitcoind >/dev/null 2>&1; then
  echo "ERROR: bitcoind not on PATH. The install user must deploy /usr/local/bin/bitcoind first." >&2
  exit 1
fi

VER=$(bitcoind --version | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [[ ! "$VER" =~ ^30\. ]]; then
  echo "ERROR: expected bitcoind 30.x, found $VER." >&2
  exit 1
fi

echo ">> [$USER] writing $CONF (RPC=$RPC P2P=$P2P ZMQ=$ZMQ_BLOCK,$ZMQ_TX)"
mkdir -p "$CONF_DIR"
cat >"$CONF" <<EOF
[signet]
server=1
txindex=1
rpcuser=devuser
rpcpassword=devpass123
rpcbind=127.0.0.1
rpcport=$RPC
rpcallowip=127.0.0.1
port=$P2P
zmqpubrawblock=tcp://127.0.0.1:$ZMQ_BLOCK
zmqpubrawtx=tcp://127.0.0.1:$ZMQ_TX
fallbackfee=0.00001
dbcache=2048

# PlanB custom signet — same challenge as the miner under user 'install'
signetchallenge=512103da0ee65a81d9d035a9bfff4810c5065d647153f3396b1fde56158cdf04bbace451ae
dnsseed=0

# Peer with the local mining node
addnode=127.0.0.1:38333
EOF
chmod 600 "$CONF"

echo ">> [$USER] starting bitcoind -signet -daemon"
bitcoind -signet -daemon

echo ">> [$USER] adding @reboot crontab entry (idempotent)"
CRON_LINE="@reboot /usr/local/bin/bitcoind -signet -daemon"
if ! ( crontab -l 2>/dev/null | grep -Fxq "$CRON_LINE" ); then
  ( crontab -l 2>/dev/null || true; echo "$CRON_LINE" ) | crontab -
fi

echo ">> [$USER] waiting for RPC..."
for _ in $(seq 1 30); do
  if bitcoin-cli -signet getblockchaininfo >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! bitcoin-cli -signet getblockchaininfo >/dev/null 2>&1; then
  echo "ERROR: bitcoind didn't come up within 30s. Check ~/.bitcoin/signet/debug.log" >&2
  exit 1
fi

echo
echo "=== bitcoind status ==="
bitcoin-cli -signet getblockchaininfo | grep -E '"(chain|blocks|headers|bestblockhash)"' | sed 's/^/  /'
echo "  peers: $(bitcoin-cli -signet getconnectioncount)"
echo
echo "Done."
echo "  logs:  ~/.bitcoin/signet/debug.log"
echo "  stop:  bitcoin-cli -signet stop"
echo "  next:  bash ../lnd/deploy-lnd-signet.sh"
