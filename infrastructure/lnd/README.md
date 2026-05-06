# LND signet node — one-click install

`deploy-lnd-signet.sh` deploys an LND lightning node under the current user's home directory,
backed by their own `bitcoind` (deployed via
[`../bitcoin/deploy-signet-peer.sh`](../bitcoin/)).

> **Playground-grade.** The wallet is auto-created with LND's built-in
> default password `hello` (via `noseedbackup=true`) and auto-unlocks on
> every restart. Fine for the teaching signet — never use this setup with
> real funds.

## Getting the script

SSH as the target user (`alice`, `bob`, or `charlie`) and clone the repo
(read-only, no GitHub login needed):

```bash
git clone https://github.com/PlanB-Network/planb-signet-playground.git ~/planb-signet-playground
```

## Usage

```bash
# Prereq: bitcoind peer is up (see ../bitcoin/README.md)
cd ~/planb-signet-playground/infrastructure/lnd
bash deploy-lnd-signet.sh
```

The script aborts safely if `~/.lnd-signet/lnd.conf` already exists.
To start over: `pkill -u $USER -x lnd && rm -rf ~/.lnd-signet`.

## What it does

1. Writes `~/.lnd-signet/lnd.conf` with per-user ports + alias `<user>-planb` + `noseedbackup=true`
2. Starts `lnd` in the background via `nohup` (survives logout)
3. Adds an idempotent `@reboot` crontab entry that re-launches LND ~10s after boot
4. Waits for the wallet to auto-create / auto-unlock and prints node info

No `sudo` or systemd. Logs go to `~/.lnd-signet/lnd.log`.

The `noseedbackup=true` flag tells LND to generate a random seed on first
start and store the wallet under its built-in default password `hello`.
Subsequent starts auto-unlock with the same default — so `@reboot` "just
works" with no password file on disk.

## Ports

| user    | LND P2P | LND gRPC | LND REST |
|---------|---------|----------|----------|
| install | 9736    | 10010    | 8081     |
| alice   | 9746    | 10020    | 8091     |
| bob     | 9756    | 10030    | 8101     |
| charlie | 9766    | 10040    | 8111     |

P2P binds `0.0.0.0` (so peers can connect); gRPC + REST bind `127.0.0.1`
(admin macaroon access stays local).

## Operations

The script prints a paste-able `lncli` alias at the end. Once it's in
your `~/.bashrc`:

```bash
lncli getinfo                      # status, pubkey, sync
lncli newaddress p2tr              # get a signet address (then ask the miner to fund it)
lncli connect <pubkey>@<host>:<port>
lncli openchannel <pubkey> <sats>
pkill -u $USER -x lnd              # graceful-ish stop
tail -f ~/.lnd-signet/lnd.log      # live log
```

To get test signet coins, ask the miner (user `install`) to send to your
address from its bitcoind wallet, or use the playground faucet once it's
deployed.

## `install`'s LND is different (predates this script)

The mining/signing node `install` was set up **manually** before this
script existed, so it does NOT use `noseedbackup=true`. Its wallet is
encrypted and unlocks via a password
file rather than the LND default. The relevant lines in
`/home/install/.lnd-signet/lnd.conf`:

```ini
tlsextradomain=host.docker.internal
wallet-unlock-password-file=/home/install/.lnd-signet/wallet-password
```

`/home/install/.lnd-signet/wallet-password` is mode `0600`, owned by
`install` 

`tlsextradomain=host.docker.internal` was added so the mempool docker
`api` container can reach LND's REST endpoint via the Docker bridge with
TLS hostname verification intact (see
[`../mempool/claude-plans/lightning-tab-plan.md`](../mempool/claude-plans/lightning-tab-plan.md)).

`install`'s LND runs under `systemd` (unit `lnd-signet.service`), not
the `@reboot` cron used by the script-deployed nodes.
