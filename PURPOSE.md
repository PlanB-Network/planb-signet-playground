# Purpose

## Why this exists

Plan B Network teaches Bitcoin. To teach end-to-end, students need a network where they can **actually transact** — onchain and over Lightning — without burning real sats and without waiting for slow public testnets.

The Plan B Signet Playground is that network: a custom Signet we control, with a faucet, a daily quiz, and the infrastructure to make it usable in minutes by any educator or student.

## Principles

- **MVP > polish.** 2 weeks. Ship something usable; iterate after.
- **Composable, not monolithic.** Each squad ships an independent surface (faucet, quiz, website) that talks to shared infra.
- **External-first.** A new student should be able to connect, get sats, and play within 5 minutes of landing on the website.
- **Privacy-aware defaults.** Don't leak users' IPs, addresses, or quiz history beyond what is strictly required.
- **Documented enough to restart without asi0.** If a single person is the only one who can debug it, it's not done.

## How we work

- **Squads own folders.** `faucet/`, `quiz/`, `website/`, `infrastructure/`, `qa/` — each squad has full autonomy inside its folder.
- **Shared decisions in `docs/`.** Cross-squad concerns (architecture, contracts, naming) get documented there.
- **Issues are tagged per squad.** `squad/faucet`, `squad/quiz`, `squad/infra`, `squad/website`, `squad/qa`.
- **QA is independent.** QA tests from a real user's POV against staging. Bugs file as issues, not Slack messages.
- **Weekly checkpoint.** Each squad posts a short status: shipped / blocked / asks.

## Definition of "MVP done"

- A new external student lands on the website, follows the tutorial, gets sats from the faucet, plays the quiz, and wins sats — all on custom Signet, with no human in the loop.
- The infra survives a VPS reboot and restarts via documented runbook.
- QA has signed off on the happy path for all three webapps.
