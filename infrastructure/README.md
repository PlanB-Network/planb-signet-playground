# Squad 3 — Infrastructure

> Operates the private custom Signet network and shared Lightning services that power the rest of the playground.

## Goal

A custom Signet that everyone — webapps and external students alike — can rely on. Self-healing where possible, debuggable by anyone on the squad.

## Deliverables

- [ ] `bitcoind` on Signet as **main coordinator** (custom Signet challenge)
- [ ] 2–3 peer `bitcoind` nodes on separate VPS
- [ ] Cron-driven block signing at a regular interval
- [ ] 2–3 LND nodes connected in a basic topology
- [ ] LNbits instance pointed at custom Signet
- [ ] Mempool.space instance on custom Signet *(stretch)*
- [ ] Privacy-aware defaults for anyone connecting to the network
- [ ] [`runbook.md`](./runbook.md) — restart, debug, rotate keys, recover from common failures, **without asi0**

## Stack

- Linux VPS (multiple)
- `bitcoind`, LND, LNbits
- Docker / docker-compose where it simplifies
- cron (or systemd timer) for block signing

## Contracts exposed to other squads

Document these in [`runbook.md`](./runbook.md) so Squads 1 and 2 can integrate without asking:

- `bitcoind` RPC endpoint + credentials
- LND gRPC / REST endpoint + macaroon scope
- LNbits API base URL + admin/user keys
- Custom Signet network params (challenge, magic, seed nodes)

## Team — complete ✅

- asi0 *(lead)*
- Natiii (Nathalie)
- CodingInLondon
- TGA
- dillamond

## How to contribute

1. Open an issue tagged `squad/infra`.
2. Any change that touches public contracts → update [`runbook.md`](./runbook.md) **in the same PR**.
3. Coordinate maintenance windows in advance — other squads depend on this.
