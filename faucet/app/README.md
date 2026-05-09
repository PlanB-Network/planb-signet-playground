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
- Rate limiting (configurable, disabled by default for dev)

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
- `/api/status` endpoint showing Bitcoin node and LND online status
- Live node status indicator in the Lightning section
- Mobile-responsive layout

## Requirements

- Python 3.8+
- Bitcoin Core running on Signet (custom PlanB Signet)
- LND running on Signet (for Lightning faucet)
- The following Python packages:

```bash
pip install flask python-bitcoinrpc
```

## Configuration

Open `web_faucet.py` and update the RPC settings to match your `bitcoin.conf`:

```python
RPC_USER = 'your_rpc_username'
RPC_PASSWORD = 'your_rpc_password'
RPC_HOST = '127.0.0.1'
RPC_PORT = 38332
RPC_WALLET = 'your_wallet_name'
```

LND settings (adjust path and port if different):

```python
LNCLI_BASE = ['lncli', '--lnddir=/home/YOUR_USERNAME/.lnd-signet', '--rpcserver=127.0.0.1:10010']
LN_MAX_SATS = 10000
```

Rate limiting (set to 24 before going public):

```python
RATE_LIMIT_HOURS = 0  # 0 = disabled (dev mode), 24 = production
```

## Running

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
