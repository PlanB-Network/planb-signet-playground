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
- 5 questions per day, deterministically shuffled per `(date, lightningAddress)`
  so each user gets a stable but personalised set.
- One attempt per Lightning address per day (UTC).
- Reward: 1500 sats only on a perfect 5/5.
- Two payout paths:
  - **A. Custodial** — if the user has registered via `/api/create-user`,
    LNbits creates a wallet under the user, the quiz creates an invoice on it,
    and the admin "Big Pot" wallet pays that invoice. Funds stay inside LNbits.
  - **B. External LNURL fallback** — if the user has no custodial wallet, the
    server resolves their Lightning address via LNURL-pay and the admin wallet
    pays the resulting invoice.
- All attempts and payments are logged to a local SQLite DB (`winners_log.db`).

## API

### Quiz endpoints

| Method | Path                                    | Body / Params                             | Purpose                                                             |
| ------ | --------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------- |
| `POST` | `/api/create-user`                      | `{ lightningAddress, username?, email? }` | Create or fetch a custodial wallet on LNbits                        |
| `GET`  | `/api/wallet-balance/:lightningAddress` | —                                         | Read the user's custodial wallet balance                            |
| `POST` | `/api/start`                            | `{ lightningAddress? }`                   | Start today's quiz; identity comes from session cookie if logged in |
| `POST` | `/api/submit`                           | `{ score, total, lightningAddress? }`     | Record attempt + trigger payout if 5/5                              |
| `GET`  | `/api/logs`                             | —                                         | List all attempts (newest first)                                    |

### LNURL-auth endpoints (LUD-04)

The quiz implements LNURL-auth so a user with an existing Lightning wallet can
log in by signing a challenge — no password, no email, no LN address re-typing
on return visits.

| Method | Path                         | Auth       | Purpose                                                          |
| ------ | ---------------------------- | ---------- | ---------------------------------------------------------------- |
| `GET`  | `/api/auth/lnurl/init`       | —          | Generate a fresh `k1` + bech32 LNURL for the QR code             |
| `GET`  | `/api/auth/lnurl/callback`   | wallet sig | Wallet hits this with `?tag=login&k1=&sig=&key=` to authenticate |
| `GET`  | `/api/auth/lnurl/status?k1=` | —          | Frontend polls this; sets `quiz_session` cookie on success       |
| `GET`  | `/api/auth/me`               | session    | Return the current user                                          |
| `POST` | `/api/auth/logout`           | session    | Clear the session                                                |
| `POST` | `/api/auth/payout-address`   | session    | Set/update the LN address rewards are sent to                    |

Identity model:

- The `linkingKey` (secp256k1 pubkey, deterministic per wallet+domain per
  LUD-05) IS the account ID. Stored in the `auth_users` table.
- The Lightning address is just a payout target. Set once on first login,
  reusable forever.
- A successful LNURL-auth login mints a `quiz_session` cookie (httpOnly,
  SameSite=Lax, 30-day TTL, `Secure` in production).
- `/api/start` and `/api/submit` accept the session cookie as identity. The
  body's `lightningAddress` field is now optional and only used as a fallback.

See [`test.http`](./test.http) for a runnable smoke-test sequence (including a
node one-liner to sign a `k1` for headless callback testing).

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

## LNbits integration

User and wallet provisioning uses the **admin core API** (no extension required):

| Step                     | Endpoint                                   | Auth        |
| ------------------------ | ------------------------------------------ | ----------- |
| Create LNbits account    | `POST /users/api/v1/user`                  | Admin key   |
| Create wallet for user   | `POST /users/api/v1/user/{user_id}/wallet` | Admin key   |
| Create invoice on wallet | `POST /api/v1/payments` (out: false)       | Invoice key |
| Big Pot pays invoice     | `POST /api/v1/payments` (out: true)        | Admin key   |
| Read wallet balance      | `GET /api/v1/wallet`                       | Invoice key |

The lightning address is stored in LNbits' `external_id` field (LNbits' username
regex disallows `@` and `.com`). Local SQLite maps `lightning_address →
lnbits_user_id, wallet_id, inkey, adminkey`.

## Open items

- [ ] Get the production `LNBITS_URL` + a fresh `LNBITS_ADMIN_KEY` from Squad 3
      (the key originally hardcoded in the student repo MUST be rotated — assume
      compromised).
- [ ] Point DNS A record `quiz-signet.planb.academy` → VPS IP.
- [ ] Decide whether the question pool (21 questions from `btc101`) is enough
      seasonal coverage for daily play, or rotate across modules.
- [ ] **Next PR**: full UX redesign with two clear entry doors (BYOW vs. issued
      custodial), and a QR for pairing the LNbits PWA on the user's phone after
      custodial wallet creation.
- [ ] QA sign-off: full happy path on the VPS — register, take the quiz, score
      5/5, see sats land. Test LNURL-auth login with at least Wallet of Satoshi
      and Phoenix.
