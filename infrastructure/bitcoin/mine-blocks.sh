#!/usr/bin/env bash
#
# Mine N signet blocks on the PlanB miner node, one every <interval> seconds.
# Run as user 'install' on the VPS.
#
#   bash mine-blocks.sh <count> <interval-seconds>
#   bash mine-blocks.sh 200 60
#
# Foreground by default. For long runs use nohup:
#   nohup bash mine-blocks.sh 200 60 > ~/.bitcoin/miner-oneoff.log 2>&1 & disown
#
# Coinbase rewards go to a single reused address persisted at
# ~/.bitcoin/miner-oneoff-address (generated on first run).

set -euo pipefail

MINER=/home/install/bitcoin-source/contrib/signet/miner
GRIND=/usr/local/bin/bitcoin-util
ADDR_FILE="$HOME/.bitcoin/miner-oneoff-address"
NBITS=1e0377ae

if [[ "$USER" != "install" ]]; then
  echo "ERROR: must run as user 'install', not '$USER'." >&2
  exit 1
fi

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <count> <interval-seconds>" >&2
  exit 1
fi

COUNT=$1
INTERVAL=$2

if ! [[ "$COUNT" =~ ^[1-9][0-9]*$ ]] || ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
  echo "ERROR: count must be a positive integer, interval a non-negative integer." >&2
  exit 1
fi

[[ -x "$MINER" ]] || { echo "ERROR: $MINER not found/executable." >&2; exit 1; }
[[ -x "$GRIND" ]] || { echo "ERROR: $GRIND not found/executable." >&2; exit 1; }
bitcoin-cli -signet getblockchaininfo >/dev/null 2>&1 || { echo "ERROR: bitcoind not reachable via RPC." >&2; exit 1; }
bitcoin-cli -signet listwallets | grep -q '"miner"' || { echo "ERROR: 'miner' wallet not loaded." >&2; exit 1; }

if [[ -s "$ADDR_FILE" ]]; then
  ADDR=$(<"$ADDR_FILE")
else
  ADDR=$(bitcoin-cli -signet -rpcwallet=miner getnewaddress)
  echo "$ADDR" > "$ADDR_FILE"
  chmod 600 "$ADDR_FILE"
fi

START_HEIGHT=$(bitcoin-cli -signet getblockcount)
START_TS=$(date +%s)

echo ">> mining $COUNT blocks at ${INTERVAL}s interval"
echo ">> coinbase address: $ADDR"
echo ">> start height:     $START_HEIGHT"

CURRENT=$START_HEIGHT
trap 'echo ">> interrupted at block $CURRENT (mined $((CURRENT - START_HEIGHT))/$COUNT)"; exit 130' INT TERM

for i in $(seq 1 "$COUNT"); do
  "$MINER" --cli="bitcoin-cli -signet" generate \
    --grind-cmd="$GRIND grind" \
    --address="$ADDR" \
    --nbits="$NBITS" \
    --set-block-time="$(date +%s)" >/dev/null

  CURRENT=$(bitcoin-cli -signet getblockcount)
  HASH=$(bitcoin-cli -signet getbestblockhash)
  echo "[$i/$COUNT] height=$CURRENT hash=${HASH:0:16}..."

  if (( i < COUNT && INTERVAL > 0 )); then
    sleep "$INTERVAL"
  fi
done

ELAPSED=$(( $(date +%s) - START_TS ))
echo ">> done. mined $((CURRENT - START_HEIGHT)) blocks in ${ELAPSED}s. tip=$CURRENT"
