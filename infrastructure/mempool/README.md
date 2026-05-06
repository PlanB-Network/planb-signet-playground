# Overview of the mempool install for PlanB signet playground

Objective: spin up a [mempool.space](https://mempool.space)-style
block explorer that serves our custom signet to students.

Validated on Ubuntu 24.04 (host `flagrant-feedback`, user `install`), April 2026.

## What gets deployed

Three Docker containers, orchestrated by `docker compose`:

- **`web`** — nginx + Angular SPA (the UI students see).
- **`api`** — Node.js backend. Talks to `bitcoind` over JSON-RPC + ZMQ.
- **`db`** — MariaDB. Caches block stats / mining data for the charts.

No Electrum Server for now: 
- address-history search is the only feature Electrum Server would add 
- block + transaction views work fine without it.

## Prerequisites

1. **`bitcoind` installed** (Bitcoin Core). On this host it lives at
   `/usr/local/bin/bitcoind` and uses the default datadir `~/.bitcoin`.
2. **`~/.bitcoin/bitcoin.conf`** must include the `[signet]` section with
   `server=1`, `txindex=1`, RPC credentials, ZMQ ports, and the custom
   `signetchallenge`. See step 1 for the full reference config.
3. ~2 GB free disk for Docker images + MariaDB volume.
4. `git` on `$PATH`.

## 1. Configure and start bitcoind

Your `~/.bitcoin/bitcoin.conf` should contain:

```ini
[signet]
fallbackfee=0.00001
server=1
txindex=1
rpcuser=devuser
rpcpassword=devpass123
zmqpubrawblock=tcp://0.0.0.0:28332
zmqpubrawtx=tcp://0.0.0.0:28333
dbcache=4096

# Allow RPC from the docker bridge so the mempool api container can reach us.
rpcbind=0.0.0.0
rpcallowip=127.0.0.1
rpcallowip=172.16.0.0/12

# Our custom signet — blocks signed by our key
signetchallenge=512103da0ee65a81d9d035a9bfff4810c5065d647153f3396b1fde56158cdf04bbace451ae
# Don't connect to global signet peers
dnsseed=0
```

Key points vs. a default config:
- **ZMQ binds to `0.0.0.0`** so the `api` container can receive block/tx
  notifications over the Docker bridge.
- **`rpcbind=0.0.0.0`** + `rpcallowip=172.16.0.0/12` lets the `api`
  container reach RPC via `host.docker.internal`. The `rpcallowip` restricts
  access to localhost and the Docker bridge subnet.

Start `bitcoind`:

```bash
bitcoind -signet -daemon
```

Confirm it's running:

```bash
bitcoin-cli -signet getblockchaininfo
```

Expect `"chain": "signet"` and a non-zero `blocks` count.

## 2. Install Docker

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
```

Add your user to the `docker` group:

```bash
sudo usermod -aG docker "$USER"
```

For the group change to take effect, either log out and back in, or use
`newgrp docker` / `sg docker -c "<command>"` in the current session.

Verify:

```bash
docker ps          # must succeed without sudo
docker compose version
```

## 3. Clone the upstream mempool repo

```bash
git clone https://github.com/mempool/mempool.git ~/mempool-space
cd ~/mempool-space
git checkout v3.3.1
```

## 4. Pre-create the bind-mount directories

If you skip this, Docker will auto-create `mysql/data` and `data/` as `root`,
and the containers (running as uid 1000) will crash with "Permission denied".

```bash
mkdir -p ~/mempool-space/mysql/data ~/mempool-space/data
```

## 5. Create the compose file

Create `~/mempool-space/docker-compose.yml`:

```yaml
services:
  web:
    environment:
      FRONTEND_HTTP_PORT: "8080"
      BACKEND_MAINNET_HTTP_HOST: "api"
      MEMPOOL_NETWORK: "signet"
    image: mempool/frontend:latest
    user: "1000:1000"
    restart: on-failure
    stop_grace_period: 1m
    command: "./wait-for db:3306 --timeout=720 -- nginx -g 'daemon off;'"
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      db:
        condition: service_healthy
      api:
        condition: service_started

  api:
    environment:
      MEMPOOL_NETWORK: "signet"
      MEMPOOL_BACKEND: "none"
      CORE_RPC_HOST: "host.docker.internal"
      CORE_RPC_PORT: "38332"
      CORE_RPC_USERNAME: "devuser"
      CORE_RPC_PASSWORD: "devpass123"
      DATABASE_ENABLED: "true"
      DATABASE_HOST: "db"
      DATABASE_DATABASE: "mempool"
      DATABASE_USERNAME: "mempool"
      DATABASE_PASSWORD: "mempool"
      STATISTICS_ENABLED: "true"
    image: mempool/backend:latest
    user: "1000:1000"
    restart: on-failure
    stop_grace_period: 1m
    command: "./wait-for-it.sh db:3306 --timeout=720 --strict -- ./start.sh"
    volumes:
      - ./data:/backend/cache
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy

  db:
    environment:
      MYSQL_DATABASE: "mempool"
      MYSQL_USER: "mempool"
      MYSQL_PASSWORD: "mempool"
      MYSQL_ROOT_PASSWORD: "admin"
      MARIADB_AUTO_UPGRADE: "1"
    image: mariadb:10.5.21
    user: "1000:1000"
    restart: on-failure
    stop_grace_period: 1m
    volumes:
      - ./mysql/data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "mempool", "-pmempool"]
      interval: 5s
      timeout: 5s
      retries: 10
