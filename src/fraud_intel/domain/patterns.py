"""Find the risky patterns in a set of scored bookings, without knowing in advance what was planted."""

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


@dataclass(frozen=True)
class PatternFinding:
    key: str
    title: str
    sentence: str
    bookings: int
    fraud_rate: float
    lift: float
    fraud_usd: float
    # True for a pattern that looks risky but is not, so analysts do not over-react to it
    caution: bool
    window_start: pd.Timestamp | None


def detect_patterns(
    frame: pd.DataFrame,
    high_value_usd: float,
    burst_window_days: int,
    burst_min_ratio: float,
    burst_min_count: int,
    big_booking_usd: float,
) -> list[PatternFinding]:
    """Run every check on the given bookings; risky findings come first, ordered by how much they raise fraud."""
    if frame is None:
        raise ValueError("frame is required")
    if burst_window_days < 1 or burst_min_ratio <= 1 or burst_min_count < 1:
        raise ValueError("burst settings must be positive, and the ratio above 1")
    approved = frame[frame["status"] == "approved"]
    base_rate = float(approved["is_fraud"].mean()) if len(approved) else 0.0
    risky = [
        _burst(
            frame=frame,
            high_value_usd=high_value_usd,
            window_days=burst_window_days,
            min_ratio=burst_min_ratio,
            min_count=burst_min_count,
            base_rate=base_rate,
        ),
        _rule_pattern(
            frame=frame,
            rule="velocity",
            title="Many bookings in one hour",
            action="made several bookings within an hour",
            base_rate=base_rate,
        ),
        _rule_pattern(
            frame=frame,
            rule="card_testing",
            title="Card testing",
            action="tried several cards, or kept getting declined, within an hour",
            base_rate=base_rate,
        ),
        _geo_mismatch(frame=frame, base_rate=base_rate),
    ]
    found = sorted((finding for finding in risky if finding is not None), key=lambda finding: -finding.lift)
    caution = _big_returning(frame=frame, big_booking_usd=big_booking_usd, base_rate=base_rate)
    return found + ([caution] if caution is not None else [])


def _stats(rows: pd.DataFrame, base_rate: float) -> tuple[float, float, float]:
    approved = rows[rows["status"] == "approved"]
    fraud_rate = float(approved["is_fraud"].mean()) if len(approved) else 0.0
    lift = fraud_rate / base_rate if base_rate > 0 else 0.0
    fraud_usd = float(approved.loc[approved["is_fraud"], "amount_usd"].sum())
    return fraud_rate, lift, fraud_usd


def _burst(
    frame: pd.DataFrame, high_value_usd: float, window_days: int, min_ratio: float, min_count: int, base_rate: float
) -> PatternFinding | None:
    """The country and method whose high-value bookings in some short window most exceed their usual level."""
    big = frame[frame["amount_usd"] >= high_value_usd]
    if big.empty:
        return None
    days = pd.date_range(frame["date"].min(), frame["date"].max(), freq="D")
    counts = big.groupby(["billing_country", "payment_method", "date"]).size()
    best = None
    for (country, method), series in counts.groupby(level=["billing_country", "payment_method"]):
        rolling = series.droplevel([0, 1]).reindex(days, fill_value=0).rolling(window_days).sum().dropna()
        if rolling.empty:
            continue
        # the median window is the segment's normal level, so one burst does not raise its own baseline
        usual = max(float(rolling.median()), 1.0)
        peak = float(rolling.max())
        ratio = peak / usual
        if peak >= min_count and ratio >= min_ratio and (best is None or ratio > best[0]):
            best = (ratio, country, method, rolling.idxmax(), usual)
    if best is None:
        return None
    ratio, country, method, window_end, usual = best
    window_start = window_end - timedelta(days=window_days - 1)
    rows = big[
        (big["billing_country"] == country)
        & (big["payment_method"] == method)
        & (big["date"] >= window_start)
        & (big["date"] <= window_end)
    ]
    fraud_rate, lift, fraud_usd = _stats(rows=rows, base_rate=base_rate)
    return PatternFinding(
        key="burst",
        title=f"{country} {method}: a {window_days}-day burst",
        sentence=(
            f"{len(rows):,} {country} {method} bookings over ${high_value_usd:,.0f} between "
            f"{window_start:%d %b} and {window_end:%d %b}, {ratio:.1f}x the usual {window_days}-day level "
            f"({usual:.0f}). {fraud_rate:.0%} of the approved ones turned out to be fraud."
        ),
        bookings=len(rows),
        fraud_rate=fraud_rate,
        lift=lift,
        fraud_usd=fraud_usd,
        caution=False,
        window_start=window_start,
    )


def _rule_pattern(frame: pd.DataFrame, rule: str, title: str, action: str, base_rate: float) -> PatternFinding | None:
    fired = frame["rules_fired"].fillna("").str.split(", ").apply(lambda names: rule in names)
    rows = frame[fired]
    if rows.empty:
        return None
    fraud_rate, lift, fraud_usd = _stats(rows=rows, base_rate=base_rate)
    return PatternFinding(
        key=rule,
        title=title,
        sentence=(
            f"{rows['customer_id'].nunique():,} customers {action} ({len(rows):,} bookings). "
            f"{fraud_rate:.0%} of the approved ones turned out to be fraud."
        ),
        bookings=len(rows),
        fraud_rate=fraud_rate,
        lift=lift,
        fraud_usd=fraud_usd,
        caution=False,
        window_start=None,
    )


def _geo_mismatch(frame: pd.DataFrame, base_rate: float) -> PatternFinding | None:
    rows = frame[frame["ip_country"] != frame["billing_country"]]
    if rows.empty:
        return None
    fraud_rate, lift, fraud_usd = _stats(rows=rows, base_rate=base_rate)
    top_ips = rows.loc[rows["is_fraud"], "ip_country"].value_counts().head(3).index.tolist()
    where = f" The most common IP countries among the fraud were {', '.join(top_ips)}." if top_ips else ""
    return PatternFinding(
        key="geo_mismatch",
        title="IP in another country",
        sentence=(
            f"{len(rows):,} bookings came from an IP in a different country than the billing address. "
            f"{fraud_rate:.0%} of the approved ones turned out to be fraud.{where}"
        ),
        bookings=len(rows),
        fraud_rate=fraud_rate,
        lift=lift,
        fraud_usd=fraud_usd,
        caution=False,
        window_start=None,
    )


def _big_returning(frame: pd.DataFrame, big_booking_usd: float, base_rate: float) -> PatternFinding | None:
    big = frame[frame["amount_usd"] >= big_booking_usd]
    returning = big[big["customer_type"] == "returning"]
    if returning.empty:
        return None
    fraud_rate, lift, fraud_usd = _stats(rows=returning, base_rate=base_rate)
    new_rate, _, _ = _stats(rows=big[big["customer_type"] == "new"], base_rate=base_rate)
    return PatternFinding(
        key="big_returning",
        title="Big bookings alone are not a red flag",
        sentence=(
            f"{len(returning):,} bookings over ${big_booking_usd:,.0f} came from returning customers, and "
            f"{fraud_rate:.1%} of them were fraud, against {new_rate:.0%} for first-time customers. "
            "Judge big bookings by who makes them, not by size."
        ),
        bookings=len(returning),
        fraud_rate=fraud_rate,
        lift=lift,
        fraud_usd=fraud_usd,
        caution=True,
        window_start=None,
    )
