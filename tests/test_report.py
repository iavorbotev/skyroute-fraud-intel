from datetime import date

import pandas as pd

from fraud_intel.domain.report import daily_top, summary_insight


def test_daily_top_ranks_by_score_and_names_the_costliest_group() -> None:
    rows = [
        ("tx_1", 90, "high", "AR", "RU", 1200.0, "Refund and block card"),
        ("tx_2", 70, "high", "AR", "AR", 900.0, "Contact customer to verify before travel"),
        ("tx_3", 65, "high", "BR", "BR", 500.0, "Contact customer to verify before travel"),
        ("tx_4", 10, "low", "MX", "MX", 300.0, "No action"),
        ("tx_5", 95, "high", "AR", "RU", 2000.0, "Refund and block card"),
    ]
    frame = pd.DataFrame(
        rows,
        columns=["transaction_id", "risk_score", "risk_level", "billing_country", "ip_country", "amount_usd", "action"],
    ).rename(columns={"action": "recommended_action"})
    frame["timestamp_utc"] = pd.Timestamp("2026-09-18 10:00")
    frame.loc[4, "timestamp_utc"] = pd.Timestamp("2026-09-19 10:00")
    frame = frame.assign(
        in_scored_window=True, customer_id="c", payment_method="card", card_bin="451234", status="approved", reasons="x"
    )

    top = daily_top(frame=frame, day=date(2026, 9, 18), top_n=3)

    assert list(top["transaction_id"]) == ["tx_1", "tx_2", "tx_3"]
    assert list(top["rank"]) == [1, 2, 3]
    assert summary_insight(top=top) == (
        "Today's highest risk: AR card bookings "
        "(2 high-risk transactions, $2,100 total exposure, 1 from IPs outside AR)."
    )
