#!/usr/bin/env bash
# Sweep the miner wallet (run as the main SSH user) and split the balance
# equally across the main + alice/bob/charlie LND on-chain wallets.
# Recipients whose user/node isn't ready yet are skipped.
set -euo pipefail

NETWORK="${NETWORK:-signet}"
MINER_WALLET="${MINER_WALLET:-miner}"
RECIPIENTS=(main alice bob charlie)

# LND RPC ports per recipient. Adjust bob's port once his node is provisioned.
declare -A LND_PORT=( [main]=10010 [alice]=10020 [bob]=10030 [charlie]=10040 )

bcli_main() { bitcoin-cli -"$NETWORK" -rpcwallet="$MINER_WALLET" "$@"; }

lnd_newaddr() {
  local u="$1" port="${LND_PORT[$1]}"
  if [[ "$u" == "main" ]]; then
    lncli --network="$NETWORK" \
      --lnddir="$HOME/.lnd-$NETWORK" \
      --rpcserver="localhost:$port" \
      newaddress p2wkh | jq -r '.address'
  else
    sudo -u "$u" lncli --network="$NETWORK" \
      --lnddir="/home/$u/.lnd-$NETWORK" \
      --rpcserver="localhost:$port" \
      newaddress p2wkh | jq -r '.address'
  fi
}

declare -A addr_of
existing=()
for u in "${RECIPIENTS[@]}"; do
  if [[ "$u" != "main" ]]; then
    id "$u" &>/dev/null || { echo "skip: user '$u' does not exist"; continue; }
  fi
  [[ -n "${LND_PORT[$u]:-}" ]] || { echo "skip: no LND port mapped for '$u'"; continue; }
  if addr=$(lnd_newaddr "$u" 2>/dev/null) && [[ -n "$addr" && "$addr" != "null" ]]; then
    existing+=("$u")
    addr_of[$u]="$addr"
    echo "found: $u -> $addr"
  else
    echo "skip: lncli newaddress failed for '$u' (lnd locked/down?)"
  fi
done

[[ ${#existing[@]} -gt 0 ]] || { echo "no recipients found" >&2; exit 1; }

balance=$(bcli_main getbalance)
n=${#existing[@]}
share=$(awk -v b="$balance" -v n="$n" 'BEGIN { printf "%.8f", b / n }')
[[ $(awk -v s="$share" 'BEGIN { print (s > 0) }') == 1 ]] || {
  echo "miner balance is zero ($balance BTC)" >&2; exit 1; }

outputs="{" subtract="["
first=1
for u in "${existing[@]}"; do
  [[ $first -eq 0 ]] && { outputs+=","; subtract+=","; }
  outputs+="\"${addr_of[$u]}\":$share"
  subtract+="\"${addr_of[$u]}\""
  first=0
done
outputs+="}"; subtract+="]"

echo "sending $share BTC to each of: ${existing[*]} (fee subtracted from recipients)"
txid=$(bcli_main sendmany "" "$outputs" 1 "" "$subtract")
echo "txid: $txid"
