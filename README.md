# ₿ PlanB Signet Playground

**A private Bitcoin network built for learning — where every cohort can build, experiment, and break things without consequences.**

[![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)
[![Bitcoin: Signet](https://img.shields.io/badge/Bitcoin-Custom%20Signet-blue.svg)](#how-does-our-network-work)
[![Status: In Development](https://img.shields.io/badge/Status-In%20Development-yellow.svg)](#what-were-building-phase-1)

---

## What Is This?

You know how Bitcoin works - people send coins, transactions get confirmed in blocks, and the whole thing runs on a global network. But what if you could have **your own version of that entire network** - one that's free to use, instant, and completely safe to experiment with?

That's what this is.

**PlanB Signet Playground** is a private Bitcoin network run by [Plan ₿ Network](https://planb.network). It behaves exactly like the real Bitcoin network — same rules, same transaction format, same everything — except:

- The coins have **no real value** (so you can't lose money)
- Blocks are created **instantly** (no 10-minute waits)
- **We control it** (so we can tailor it for learning)

Think of it like a flight simulator for Bitcoin. Everything feels real, but you can't crash.

### What Can You Do With It?

- **Get free test coins** from the faucet and send them to each other
- **See your transactions live** in the block explorer (like a Bitcoin search engine)
- **Follow guided exercises** — generate your own keys, build transactions by hand, understand what's really happening under the hood
- **Build apps on top of it** — wallets, payment tools, anything you'd build on real Bitcoin

It's permanent. Every new cohort picks up where the last one left off.

---

## Why Not Just Use Testnet?

Bitcoin already has test networks, but they all have trade-offs for education:

| Network | How Fast? | Who Can Join? | Who Runs It? | Good For |
|---------|-----------|--------------|--------------|----------|
| **Real Bitcoin** | ~10 min per block | Everyone | Nobody (decentralized) | Real money |
| **Testnet** | ~10 min per block | Everyone | Nobody | General testing |
| **Our Custom Signet** ✦ | **Instant** ⚡ | **Our students** | **Us** | **Learning** |
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

## What We're Building (Phase 1)

Phase 1 is the foundation — getting the core network live so students can send their first transactions.

### The 6 Pieces

```
┌─────────────────────────────────────────────────────────┐
│                    What Students See                     │
│                                                         │
│   📊 Dashboard          🔍 Block Explorer               │
│   Network stats,        Search transactions,            │
│   faucet, exercises     view blocks (Mempool.space)     │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                    Behind the Scenes                     │
│                                                         │
│   🖥 API Server         🗄 Database                     │
│   Handles requests,     Tracks users, exercises,        │
│   talks to Bitcoin      and faucet history               │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                    The Bitcoin Layer                     │
│                                                         │
│   🔲 Bitcoin Node       ⛏ Block Signer                 │
│   Runs our private      Creates new blocks              │
│   network               on demand                       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Here's what each piece does:

| # | Component | What It Does | Analogy |
|---|-----------|-------------|---------|
| 1 | **Bitcoin Node** | Runs our private Bitcoin network. Validates transactions, stores the blockchain. | The engine of the car |
| 2 | **Block Signer** | Creates new blocks whenever we want — instantly confirming transactions. | The "print new page" button for the ledger |
| 3 | **Block Explorer** | A website where you can look up any transaction, address, or block on our network. | Google, but for Bitcoin transactions |
| 4 | **Faucet** | Gives students free test coins. You enter your address, it sends you Bitcoin. | An ATM that gives out free practice money |
| 5 | **Dashboard** | The main website students use — shows network stats, access to the faucet, and guided exercises. | The classroom portal |
| 6 | **Database** | Stores user accounts, which exercises they've completed, and faucet history. | The gradebook |

### Phase 1 Checklist

- [x] Project architecture & documentation
- [ ] Custom signet node with challenge script
- [ ] Block signer/miner (on-demand + interval)
- [ ] Self-hosted Mempool block explorer
- [ ] Faucet API with rate limiting
- [ ] React dashboard with network stats
- [ ] PostgreSQL + Prisma for user & exercise data
- [ ] Guided exercises: key generation, creating transactions

---

## How It All Connects

Here's the flow when a student uses the platform:

```
Student opens the Dashboard
        │
        ▼
"I need test coins" ──► Faucet sends coins ──► Bitcoin Node records the transaction
        │                                              │
        ▼                                              ▼
"I want to send coins                          Block Signer creates a block
 to a classmate" ──────► Bitcoin Node ◄──────── (transaction is now confirmed)
        │                                              │
        ▼                                              ▼
"Did it work?" ────────► Block Explorer shows the confirmed transaction
        │
        ▼
"Mark exercise done" ──► API saves progress to the Database
```

---

## Tech Stack (What We're Building With)

For non-technical readers: these are the tools and programming languages we use. For developers: this is your setup guide.

| What | Tool | One-Line Explanation |
|------|------|---------------------|
| **Bitcoin Network** | Bitcoin Core 28+ | The official Bitcoin software, configured to run our private network |
| **Block Explorer** | Mempool.space | Open-source transaction viewer — we run our own copy pointed at our network |
| **Backend API** | Fastify + TypeScript | The server that handles requests (faucet, exercises, network data) |
| **Frontend Dashboard** | React + Vite + Tailwind | The website students interact with |
| **Database** | PostgreSQL | Stores users, exercises, and faucet records |
| **Cache** | Redis | Prevents faucet abuse (rate limiting) and powers real-time updates |
| **ORM** | Prisma | Translates between our code and the database — auto-generates types |

---

## Project Structure

The project is split into **three repositories** — each can be developed and run independently.

| Repository | What | Built With |
|-----------|------|-----------|
| **planb-signet-playground** (this repo) | Bitcoin node config, database schema, scripts, docs | Bitcoin Core, Prisma, Shell |
| **planb-signet-api** | Backend server — faucet, network info, exercise tracking | Fastify, TypeScript, Prisma |
| **planb-signet-dashboard** | The website students use | React, Vite, Tailwind CSS |

<details>
<summary><b>📁 This repo — Infrastructure & Config</b></summary>

```
planb-signet-playground/
├── node/
│   ├── bitcoin.conf                 # Our private network's settings
│   └── scripts/
│       ├── init-wallet.sh           # Sets up the signing wallet
│       └── mine-blocks.sh           # Creates new blocks
├── explorer/
│   └── mempool-config.json          # Points Mempool.space at our network
├── database/prisma/
│   ├── schema.prisma                # Database table definitions
│   └── seed.ts                      # Pre-loads exercises into the database
├── scripts/
│   ├── setup.sh                     # One-command setup
│   ├── generate-signet-keys.sh      # Creates the block-signing key
│   └── health-check.sh              # Checks everything is running
├── docs/exercises/                   # Student exercise guides
├── .env.example                      # Configuration template
└── LICENSE
```

</details>

<details>
<summary><b>📁 planb-signet-api — Backend</b></summary>

```
planb-signet-api/
├── src/
│   ├── index.ts                     # Server startup
│   ├── routes/
│   │   ├── network.ts               # "What's the network status?"
│   │   ├── blocks.ts                # "Show me recent blocks" / "Mine a block"
│   │   ├── faucet.ts                # "Send me test coins"
│   │   └── wallet.ts                # Wallet helpers for exercises
│   ├── services/
│   │   ├── bitcoin-rpc.ts           # Talks to the Bitcoin node
│   │   ├── faucet.ts                # Faucet logic + abuse prevention
│   │   ├── miner.ts                 # Triggers block creation
│   │   └── network.ts               # Reads chain stats
│   ├── plugins/
│   │   ├── auth.ts                  # Checks API keys
│   │   └── rate-limit.ts            # Prevents spam
│   └── lib/
│       ├── config.ts                # Loads settings from .env
│       ├── logger.ts                # Structured logging
│       └── prisma.ts                # Database connection
├── prisma/schema.prisma
├── .env.example
├── package.json
└── tsconfig.json
```

</details>

<details>
<summary><b>📁 planb-signet-dashboard — Frontend</b></summary>

```
planb-signet-dashboard/
├── index.html
├── src/
│   ├── main.tsx                     # App entry point
│   ├── router.tsx                   # Page routing
│   ├── pages/
│   │   ├── Home.tsx                 # Network overview + stats
│   │   ├── Faucet.tsx               # "Get test coins" page
│   │   ├── Explorer.tsx             # Block explorer view
│   │   ├── Exercises.tsx            # Exercise list
│   │   └── Exercise.tsx             # Single exercise page
│   ├── components/                  # Reusable UI pieces
│   └── lib/
│       ├── api-client.ts            # Talks to the backend API
│       └── hooks.ts                 # Data fetching helpers
├── vite.config.ts
├── tailwind.config.ts
├── .env.example                     # Set VITE_API_URL here
├── package.json
└── tsconfig.json
```

</details>

---

## Database

We store five types of data:

| Table | What It Stores | Example |
|-------|---------------|---------|
| **Cohorts** | Each class/group | "Cohort 7 — Jan 2027" |
| **Users** | Students in each cohort | "Alice, pubkey abc123..." |
| **Faucet Requests** | Every time someone asked for test coins | "Alice got 10,000 sats to address tb1q..." |
| **Exercises** | The available exercises | "Generate your first keypair" |
| **Exercise Progress** | Who completed what | "Alice completed Exercise 1 on March 5" |

Redis (an in-memory cache) handles the fast stuff: rate limiting the faucet (so nobody spams it) and pushing real-time block notifications to the dashboard.

---

## What's Next? (Future Phases)

Phase 1 is the foundation. Once it's solid, we'll layer on:

| Phase | What | Why |
|-------|------|-----|
| **2 — Lightning** | Instant payments via payment channels | Learn how Bitcoin scales |
| **3 — L3 Protocols** | Cashu (privacy tokens), Ark (scaling), Fedimint (community banks) | Explore the cutting edge |
| **4 — Advanced** | Smart contracts (RGB), multi-signer governance | Build anything on Bitcoin |

---

## Prerequisites

- **Bitcoin Core 28+** — compiled with signet support
- **Node.js 20+** and **npm 10+**
- **PostgreSQL 16+**
- **Redis 7+**
- **Git**

---

## Getting Started

### 1. Infrastructure (this repo)

```bash
git clone https://github.com/planb-network/planb-signet-playground.git
cd planb-signet-playground

cp .env.example .env
# Edit .env with your Bitcoin RPC credentials, DB connection, Redis URL

./scripts/generate-signet-keys.sh

bitcoind -conf=$(pwd)/node/bitcoin.conf -daemon

npx prisma migrate deploy --schema=database/prisma/schema.prisma
npx prisma db seed --schema=database/prisma/schema.prisma
```

### 2. Backend API

```bash
git clone https://github.com/planb-network/planb-signet-api.git
cd planb-signet-api

cp .env.example .env
# Edit .env — set DATABASE_URL, REDIS_URL, BITCOIN_RPC_* vars

npm install
npm run dev             # Development (watch mode)
```

### 3. Frontend Dashboard

```bash
git clone https://github.com/planb-network/planb-signet-dashboard.git
cd planb-signet-dashboard

cp .env.example .env
# Edit .env — set VITE_API_URL to point to the running API

npm install
npm run dev             # Development at http://localhost:5173
```

---

## Contributing

We welcome contributions from PlanB students, alumni, and the broader Bitcoin community.

---

## License

[MIT License](LICENSE)

