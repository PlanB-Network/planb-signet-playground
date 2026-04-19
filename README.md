# ₿ Plan B Signet Playground

**A private Bitcoin network built for learning — where every cohort can build, experiment, and break things without consequences.**

[![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)
[![Bitcoin: Custom Signet](https://img.shields.io/badge/Bitcoin-Custom%20Signet-blue.svg)](#how-does-our-network-work)
[![Status: MVP in progress](https://img.shields.io/badge/Status-MVP%20in%20progress-yellow.svg)](#the-squads)

**Timeline:** 2-week MVP · **Team:** ~12 students across 4 squads + QA

---

## What Is This?

You know how Bitcoin works — people send coins, transactions get confirmed in blocks, and the whole thing runs on a global network. But what if you could have **your own version of that entire network** — one that's free to use, instant, and completely safe to experiment with?

That's what this is.

**Plan B Signet Playground** is a private Bitcoin network run by [Plan ₿ Network](https://planb.network). It behaves exactly like the real Bitcoin network — same rules, same transaction format, same everything — except:

- The coins have **no real value** (so you can't lose money)
- Blocks are created **instantly** (no 10-minute waits)
- **We control it** (so we can tailor it for learning)

Think of it like a flight simulator for Bitcoin. Everything feels real, but you can't crash.

### What Can You Do With It?

- **Get free test sats** from the [faucet](./faucet/) — onchain or over Lightning
- **Win sats by answering Bitcoin questions** in the [daily quiz](./quiz/)
- **See your transactions live** in the block explorer
- **Connect your own wallet** (Sparrow, a Lightning wallet that supports custom Signet)
- **Build apps on top of it** — wallets, payment tools, anything you'd build on real Bitcoin

It's permanent. Every new cohort picks up where the last one left off.

---

## Why Not Just Use Testnet?

Bitcoin already has test networks, but they all have trade-offs for education:

| Network | How Fast? | Who Can Join? | Who Runs It? | Good For |
|---------|-----------|--------------|--------------|----------|
| **Real Bitcoin** | ~10 min per block | Everyone | Nobody (decentralized) | Real money |
| **Testnet** | ~10 min per block | Everyone | Nobody | General testing |
| **Our Custom Signet** ✦ | **Instant** ⚡ | **Plan B students & guests** | **Us** | **Learning** |
| **Regtest** | Instant | Just your computer | You alone | Solo practice |

The problem with Testnet is it's slow (10 minutes between blocks) and public (random people spam it). Regtest is fast but it's solo — you can't practice with classmates.

**Our Custom Signet** is the best of both: instant blocks **and** a shared network where everyone can interact.

---

## How Does Our Network Work?

On the real Bitcoin network, miners compete to create blocks using massive computing power. On our network, we skip all that. Instead, we use a **digital key** — whoever holds this key can create a new block instantly, on demand.

This is what makes it a "Signet" — blocks are **signed** (approved) by a specific key instead of being mined. The rest works identically to real Bitcoin: same transaction rules, same addresses, same wallet software.

> **In plain terms:** We have a master key that lets us press a "create block" button whenever we want. Students' transactions follow the real Bitcoin rules — our key just decides *when* those transactions get confirmed.

Right now, one person holds the signing key (simple to manage). In the future, we can require 2-out-of-3 people to agree before creating a block — teaching governance and shared responsibility.

---

## The Squads

The MVP is built by **four squads + a QA track**, each owning one folder. Pick a squad, read its README, ship.

| Squad | Folder | Mission | Status |
|---|---|---|---|
| 1 — Faucet | [`faucet/`](./faucet/) | Self-service onchain + Lightning faucet | 🔴 open slots |
| 2 — Daily Quiz | [`quiz/`](./quiz/) | Daily BTC quiz that pays out sats | 🔴 open slots |
| 3 — Infrastructure | [`infrastructure/`](./infrastructure/) | Custom Signet, LND, LNbits on VPS | 🟢 staffed |
| 4 — Website & Docs | [`website/`](./website/) | Public front door + onboarding | 🔴 open slots |
| QA | [`qa/`](./qa/) | Cross-squad testing & UX sign-off | 🟡 1 slot open |

Each squad picks its own stack. Cross-squad contracts (RPC endpoints, API URLs, network params) are published by Squad 3 in [`infrastructure/runbook.md`](./infrastructure/runbook.md).

---

## How It All Connects

Here's the flow when a student uses the playground:

```
Student opens the website (Squad 4)
        │
        ▼
"I need test sats" ──► Faucet (Squad 1) ──► Bitcoin node (Squad 3)
        │                                          │
        ▼                                          ▼
"I want to send onchain                      Block signer creates a block
 to a classmate" ─────────► bitcoind ◄────── (transaction is now confirmed)
        │
        ▼
"I want to win more sats" ──► Daily Quiz (Squad 2) ──► LNbits payout (Squad 3)
        │
        ▼
QA verifies the whole flow works for an external student.
```

A more complete component view lives in [`docs/architecture.md`](./docs/architecture.md).

---

## Project Structure

This is a **monorepo**. Each squad owns one top-level folder; project-level docs live in `docs/`.

```
planb-signet-playground/
├── README.md                  ← you are here
├── PURPOSE.md                 ← vision, principles, definition of done
├── .gitignore
│
├── infrastructure/            ← Squad 3
│   ├── README.md
│   └── runbook.md             ← restart/debug the network without asi0
│
├── faucet/                    ← Squad 1
│   └── README.md
│
├── quiz/                      ← Squad 2
│   └── README.md
│
├── website/                   ← Squad 4
│   └── README.md
│
├── qa/                        ← cross-squad testing
│   └── README.md
│
└── docs/
    └── architecture.md        ← system overview diagram
```

Each squad README documents its own goal, deliverables, stack, dependencies, and team.

---

## MVP Definition of Done

A new external student lands on the website, follows the tutorial, gets sats from the faucet, plays the quiz, and wins sats — all on custom Signet, with **no human in the loop**. The infra survives a VPS reboot via documented runbook. QA has signed off on the happy path for all three webapps.

Full criteria in [`PURPOSE.md`](./PURPOSE.md).

---

## Roadmap

The MVP is just the foundation. Once it's solid, we'll layer on:

| Phase | What | Why |
|-------|------|-----|
| **MVP** *(current)* | Custom Signet · onchain + LN faucet · daily quiz · website · QA sign-off | Get external students transacting end-to-end |
| **Phase 2** | Cashu ecash faucet · richer educator toolbox · Mempool.space instance | Broaden the surface students can play with |
| **Phase 3** | Ark · Fedimint · multi-signer block-signing governance | Explore L2/L3 and shared responsibility |
| **Phase 4** | RGB / smart contracts · Plan B webapp wallet for custom Signet | Build anything on Bitcoin |

---

## Getting Started

### As a contributor (squad member)

1. Read [`PURPOSE.md`](./PURPOSE.md) and [`docs/architecture.md`](./docs/architecture.md).
2. Open your squad's folder and read its `README.md`.
3. Open issues / PRs against your squad's folder only — coordinate before touching shared concerns.

### As an external student (after MVP launch)

Head to the [website](./website/) and follow the onboarding. You'll need:

- A Bitcoin wallet that supports custom Signet (e.g. **Sparrow**)
- A Lightning wallet that supports custom Signet (TBD — see [`docs/architecture.md`](./docs/architecture.md))

---

## Contributing

We welcome contributions from Plan B students, alumni, and the broader Bitcoin community. Issues are tagged per squad (`squad/faucet`, `squad/quiz`, `squad/infra`, `squad/website`, `squad/qa`); pick one and dive in.

---

## License

[MIT License](LICENSE)
