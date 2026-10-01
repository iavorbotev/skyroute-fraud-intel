"""Fraud rates from the settled history window: the slow path the stream only looks up."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from pandas.api.typing import DataFrameGroupBy


@dataclass(frozen=True)
class SettledRates:
    overall_rate: float
    segment_rates: dict[tuple[str, str], float]
    bin_rates: dict[str, float]
    amount_p99: dict[tuple[str, str], float]

    def segment_rate(self, country: str, method: str) -> float:
        return self.segment_rates.get((country, method), self.overall_rate)

    def bin_rate(self, card_bin: str | None) -> float | None:
        return None if card_bin is None else self.bin_rates.get(card_bin)


def scored_window_start(frame: pd.DataFrame, scored_window_days: int) -> datetime:
    """The scored window is the last N calendar days; everything before it counts as settled history."""
    if frame is None or frame.empty:
        raise ValueError("need at least one transaction to place the scored window")
    if scored_window_days < 1:
        raise ValueError("scored_window_days must be at least 1")
    # the date part of the latest event, as midnight
    last_day = datetime.fromisoformat(str(frame["timestamp_utc"].max())[:10])
    return last_day + timedelta(days=1) - timedelta(days=scored_window_days)


def build_settled_rates(history: pd.DataFrame, prior_strength: float, outlier_quantile: float) -> SettledRates:
    """Smoothed fraud rates per (country, method) and per BIN, plus amount cut-offs, from history only."""
    if history is None:
        raise ValueError("history is required")
    if prior_strength < 0 or not 0 < outlier_quantile < 1:
        raise ValueError("prior_strength must be >= 0 and outlier_quantile in (0, 1)")
    approved = history[history["status"] == "approved"]
    if approved.empty:
        return SettledRates(overall_rate=0.0, segment_rates={}, bin_rates={}, amount_p99={})
    overall_rate = float(approved["is_fraud"].mean())

    def smoothed(groups: DataFrameGroupBy) -> pd.Series:
        # pull small groups toward the overall rate so three frauds out of ten bookings do not read as 30%
        counts = groups["is_fraud"].agg(["sum", "count"])
        return (counts["sum"] + prior_strength * overall_rate) / (counts["count"] + prior_strength)

    segment_rates = smoothed(approved.groupby(["billing_country", "payment_method"])).rename("rate").reset_index()
    card_rows = approved[approved["card_bin"].notna() & (approved["card_bin"] != "")]
    bin_rates = smoothed(card_rows.groupby("card_bin"))
    amount_p99 = (
        history.groupby(["billing_country", "payment_method"])["amount_usd"].quantile(outlier_quantile).reset_index()
    )
    return SettledRates(
        overall_rate=overall_rate,
        segment_rates={
            (str(country), str(method)): float(value)
            for country, method, value in zip(
                segment_rates["billing_country"], segment_rates["payment_method"], segment_rates["rate"], strict=True
            )
        },
        bin_rates={str(key): float(value) for key, value in bin_rates.items()},
        amount_p99={
            (str(country), str(method)): float(value)
            for country, method, value in zip(
                amount_p99["billing_country"], amount_p99["payment_method"], amount_p99["amount_usd"], strict=True
            )
        },
    )
