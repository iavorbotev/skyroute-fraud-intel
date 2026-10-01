"""Explainable risk rules: each one looks at a transaction and its signals and returns a plain reason or None."""

from collections.abc import Callable
from dataclasses import dataclass

from fraud_intel.config import RiskConfig
from fraud_intel.domain.model import Signals, Transaction


@dataclass(frozen=True)
class RuleHit:
    name: str
    points: int
    reason: str


type RuleCheck = Callable[[Transaction, Signals, RiskConfig], str | None]


def evaluate_rules(transaction: Transaction, signals: Signals, risk: RiskConfig) -> list[RuleHit]:
    if transaction is None or signals is None or risk is None:
        raise ValueError("transaction, signals and risk config are required")
    hits = []
    for name, check in _rule_checks().items():
        reason = check(transaction, signals, risk)
        if reason is not None:
            hits.append(RuleHit(name=name, points=risk.points[name], reason=reason))
    return hits


def score_hits(hits: list[RuleHit], risk: RiskConfig) -> tuple[int, str]:
    score = min(100, sum(hit.points for hit in hits))
    if score >= risk.high_threshold:
        return score, "high"
    if score >= risk.medium_threshold:
        return score, "medium"
    return score, "low"


def recommend_action(transaction: Transaction, risk_level: str, rules_fired: set[str]) -> str:
    if risk_level == "low":
        return "No action"
    if not transaction.approved:
        return "Watch customer and IP" if risk_level == "high" else "No action"
    if risk_level == "high" and rules_fired & {"velocity", "card_testing", "risky_bin"}:
        return "Refund and block card"
    if risk_level == "high":
        return "Contact customer to verify before travel"
    return "Manual review"


def _rule_checks() -> dict[str, RuleCheck]:
    return {
        "velocity": _velocity,
        "card_testing": _card_testing,
        "geo_mismatch": _geo_mismatch,
        "new_customer_high_value": _new_customer_high_value,
        "value_outlier": _value_outlier,
        "last_minute": _last_minute,
        "night_hours": _night_hours,
        "risky_segment": _risky_segment,
        "risky_bin": _risky_bin,
    }


def _velocity(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.attempts_in_window < risk.velocity_min_attempts:
        return None
    minutes = max(1, round(signals.minutes_since_first_in_window))
    return f"{signals.attempts_in_window} bookings from this customer in {minutes} min"


def _card_testing(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.distinct_bins_in_window >= risk.card_testing_min_bins:
        return f"{signals.distinct_bins_in_window} different cards tried in {risk.velocity_window_minutes} min"
    if signals.declines_in_window >= risk.card_testing_min_declines:
        return f"{signals.declines_in_window} declines in {risk.velocity_window_minutes} min before this attempt"
    return None


def _geo_mismatch(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if transaction.ip_country == transaction.billing_country:
        return None
    return f"IP in {transaction.ip_country}, billing in {transaction.billing_country}"


def _new_customer_high_value(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.is_returning or transaction.amount_usd < risk.high_value_usd:
        return None
    return f"First booking, ${transaction.amount_usd:,.0f}"


def _value_outlier(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.amount_p99 is None or transaction.amount_usd <= signals.amount_p99:
        return None
    return (
        f"${transaction.amount_usd:,.0f} is above 99% of past "
        f"{transaction.billing_country} {transaction.payment_method} bookings (${signals.amount_p99:,.0f})"
    )


def _last_minute(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.hours_to_departure is None:
        return None
    if signals.hours_to_departure > risk.last_minute_hours or transaction.amount_usd < risk.last_minute_min_usd:
        return None
    return f"Travel starts in {max(0, round(signals.hours_to_departure))} h"


def _night_hours(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if not risk.night_start_hour <= signals.local_hour < risk.night_end_hour:
        return None
    return f"Booked at {signals.local_hour:02d}:00-{signals.local_hour:02d}:59 local time"


def _risky_segment(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    # with no settled history there is no rate to compare against, so the rule stays silent
    if signals.segment_rate is None or signals.overall_rate <= 0:
        return None
    if signals.segment_rate < risk.risky_segment_multiplier * signals.overall_rate:
        return None
    return (
        f"{transaction.billing_country} {transaction.payment_method} had {signals.segment_rate:.1%} fraud "
        f"last month (overall {signals.overall_rate:.1%})"
    )


def _risky_bin(transaction: Transaction, signals: Signals, risk: RiskConfig) -> str | None:
    if signals.bin_rate is None or signals.overall_rate <= 0:
        return None
    if signals.bin_rate < risk.risky_bin_multiplier * signals.overall_rate:
        return None
    return f"BIN {transaction.card_bin} had {signals.bin_rate:.1%} fraud last month"
