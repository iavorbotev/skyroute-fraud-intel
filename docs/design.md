# How it works

## What happens to a transaction?

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

## Where is each piece in the code?

- `domain/` is pure logic with no I/O: the event model, customer memory, settled rates, rules, alert watchers, metrics, and the report.
- `application/stream.py` is the loop that pushes each event through the scorer and the alert watchers.
- `application/ports.py` holds two interfaces, `TransactionStore` and `Notifier`.
- `infrastructure/` implements them: DuckDB storage, file readers, the console notifier, and the data generator.
- `cli.py` and `dashboard/` build the real pieces and pass them in. Tests pass in-memory fakes instead.

## How do I feed in my own batch?

`ingest` takes any CSV or JSON batch, for example `make ingest DATA=my_batch.csv`.
It skips transaction IDs it has already seen, so feeding the same file twice is safe.

A batch needs only the fields the brief lists: `transaction_id`, `timestamp_utc`, `customer_id`, `billing_country`, `ip_country`, `payment_method`, `amount_usd`, `status` (approved/declined), and `is_fraud`.
`card_bin`, `customer_email`, `booking_type`, `destination_country`, and `departure_date` are optional.
Rules that need a missing field stay silent: without `departure_date` there is no last-minute rule, and without `card_bin` no BIN rule.
A batch of only 30 days has no settled history before it, so the three history rules (risky segment, risky BIN, value outlier) stay silent and the score rests on behavior: velocity, card testing, IP mismatch, new customer, and night bookings.

## Which make targets are there?

| Target | What it does |
|---|---|
| `make install` | install dependencies with uv |
| `make check` | ruff lint and format check, ty type check, and the pytest suite |
| `make data` | rebuild the seeded dataset from `config.toml` (it is also committed) |
| `make ingest` | load `DATA`, replay the stream, save scores and alerts |
| `make report DATE=2026-09-18` | export that day's Top 50 to `reports/` as CSV and JSON |
| `make dashboard PORT=8501` | run the dashboard locally |
| `make up` / `make down` / `make logs` | start, stop, or follow the Docker version |
| `make clean` | remove the database, reports, and caches |

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
Today that prints to the console (see `make logs`).
A Slack or PagerDuty notifier would implement the same `notify` method.

## How would it scale?

We built none of this, but the design leaves room for it:

- **Live source:** swap the file replay for a Kafka or Redpanda consumer. The scorer stays the same.
- **Scale out:** split events by `customer_id` across scorer copies, as Kafka partitions do. Each copy only keeps memory for its own customers.
- **Country alerts:** these need every customer's events, so they move to a second stage keyed by country.
- **Durable memory:** "has booked before" must be kept forever, so in production it lives in a key-value store such as Redis. Today a restart rebuilds memory by replaying stored events.
- **Settled rates:** these become a nightly batch job that refreshes a lookup table the stream reads.
- **Re-scoring:** `ingest` replays all stored events today, which takes seconds at 80,000. At larger volume the scorer would save its memory and replay only new events.

## What about Apache Beam?

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