```

If your bitcoind RPC credentials differ from `devuser` / `devpass123`,
change `CORE_RPC_USERNAME` / `CORE_RPC_PASSWORD` to match
`~/.bitcoin/bitcoin.conf`.

## 6. Bring the stack up

```bash
cd ~/mempool-space

docker compose up -d db
# Wait until healthy (~10s)
docker inspect -f '{{.State.Health.Status}}' mempool-space-db-1

docker compose up -d api
# Expect "The mempool is now in sync!" within ~30s
docker compose logs --tail=20 api

docker compose up -d web
```

## 7. Validate

Tip heights match:

```bash
curl -s http://127.0.0.1:8080/api/v1/blocks/tip/height ; echo
bitcoin-cli -signet getblockcount
```

RPC reachable from a container (sanity-check the bridge + allow-list):

```bash
docker run --rm --add-host=host.docker.internal:host-gateway \
  curlimages/curl:latest \
  -s --user devuser:devpass123 \
  --data-binary '{"jsonrpc":"1.0","id":"t","method":"getblockcount","params":[]}' \
  -H 'content-type: text/plain;' \
  http://host.docker.internal:38332/
# Expect: {"result":<height>,"error":null,"id":"t"}
```

Open `http://127.0.0.1:8080` in a browser and confirm blocks are visible.

## Daily operations

```bash
# Start / stop
docker compose -f ~/mempool-space/docker-compose.yml up -d
docker compose -f ~/mempool-space/docker-compose.yml down

# Tail backend logs (most useful when something looks stuck)
docker compose -f ~/mempool-space/docker-compose.yml logs -f api

# Full reset (wipes MariaDB cache; chain data on bitcoind is untouched)
docker compose -f ~/mempool-space/docker-compose.yml down -v
mkdir -p ~/mempool-space/mysql/data ~/mempool-space/data
docker compose -f ~/mempool-space/docker-compose.yml up -d
```

## Frontend customization (applied 2026-05-01)

See [web-customisation.md](web-customisation.md) for full details.


## Exposing to students

The compose file binds `127.0.0.1:8080` only.

- **LAN bind**: change the `ports` line to `"0.0.0.0:8080:8080"`, open the
  firewall (`sudo ufw allow 8080/tcp`), share `http://<host-ip>:8080`.

For anything public, front with nginx + Let's Encrypt rather than
exposing `:8080` directly.


## Exposing via domain name (applied 2026-05-04)

The explorer is accessible at **https://mempool-signet.planb.academy**.

A DNS `A` record points `mempool-signet.planb.academy` to the VPS IP (`86.104.228.47`). On the host, **Caddy** (v2) acts as the reverse proxy and handles TLS automatically via Let's Encrypt. The entire config lives in `/etc/caddy/Caddyfile`:

