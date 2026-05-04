# Signet peer node — one-click install

`deploy-signet-peer.sh` deploys a non-mining `bitcoind` peer of the PlanB custom signet under the
current user's home directory. Intended for users `alice`, `bob`, `charlie`
on the playground VPS.

The peer connects to the mining node (user `install`) at `127.0.0.1:38333`
and uses the same `signetchallenge`, so it will accept the miner's blocks
as valid.

## Getting the script

SSH as the target user (`alice`, `bob`, or `charlie`) and clone the repo
(read-only, no GitHub login needed):

```bash
git clone https://github.com/PlanB-Network/planb-signet-playground.git ~/planb-signet-playground
```

## Usage

```bash
cd ~/planb-signet-playground/infrastructure/bitcoin
bash deploy-signet-peer.sh
```

The script aborts safely if `~/.bitcoin/bitcoin.conf` already exists.
To start over: `bitcoin-cli -signet stop && rm -rf ~/.bitcoin` then re-run.

## What it does

1. Writes `~/.bitcoin/bitcoin.conf` with per-user ports (see table below)
2. Starts `bitcoind -signet -daemon`
3. Adds an idempotent `@reboot` line to the user's crontab so it survives reboots
4. Polls until RPC is up and prints chain status

No `sudo` or systemd is involved. Logs go to `~/.bitcoin/signet/debug.log`.

## Ports

| user    | RPC   | P2P   | ZMQ block | ZMQ tx |
|---------|-------|-------|-----------|--------|
| install | 38332 | 38333 | 28332     | 28333  |
| alice   | 38342 | 38343 | 28342     | 28343  |
| bob     | 38352 | 38353 | 28352     | 28353  |
| charlie | 38362 | 38363 | 28362     | 28363  |

RPC is bound to `127.0.0.1` only; P2P binds to `0.0.0.0` so the network can form.

## Operations

```bash
bitcoin-cli -signet getblockchaininfo     # status
bitcoin-cli -signet getpeerinfo           # peer list (should include 127.0.0.1:38333)
bitcoin-cli -signet stop                  # graceful shutdown
tail -f ~/.bitcoin/signet/debug.log       # live log
```

After installing bitcoind, run [`../lnd/deploy-lnd-signet.sh`](../lnd/) to
add a Lightning node on top.
