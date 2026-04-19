# Squad 2 — Daily Quiz

> Daily Bitcoin/Lightning quiz. Correct answers win sats on Plan B Signet.

## Goal

Showcase education + payments end-to-end: a user answers questions, gets paid in real-feeling sats over Lightning. One quiz per day, no gaming.

## Deliverables

- [ ] Quiz content extraction from the BEC repo
  - Source: [`PlanB-Network/bitcoin-educational-content`](https://github.com/PlanB-Network/bitcoin-educational-content) — e.g. `courses/btc101/quizz/003/en.yml`
- [ ] Quiz UX: one set per day, scoring, immediate feedback, **one attempt per user per day**
- [ ] Payout flow:
  - [ ] **Open question:** custodial wallet on our side, OR bring-your-own wallet with a pre-opened channel? Decide before week 1 ends.
  - [ ] Send sats automatically on correct answers
- [ ] Minimal user identity (email, LN address, or NIP-05 — pick one and document why)
- [ ] Winner log / audit trail (who got how many sats, when)

## Stack (suggested — open to change)

- Frontend: React + TypeScript
- Backend: Node or Python (your call — biz student should not have to read it)
- Payouts: LNbits API (against Squad 3's LNbits instance)

## Dependencies

- Squad 3: LNbits API + ability to fund the quiz wallet
- Squad 1: align on identity story so a single LN address works for both faucet and quiz
- Support: **asi0** is on call for this squad

## Team

- 2 biz — *open*
- 2 devs — *open* (lead TBD)
- Confirmed: Dan_sk1
- Support: asi0

## How to contribute

1. Open an issue tagged `squad/quiz` describing what you'll work on.
2. PRs against `quiz/` only.
3. The custodial-vs-BYOW decision is the highest-priority unblocker — surface it in week 1.
