# QA Track — Cross-squad

> Independent testing across faucet, quiz, and website from a real user's perspective. Catch bugs, UX friction, and doc gaps before external students touch it.

## Goal

Be the first "external user" of the playground. If you can't figure it out from the docs, neither can a student.

## Deliverables

- [ ] Test plan per squad (happy path + edge cases) — see [`test-plans/`](./test-plans/)
- [ ] Weekly test runs against staging
- [ ] Bug reports filed as GitHub issues, tagged `squad/<owner>` + `bug`
- [ ] Final UX sign-off before MVP release

## Test plans

Create one file per squad:

- `test-plans/faucet.md`
- `test-plans/quiz.md`
- `test-plans/website.md`
- `test-plans/infrastructure.md` *(connection / runbook validation)*

Each plan should cover:
- Happy path (numbered steps + expected outcome)
- Edge cases (rate limits, invalid inputs, double-submit, network failure)
- Pass/fail criteria

## How to file a bug

1. Open a GitHub issue.
2. Title: `[squad/<owner>] short description`.
3. Body: steps to reproduce, expected vs actual, environment, screenshot if UI.
4. Tag: `bug` + `squad/faucet|quiz|website|infra`.

## Weekly cadence

- **Mon** — pull latest from staging, plan the week's tests
- **Wed–Thu** — execute test runs
- **Fri** — file bugs, post weekly QA summary in the project chat

## Team

- Rogzy *(confirmed)*
- 1 biz — *open*

## Sign-off

A squad's deliverable is **MVP-ready** when its test plan's happy path runs green twice in a row on staging.
