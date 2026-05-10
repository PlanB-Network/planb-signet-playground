# Squad 2 — Daily Quiz

> Daily Bitcoin/Lightning quiz. Correct answers win sats on Plan B Signet.

## Goal

Showcase education + payments end-to-end: a user answers questions, gets paid in real-feeling sats over Lightning. One quiz per day, no gaming.

## Status

V0 imported from [`DanieleSK/quiz-planB`](https://github.com/DanieleSK/quiz-planB)
(authors: DanieleSK, Simone Da Re). Code now lives in [`./app/`](./app/) — see
[`app/README.md`](./app/README.md) for run + deploy instructions.

## Deliverables

- [x] Quiz content extraction from the BEC repo
  - Source: [`PlanB-Network/bitcoin-educational-content`](https://github.com/PlanB-Network/bitcoin-educational-content) — pulled live from `courses/btc101/quizz/<id>/en.yml` (21 questions)
- [x] Quiz UX: one set per day, scoring, immediate feedback, **one attempt per user per day**
- [x] Payout flow:
  - [x] **Decision:** both paths shipped — custodial wallet (default, created by `/api/create-user`) with LNURL fallback for users who bring their own Lightning address.
  - [x] Send sats automatically on correct answers (1500 sats on 5/5)
- [x] Minimal user identity — Lightning address (one identifier that doubles as a payout target, shared with the faucet flow)
- [x] Winner log / audit trail (who got how many sats, when) — SQLite `attempts` + `payments` tables

Open items are tracked in [`app/README.md`](./app/README.md#open-items).

## Stack (as shipped)

- Backend: Node 20 + Express 5 + SQLite (single `server.js`)
- Frontend: vanilla HTML/JS served from `app/public/`
- Payouts: LNbits API (against Squad 3's LNbits instance)

## Dependencies

- Squad 3: LNbits API + ability to fund the quiz wallet
- Squad 1: align on identity story so a single LN address works for both faucet and quiz
- Support: **asi0** is on call for this squad

## Team

- 2 biz — _open_
- 2 devs — _open_ (lead TBD)
- Confirmed: Dan_sk1
- Support: asi0

## How to contribute

1. Open an issue tagged `squad/quiz` describing what you'll work on.
2. PRs against `quiz/` only.
3. The custodial-vs-BYOW decision is the highest-priority unblocker — surface it in week 1.
