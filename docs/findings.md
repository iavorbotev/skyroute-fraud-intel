# What the test data shows

The dataset is synthetic.
It covers 60 days and 80,653 transactions, at SkyRoute's real volume of 40,000 a month.
The first 30 days are the settled history at the old fraud level.
In the last 30, a fraud wave starts on day 10, and we planted known attack patterns.
So these findings show that the system recovers what we planted, and how clearly.

## How much did fraud grow?

The fraud rate nearly quadrupled.
It went from 0.74% of approved bookings in August to 2.72% in September.
It was 1.06% in the first nine days of September, then 3.43% once the wave started.
September chargebacks cost $837,448 including fees, against $198,783 in August.

## Where is the money going?

Cards carry almost all of it, and Argentina most of all.

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

## Was there a single attack?

Yes: an Argentina card attack ran for three days.
Between 17 and 19 September, 111 Argentine card bookings over $800 turned out to be fraud.
The country-burst alert fired on the first evening, 17 September at 21:23 UTC: "44 card bookings over $800 from AR in 24 h vs 15 a day last week".

## Which behaviors separate fraud from normal bookings?

| Behavior | Fraud rate with it | Without it |
|---|---|---|
| IP country differs from billing | 36.2% | 1.7% |
| Booked 00:00-05:59 local time | 20.1% | 1.8% |
| Travel starts within 2 days | 14.4% | 1.7-3.2% |
| New customer | 6.7% | 0.4% |
| $1,500 or more | 23.2% | 0.4% under $200 |

## Does the score find fraud?

Yes, and it stays explainable.

- Approved bookings scored "high" turned out to be fraud 83% of the time (201 of 243), against a 2.7% base rate.
- "Medium" or "high" covers 4.5% of approved bookings and catches 66% of all fraud.
- Card testing (65% fraud when it fires) and velocity (63%) are the sharpest rules.
- `risky_segment` is the weakest (5.5%). Last month it flagged only BR card, while this month's attack hit AR card. Segment rates look backward, and the fraudsters had moved on, which is SkyRoute's complaint about weekly reports. The per-event rules caught the new attack instead.

49 alerts fired: 20 velocity bursts, 20 card-testing runs, 8 high-risk share spikes, and the Argentina burst.

## What should SkyRoute do?

1. **Check card bookings before approval, not after.** Send high-score card bookings to 3-D Secure or manual review before the ticket is issued. Today's rules engine only flags after approval, which is why fraud completes.
2. **Hold last-minute bookings from new customers with foreign IPs.** That combination is where the money goes. A short verification call costs less than a $1,500 chargeback.
3. **Steer new customers toward PIX, OXXO, and PSE.** Fraud on local methods is 0.2-0.6%, against 2-8% on cards.
4. **Watch-list the BINs and customers the score flags.** Block card-testing customers after the first burst, and review BINs whose settled fraud rate is 3x normal.
5. **Page someone on the country-burst alert.** It caught the Argentina attack on its first evening, two days before it ended.
