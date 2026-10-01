"""Fraud metrics over scored transactions: rates per segment, per day, and how well the score separates fraud."""

import pandas as pd


def add_dimensions(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the analysis bands the dashboard slices by."""
    if frame is None:
        raise ValueError("frame is required")
    enriched = frame.copy()
    enriched["date"] = enriched["timestamp_utc"].dt.normalize()
    enriched["value_band"] = pd.cut(
        enriched["amount_usd"],
        bins=[0, 200, 500, 800, 1500, float("inf")],
        labels=["< $200", "$200-500", "$500-800", "$800-1,500", "$1,500+"],
    )
    days_out = (enriched["departure_date"] - enriched["timestamp_utc"]).dt.total_seconds() / 86400
    enriched["days_to_departure"] = pd.cut(
        days_out,
        bins=[-float("inf"), 2, 7, 30, float("inf")],
        labels=["0-2 days", "3-7 days", "8-30 days", "30+ days"],
    )
    enriched["booking_hour"] = enriched["local_hour"].map(lambda hour: f"{int(hour):02d}:00")
    enriched["night_booking"] = enriched["local_hour"].lt(6).map({True: "00:00-05:59", False: "06:00-23:59"})
    enriched["ip_matches_billing"] = (enriched["ip_country"] == enriched["billing_country"]).map(
        {True: "IP matches billing", False: "IP differs from billing"}
    )
    return enriched


def headline(frame: pd.DataFrame, chargeback_fee_usd: float) -> dict[str, float]:
    if frame is None:
        raise ValueError("frame is required")
    approved = frame[frame["status"] == "approved"]
    frauds = approved[approved["is_fraud"]]
    return {
        "attempts": len(frame),
        "approved": len(approved),
        "auth_rate": len(approved) / len(frame) if len(frame) else 0.0,
        "frauds": len(frauds),
        "fraud_rate": len(frauds) / len(approved) if len(approved) else 0.0,
        "fraud_usd": float(frauds["amount_usd"].sum()),
        # the merchant loses the booking value and pays a fee on every chargeback
        "chargeback_cost_usd": float(frauds["amount_usd"].sum()) + len(frauds) * chargeback_fee_usd,
    }


def segment_summary(frame: pd.DataFrame, by: list[str], chargeback_fee_usd: float) -> pd.DataFrame:
    """Attempts, auth rate, fraud rate, and losses per segment; fraud rate is frauds over approved bookings."""
    if frame is None or not by:
        raise ValueError("frame and at least one grouping column are required")
    work = frame.assign(
        approved=frame["status"] == "approved",
        fraud=frame["is_fraud"] & (frame["status"] == "approved"),
        fraud_usd=frame["amount_usd"].where(frame["is_fraud"], 0.0),
    )
    summary = work.groupby(by, observed=True).agg(
        attempts=("transaction_id", "count"),
        approved=("approved", "sum"),
        frauds=("fraud", "sum"),
        fraud_usd=("fraud_usd", "sum"),
        avg_amount_usd=("amount_usd", "mean"),
    )
    summary["auth_rate"] = summary["approved"] / summary["attempts"]
    summary["fraud_rate"] = (summary["frauds"] / summary["approved"]).fillna(0.0)
    summary["chargeback_cost_usd"] = summary["fraud_usd"] + summary["frauds"] * chargeback_fee_usd
    return summary.reset_index()


def comparison_sentence(segments: pd.DataFrame, label_columns: list[str], min_approved: int) -> str | None:
    """'Fraud is 4.2% for AR card but 0.6% for BR pix': the riskiest and safest segments with enough volume."""
    if segments is None or not label_columns:
        raise ValueError("segments and label_columns are required")
    sized = segments[segments["approved"] >= min_approved]
    if len(sized) < 2:
        return None
    ranked = sized.sort_values("fraud_rate")
    riskiest, safest = ranked.iloc[-1], ranked.iloc[0]

    def label(row: pd.Series) -> str:
        return " ".join(str(row[column]) for column in label_columns)

    return (
        f"Fraud is {riskiest['fraud_rate']:.1%} for {label(riskiest)} "
        f"but {safest['fraud_rate']:.1%} for {label(safest)}."
    )


def score_check(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """Use the chargeback labels only to measure the score: fraud rate per level, precision and recall of 'high'."""
    if frame is None:
        raise ValueError("frame is required")
    approved = frame[frame["in_scored_window"] & (frame["status"] == "approved")]
    by_level = approved.groupby("risk_level").agg(bookings=("transaction_id", "count"), frauds=("is_fraud", "sum"))
    by_level = by_level.reindex(["low", "medium", "high"]).fillna(0)
    by_level["fraud_rate"] = (by_level["frauds"] / by_level["bookings"]).fillna(0.0)
    by_level["share_of_all_fraud"] = by_level["frauds"] / max(by_level["frauds"].sum(), 1)
    high = approved["risk_level"] == "high"
    flagged = approved["risk_level"].isin(["medium", "high"])
    total_fraud = max(int(approved["is_fraud"].sum()), 1)
    stats = {
        "precision_high": float(approved.loc[high, "is_fraud"].mean()) if high.any() else 0.0,
        "recall_high": float(approved.loc[high, "is_fraud"].sum()) / total_fraud,
        "recall_medium_or_high": float(approved.loc[flagged, "is_fraud"].sum()) / total_fraud,
        "base_rate": float(approved["is_fraud"].mean()) if len(approved) else 0.0,
    }
    return by_level.reset_index(), stats
