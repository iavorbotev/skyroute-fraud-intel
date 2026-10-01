from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd
import pytest

from fraud_intel.application.stream import StreamProcessor, run_pipeline
from fraud_intel.config import AppConfig
from fraud_intel.domain.model import Transaction, transactions_from_frame
from fraud_intel.domain.settled import SettledRates
from fraud_intel.infrastructure.generator import generate_transactions
from tests.fakes import FakeStore, RecordingNotifier


def _event(transaction_id: str, minute: int, customer_id: str) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        timestamp_utc=datetime(2026, 9, 10, 15, 0) + timedelta(minutes=minute),
        customer_id=customer_id,
        customer_email=f"{customer_id}@gmail.com",
        billing_country="BR",
        ip_country="BR",
        payment_method="pix",
        card_bin=None,
        amount_usd=300.0,
        status="approved",
        is_fraud=False,
        booking_type="flight",
        destination_country="BR",
        departure_date=datetime(2026, 10, 10),
    )


def _processor(config: AppConfig) -> StreamProcessor:
    rates = SettledRates(overall_rate=0.01, segment_rates={}, bin_rates={}, amount_p99={})
    return StreamProcessor(
        rates=rates, scored_from=datetime(2026, 1, 1), risk=config.risk, utc_offset_hours=config.utc_offset_hours
    )


def test_velocity_counts_only_the_last_hour(config: AppConfig) -> None:
    events = [_event(f"tx_{minute}", minute, "c_1") for minute in (0, 10, 20, 200)]
    scored = list(_processor(config=config).process(transactions=events))

    assert "velocity" in scored[2].rules_fired
    assert "3 bookings from this customer in 20 min" in scored[2].reasons
    assert "velocity" not in scored[3].rules_fired


def test_events_out_of_time_order_are_refused(config: AppConfig) -> None:
    events = [_event("tx_late", 30, "c_1"), _event("tx_early", 0, "c_2")]
    with pytest.raises(ValueError, match="time order"):
        list(_processor(config=config).process(transactions=events))


@pytest.fixture(scope="module")
def small_config(config: AppConfig) -> AppConfig:
    return replace(config, generator=replace(config.generator, transactions_per_window=3000, customers_per_window=2000))


def _scores(config: AppConfig, frame: pd.DataFrame) -> pd.Series:
    store = FakeStore()
    store.append_raw(frame=frame)
    run_pipeline(store=store, config=config, notifier=RecordingNotifier())
    return store.load_scored().set_index("transaction_id")["risk_score"]


def test_scores_never_see_scored_window_labels(small_config: AppConfig) -> None:
    frame = generate_transactions(config=small_config)
    flipped = frame.copy()
    scored_window = flipped["timestamp_utc"] >= flipped["timestamp_utc"].max() - pd.Timedelta(days=29)
    approved = flipped["status"] == "approved"
    flipped.loc[scored_window & approved, "is_fraud"] = ~flipped.loc[scored_window & approved, "is_fraud"]

    pd.testing.assert_series_equal(
        _scores(config=small_config, frame=frame), _scores(config=small_config, frame=flipped)
    )


def test_two_batches_score_the_same_as_one(small_config: AppConfig) -> None:
    store = FakeStore()
    store.append_raw(frame=generate_transactions(config=small_config))
    raw = store.load_raw()
    events = transactions_from_frame(frame=raw)
    middle = len(events) // 2

    def processor() -> StreamProcessor:
        return StreamProcessor(
            rates=SettledRates(overall_rate=0.01, segment_rates={}, bin_rates={}, amount_p99={}),
            scored_from=raw["timestamp_utc"].min().to_pydatetime(),
            risk=small_config.risk,
            utc_offset_hours=small_config.utc_offset_hours,
        )

    streamed = processor()
    in_two = list(streamed.process(transactions=events[:middle])) + list(streamed.process(transactions=events[middle:]))
    in_one = list(processor().process(transactions=events))
    assert in_two == in_one
    assert any(item.risk_level == "high" for item in in_one)
