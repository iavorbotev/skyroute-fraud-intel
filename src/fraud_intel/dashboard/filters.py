"""Sidebar filters as plain data, so the slicing logic is testable without Streamlit."""

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    countries: tuple[str, ...]
    methods: tuple[str, ...]
    risk_levels: tuple[str, ...]


def apply_filters(frame: pd.DataFrame, filters: Filters, use_dates: bool) -> pd.DataFrame:
    """Keep rows matching every filter; an empty selection means "all", and a risk filter drops unscored rows."""
    if frame is None or filters is None:
        raise ValueError("frame and filters are required")
    keep = pd.Series(True, index=frame.index)
    if filters.countries:
        keep &= frame["billing_country"].isin(filters.countries)
    if filters.methods:
        keep &= frame["payment_method"].isin(filters.methods)
    if use_dates:
        day = frame["timestamp_utc"].dt.date
        keep &= (day >= filters.start) & (day <= filters.end)
    if filters.risk_levels:
        keep &= frame["risk_level"].isin(filters.risk_levels)
    return frame[keep]
