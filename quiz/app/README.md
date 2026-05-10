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

| Method | Path                                    | Body / Params                             | Purpose                                              |
| ------ | --------------------------------------- | ----------------------------------------- | ---------------------------------------------------- |
| `POST` | `/api/create-user`                      | `{ lightningAddress, username?, email? }` | Create or fetch a custodial wallet on LNbits         |
| `GET`  | `/api/wallet-balance/:lightningAddress` | —                                         | Read the user's custodial wallet balance             |
| `POST` | `/api/start`                            | `{ lightningAddress }`                    | Start today's quiz, returns 5 personalised questions |
| `POST` | `/api/submit`                           | `{ lightningAddress, score, total }`      | Record attempt + trigger payout if 5/5               |
| `GET`  | `/api/logs`                             | —                                         | List all attempts (newest first)                     |

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

## Deploy to the VPS (V1)

The pattern below mirrors
[`infrastructure/lnbits/README.md`](../../infrastructure/lnbits/README.md):
systemd to keep the process alive, Caddy to terminate TLS.

### 1. Install on the VPS

```bash
ssh alice@<host>
cd /opt
sudo git clone <this-repo>.git planb-signet-playground
sudo chown -R alice:alice planb-signet-playground
cd planb-signet-playground/quiz/app
cp .env.example .env
vim .env   # paste the real LNbits URL + admin key
npm install --omit=dev
mkdir -p /var/lib/planb-quiz   # persistent SQLite location
```

Set `DB_PATH=/var/lib/planb-quiz/winners_log.db` in `.env` so the database
survives a redeploy.

### 2. systemd unit

`/etc/systemd/system/planb-quiz.service`:

```ini
[Unit]
Description=Plan B Signet — Daily Quiz
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=alice
WorkingDirectory=/opt/planb-signet-playground/quiz/app
EnvironmentFile=/opt/planb-signet-playground/quiz/app/.env
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now planb-quiz
sudo journalctl -u planb-quiz -f
```

### 3. Caddy reverse proxy

Add to `/etc/caddy/Caddyfile`:

```caddy
quiz-signet.planb.academy {
    reverse_proxy 127.0.0.1:3000
}
```

```bash
sudo systemctl reload caddy
```

### 4. Smoke test

```bash
curl -s https://quiz-signet.planb.academy/api/logs | jq
```

## Open items before V1 ships

- [ ] Get the production `LNBITS_URL` + a fresh `LNBITS_ADMIN_KEY` from Squad 3
      (the key originally hardcoded in the student repo MUST be rotated — assume
      compromised).
- [ ] Pick the public domain (default suggestion: `quiz-signet.planb.academy`)
      and update Caddy + Squad 4's website link.
- [ ] Tighten CORS — currently `cors()` allows any origin; restrict to the
      Squad 4 website domain in production.
- [ ] Decide on basic abuse limits (per-IP rate limit on `/api/start` and
      `/api/create-user`). Today the only limit is "one attempt per Lightning
      address per day".
- [ ] Add a healthcheck endpoint (`GET /health` returning 200) for systemd /
      Caddy / monitoring.
- [ ] Review whether the question pool (21 questions from `btc101`) is enough
      seasonal coverage for daily play, or if we should rotate across modules.
- [ ] QA sign-off (Squad QA): full happy path on the VPS — register, take the
      quiz, score 5/5, see sats land in both custodial and BYOW flows.
