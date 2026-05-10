# Quiz app — Plan B Signet Daily Quiz

> Express + SQLite + LNbits service that serves a daily 5-question Bitcoin quiz
> and pays 1500 sats over Lightning to anyone who scores 5/5.

## Source & credit

Imported from [`DanieleSK/quiz-planB`](https://github.com/DanieleSK/quiz-planB)
(at commit `ac8c35e`). Original authors:

- **DanieleSK** — `<visconteo@protonmail.com>`
- **Simone Da Re** — `<simonedare.office@gmail.com>`

Centralised here so the playground hosts one canonical copy of every Squad's
deliverable. The original repo is preserved for history; future changes happen
in this monorepo.

## What it does

- 21 questions sourced live from
  [`PlanB-Network/bitcoin-educational-content`](https://github.com/PlanB-Network/bitcoin-educational-content)
  (`courses/btc101/quizz/<id>/en.yml`).
- 5 questions per day, deterministically shuffled per `(date, linking_key)`
  so each user gets a stable but personalised set.
- One attempt per linking_key per day (UTC).
- Reward: 1500 sats only on a perfect 5/5, claimed via LNURL-withdraw.
- Attempts and withdraws are logged to a local SQLite DB.

## Identity & payout model

- **Identity** = LNURL-auth `linking_key` (secp256k1 pubkey, deterministic per
  `(wallet, domain)` per LUD-05). Wallets sign a challenge; we never store a
  password, email, or Lightning address.
- **Wallets** are kept on the user's side. Users who arrive without one click
  "Get one free" and we provision a fresh LNbits wallet on the Plan B Signet,
  handing them a pairing URL their phone can scan to install it as a PWA. The
  pairing URL is the only credential they receive.
- **Payout** is **LNURL-withdraw** (LUD-03). On a 5/5, we mint a withdraw QR
  the user scans with their wallet. The wallet pushes a bolt11; the Big Pot
  admin wallet on LNbits pays it. Sats land in whatever wallet did the scan.
- The Plan B custom Signet means **all wallets on both ends live in our LNbits
  instance** (signet bolt11 is not routable to mainnet). Mainnet wallets work
  for LNURL-auth signing, but **not** for receiving the withdraw — by design,
  the funnel pushes those users to provision a signet wallet.

## API

### Quiz

| Method | Path          | Auth    | Purpose                                                                 |
| ------ | ------------- | ------- | ----------------------------------------------------------------------- |
| `POST` | `/api/start`  | session | Start today's quiz, returns 5 questions seeded by `(date, linking_key)` |
| `POST` | `/api/submit` | session | Log attempt; on 5/5 returns the LNURL-withdraw payload                  |
| `GET`  | `/api/logs`   | —       | Audit list of attempts                                                  |

### LNURL-auth (LUD-04)

| Method | Path                         | Auth       | Purpose                                                      |
| ------ | ---------------------------- | ---------- | ------------------------------------------------------------ |
| `GET`  | `/api/auth/lnurl/init`       | —          | Generate `k1` + bech32 LNURL for the QR code                 |
| `GET`  | `/api/auth/lnurl/callback`   | wallet sig | Wallet hits with `?tag=login&k1=&sig=&key=` (verifies ECDSA) |
| `GET`  | `/api/auth/lnurl/status?k1=` | —          | Frontend polls; on success mints `quiz_session` cookie       |
| `GET`  | `/api/auth/me`               | session    | Returns `{ linking_key }`                                    |
| `POST` | `/api/auth/logout`           | session    | Clears the session                                           |

### Wallet provisioning (anonymous)

| Method | Path                 | Auth | Purpose                                                                   |
| ------ | -------------------- | ---- | ------------------------------------------------------------------------- |
| `POST` | `/api/wallet/create` | —    | Creates a fresh LNbits user+wallet; returns `{ pairing_url }` for the PWA |

The pairing URL is `https://lnbits-signet.planb.academy/wallet?usr=&wal=` —
opening it on a phone installs the LNbits PWA pre-loaded with the user's
credentials. The wallet is decoupled from the LNURL-auth identity: after
pairing, the user signs in with their freshly-installed PWA via the standard
LNURL-auth flow.

### LNURL-withdraw (LUD-03)

| Method | Path                       | Auth    | Purpose                                                                                         |
| ------ | -------------------------- | ------- | ----------------------------------------------------------------------------------------------- |
| `GET`  | `/api/withdraw/lnurl?k1=`  | —       | Phase 1 returns `withdrawRequest` params; phase 2 (with `pr=`) pays the bolt11 from the Big Pot |
| `GET`  | `/api/withdraw/status?k1=` | session | Frontend polls; returns `{ claimed, expired, … }`                                               |
| `GET`  | `/api/withdraw/active`     | session | Returns the user's most recent unclaimed unexpired withdraw                                     |

A successful 5/5 mints one withdraw_request with a 1-hour TTL; the row's
`claimed_at`/`payment_hash` columns serve as the audit log.

See [`test.http`](./test.http) for a runnable smoke-test sequence.

## Run locally

Prereqs: Node ≥ 20, a reachable LNbits instance, and an admin wallet on it.

```bash
cd quiz/app
cp .env.example .env
# edit .env — set LNBITS_URL and LNBITS_ADMIN_KEY
npm install
npm start
# → http://localhost:3000
```

The server refuses to boot if `LNBITS_URL` or `LNBITS_ADMIN_KEY` is missing.

## Deploy to the VPS

Docker + Caddy. The deploy script lives in [`../deploy/`](../deploy/) — see
[`../deploy/deploy.sh`](../deploy/deploy.sh).

```bash
ssh alice@<host>
cd /opt
sudo git clone <this-repo>.git planb-signet-playground
sudo chown -R alice:alice planb-signet-playground
cd planb-signet-playground/quiz/deploy
./deploy.sh           # first run — prompts for LNBITS_URL + LNBITS_ADMIN_KEY
```

What `deploy.sh` does on first run:

1. Prompts for `LNBITS_URL` and `LNBITS_ADMIN_KEY`, writes `.env.prod` (chmod 600).
2. Builds the image (`quiz/app/Dockerfile`) and starts the container, binding
   `127.0.0.1:3000` only — Caddy on the host fronts it.
3. Appends a `quiz-signet.planb.academy { reverse_proxy 127.0.0.1:3000 }`
   block to `/etc/caddy/Caddyfile` (if not already there) and reloads Caddy.

To redeploy after a `git pull`:

```bash
cd /opt/planb-signet-playground/quiz/deploy
./deploy.sh update
```

Other commands: `./deploy.sh status`, `./deploy.sh logs`.

The SQLite DB is bind-mounted at `quiz/deploy/data/winners_log.db` and survives
container rebuilds.

### Smoke test

```bash
curl -s https://quiz-signet.planb.academy/api/logs | jq
```

## LNbits calls (admin core API only — no extensions required)

| Where                           | Endpoint                              | Auth      |
| ------------------------------- | ------------------------------------- | --------- |
| `/api/wallet/create`            | `POST /users/api/v1/user`             | Admin key |
| `/api/wallet/create`            | `POST /users/api/v1/user/{id}/wallet` | Admin key |
| `/api/withdraw/lnurl` (phase 2) | `POST /api/v1/payments` (`out: true`) | Admin key |

## Open items

- [ ] Get the production `LNBITS_URL` + a fresh `LNBITS_ADMIN_KEY` from Squad 3
      (the key originally hardcoded in the student repo MUST be rotated — assume
      compromised).
- [ ] Point DNS A record `quiz-signet.planb.academy` → VPS IP.
- [ ] Decide whether the question pool (21 questions from `btc101`) is enough
      for daily play or rotate across modules.
- [ ] QA sign-off: full happy path on the VPS — sign in via LNURL-auth (or
      provision a wallet first, pair on phone, then sign in), score 5/5, scan
      the withdraw QR, sats land in the paired wallet.
