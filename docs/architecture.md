# Architecture Overview

## High-level diagram

```
                                          ┌──────────┐
                                          │ Sparrow  │
                                          └────┬─────┘
                                               │
   ┌────────────────────┐         ┌────────────▼────────────┐         ┌────────────────────┐
   │ External educators │◄───────►│    External student     │◄───────►│ Lightning wallet   │
   └─────────┬──────────┘         └────────────┬────────────┘         │ (Signet-supporting)│
             │                                 │                      └────────────────────┘
             ▼                                 │
   ┌────────────────────┐                      │
   │ Plan B Signet      │                      │
   │ Toolbox            │                      │
   │ ┌────────────────┐ │                      │
   │ │ Educator       │ │                      │
   │ │ vibecoders     │ │                      │
   │ │ onboarding     │ │                      │
   │ └────────────────┘ │                      │
   │ ┌────────────────┐ │                      │
   │ │ Education      │ │                      │
   │ │ webpage with   │ │                      │
   │ │ Signet         │ │                      │
   │ └────────────────┘ │                      │
   └────────┬───────────┘                      │
            │                                  ▼
            │                         ┌────────────────┐
            │                         │ Faucet         │
            │                         │ (custom Signet)│
            │                         └───────┬────────┘
            │                                 │
            ▼                                 ▼
   ┌────────────────┐  ┌──────────────┐  ┌──────────────┐
   │  External      │  │  Mini webapp │  │  Mini webapp │
   │  mini-app      │  │  1 (faucet)  │  │  2 (quiz)    │
   └────────┬───────┘  └──────┬───────┘  └──────┬───────┘
            │                 │                 │
            └─────────────────┼─────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ Plan B Signet INFRA on VPS                                   │
   │  ┌────────────┐  ┌─────┐  ┌────────────┐  ┌──────────────┐  │
   │  │ bitcoin    │  │ LND │  │ cashu mint │  │ Ark? (later) │  │
   │  │ core       │  │     │  │            │  │              │  │
   │  └────────────┘  └─────┘  └────────────┘  └──────────────┘  │
   └──────────────────────────────────────────────────────────────┘
```

## Components

### Plan B Signet INFRA (Squad 3)
The base layer. Custom Signet `bitcoind` cluster + LND nodes + LNbits. Optionally Cashu mint and Ark in later iterations. Owns the network parameters and operational runbook.

### Mini webapps (Squads 1 & 2)
Independent web surfaces consuming infra services:
- **Webapp 1 — Faucet:** distributes onchain + LN test sats.
- **Webapp 2 — Daily Quiz:** runs the daily quiz and pays out via LNbits.
- **Webapp 3** *(reserved)*: future — could be a block explorer view, channel-graph visualizer, or another educational tool.

### Plan B Signet Toolbox (Squad 4)
Front door. Educator-facing onboarding tutorial + the public education webpage that links everything together.

### External actors
- **External students** connect their Sparrow / LN wallet to the custom Signet, hit the faucet, then play the quiz.
- **External educators** use the toolbox to run their own sessions.
- **External mini-apps** (third-party) can integrate against the same infra contracts published in the runbook.

### Out of scope for MVP
- Plan B webapp wallet supporting custom Signet (too hard for 2 weeks).

## Data & trust boundaries

- All sats are **test sats** on a custom Signet. Zero real-money exposure.
- Custodial decisions (e.g. quiz payouts) must be documented in the relevant squad's README.
- Privacy default: don't log user IPs or LN addresses beyond what is needed for rate-limiting.

## Cross-squad contracts

Stable URLs and credentials are published by Squad 3 in [`infrastructure/runbook.md`](../infrastructure/runbook.md). Webapps must consume those — no out-of-band sharing of endpoints.
