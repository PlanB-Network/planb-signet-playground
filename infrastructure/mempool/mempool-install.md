# Install mempool for a private signet (draft local version)

A from-scratch recipe to spin up a [mempool.space](https://mempool.space)-style
block explorer that serves our custom signet to students. No prior familiarity
with mempool is assumed.

This is a draft version tested on a local Ubuntu VM and must be adapted to work on the PlanB VPS.

## What gets deployed

Three Docker containers, orchestrated by `docker compose`:

- **`web`** — nginx + Angular SPA (the UI students see).
- **`api`** — Node.js backend. Talks to `bitcoind` over JSON-RPC + ZMQ.
- **`db`** — MariaDB. Caches block stats / mining data for the charts.

No electrs: address-history search is the only feature we give up, and block
+ transaction views work fine without it.

## Prerequisites

1. **`bitcoind` running on the custom signet** on this host — same layout as
   the squad's signet setup (datadir `~/.bitcoin-miner`, RPC `:38332`, P2P
   `:38333`, ZMQ `rawblock` `:28332` and `rawtx` `:28333`, `txindex=1`).
   Confirm with:
   ```bash
   bitcoin-cli -datadir=$HOME/.bitcoin-miner -signet getblockchaininfo
   ```
   Expect `"chain": "signet"` and a non-zero `blocks` count.
2. **Docker Engine + Docker Compose v2** installed. On Ubuntu:
   ```bash
   sudo apt install docker.io docker-compose-v2
   ```
3. ~2 GB free disk for images + MariaDB volume (the chain itself is tiny).
4. `git`, `python3` on `$PATH`.

## 1. Give your user access to Docker

By default `docker` commands need root. Add yourself to the `docker` group
once, then fully log out of the desktop session (or reboot) so the new group
is present in every shell.

```bash
sudo usermod -aG docker "$USER"
# log out of the GUI session and back in, or reboot
```

Verify after re-login:

```bash
docker ps   # must succeed without sudo
```

> **Why fully log out:** group membership is loaded at shell/session start.
> `newgrp docker` works for the one terminal you type it in, but child
> processes and new terminals started from a still-logged-in desktop won't
> have it. A full session restart is the only reliable way.

## 2. Open bitcoind RPC to the Docker bridge

The `api` container reaches `bitcoind` on the host. On Linux that means
going through the docker bridge network (`172.17.0.0/16` by default), so
RPC must accept connections from it.

Append to `~/.bitcoin-miner/bitcoin.conf`:

```ini
# Allow RPC from the docker bridge so the mempool api container can reach us.
# P2P stays loopback-only via the existing bind=127.0.0.1.
rpcbind=0.0.0.0
rpcallowip=127.0.0.1
rpcallowip=172.16.0.0/12
```

Restart `bitcoind`:

```bash
bitcoin-cli -datadir=$HOME/.bitcoin-miner -signet stop
sleep 3
bitcoind -datadir=$HOME/.bitcoin-miner -signet -daemon
bitcoin-cli -datadir=$HOME/.bitcoin-miner -signet -rpcwait getblockcount
```

> **Gotcha:** `curl` from the host to the docker bridge IP (`172.17.0.1`)
> will return **403 Forbidden** because the kernel uses the host's own IP
> as the source, not a bridge IP. This is *not* a bug — from inside a real
> container the source IP is in `172.17.0.0/16` and the allow-list matches.
> Validate from a container instead (see step 7).

## 3. Clone the upstream mempool repo

```bash
git clone https://github.com/mempool/mempool.git ~/Documents/dev/mempool
cd ~/Documents/dev/mempool
git checkout $(git tag --sort=-v:refname \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
```

At the time of writing the latest release is `v3.3.1`.

## 4. Pre-create the bind-mount directories

If you skip this, Docker will auto-create `mysql/data` and `data/` as `root`,
and the containers (running as uid 1000) will crash on startup with
"Permission denied". Creating them yourself fixes ownership up front:

```bash
mkdir -p ~/Documents/dev/mempool/mysql/data ~/Documents/dev/mempool/data
```

## 5. Drop in the compose file

Create `~/Documents/dev/mempool/docker-compose.yml` with the contents below.
The important deltas vs. the upstream sample are called out in comments.

