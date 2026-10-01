"""Daily Top N report: the riskiest bookings of one day, each with a reason and an action."""

from datetime import date

import pandas as pd


def daily_top(frame: pd.DataFrame, day: date, top_n: int) -> pd.DataFrame:
    if frame is None or day is None or top_n < 1:
        raise ValueError("frame, day and a positive top_n are required")
    on_day = frame[frame["in_scored_window"] & (frame["timestamp_utc"].dt.date == day)]
    top = on_day.sort_values(["risk_score", "amount_usd"], ascending=[False, False]).head(top_n)
    columns = [
        "transaction_id",
        "timestamp_utc",
        "customer_id",
        "billing_country",
        "ip_country",
        "payment_method",
        "card_bin",
        "amount_usd",
        "status",
        "risk_score",
        "risk_level",
        "reasons",
        "recommended_action",
    ]
    report = top.loc[:, columns].reset_index(drop=True)
    report.insert(0, "rank", range(1, len(report) + 1))
    return report


def summary_insight(top: pd.DataFrame) -> str:
    """One line naming the group of flagged bookings with the most money at stake."""
    if top is None:
        raise ValueError("top is required")
    flagged = top[top["risk_level"] == "high"]
    if flagged.empty:
        return "No high-risk bookings on this day."
    # group by market and method; fraud rings rotate IP countries, so splitting by IP would scatter one attack
    groups = (
        flagged.assign(foreign_ip=flagged["ip_country"] != flagged["billing_country"])
        .groupby(["billing_country", "payment_method"])
        .agg(
            transactions=("transaction_id", "count"),
            exposure=("amount_usd", "sum"),
            foreign_ip=("foreign_ip", "sum"),
        )
        .sort_values(["exposure", "transactions"], ascending=False)
    )
    (billing_country, method), worst = next(iter(groups.iterrows()))
    return (
        f"Today's highest risk: {billing_country} {method} bookings "
        f"({int(worst['transactions'])} high-risk transactions, ${worst['exposure']:,.0f} total exposure, "
        f"{int(worst['foreign_ip'])} from IPs outside {billing_country})."
    )