```
{
    email asi0@decouvrebitcoin.com
}

mempool-signet.planb.academy {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy listens on `:80` and `:443`, terminates TLS, and forwards plain HTTP to the `web` container on `127.0.0.1:8080` (which remains loopback-only — not directly reachable from the internet). Caddy requests and renews the Let's Encrypt certificate automatically; no Certbot or manual cert management is required.

The firewall must allow inbound `80/tcp` and `443/tcp` in addition to the existing `8080/tcp` rule (which can be removed once Caddy is in place).

## Lightning tab (applied 2026-05-06)

The Lightning tab is enabled and points the mempool backend at `install`'s LND v0.20.1-beta. With v3.3.1 of the mempool stack, the same `mempool/backend` image handles graph indexing, Lightning Stats, and forensics in one process — no separate `lightning` worker container.

Three changes were made to wire it up:

### 1. LND (`~/.lnd-signet/lnd.conf` on host `install`)

Added under `[Application Options]`:

```ini
tlsextradomain=host.docker.internal
wallet-unlock-password-file=/home/install/.lnd-signet/wallet-password
restlisten=127.0.0.1:8081
restlisten=172.17.0.1:8081
restlisten=172.18.0.1:8081
```

- `tlsextradomain` — adds `host.docker.internal` to the TLS cert's SAN list so the api container can verify the hostname. Cert was regenerated by deleting `tls.cert`/`tls.key` and restarting LND. (LND also auto-added `172.17.0.1` and `172.18.0.1` to the SAN list when it regenerated the cert.)
- `wallet-unlock-password-file` — auto-unlocks install's encrypted wallet on every restart (password `helloworld`, stored mode `0600`). Background and rationale in [`../lnd/README.md`](../lnd/README.md).
- The two extra `restlisten` lines bind LND REST to the docker bridge IPs as well as loopback. Without those, the api container reaches `host.docker.internal` (`172.17.0.1`) but LND wasn't listening there → `ECONNREFUSED`. `ufw` is inactive, so we bind to bridge IPs only — never to `0.0.0.0`.

### 2. mempool docker-compose (`~/mempool-space/docker-compose.yml`)

Added to the `api` service `environment:`:

```yaml
LIGHTNING_ENABLED: "true"
LIGHTNING_BACKEND: "lnd"
LIGHTNING_TOPOLOGY_FOLDER: "/backend/cache/topology"
LND_REST_API_URL: "https://host.docker.internal:8081"
LND_TLS_CERT_PATH: "/lnd/tls.cert"
LND_MACAROON_PATH: "/lnd/readonly.macaroon"
```

Added to the `api` service `volumes:` (read-only bind mounts of LND credentials):

```yaml
- /home/install/.lnd-signet/tls.cert:/lnd/tls.cert:ro
- /home/install/.lnd-signet/data/chain/bitcoin/signet/readonly.macaroon:/lnd/readonly.macaroon:ro
```

Added to the `web` service `environment:` (makes the Angular SPA render the Lightning tab in the nav):

```yaml
LIGHTNING: "true"
```

Both files are mode `0644` owned by uid 1000; the containers run as `1000:1000`, so no chmod is required.

### 3. Topology cache directory

```bash
mkdir -p ~/mempool-space/data/topology
docker compose up -d --force-recreate api web
```

### Current graph

By design the graph is small: 4 nodes (install, alice, charlie, asi0 test laptop) and 3 channels (10 BTC alice↔install, 10 BTC charlie↔install, 5 BTC install↔asi0). It will grow as students bring up their own LND nodes and open channels.

### Future hardening (deferred)

LND's default chain hash is the standard signet genesis hash, so our LNDs accept BOLT init handshakes from public-signet bootstrap nodes. The gossip graph stays clean (LND filters channel announcements that don't validate against our bitcoind), but the per-peer noise is real. Setting `bitcoin.signetchallenge=` on every LND in the playground would derive a custom chain hash and cleanly block all public-signet peer connections at the BOLT init layer. See `infrastructure/mempool/lightning-tab-plan.md` for the analysis.

## Troubleshooting

- **`mempool-space-db-1` stuck "unhealthy" with InnoDB permission errors** —
  the `mysql/data` bind-mount was auto-created as root. Fix:
  ```bash
  docker compose down
  sudo rm -rf ~/mempool-space/mysql ~/mempool-space/data
  mkdir -p ~/mempool-space/mysql/data ~/mempool-space/data
  docker compose up -d db
  ```
- **`api` can't reach bitcoind** — verify from inside a container with
  the curl snippet in step 7. If it fails, check that `rpcbind` and
  `rpcallowip` are set in `~/.bitcoin/bitcoin.conf` and restart bitcoind.
- **UI shows tip 0 forever** — `MEMPOOL_NETWORK` on the `api` service
  doesn't match the node's chain. Must be `signet`.
- **Mined block doesn't appear** — confirm ZMQ ports in `bitcoin.conf`
  match what the backend expects (28332 / 28333) and that ZMQ binds to
  `0.0.0.0`, not `127.0.0.1`.
- **403 Forbidden when curling bitcoind from the host** — expected, not a
  bug. The kernel uses the host's own IP as source, not a bridge IP. Use the
  container-based curl from step 7 to test instead.
- **`docker ps` says permission denied** — group change from step 2
  didn't take effect. Log out and back in, or use `sg docker -c "..."`.
