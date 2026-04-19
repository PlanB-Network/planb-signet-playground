# Squad 1 — Faucet

> Self-service faucet on Plan B custom Signet. Onchain + Lightning. Zero human in the loop.

## Goal

External students should be able to get test sats — onchain to a Signet address, or over Lightning to an invoice — without any manual approval.

## Deliverables

- [ ] Web UI: paste an address or LN invoice, click, receive sats
- [ ] Onchain faucet: sends to a given Signet address via `bitcoind` RPC
- [ ] Lightning faucet:
  - [ ] If user already has a channel with us → pay their invoice
  - [ ] If new user (no channel) → guide them through "open a channel with us"
- [ ] Anti-abuse: per-IP rate limit, per-address cap, daily ceiling
- [ ] Status page: faucet balance, last N drips, current limits
- [ ] (Stretch) Cashu ecash faucet

## Stack

- **Lightning:** LNbits (against the shared LND from Squad 3)
- **Onchain:** `bitcoind` RPC (Signet)
- **Frontend:** minimal — vanilla HTML/JS or small React app, your call

## Dependencies

- Squad 3 must expose: `bitcoind` RPC endpoint, LNbits API, LND node URI
- Coordinate with Squad 4 on landing-page link and visual identity

## Team

- 1 biz lead — *open*
- 3 devs — *open* (lead TBD)
- Confirmed: Golan Binder

## How to contribute

1. Open an issue tagged `squad/faucet` describing what you'll work on.
2. PRs against `faucet/` only — do not touch other squads' folders without coordination.
3. Add `qa/` test cases for any new user-facing flow.
