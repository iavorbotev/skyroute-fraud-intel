# Plan: SkyRoute fraud intelligence pipeline and dashboard

## Context

Yuno's take-home challenge asks for a fraud intelligence system for SkyRoute Travel, a Latin American online travel agency.
Their fraud rate jumped from 0.8% to 3.2% three weeks ago, and they only see it in weekly reports.
The system must take in a batch of transactions, find suspicious patterns, score each transaction with reasons, and show it all in an interactive dashboard.
The timer started at about 13:53, so the hard deadline is about **15:50**.

Scoring weights: dashboard 25, pipeline 20, pattern detection 20, actionable insights 15, code quality 10, docs 10.

Decisions from the interview:

- **Stack:** Python with uv, DuckDB for storage, and Streamlit with Plotly for the dashboard.
- **Architecture:** an in-process stream. A scorer reads transactions one at a time in time order and keeps a short memory per customer. A batch file is just a replay of that stream, so the batch path and the live path run the same code.
- **No Apache Beam for now.** The stream is built as Beam-shaped steps (read, key by customer, score with state, write). The README has a section on moving to Beam: each step maps to a Beam transform, with Dataflow or Flink for scale-out and Beam's event-time tools for late chargebacks. It also says why we skipped Beam: the time limit, local streaming needs Flink plus Java, and 40,000 bookings a month don't need it.
- **Live view:** the stream replays all 60 days in seconds at startup, then the dashboard opens on the results. The README explains how a live mode would work.
- **Delivery:** a public GitHub repo, run with `docker compose up`. No hosted deployment.
- **Scoring:** weighted rules plus segment fraud rates.
- **Leakage guard:** generate 60 days of data. The first 30 days are a settled history (old 0.8% fraud rate), and segment and BIN fraud rates come only from that history. We score the last 30 days. The stream also rules out look-ahead, because the scorer never sees a future event.
- **Stretch goals:** build both, kept small, after the core work. Cut alerts first, then export, if we run late.

Choices I made without asking (say so if you disagree):

- Repo lives in `Yuno/` itself. `.gitignore` keeps out the challenge PDF, `.claude/`, and the shell and editor dotfiles the sandbox masks (`.bashrc`, `.idea`, `.vscode`, `.mcp.json`, and the like).
- The GitHub repo is `skyroute-fraud-intel`, **public**, under your account, because the challenge requires a public link.
- Volume is 40,000 transactions per 30-day window (80,000 total), SkyRoute's real monthly volume, set in the config file. That gives about 260 settled frauds in the history window, enough for stable segment and BIN rates.
- Docker commands run outside the bash sandbox, which blocks the Docker socket. I checked: Docker works there.

## How the pieces fit

```
config.toml ──► generator ──► data/transactions.csv.gz
                                   │
        fraud-intel ingest <file> ─┤  (CSV or JSON batch, validated, deduped by transaction_id)
                                   ▼
                        raw transactions (DuckDB)
                     │                          │
       slow path: settled rates          fast path: the stream, in time order
       (history window only,             ┌──────────────────────────────────────┐
        computed once per run) ────────► │ for each event:                      │
                                         │   read customer memory               │
                                         │   apply rules + settled rates        │
                                         │   emit score, reasons, action        │
                                         │   alert watchers see the event       │
                                         │   update memory, drop events > 1 h   │
                                         └──────────────────────────────────────┘
                                                │                    │
                                       scored sink (DuckDB)   alerts ─► Notifier (console)
                                                │                    │
                                                ▼                    ▼
                                  Streamlit dashboard, daily Top 50 export, alert feed
```

Clean architecture, kept thin:

- `domain/` has the pure logic, with no I/O:
  - the `Transaction` and `ScoredTransaction` dataclasses and the schema check
  - `CustomerMemory`, which holds one customer's recent attempts, declines, and cards, plus whether they have booked before
  - the settled rate table
  - rules, scoring, and actions
  - alert watchers, which keep their own running counts
  - metrics and the daily report, which run over scored rows in pandas
- `application/ports.py` defines the `Protocol`s `TransactionSource`, `ScoredSink`, `ScoredStore` (reads for the dashboard), and `Notifier`.
- `application/stream.py` is the loop. It builds the settled rates from the history, then pushes each event through the scorer and the watchers.
- `infrastructure/` has `FileSource` (CSV/JSON, sorted by time), the DuckDB store and sink, the generator, and `ConsoleNotifier`.
- `cli.py` and `dashboard/` are the entry points. They build the real adapters and inject them.
- Tests use an in-memory `ListSource`, `FakeStore`, and `RecordingNotifier`, not mocks. One integration test runs DuckDB with `:memory:`.

How it scales (the README says this, and we build none of it now):

