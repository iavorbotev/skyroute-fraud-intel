from datetime import datetime, timedelta

import pandas as pd
import pytest

from fraud_intel.application.stream import run_pipeline
from fraud_intel.config import AppConfig
from fraud_intel.domain.metrics import add_dimensions
from fraud_intel.domain.patterns import detect_patterns
from fraud_intel.infrastructure.generator import generate_transactions
from tests.fakes import FakeStore, RecordingNotifier


def _detect(config: AppConfig, frame: pd.DataFrame) -> dict:
    findings = detect_patterns(
        frame=frame,
        high_value_usd=config.risk.high_value_usd,
        burst_window_days=config.patterns.burst_window_days,
        burst_min_ratio=config.patterns.burst_min_ratio,
        burst_min_count=config.patterns.burst_min_count,
        big_booking_usd=config.patterns.big_booking_usd,
    )
    return {finding.key: finding for finding in findings}


@pytest.fixture(scope="module")
def scored_window(config: AppConfig) -> pd.DataFrame:
    store = FakeStore()
    store.append_raw(frame=generate_transactions(config=config))
    run_pipeline(store=store, config=config, notifier=RecordingNotifier())
    scored = add_dimensions(frame=store.load_scored())
    return scored[scored["in_scored_window"]]


def test_finds_the_patterns_planted_in_the_generated_data(config: AppConfig, scored_window: pd.DataFrame) -> None:
    findings = _detect(config=config, frame=scored_window)
    burst = findings["burst"]
    cluster_start = scored_window["timestamp_utc"].min().normalize() + timedelta(
        days=config.generator.ar_cluster_start_day
    )

    assert burst.title.startswith("AR card")
    assert abs((burst.window_start - cluster_start).days) <= 2
    for key in ("burst", "velocity", "card_testing", "geo_mismatch"):
        assert findings[key].lift > 3, key
    assert findings["big_returning"].lift < 1


def test_burst_detection_is_not_tied_to_one_market(config: AppConfig) -> None:
    # 30 quiet days of one high-value CO pix booking a day, then 15 in three days
    start = datetime(2026, 9, 1)
    rows = [(start + timedelta(days=day, hours=12), False) for day in range(30)]
    rows += [(start + timedelta(days=20, hours=hour), True) for hour in range(0, 72, 5)]
    frame = pd.DataFrame(
        {
            "transaction_id": [f"tx_{index}" for index in range(len(rows))],
            "timestamp_utc": [moment for moment, _ in rows],
            "customer_id": [f"c_{index}" for index in range(len(rows))],
            "billing_country": "CO",
            "ip_country": "CO",
            "payment_method": "pix",
            "amount_usd": 1200.0,
            "status": "approved",
            "is_fraud": [fraud for _, fraud in rows],
            "rules_fired": "",
            "customer_type": "new",
            "in_scored_window": True,
        }
    )
    frame["date"] = frame["timestamp_utc"].dt.normalize()

    burst = _detect(config=config, frame=frame)["burst"]

    assert burst.title.startswith("CO pix")
    assert burst.window_start == start + timedelta(days=20)
