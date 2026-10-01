from dataclasses import replace
from datetime import datetime

from fraud_intel.config import AppConfig
from fraud_intel.domain.model import Signals, Transaction
from fraud_intel.domain.rules import evaluate_rules, recommend_action, score_hits


def _risky_booking() -> Transaction:
    return Transaction(
        transaction_id="tx_1",
        timestamp_utc=datetime(2026, 9, 18, 6, 30),
        customer_id="c_new",
        customer_email="c_new@gmail.com",
        billing_country="AR",
        ip_country="RU",
        payment_method="card",
        card_bin="451234",
        amount_usd=1240.0,
        status="approved",
        is_fraud=False,
        booking_type="flight",
        destination_country="BR",
        departure_date=datetime(2026, 9, 19),
    )


def _signals(attempts: int, is_returning: bool) -> Signals:
    return Signals(
        attempts_in_window=attempts,
        declines_in_window=0,
        distinct_bins_in_window=1,
        minutes_since_first_in_window=25,
        is_returning=is_returning,
        local_hour=3,
        hours_to_departure=20,
        overall_rate=0.01,
        segment_rate=0.03,
        bin_rate=0.06,
        amount_p99=1100.0,
    )


def test_risky_booking_gets_reasons_and_a_card_action(config: AppConfig) -> None:
    booking = _risky_booking()
    hits = evaluate_rules(transaction=booking, signals=_signals(attempts=3, is_returning=False), risk=config.risk)
    score, level = score_hits(hits=hits, risk=config.risk)
    reasons = {hit.name: hit.reason for hit in hits}

    assert reasons["velocity"] == "3 bookings from this customer in 25 min"
    assert reasons["geo_mismatch"] == "IP in RU, billing in AR"
    assert reasons["new_customer_high_value"] == "First booking, $1,240"
    assert reasons["risky_bin"] == "BIN 451234 had 6.0% fraud last month"
    assert (score, level) == (100, "high")
    assert recommend_action(transaction=booking, risk_level=level, rules_fired=set(reasons)) == "Refund and block card"


def test_ordinary_booking_stays_low(config: AppConfig) -> None:
    ordinary = replace(_risky_booking(), amount_usd=300.0, departure_date=datetime(2026, 10, 30))
    calm = replace(_signals(attempts=1, is_returning=True), local_hour=14, hours_to_departure=1000, bin_rate=0.01)
    hits = evaluate_rules(transaction=ordinary, signals=calm, risk=config.risk)

    assert [hit.name for hit in hits] == ["geo_mismatch", "risky_segment"]
    assert score_hits(hits=hits, risk=config.risk) == (35, "medium")
