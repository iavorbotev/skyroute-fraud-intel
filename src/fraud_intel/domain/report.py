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
    groups = (
        flagged.groupby(["payment_method", "ip_country", "billing_country"])
        .agg(transactions=("transaction_id", "count"), exposure=("amount_usd", "sum"))
        .sort_values(["exposure", "transactions"], ascending=False)
    )
    (method, ip_country, billing_country), worst = next(iter(groups.iterrows()))
    origin = (
        f"from {billing_country}"
        if ip_country == billing_country
        else f"from IPs in {ip_country} billed in {billing_country}"
    )
    return (
        f"Today's highest risk: {method} bookings {origin} "
        f"({int(worst['transactions'])} transactions, ${worst['exposure']:,.0f} total exposure)."
    )
