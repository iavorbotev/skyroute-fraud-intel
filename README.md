# SkyRoute fraud intelligence

SkyRoute's fraud rate jumped from 0.8% to 3.2%, and their risk team only sees it in weekly reports.
This project scores every booking with plain-language reasons, raises alerts, and shows it all in a dashboard an analyst can act on today.
It explains what is happening and blocks nothing.

## How do I run it?

```bash
make up      # Docker, then open http://localhost:8501
make all     # without Docker (needs uv): checks, data, scoring, report, dashboard
make help    # every step on its own
```

To score your own file, run `make ingest DATA=my_batch.csv`.
It needs only the fields the brief lists ([details](docs/design.md#how-do-i-feed-in-my-own-batch)).

## What will a reviewer see?

| Page | Question it answers |
|---|---|
| Overview | What is our fraud rate, what does it cost, and which country and method is riskiest? |
| Countries | How does each country compare with the others and with last month? |
| Payment methods | How does each method compare with the others and with last month? |
| Patterns | What stands out (bursts, velocity, card testing, IP mismatch, found automatically), and which behaviors go with fraud? |
| Transactions | Which bookings should we look at first, and why was each one flagged? |
| Daily report | What are today's 50 riskiest bookings and what should we do with each? |
| Alerts | What fired that needs attention now? |
| Score check | Does the risk score find fraud? |

The filter bar at the top of every page narrows everything, and an empty dropdown means "all".
For "high-risk card transactions in Mexico", open Transactions and pick MX, card, and high.

## What did we find?

The data is synthetic: 80,653 transactions over 60 days, with attack patterns planted in the last 30.

- **Fraud nearly quadrupled,** from 0.74% of approved bookings in August to 2.72% in September, costing $837,448.
- **Cards carry almost all of it:** 2-8% fraud on cards against 0.2-0.6% on PIX, OXXO, PSE, and Boleto. Argentine cards lead at 7.9%.
- **An Argentina card attack ran for three days.** The country-burst alert fired on its first evening.
- **Five behaviors mark fraud:** IP country differs from billing (36% fraud), night bookings (20%), travel within 2 days (14%), new customers (6.7%), and $1,500+ bookings (23%).
- **The score works:** "high" is fraud 83% of the time against a 2.7% base rate, and "medium" or "high" catches 66% of fraud.

What SkyRoute should do: check card bookings before approval, hold last-minute bookings from new customers with foreign IPs, steer new customers to local methods, watch-list flagged BINs and card testers, and page someone on the country-burst alert.

Full numbers and reasoning: [docs/findings.md](docs/findings.md).

## How does it work?

```
CSV / JSON batch
   │  validate, drop duplicates
   ▼
DuckDB: raw transactions ──► fraud rates from the settled month
   │                                     │
   │  replay in time order               │  looked up per event
   ▼                                     ▼
Stream: one event at a time, with a short memory per customer
   ├──► score, reasons, action ──► dashboard, Top 50 export
   └──► alert watchers ──────────► notifier
```

The scorer reads transactions in time order, the way a payment gateway would send them, so batch and live traffic run the same code.
Behavior signals (velocity, card testing) update with every event.
Fraud rates per segment come only from the settled month before, because chargebacks arrive weeks late.

Rules, alerts, code layout, scaling, and Apache Beam: [docs/design.md](docs/design.md).
Future work, including statistical tests for alerts: [docs/roadmap.md](docs/roadmap.md).

## Design decisions

- **Rules over ML.** The brief values explainable logic. Each rule maps to a sentence an analyst can check.
- **Synthetic data from a seeded generator, not an LLM.** The same `config.toml` always gives a byte-identical file. Pattern sizes scale with volume, so the patterns stand out the same at 500 rows or 40,000 a month.
- **No leakage, and tests prove it.** A test flips every fraud label in the scored window and checks that no score changes. Another test checks that two half-batches score the same as one full replay.
- **DuckDB for storage.** It is one file, needs no server, handles SQL over millions of rows, and the dashboard opens it read-only.
- **Streamlit for the dashboard.** All Python, with filters and downloads built in. The filter logic is a plain function, so it is tested without a browser.

## How was this built?

I built it with Claude Code, an AI coding assistant, running in a sandboxed container.

- **Rules first.** [`.claude/CLAUDE.md`](.claude/CLAUDE.md) holds my standing instructions: plain-language writing, clean architecture, red/green TDD with fakes instead of mocks, no data leakage, and lint and tests before every commit.
- **Plan by interview.** Before writing code, Claude read the brief and asked me one question at a time, each with its trade-off. I made the calls: Streamlit, Docker instead of hosting, rules plus segment rates, a settled month against leakage, and an in-process stream instead of Kafka or Beam. The plan and a log of every task are in [`PLAN.md`](PLAN.md).
- **Small, tested steps.** For each task Claude wrote a failing test, then the code, ran ruff, ty, and pytest, and committed.
- **Checked like a user.** It ran the app in Docker and investigated every page, which caught bugs the tests missed.
- **I steered the product.** After using the dashboard, I asked for Yuno's colors, filters on top, the Countries and Payment methods pages, the automatic pattern highlights, and a check against the brief's acceptance criteria.
- **Reviewed before submission.** Before submitting, I had Claude review the whole solution against the brief, item by item, and fix what fell short.
