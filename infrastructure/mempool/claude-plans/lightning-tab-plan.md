# Plan: Enable Lightning tab in mempool

## Context

The mempool block explorer at `https://mempool-signet.planb.academy` works for blocks/transactions but the Lightning tab is missing — the `api` container has no Lightning config (we deployed before any LN nodes existed).

`install`, `alice`, and `charlie` all run **LND v0.20.1-beta** . All three have channels open. Mining and gossip propagation work; install's local graph is 4 nodes / 3 edges (install + alice + charlie + asi0 test laptop, with 2 hub channels of 10 BTC each plus the 5 BTC test channel).

### Note on the public-signet pollution we found and chose to defer

Investigation showed each LND has 3 polluted peer connections to public-signet bootstrap nodes (9 total across the 3 nodes). However, **the gossip graph is already clean** on every node — LND validates incoming `channel_announcement`s against the local bitcoind, and public-signet channels never validate against our chain (we diverge from public signet at block 1 because of our `signetchallenge`). So the polluted peers exist at the BOLT init level but contribute zero data to the gossip graph that mempool reads.

A separate "Phase A" plan exists for full chain_hash isolation via `--bitcoin.signetchallenge=` on every LND; deferred for now. Worth doing later as defense-in-depth and to clean up the per-peer noise, but not required for the Lightning tab to look right.

## Approach

mempool's `api` container talks to install's LND via REST. Three things need to line up:

1. **TLS**: LND's current `tls.cert` covers `localhost`, hostname `flagrant-feedback`, and the public IP — but **not** `host.docker.internal`. Without that SAN, the api container's HTTPS call fails hostname verification. Fix: add `tlsextradomain=host.docker.internal` to lnd.conf, regenerate cert, restart LND.
2. **Credentials**: bind-mount install's `readonly.macaroon` + `tls.cert` into the `api` container at `/lnd/`.
3. **Config**: set Lightning env vars on the `api` service so the backend crawls; set `LIGHTNING=true` on `web` so the tab renders.

In v3.3.1, the same `mempool/backend` image handles graph indexing AND historical/forensics workers in one process when `LIGHTNING_ENABLED=true`. No separate container.

## Critical files

- `/home/install/.lnd-signet/lnd.conf` (VPS) — add `tlsextradomain=host.docker.internal`
- `/home/install/.lnd-signet/tls.cert` + `tls.key` (VPS) — delete, LND regenerates on restart
- `/home/install/mempool-space/docker-compose.yml` (VPS) — add Lightning env vars + bind mounts
- `infrastructure/mempool/README.md` (this repo) — append a "Lightning tab" section

## Impact on existing channels

The TLS cert is only used for LND's operator interfaces (gRPC :10010, REST :8081). Peer-to-peer LN traffic uses BOLT's Noise_XK handshake keyed off the node identity key (in `wallet.db`), not TLS. Channel state lives in `channel.db`. Neither file is touched.

The LND restart itself causes:
- ~5–30 s peer disconnect window
- channels briefly show `active=False` until peers exchange `channel_reestablish`
- `noseedbackup=true` → no unlock prompt, LND comes back unattended
- force-close risk is essentially zero with idle channels and matching LND versions on both sides

| Channel                       | Counterparty state | Reconnect | Risk |
|-------------------------------|--------------------|-----------|------|
| install ↔ alice (10 BTC)      | online, same VPS   | seconds   | none |
| install ↔ charlie (10 BTC)    | online, same VPS   | seconds   | none |
| install ↔ asi0 laptop (5 BTC) | offline (already)  | n/a       | none |

## Steps

### 1. Regenerate install's LND TLS cert (with auto-unlock fix)

> **Heads-up**: install's LND wallet is encrypted with password `helloworld`
> (it was created manually before `deploy-lnd-signet.sh` existed, so
> `noseedbackup=true` was never set). Without an auto-unlock mechanism, any
> restart leaves LND locked. We solve that here once and for all by adding
> a `wallet-unlock-password-file` directive 

On the VPS as `install`:

```bash
# 1a. Create the wallet-password file (0600, no trailing newline)
umask 077
printf 'helloworld' > ~/.lnd-signet/wallet-password
chmod 600 ~/.lnd-signet/wallet-password

# 1b. Edit ~/.lnd-signet/lnd.conf so [Application Options] contains, at the bottom:
#       tlsextradomain=host.docker.internal
#       wallet-unlock-password-file=/home/install/.lnd-signet/wallet-password
#     (Use sed/vim. If a stray tlsextradomain= line lives in [protocol] from a
#     previous attempt, remove it first.)

# 1c. Delete current cert/key so LND regenerates on next start
rm ~/.lnd-signet/tls.cert ~/.lnd-signet/tls.key

# 1d. Restart LND
sudo systemctl restart lnd-signet.service

# 1e. Wait, then verify it came back unlocked
sleep 18
sudo systemctl status lnd-signet.service --no-pager | head -8
lncli --lnddir=/home/install/.lnd-signet --rpcserver=127.0.0.1:10010 --network=signet getinfo \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("identity:", d["identity_pubkey"][:16], "synced:", d["synced_to_chain"])'

# 1f. Verify the new TLS SAN
openssl x509 -in ~/.lnd-signet/tls.cert -noout -text | grep -A1 'Subject Alternative Name'
```

If `getinfo` succeeds with `identity` printed but `synced_to_chain: False`,
LND is stuck in initial chain-backend wait because the latest signet block
is older than ~2 hours. **Mine one block to nudge it**:

```bash
bash ~/planb-signet-playground/infrastructure/bitcoin/mine-blocks.sh 1 1
```

Within ~5–10 s after the new block, `synced_to_chain` flips to `True` and
LND starts dialling peers. Then confirm channels:

```bash
lncli --lnddir=/home/install/.lnd-signet --rpcserver=127.0.0.1:10010 --network=signet \
  listchannels | python3 -c 'import sys,json; d=json.load(sys.stdin); [print(c["channel_point"], "active="+str(c["active"])) for c in d["channels"]]'
```

Expect the two hub channels (alice, charlie) back to `active=True`. The
asi0 laptop channel stays inactive (peer is offline).

### 1.5. Bind LND REST to docker bridge IPs (discovered during execution)

LND's default `restlisten=127.0.0.1:8081` is unreachable from the api
container — Docker's `host.docker.internal:host-gateway` resolves to
`172.17.0.1` (docker0 bridge), and even traffic via the mempool's own
bridge gateway (`172.18.0.1`) lands on the host kernel, not on
loopback. The mempool api gets `ECONNREFUSED 172.17.0.1:8081` if LND is
loopback-only.

**Fix**: add bridge-IP REST listeners alongside the existing loopback
one, in `~/.lnd-signet/lnd.conf` `[Application Options]`:

```ini
restlisten=127.0.0.1:8081
restlisten=172.17.0.1:8081
restlisten=172.18.0.1:8081
```

Both bridge IPs are already in the LND TLS cert SANs (LND auto-detected
local interfaces when it regenerated the cert in step 1c), so TLS still
verifies cleanly.

Why not `0.0.0.0`? `ufw` is currently inactive on this host, so binding
to `0.0.0.0` would expose `:8081` to the public IP. Bridge-IP-only
binding limits reachability to local containers, no firewall required.

After editing, restart LND once more:

```bash
sudo systemctl restart lnd-signet.service
sleep 5
bash ~/planb-signet-playground/infrastructure/bitcoin/mine-blocks.sh 1 1   # nudge sync
ss -tln | grep :8081   # expect 3 listeners: 127.0.0.1, 172.17.0.1, 172.18.0.1
```

### 2. Update `~/mempool-space/docker-compose.yml`

Add to the `api` service `environment:` block:
```yaml
LIGHTNING_ENABLED: "true"
LIGHTNING_BACKEND: "lnd"
LIGHTNING_TOPOLOGY_FOLDER: "/backend/cache/topology"
LND_REST_API_URL: "https://host.docker.internal:8081"
LND_TLS_CERT_PATH: "/lnd/tls.cert"
LND_MACAROON_PATH: "/lnd/readonly.macaroon"
```

Extend the `api` service `volumes:`:
```yaml
volumes:
  - ./data:/backend/cache
  - /home/install/.lnd-signet/tls.cert:/lnd/tls.cert:ro
  - /home/install/.lnd-signet/data/chain/bitcoin/signet/readonly.macaroon:/lnd/readonly.macaroon:ro
```

Add to the `web` service `environment:`:
```yaml
LIGHTNING: "true"
```

Files are mode `0644` owned by uid 1000; container runs as 1000:1000 → no chmod needed.

### 3. Recreate the affected containers

```bash
cd ~/mempool-space
mkdir -p ./data/topology
docker compose up -d --force-recreate api web
docker compose logs --tail=80 api
```

Look for log lines mentioning `lightning`, `topology`, `LND` connection / handshake. First crawl finishes within seconds (3-edge graph).

### 4. Update repo documentation

Append to `infrastructure/mempool/README.md` after the "Frontend customization" section:

- Heading: `## Lightning tab (applied 2026-05-05)`
- Note that v3.3.1 handles Lightning crawling inside the existing `api` container (no separate worker).
- List the env vars added to `api` and `web`.
- List the two bind mounts.
- Document the `tlsextradomain=host.docker.internal` step on LND.
- Note the small graph (4 nodes, 3 channels today; grows as students open channels).
- Cross-reference the deferred chain_hash isolation plan as a future hardening step.

## Verification

1. **TLS sanity from inside the api container**:
   ```bash
   docker exec mempool-space-api-1 sh -c \
     'wget -qO- --ca-certificate=/lnd/tls.cert \
        --header="Grpc-Metadata-macaroon: $(xxd -ps -u -c 1000 /lnd/readonly.macaroon)" \
        https://host.docker.internal:8081/v1/getinfo' | head -c 200
   ```
   Expect JSON containing `"identity_pubkey": "02f3539d…1a9f60"` (install's node).

2. **mempool API endpoint**:
   ```bash
   curl -s http://127.0.0.1:8080/api/v1/lightning/statistics/latest | head
   curl -s http://127.0.0.1:8080/api/v1/lightning/nodes/rankings/connectivity | head
   ```
   Should return non-empty after the first crawl.

3. **Browser**: open `https://mempool-signet.planb.academy`, confirm the **Lightning** tab is in the nav, and that the graph view shows install + alice + charlie + asi0, with 3 channels at the documented capacities.

4. **Logs clean**: `docker compose logs api 2>&1 | grep -iE 'lightning|lnd|tls' | tail -30` — no repeated TLS / macaroon errors.

## Rollback

Revert the compose-file changes, run `docker compose up -d --force-recreate api web`. The TLS cert change on LND is independently useful (no harm if left in place even if mempool Lightning is rolled back).