```yaml
services:
  web:
    environment:
      FRONTEND_HTTP_PORT: "8080"
      BACKEND_MAINNET_HTTP_HOST: "api"
      MEMPOOL_NETWORK: "signet"          # single-network deployment
      BACKEND_MAINNET_ENABLED: "false"   # no mainnet anywhere
    image: mempool/frontend:planb-signet # locally built (see step 6)
    build:
      context: ./frontend
      dockerfile: Dockerfile
    user: "1000:1000"
    restart: on-failure
    stop_grace_period: 1m
    command: "./wait-for db:3306 --timeout=720 -- nginx -g 'daemon off;'"
    ports:
      - "127.0.0.1:8080:8080"           # loopback only for now
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:8080/ | grep -q '<html' || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s

  api:
    environment:
      MEMPOOL_NETWORK: "signet"
      MEMPOOL_BACKEND: "none"            # no electrs
      CORE_RPC_HOST: "host.docker.internal"
      CORE_RPC_PORT: "38332"
      CORE_RPC_USERNAME: "devuser"       # from ~/.bitcoin-miner/bitcoin.conf
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
      - "host.docker.internal:host-gateway"   # Linux-specific; resolves the host
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:8999/api/v1/backend-info | grep -q . || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

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
change `CORE_RPC_USERNAME` / `CORE_RPC_PASSWORD` on the `api` service to
match `~/.bitcoin-miner/bitcoin.conf`.

## 6. Customise the frontend (optional but recommended)

Three small edits make it obvious students aren't on a public network.

### 6a. Title

Edit `frontend/src/index.mempool.html`:

```html
<title>mempool - PlanB Signet Explorer</title>
```

### 6b. Wordmark suffix

In `frontend/src/app/components/master-page/master-page.component.html`,
there are **two** copies of the logo block (large-screen + mobile). After
each `<app-svg-images ... name="mempoolSpace" ...>` line, add:

```html
<span class="planb-signet-suffix">(PlanB Signet)</span>
```

Then in `frontend/src/app/components/master-page/master-page.component.scss`,
add one rule:

```scss
.planb-signet-suffix {
  margin-left: 0.6rem;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--transparent-fg);
  white-space: nowrap;
  align-self: center;
}
```

### 6c. Remove the network switcher

In the same `master-page.component.html`, delete the entire
`<div ngbDropdown class="dropdown-container" ...>...</div>` block
(~14 lines). There's only one.

### 6d. Stage the frontend build context and build

The upstream frontend Dockerfile expects `nginx.conf`, `nginx-mempool.conf`
and `docker/frontend/*` to be present inside `frontend/`. `docker/init.sh`
does this staging:

```bash
cd ~/Documents/dev/mempool
sh ./docker/init.sh
```

Then build the custom image (referenced as
`mempool/frontend:planb-signet` in the compose file):

```bash
docker compose build web
```

This runs `npm install` + an Angular production build inside the
container. Expect 5–15 minutes the first time (GeoIP downloads + full
frontend compile).

> If you skip step 6, delete the entire `build:` block and change the
> image line to `image: mempool/frontend:latest` to use the upstream
> image as-is.

## 7. Bring the stack up in order

```bash
cd ~/Documents/dev/mempool

docker compose up -d db
# Wait until healthy (should take <10s)
docker inspect -f '{{.State.Health.Status}}' mempool-db-1

docker compose up -d api
# Tail logs briefly; expect "The mempool is now in sync!" within ~30s
docker compose logs --tail=20 api

docker compose up -d web
```

## 8. Validate

Tip heights match:

```bash
curl -s http://127.0.0.1:8080/api/v1/blocks/tip/height ; echo
bitcoin-cli -datadir=$HOME/.bitcoin-miner -signet getblockcount
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

Open `http://127.0.0.1:8080` in a browser:

- Tab title is "mempool - PlanB Signet Explorer".
- Header wordmark says "(PlanB Signet)".
- No network switcher.
- Tip height matches the node.
- Clicking a block shows its transactions.
- Mining one more block (via the signet miner) causes the UI to update
  within a few seconds — that tests the ZMQ path.

## Daily operations

```bash
# Start / stop
docker compose -f ~/Documents/dev/mempool/docker-compose.yml up -d
docker compose -f ~/Documents/dev/mempool/docker-compose.yml down

# Tail backend logs (most useful when something looks stuck)
docker compose -f ~/Documents/dev/mempool/docker-compose.yml logs -f api

# Rebuild the frontend after further edits
cd ~/Documents/dev/mempool
docker compose build web && docker compose up -d web

# Full reset (wipes MariaDB cache; chain data on bitcoind is untouched)
docker compose -f ~/Documents/dev/mempool/docker-compose.yml down -v
# then recreate mysql/data and data/ (they get deleted with the volume)
mkdir -p ~/Documents/dev/mempool/mysql/data ~/Documents/dev/mempool/data
```

## Exposing to students

The compose file binds `127.0.0.1:8080` only. 


- **LAN bind**: change the `ports` line to `"0.0.0.0:8080:8080"`, open the
  firewall (`sudo ufw allow 8080/tcp`), share `http://<host-ip>:8080`.


For anything public, front with nginx + Let's Encrypt rather than
exposing `:8080` directly.

## Troubleshooting cheatsheet

- **`mempool-db-1` stuck "unhealthy" with InnoDB permission errors** —
  the `mysql/data` bind-mount was auto-created as root. Fix with:
  ```bash
  docker compose down
  sudo rm -rf ~/Documents/dev/mempool/mysql ~/Documents/dev/mempool/data
  mkdir -p ~/Documents/dev/mempool/mysql/data ~/Documents/dev/mempool/data
  docker compose up -d db
  ```
- **`api` can't reach bitcoind** — verify from inside a container with
  the curl snippet in step 8. If it fails, the `rpcallowip` / `rpcbind`
  change in step 2 didn't take effect (restart bitcoind and re-check the
  debug log).
- **UI shows tip 0 forever** — `MEMPOOL_NETWORK` on the `api` service
  doesn't match the node's chain. Must be `signet` for our setup.
- **Mined block doesn't appear** — confirm ZMQ ports in `bitcoin.conf`
  match what the backend expects (28332 / 28333), and that nothing is
  firewalling the docker bridge.
- **403 Forbidden when curling bitcoind from the host** — expected, not a
  bug. Use the container-based curl from step 8 to test instead.
- **`docker ps` says permission denied** — group change from step 1
  didn't take effect in this shell. Fully log out of the desktop session
  and back in.
