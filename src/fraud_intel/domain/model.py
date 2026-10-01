"""Events that flow through the stream: a raw transaction in, a scored transaction out."""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True, slots=True)
class Transaction:
    transaction_id: str
    timestamp_utc: datetime
    customer_id: str
    customer_email: str
    billing_country: str
    ip_country: str
    payment_method: str
    card_bin: str | None
    amount_usd: float
    status: str
    is_fraud: bool
    booking_type: str
    destination_country: str
    departure_date: datetime | None

    @property
    def approved(self) -> bool:
        return self.status == "approved"


@dataclass(frozen=True, slots=True)
class Signals:
    """What the scorer knows at payment time: customer memory plus settled history lookups."""

    attempts_in_window: int
    declines_in_window: int
    distinct_bins_in_window: int
    minutes_since_first_in_window: float
    is_returning: bool
    local_hour: int
    hours_to_departure: float | None
    overall_rate: float
    segment_rate: float | None
    bin_rate: float | None
    amount_p99: float | None


@dataclass(frozen=True, slots=True)
class ScoredTransaction:
    transaction: Transaction
    in_scored_window: bool
    is_returning: bool
    local_hour: int
    risk_score: int | None
    risk_level: str | None
    rules_fired: tuple[str, ...]
    reasons: tuple[str, ...]
    recommended_action: str | None


def transactions_from_frame(frame: pd.DataFrame) -> list[Transaction]:
    """Turn a validated, time-sorted frame into events; this is the file/batch source of the stream."""
    if frame is None:
        raise ValueError("frame is required")
    events = []
    for row in frame.to_dict(orient="records"):
        card_bin = None if pd.isna(row["card_bin"]) or row["card_bin"] == "" else str(row["card_bin"])
        events.append(
            Transaction(
                transaction_id=str(row["transaction_id"]),
                timestamp_utc=row["timestamp_utc"].to_pydatetime(),
                customer_id=str(row["customer_id"]),
                customer_email=str(row["customer_email"]),
                billing_country=str(row["billing_country"]),
                ip_country=str(row["ip_country"]),
                payment_method=str(row["payment_method"]),
                card_bin=card_bin,
                amount_usd=float(row["amount_usd"]),
                status=str(row["status"]),
                is_fraud=bool(row["is_fraud"]),
                booking_type=str(row["booking_type"]),
                destination_country=str(row["destination_country"]),
                departure_date=None if pd.isna(row["departure_date"]) else row["departure_date"].to_pydatetime(),
            )
        )
    return events


def scored_to_frame(scored: list[ScoredTransaction]) -> pd.DataFrame:
    if scored is None:
        raise ValueError("scored is required")
    records = []
    for item in scored:
        event = item.transaction
        records.append(
            {
                "transaction_id": event.transaction_id,
                "timestamp_utc": event.timestamp_utc,
                "customer_id": event.customer_id,
                "customer_email": event.customer_email,
                "billing_country": event.billing_country,
                "ip_country": event.ip_country,
                "payment_method": event.payment_method,
                "card_bin": event.card_bin,
                "amount_usd": event.amount_usd,
                "status": event.status,
                "is_fraud": event.is_fraud,
                "booking_type": event.booking_type,
                "destination_country": event.destination_country,
                "departure_date": event.departure_date,
                "in_scored_window": item.in_scored_window,
                "customer_type": "returning" if item.is_returning else "new",
                "local_hour": item.local_hour,
                "risk_score": item.risk_score,
                "risk_level": item.risk_level,
                "rules_fired": ", ".join(item.rules_fired),
                "reasons": "; ".join(item.reasons),
                "recommended_action": item.recommended_action,
            }
        )
    frame = pd.DataFrame.from_records(records)
    frame["risk_score"] = frame["risk_score"].astype("Int64")
    # fix the types of columns that can be entirely empty, or the store guesses them (all-null becomes integer)
    frame["departure_date"] = pd.to_datetime(frame["departure_date"])
    frame["card_bin"] = frame["card_bin"].astype("string")
    return frame
