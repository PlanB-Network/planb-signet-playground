# Bitcoin Signet Faucet

A beginner-friendly web faucet for a custom Bitcoin Signet network. Lets students request free test coins (on-chain and Lightning) and includes step-by-step setup guides for wallets and nodes — built for the PlanB Academy developer sessions.

## What is Signet?

Signet is a safe Bitcoin test network — it works exactly like real Bitcoin but the coins have no value. It's ideal for learning and development without risking real money.

## Features

### Bitcoin (on-chain)
- Request 0.001 signet BTC per address
- Address validation with live preview
- Clickable transaction ID linking to the PlanB Signet explorer
- Step-by-step wallet guides for beginners (Windows, Mac, Linux) covering Bitcoin Core, Bitcoin Knots, and Sparrow Wallet

### Lightning
- Pay up to 10,000 sats to a Signet Lightning invoice (`lnbcrt...`)
- Invoice validation with live preview
- Captcha to prevent abuse
- Rate limiting (24h by default, configurable through `.env`)
- Daily distribution ceilings for anti-abuse

### New User Guides
- **Bitcoin:** OS-aware setup guides for Bitcoin Core, Bitcoin Knots, and Sparrow Wallet
- **Lightning:** Plain-English explainers, glossary, and full wallet setup guides for:
  - **Zeus** (mobile — Android & iOS) with YouTube setup video
  - **Alby** (browser extension) with YouTube setup video
  - **LND + RTL / ThunderHub** (desktop) with YouTube setup video and 11-step beginner walkthrough, copy buttons on every command, and a quick reference cheat sheet
- FAQ accordion covering 9 common beginner questions
- Troubleshooting accordion for the most common LND errors
- Quiz reward context — students learn their Lightning wallet also receives quiz payouts via LNBits

### General
- CAPTCHA on both Bitcoin and Lightning forms
- SQLite-backed rate limiting per address / IP
- `/api/status` endpoint showing Bitcoin/LND status, wallet balance, limits, daily distribution totals, and recent drips
- Live node status indicator in the Lightning section
- Mobile-responsive layout

## Requirements

- Python 3.8+
- Bitcoin Core running on Signet (custom PlanB Signet)
- LND running on Signet (for Lightning faucet)
- The Python packages in `requirements.txt`:

```bash
python3 -m pip install -r requirements.txt
```

## Configuration

Copy the example file and fill deployment-specific values:

```bash
cp .env.example .env
```

Important keys:

| Key | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Required production session secret; never commit the real value. |
| `RPC_USER`, `RPC_PASSWORD`, `RPC_HOST`, `RPC_PORT`, `RPC_WALLET` | Bitcoin Core RPC endpoint exposed by the Signet stack. |
| `BTC_AMOUNT`, `EXPLORER_URL` | On-chain drip amount and explorer base URL. |
| `RATE_LIMIT_HOURS`, `BTC_DAILY_LIMIT_SATS`, `LN_DAILY_LIMIT_SATS` | Anti-abuse limits. Defaults are production-safe. |
| `LNCLI_BIN`, `LND_DIR`, `LND_RPCSERVER`, `LN_MAX_SATS` | Temporary local `lncli` backend until the Squad 3 LNbits contract replaces it. |
| `DB_PATH`, `LOG_DIR` | Runtime SQLite and log locations. |

For throwaway local development only, set `ALLOW_INSECURE_DEV_SECRET=true` if you do not want to generate a Flask secret yet.

## Running

```bash
./start_all.sh
```

Or directly:

```bash
python3 web_faucet.py
```

The faucet runs on `http://localhost:5000` by default.

## Systemd Services

Four user-level systemd services manage the full stack:

| Service | Description |
|---|---|
| `rpc-proxy.service` | IPv4→IPv6 bridge for Bitcoin RPC |
| `bitcoind-signet.service` | Bitcoin Core on Signet |
| `lnd-signet.service` | LND Lightning node on Signet |
| `web-faucet.service` | Flask web faucet |

```bash
systemctl --user start rpc-proxy bitcoind-signet lnd-signet web-faucet
```

## Bitcoin Core Signet config (bitcoin.conf)

```ini
[signet]
server=1
txindex=1
rpcuser=youruser
rpcpassword=yourpass
signetchallenge=512103da0ee65a81d9d035a9bfff4810c5065d647153f3396b1fde56158cdf04bbace451ae
dnsseed=0
addnode=86.104.228.47:38333
fallbackfee=0.0002
zmqpubrawblock=tcp://127.0.0.1:28332
zmqpubrawtx=tcp://127.0.0.1:28333
```

## LND Signet config (lnd.conf)

```ini
[Bitcoin]
bitcoin.active=1
bitcoin.signet=1
bitcoin.node=bitcoind
bitcoin.signetchallenge=PASTE_FROM_INSTRUCTOR

[Bitcoind]
bitcoind.rpchost=127.0.0.1
bitcoind.rpcuser=YOUR_RPC_USER
bitcoind.rpcpass=YOUR_RPC_PASS
bitcoind.zmqpubrawblock=tcp://127.0.0.1:28332
bitcoind.zmqpubrawtx=tcp://127.0.0.1:28333

[Application Options]
lnddir=/home/YOUR_USERNAME/.lnd-signet
rpclisten=localhost:10010
restlisten=localhost:8080
```

## Port Reference

| Service | Port |
|---|---|
| Bitcoin RPC (Signet) | 38332 |
| Bitcoin P2P (Signet) | 38333 |
| LND gRPC | 10010 |
| LND REST | 8080 |
| LND P2P | 9737 |
| Web faucet | 5000 |

## Hub Connection

Students connect their LND node to the PlanB hub to open channels:

```bash
# Get hub pubkey from instructor, then:
lncli --lnddir=~/.lnd-signet --rpcserver=localhost:10010 connect <HUB_PUBKEY>@86.104.228.47:9737
lncli --lnddir=~/.lnd-signet --rpcserver=localhost:10010 openchannel --node_key=<HUB_PUBKEY> --local_amt=50000
```