- **Source:** swap `FileSource` for a Kafka or Redpanda consumer. The scorer stays the same.
- **Scale-out:** split events by `customer_id` across scorer copies, as Kafka partitions do. Each copy only holds memory for its own customers.
- **Country-level alerts:** these need every customer's events, so they move to a second stage keyed by country.
- **Durable memory:** "has booked before" must be kept forever, so in production it lives in a key-value store such as Redis, not in process memory. Today, a restart rebuilds memory by replaying the stored events.
- **Slow path:** settled rates become a nightly batch job that refreshes a lookup table the stream reads.
- **Ingest:** re-scoring today replays all stored events (80,000 take seconds). At larger volume, the scorer saves its memory and replays only new events.

## Data generator (seeded, reproducible)

- 60 days ending at a fixed date from the config, with seed `config.seed`. Same config gives a byte-identical file.
- Countries: BR 35%, MX 25%, CO 20%, AR 10%, CL 10%.
- Methods: card 60%, PIX 20%, OXXO 10%, PSE 5%, Boleto 5%. Each method is drawn given the country, because PIX and Boleto exist only in BR, OXXO only in MX, and PSE only in CO. That way both mixes hit their targets.
- About 83% approved. Fraud is about 0.8% of approved in the history window and about 3% in the scored window.
- Amounts follow a lognormal curve with a mean near $420.
- The challenge's pattern counts (e.g. 15-20 AR frauds) assume about 500 rows. At 40,000 a month, 18 extra bookings would disappear among about 35 normal AR card bookings over $800 in 3 days. So the config sets each pattern size as a share of volume, and the patterns stand out as much as they would in the small dataset.
- About 1,000 frauds in the scored window. The planted patterns are part of that. The rest is background fraud that leans toward the risk signals (new customer, night, last minute, foreign IP), so the rules have something to find beyond the planted cases.
- Planted patterns in the scored window:
  - About 100 fraudulent AR card bookings of $800+ inside 3 days.
  - Bursts of 3-4 bookings from one customer within 1 hour, plus some customers with 5+ attempts and declines across different BINs (card testing).
  - IP country differs from billing country, including IPs outside Latin America.
  - Legitimate high-value bookings from returning customers and legitimate travelers abroad, so there are false positives. Thousands of legitimate $800+ bookings come from the amount curve itself.
  - A few BINs and segments that were already bad in the history window, so the settled rates have something to find.
- Fields: `transaction_id, timestamp_utc, customer_id, customer_email, billing_country, ip_country, payment_method, card_bin, amount_usd, status, is_fraud, booking_type (flight/hotel), destination_country, departure_date`.

## Risk logic

The scorer sees events in time order, so every rule uses only the past.
Points and thresholds live in `config.toml`.

| Rule | Fires when | Reason shown to analyst |
|---|---|---|
| velocity | 3+ attempts by the same customer in the past 60 min | "4 bookings from this customer in 1 hour" |
| card_testing | 2+ declines or 3+ different BINs for the customer in the past 60 min | "3 cards tried in 40 min" |
| geo_mismatch | IP country is not the billing country | "IP in RU, billing in BR" |
| new_customer_high_value | no earlier approved booking and amount at least $800 | "First booking, $1,240" |
| value_outlier | amount above the history p99 for its country and method | "Above 99% of past AR card bookings" |
| last_minute | departure within 48 h and amount at least $500 | "Flight leaves in 20 h" |
| night_hours | 00:00-05:59 local time in the billing country | "Booked at 03:12 local time" |
| risky_segment | settled segment rate at least 2x the overall rate | "AR cards had 2.9% fraud last month" |
| risky_bin | settled BIN rate at least 3x the overall rate | "BIN 451234 had 6% fraud last month" |

- The **settled rate** is `(frauds + m * overall_rate) / (approved + m)`, computed only from the history window. The term `m` pulls small segments toward the overall rate.
- Score is the sum of fired points, capped at 100. Levels: low below 30, medium 30-59, high 60 and up.
- **Recommended action** for approved bookings:
  - high, with a card signal (velocity, card testing, or risky BIN): "Refund and block card".
  - other high: "Contact customer to verify before travel".
  - medium: "Manual review".
  - Declined high-risk attempts get "Watch customer and IP".
- A **score check** uses the labels only to measure the score, never to compute it: fraud rate per risk level, and precision and recall of "high" in the scored window.

## Dashboard (Streamlit, 6 pages, shared sidebar filters)

The sidebar has filters for date range (defaults to the scored window), country, payment method, and risk level. Filters stay set when you switch pages.

1. **Overview:**
   - KPI tiles: fraud rate (scored window vs history), chargebacks and dollars lost including the $15-25 fee, auth rate against the healthy 82-85% band, and the count of high-risk bookings.
   - Daily fraud rate over 60 days, with the history cutoff and spike days marked.
   - Country by method heatmap.
   - Top risk segments ranked by dollars lost, plus generated comparison sentences like "Fraud is 4.2% for AR cards but 0.6% for BR PIX".
2. **Patterns:** pick a dimension (hour of day, value band, new vs returning, booking type, days to departure) and see fraud rate and volume. Also fraud rate per fired rule.
3. **Transactions:** a ranked high-risk list with score, reasons, and action, plus a detail panel for one transaction. Test case: filter to MX, card, high.
4. **Daily report (stretch):** pick a day to see its Top 50, a summary insight ("Today's highest risk: card bookings from IPs in RU billing BR, 18 transactions, $7,600 exposure"), and CSV/JSON download.
5. **Alerts (stretch):** an alert feed with severity, and counts per rule.
6. **Score check:** how well the score separates fraud.

