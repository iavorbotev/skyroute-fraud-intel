# SkyRoute fraud intelligence

SkyRoute's fraud rate jumped from 0.8% to 3.2%, and their risk team only sees it in weekly reports.
This project takes their transactions in, scores every booking with plain-language reasons, raises alerts, and shows it all in a dashboard an analyst can act on today.

It is fraud *intelligence*, not fraud prevention: it explains what is happening and what to look at first, and it blocks nothing.

## How do I run it?

With Docker:

```bash
make up        # or: docker compose up --build
```

Open http://localhost:8501.
On start, the container loads the dataset in `data/transactions.csv.gz`, replays it through the scoring stream (about 3 seconds for 80,653 events), prints every alert to the console, and starts the dashboard.
`make logs` shows the alerts, and `make down` stops it.

Without Docker, you need [uv](https://docs.astral.sh/uv/) and `make`:

```bash
make all       # install, lint, types, tests, rebuild data, score it, export a report, open the dashboard
```

Or run one step at a time (`make help` lists them):

| Target | What it does |
|---|---|
| `make install` | install dependencies with uv |
| `make check` | ruff lint and format check, ty type check, and the pytest suite |
| `make data` | rebuild the seeded dataset from `config.toml` (it is also committed) |
| `make ingest` | load `DATA`, replay the stream, save scores and alerts |
| `make report DATE=2026-09-18` | export that day's Top 50 to `reports/` as CSV and JSON |
| `make dashboard PORT=8501` | run the dashboard locally |
| `make clean` | remove the database, reports, and caches |

`ingest` takes any CSV or JSON batch, for example `make ingest DATA=my_batch.csv`.
It skips transaction IDs it has already seen, so feeding the same file twice is safe.

A batch needs only the fields the brief lists: `transaction_id`, `timestamp_utc`, `customer_id`, `billing_country`, `ip_country`, `payment_method`, `amount_usd`, `status` (approved/declined), and `is_fraud`.
`card_bin`, `customer_email`, `booking_type`, `destination_country`, and `departure_date` are optional.
Rules that need a missing field stay silent: without `departure_date` there is no last-minute rule, and without `card_bin` no BIN rule.
A batch of only 30 days has no settled history before it, so the three history rules (risky segment, risky BIN, value outlier) stay silent and the score rests on behavior: velocity, card testing, IP mismatch, new customer, and night bookings.

## What will a reviewer see?

| Page | Question it answers |
|---|---|
| Overview | What is our fraud rate, what does it cost, and which country and payment method is riskiest? |
| Countries | How does each country compare with the others and with last month? |
| Payment methods | How does each payment method compare with the others and with last month? |
| Patterns | Which behaviors go with fraud: hour of day, booking value, new vs returning, IP mismatch, days to departure? |
| Transactions | Which bookings should we look at first, and why was each one flagged? |
| Daily report | What are today's 50 riskiest bookings and what should we do with each? (CSV/JSON download) |
| Alerts | What fired that needs attention now? |
| Score check | Does the risk score find fraud? |

The Countries and Payment methods pages show every segment without any clicks.
Each has a card per segment (fraud rate and change vs last month, money lost, chargebacks, high-risk count, auth rate), daily trend charts on one shared scale, the losses split by the other dimension, and a comparison table.

The filter bar at the top of every page (dates, country, payment method, risk level) narrows all of it, and keeps its values when you switch pages.
An empty dropdown means "all".
To see "high-risk card transactions in Mexico", open Transactions and pick MX, card, and high.
Click a row to see its reasons and the recommended action.

## How does it work?

```
config.toml ──► generator ──► data/transactions.csv.gz
                                   │
        fraud-intel ingest <file> ─┤  validate, dedupe by transaction_id
                                   ▼
                        raw transactions (DuckDB)
                     │                          │
   once per run: settled fraud rates     per event: the stream, in time order
   (history window only) ──────────────► read customer memory
                                         apply rules + settled rates
                                         emit score, reasons, action
                                         alert watchers see the event
                                         update memory, forget events older than 1 h
                                                │                    │
                                       scored table (DuckDB)   alerts ─► notifier (console)
                                                │
                                                ▼
                                  Streamlit dashboard, daily Top 50 export
```

The scorer reads transactions one at a time, in time order, the way they would arrive from a payment gateway.
For each customer it keeps a short memory: attempts, declines, and cards used in the last hour, and whether they have booked before.
A batch file is just a replay of that stream, so batch and live traffic would run the same code.

Two kinds of signal become known at different times, so they are computed in two places:

- **Behavior is known right away.** Four cards tried in ten minutes is visible the moment it happens, so it updates with every event.
- **Fraud labels arrive weeks later.** A booking is only marked as fraud when the chargeback comes in. So fraud rates per country, payment method, and card BIN are computed once per run, only from the settled 30-day history window, and the stream looks them up.

The code follows that split:

- `domain/` is pure logic with no I/O: the event model, customer memory, settled rates, rules, alert watchers, metrics, and the report.
- `application/stream.py` is the loop that pushes each event through the scorer and the alert watchers.
- `application/ports.py` holds two interfaces, `TransactionStore` and `Notifier`.
- `infrastructure/` implements them: DuckDB storage, file readers, the console notifier, and the data generator.
- `cli.py` and `dashboard/` build the real pieces and pass them in. Tests pass in-memory fakes instead.

## How is each booking scored?

Every rule uses only what was known at payment time.
Points and thresholds live in `config.toml`.

| Rule | Fires when | Points | Example reason |
|---|---|---|---|
| velocity | 3+ attempts by the customer in 60 min | 25 | "4 bookings from this customer in 22 min" |
| card_testing | 3+ different cards, or 2+ declines, in 60 min | 30 | "6 different cards tried in 60 min" |
| geo_mismatch | IP country differs from billing country | 20 | "IP in RU, billing in AR" |
| new_customer_high_value | no earlier approved booking and $800+ | 20 | "First booking, $1,240" |
| value_outlier | above the 99th percentile of past bookings for that country and method | 15 | "$2,400 is above 99% of past AR card bookings ($1,560)" |
| last_minute | travel starts within 48 h and $500+ | 10 | "Travel starts in 20 h" |
| night_hours | 00:00-05:59 in the customer's country | 10 | "Booked at 03:00-03:59 local time" |
| risky_segment | the country and method had 2x the usual fraud rate last month | 15 | "BR card had 1.5% fraud last month (overall 0.7%)" |
| risky_bin | the card BIN had 3x the usual fraud rate last month | 20 | "BIN 451234 had 6.0% fraud last month" |

The score is the sum of points, capped at 100.
Below 30 is low, 30-59 is medium, and 60 or more is high.
Each segment's rate is pulled toward the overall rate, `(frauds + 200 × overall) / (approved + 200)`, so a segment with three frauds in ten bookings does not read as 30%.

Recommended actions:

- High risk with a card signal (velocity, card testing, or risky BIN): **Refund and block card**.
- Other high risk: **Contact customer to verify before travel**.
- Medium: **Manual review**.
- A declined high-risk attempt: **Watch customer and IP**.

## Which alerts fire?

Alert watchers see every scored event and keep their own running counts.

1. **Velocity burst** (critical): one customer makes 5+ attempts in 60 minutes.
2. **Card testing** (high): 3+ declines from one customer in 30 minutes.
3. **High-risk share spike** (high): the share of high-risk bookings in the last hour is at least 2x its 7-day level. This stands in for "the fraud rate doubled in the past hour", because the fraud label only arrives weeks later.
4. **Country high-value burst** (critical): $800+ card bookings from one country in 24 hours reach 3x that country's daily average over the previous week.

Each alert goes to the `Notifier` interface.
Today that prints to the console (see `docker compose logs`).
A Slack or PagerDuty notifier would implement the same `notify` method.

## What did we find in the test data?

The dataset is synthetic.
It covers 60 days and 80,653 transactions, at SkyRoute's real volume of 40,000 a month.
The first 30 days are the settled history at the old fraud level.
In the last 30, a fraud wave starts on day 10, and we planted known attack patterns.
So these findings show that the system recovers what we planted, and how clearly.

**The fraud rate nearly quadrupled.**
It went from 0.74% of approved bookings in August to 2.72% in September.
It was 1.06% in the first nine days of September, then 3.43% once the wave started.
September chargebacks cost $837,448 including fees, against $198,783 in August.

**Cards carry almost all of it, and Argentina most of all.**

| Segment | Fraud rate | Chargebacks | Lost |
|---|---|---|---|
| AR card | 7.9% | 262 | $303,447 |
| BR card | 5.5% | 196 | $154,352 |
| MX card | 4.2% | 216 | $180,939 |
| CO card | 2.5% | 129 | $105,278 |
| CL card | 2.1% | 73 | $59,734 |
| MX OXXO | 0.6% | 21 | $14,537 |
| CO PSE | 0.4% | 7 | $4,851 |
| BR Boleto | 0.2% | 4 | $2,088 |
| BR PIX | 0.2% | 12 | $12,220 |

**An Argentina card attack ran for three days.**
Between 17 and 19 September, 111 Argentine card bookings over $800 turned out to be fraud.
The country-burst alert fired on the first evening, 17 September at 21:23 UTC: "44 card bookings over $800 from AR in 24 h vs 15 a day last week".

**Five behaviors separate fraud from normal bookings:**

| Behavior | Fraud rate with it | Without it |
|---|---|---|
| IP country differs from billing | 36.2% | 1.7% |
| Booked 00:00-05:59 local time | 20.1% | 1.8% |
| Travel starts within 2 days | 14.4% | 1.7-3.2% |
| New customer | 6.7% | 0.4% |
| $1,500 or more | 23.2% | 0.4% under $200 |

**The score finds fraud and stays explainable.**

- Approved bookings scored "high" turned out to be fraud 83% of the time (201 of 243), against a 2.7% base rate.
- "Medium" or "high" covers 4.5% of approved bookings and catches 66% of all fraud.
- Card testing (65% fraud when it fires) and velocity (63%) are the sharpest rules.
- `risky_segment` is the weakest (5.5%). Last month it flagged only BR card, while this month's attack hit AR card. Segment rates look backward, and the fraudsters had moved on, which is SkyRoute's complaint about weekly reports. The per-event rules caught the new attack instead.

**49 alerts fired:** 20 velocity bursts, 20 card-testing runs, 8 high-risk share spikes, and the Argentina burst.

## What should SkyRoute do?

1. **Check card bookings before approval, not after.** Send high-score card bookings to 3-D Secure or manual review before the ticket is issued. Today's rules engine only flags after approval, which is why fraud completes.
2. **Hold last-minute bookings from new customers with foreign IPs.** That combination is where the money goes. A short verification call costs less than a $1,500 chargeback.
3. **Steer new customers toward PIX, OXXO, and PSE.** Fraud on local methods is 0.2-0.6%, against 2-8% on cards.
4. **Watch-list the BINs and customers the score flags.** Block card-testing customers after the first burst, and review BINs whose settled fraud rate is 3x normal.
5. **Page someone on the country-burst alert.** It caught the Argentina attack on its first evening, two days before it ended.

## How would it scale?

We built none of this, but the design leaves room for it:

- **Live source:** swap the file replay for a Kafka or Redpanda consumer. The scorer stays the same.
- **Scale out:** split events by `customer_id` across scorer copies, as Kafka partitions do. Each copy only keeps memory for its own customers.
- **Country alerts:** these need every customer's events, so they move to a second stage keyed by country.
- **Durable memory:** "has booked before" must be kept forever, so in production it lives in a key-value store such as Redis. Today a restart rebuilds memory by replaying stored events.
- **Settled rates:** these become a nightly batch job that refreshes a lookup table the stream reads.
- **Re-scoring:** `ingest` replays all stored events today, which takes seconds at 80,000. At larger volume the scorer would save its memory and replay only new events.

### What about Apache Beam?

Beam runs the same pipeline code as batch or streaming.
Each step here maps onto a Beam transform:

| Here | Beam |
|---|---|
| `transactions_from_frame` (replay a file) | `ReadFromText` / `ReadFromKafka` |
| key by `customer_id` | `beam.Map(lambda t: (t.customer_id, t))` |
| `StreamProcessor` with `CustomerMemory` | a stateful `DoFn` with per-key state and timers |
| `build_settled_rates` | a batch pipeline writing a side input |
| alert watchers | windowed `CombinePerKey` (sliding 1 h, 24 h) |
| DuckDB sink | `WriteToBigQuery` / Parquet |

Dataflow or Flink would handle scale-out.
Beam's event-time windows and watermarks would also handle chargebacks that arrive weeks after the payment.

We skipped Beam for three reasons:

- **Time:** this was a two-hour challenge.
- **Local streaming needs Flink plus Java:** Beam's Python local runner only does batch.
- **The volume doesn't need it:** 40,000 bookings a month is about one a minute.

## What would I add with more time?

- **Live mode:** a Redpanda source, a sink that writes small Parquet files, and a dashboard that refreshes on a timer.
- **Chargeback dates:** add a `chargeback_date` field, so settled rates use what was known on each day instead of a fixed 30-day cut.
- **Learned weights:** fit the rule points with logistic regression on a time-ordered split, and keep the reasons.
- **Analyst feedback:** let analysts mark a flag as right or wrong, and use those marks to tune thresholds.
- **More signals:** device fingerprint, email domain age, a passenger name that differs from the cardholder, and real IP geolocation.
- **Operations:** a Slack notifier, CI that runs lint and tests, and a hosted demo.

## Design decisions

- **Rules over ML.** The brief values explainable logic. Each rule maps to a sentence an analyst can check.
- **Synthetic data from a seeded generator, not an LLM.** The same `config.toml` always gives a byte-identical file. Pattern sizes scale with volume, so the patterns stand out the same at 500 rows or 40,000 a month.
- **No leakage, and tests prove it.** A test flips every fraud label in the scored window and checks that no score changes. Another test checks that two half-batches score the same as one full replay.
- **DuckDB for storage.** It is one file, needs no server, handles SQL over millions of rows, and the dashboard opens it read-only.
- **Streamlit for the dashboard.** All Python, with filters and downloads built in. The filter logic is a plain function, so it is tested without a browser.
