# Infrastructure Runbook

> Anyone on the infra squad should be able to follow this and recover the network without asi0.

## Network parameters

- **Network name:** *TBD*
- **Signet challenge:** *TBD*
- **Network magic:** *TBD*
- **Seed nodes:** *TBD*
- **Block interval:** *TBD (target: every X minutes)*

## Topology

```
                ┌─────────────────────────┐
                │  bitcoind coordinator   │  ← signs blocks via cron
                └──────────┬──────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         peer node    peer node    peer node
              │            │            │
              └─────┬──────┴────────────┘
                    │
              ┌─────┴─────┐
              ▼           ▼
            LND #1      LND #2
              │           │
              └─────┬─────┘
                    ▼
                  LNbits
```

*(replace with actual VPS hostnames + IPs once provisioned)*

## VPS inventory

| Host | Role | Owner | Notes |
|---|---|---|---|
| *TBD* | bitcoind coordinator | | |
| *TBD* | bitcoind peer | | |
| *TBD* | LND + LNbits | | |

## Public contracts (consumed by Squads 1 & 2)

- `bitcoind` RPC: *TBD*
- LND gRPC / REST: *TBD*
- LNbits API base URL: *TBD*

## Operational procedures

### Start / restart a node
*TBD — fill in once docker-compose is in place.*

### Block signing — verify it's running
*TBD*

### Rotate LND macaroon
*TBD*

### Open a channel between LND nodes
*TBD*

### Recover from coordinator crash
*TBD*

### Add a new peer node
*TBD*

## On-call

- Primary: *TBD*
- Backup: *TBD*

## Incident log

| Date | What broke | Root cause | Fix | Followup |
|---|---|---|---|---|
| | | | | |
