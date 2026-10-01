# What would I add with more time?

## Statistical tests as alert triggers

Today the alerts fire on fixed multiples, such as "2x the 7-day share" or "3x the daily average".
A fixed multiple ignores volume: 5 high-risk bookings out of 25 and 50 out of 250 look the same, but the first could easily be chance.
A test asks how likely the count would be if nothing had changed, and alerts only when that chance is small.

- **Spikes in a share** (high-risk share, decline rate): a binomial test of this hour's count against the 7-day rate.
- **Bursts in a count** (high-value card bookings per country): a Poisson test of today's count against the usual daily rate, with a separate baseline for each hour of the week, since Monday morning and Saturday night look different.
- **Slow drifts** that no single day reveals: a CUSUM chart, which adds up small daily excesses over the baseline and alerts once the running total crosses a limit.
- **Too many false alarms:** testing 5 countries x 5 methods every hour runs hundreds of tests a day, so some will fire by chance. Correcting the p-values (Benjamini-Hochberg) keeps the share of false alarms near a chosen level, such as 5%.
- **Chargeback rates** go through the same tests once they settle, a few weeks later.

## Everything else

- **Live mode:** a Redpanda source, a sink that writes small Parquet files, and a dashboard that refreshes on a timer.
- **Chargeback dates:** add a `chargeback_date` field, so settled rates use what was known on each day instead of a fixed 30-day cut.
- **Learned weights:** fit the rule points with logistic regression on a time-ordered split, and keep the reasons.
- **Analyst feedback:** let analysts mark a flag as right or wrong, and use those marks to tune thresholds.
- **More signals:** device fingerprint, email domain age, a passenger name that differs from the cardholder, and real IP geolocation.
- **Operations:** a Slack notifier, CI that runs lint and tests, and a hosted demo.