## Alert rules (stretch, watchers inside the stream)

Each watcher sees every scored event and keeps its own running counts. When a rule fires, the watcher sends an `Alert` to the `Notifier` and to the store.

1. **Velocity burst:** one customer makes 5+ attempts in 60 min. Critical.
2. **Card testing:** 3+ declines from one customer in 30 min. High.
3. **High-risk share spike:** the share of high-risk bookings in the last hour is at least 2x the 7-day hourly baseline, with a minimum count. This stands in for "fraud rate doubled", because chargeback labels arrive weeks late.
4. **Country high-value burst:** $800+ card bookings from one billing country in 24 h reach at least 3x that country's 7-day daily average. This rule catches the AR cluster.

## Tasks

After each task: ruff, ty, and pytest pass, then commit (lowercase message), push, and add a short note under the task in `PLAN.md`.
Tests are red/green, two or three per task, built on fakes.

0. **Scaffold.**
   Run `git init -b main` in `Yuno/`.
   Set up the uv project with Python 3.13, the `fraud-intel` console script, ruff, ty, pytest, and `.gitignore` (venv, `*.duckdb`, the PDF, `.claude/`, masked dotfiles).
   Add `config.toml` and `config.py`, which loads it into frozen dataclasses.
   Add `Dockerfile` and `compose.yaml` (`generate` and `ingest` if no data exists, then `streamlit` on `0.0.0.0:8501`).
   Commit `PLAN.md` with the scaffold, then run `gh repo create skyroute-fraud-intel --public --source . --push`.
1. **Generator.**
   Write `infrastructure/generator.py` and the `fraud-intel generate` command, and commit `data/transactions.csv.gz` (a few MB, which pandas reads directly).
   Tests: same seed gives the same frame; country, method, and approval mix within tolerance; the AR cluster, velocity bursts, and false-positive rows are present.
2. **Ingest, source, and store.**
   Add the schema check, `FileSource`, the DuckDB store and sink, and `fraud-intel ingest <file>` (append raw rows, dedupe, then replay the stream).
   Tests: a bad row (missing column, unknown status) is rejected with a clear message; a DuckDB round trip keeps rows and types.
3. **Stream scorer: customer memory and settled rates.**
   Add `CustomerMemory`, the settled rate table, and the stream loop in `application/stream.py`.
   Tests:
   - Velocity counts only events from the last 60 min.
   - **Leakage test:** flipping `is_fraud` on scored-window rows does not change any score.
   - **Replay test:** feeding the data as one file or as two batches gives the same scores.
4. **Rules, scoring, actions, score check, metrics.**
   Tests: a hand-built risky booking fires the expected reasons and action; the score caps at 100; segment metrics on a tiny frame give known rates.
5. **Dashboard core.**
   Build the sidebar plus Overview, Patterns, Transactions, and Score check.
   Test: a Streamlit `AppTest` smoke test renders each page with no exception, and the MX + card + high filter returns only matching rows.
6. **Export and alerts (cut if late).**
   Add the daily report and `fraud-intel report --date`, the four alert watchers, `ConsoleNotifier`, and both pages.
   Tests: the AR cluster triggers the country burst alert; the report has 50 rows sorted by score, each with an action.
7. **E2E check and README.**
   Run `docker compose up` outside the sandbox.
   Take headless screenshots of every page and fix anything that looks off.
   Write the README: how to run, the architecture and how it scales, the Apache Beam migration path, design decisions, and findings with real numbers from the dashboard, plus next steps. Then do a final push.

## Verification

- `uv run ruff check && uv run ty check && uv run pytest`: all green.
- `docker compose up`, then open `http://localhost:8501`. On the host, publish with `sbx ports claude-Yuno --publish 8501:8501`.
- Follow the reviewer path: the Overview answers "what is our fraud rate". The heatmap answers "riskiest country and method". Transactions filtered to MX + card + high shows a ranked list with reasons. Daily report downloads a 50-row CSV.
- Run `fraud-intel ingest` with a second small batch, and check that the dashboard picks it up after a refresh.
- The public GitHub URL opens without login.

## Task log

- **0. Scaffold:** done. Local Python is 3.14 (no 3.13 on the machine), so the project and the Docker image both use 3.14. pandas resolved to 3.0. `README.md` is added in task 7, so `pyproject.toml` has no `readme` field yet.
- **1. Generator:** done. 80,653 rows (two 40,000 baselines plus planted patterns), 1.8 MB gzipped, byte-identical on rerun. Fraud is 0.74% of approved in the history window and 2.72% in the scored window (919 frauds). Method mix lands on card 60.5, PIX 19.7, OXXO 10, Boleto 5, PSE 4.9. A per-country fraud weight (AR 2.5x, MX 1.3x) gives the settled segment rates something to find. Line length raised to 120.
