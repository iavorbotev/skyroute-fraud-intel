"""Alert watchers: each sees every scored event in time order and keeps its own running counts."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from fraud_intel.config import AlertConfig, RiskConfig
from fraud_intel.domain.model import ScoredTransaction


@dataclass(frozen=True)
class Alert:
    timestamp_utc: datetime
    rule: str
    severity: str
    subject: str
    message: str


class AlertWatcher(Protocol):
    def observe(self, scored: ScoredTransaction) -> list[Alert]: ...


def default_watchers(alerts: AlertConfig, risk: RiskConfig) -> list[AlertWatcher]:
    if alerts is None or risk is None:
        raise ValueError("alert and risk config are required")
    return [
        VelocityBurstWatcher(min_attempts=alerts.velocity_burst_min_attempts),
        CardTestingWatcher(min_declines=alerts.card_testing_min_declines, minutes=alerts.card_testing_window_minutes),
        HighRiskShareWatcher(multiplier=alerts.share_spike_multiplier, min_high_risk=alerts.share_spike_min_high_risk),
        CountryHighValueWatcher(
            multiplier=alerts.country_burst_multiplier,
            min_count=alerts.country_burst_min_count,
            high_value_usd=risk.high_value_usd,
        ),
    ]


def alerts_to_frame(alerts: list[Alert]) -> pd.DataFrame:
    columns = ["timestamp_utc", "rule", "severity", "subject", "message"]
    return pd.DataFrame([alert.__dict__ for alert in alerts], columns=columns)


class VelocityBurstWatcher:
    """One customer makes many attempts within an hour: a bot or a fraud ring working fast."""

    def __init__(self, min_attempts: int) -> None:
        if min_attempts < 2:
            raise ValueError("min_attempts must be at least 2")
        self._min_attempts = min_attempts
        self._attempts: dict[str, deque[datetime]] = {}
        self._last_alert: dict[str, datetime] = {}

    def observe(self, scored: ScoredTransaction) -> list[Alert]:
        event = scored.transaction
        now = event.timestamp_utc
        attempts = self._attempts.setdefault(event.customer_id, deque())
        attempts.append(now)
        while attempts[0] < now - timedelta(hours=1):
            attempts.popleft()
        # one alert per customer per hour, so a burst does not page the team six times
        recently_alerted = now - self._last_alert.get(event.customer_id, datetime.min) < timedelta(hours=1)
        if len(attempts) < self._min_attempts or recently_alerted:
            return []
        self._last_alert[event.customer_id] = now
        return [
            Alert(
                timestamp_utc=now,
                rule="velocity_burst",
                severity="critical",
                subject=event.customer_id,
                message=f"{event.customer_id} made {len(attempts)} attempts in 60 min "
                f"(${event.amount_usd:,.0f} latest, {event.billing_country} {event.payment_method})",
            )
        ]


class CardTestingWatcher:
    """Several declines in a short time from one customer: someone is testing stolen cards."""

    def __init__(self, min_declines: int, minutes: int) -> None:
        if min_declines < 1 or minutes < 1:
            raise ValueError("min_declines and minutes must be positive")
        self._min_declines = min_declines
        self._window = timedelta(minutes=minutes)
        self._declines: dict[str, deque[datetime]] = {}
        self._last_alert: dict[str, datetime] = {}

    def observe(self, scored: ScoredTransaction) -> list[Alert]:
        event = scored.transaction
        if event.approved:
            return []
        now = event.timestamp_utc
        declines = self._declines.setdefault(event.customer_id, deque())
        declines.append(now)
        while declines[0] < now - self._window:
            declines.popleft()
        recently_alerted = now - self._last_alert.get(event.customer_id, datetime.min) < self._window
        if len(declines) < self._min_declines or recently_alerted:
            return []
        self._last_alert[event.customer_id] = now
        return [
            Alert(
                timestamp_utc=now,
                rule="card_testing",
                severity="high",
                subject=event.customer_id,
                message=f"{event.customer_id} had {len(declines)} declines in {self._window.seconds // 60} min "
                f"from IP in {event.ip_country}",
            )
        ]


class HighRiskShareWatcher:
    """The share of high-risk bookings in the last hour jumps above its 7-day level.

    This stands in for "the fraud rate doubled in the past hour": chargebacks arrive weeks late,
    so the live signal has to be the risk score, not the fraud label.
    """

    def __init__(self, multiplier: float, min_high_risk: int) -> None:
        if multiplier <= 1 or min_high_risk < 1:
            raise ValueError("multiplier must be > 1 and min_high_risk >= 1")
        self._multiplier = multiplier
        self._min_high_risk = min_high_risk
        self._hour: deque[tuple[datetime, bool]] = deque()
        self._week: deque[tuple[datetime, bool]] = deque()
        self._week_high = 0
        self._hour_high = 0
        self._last_alert = datetime.min

    def observe(self, scored: ScoredTransaction) -> list[Alert]:
        if scored.risk_level is None:
            return []
        now = scored.transaction.timestamp_utc
        is_high = scored.risk_level == "high"
        for window, span in ((self._hour, timedelta(hours=1)), (self._week, timedelta(days=7))):
            window.append((now, is_high))
            while window[0][0] < now - span:
                _, dropped_high = window.popleft()
                if window is self._hour:
                    self._hour_high -= dropped_high
                else:
                    self._week_high -= dropped_high
        self._hour_high += is_high
        self._week_high += is_high
        # wait for a day of baseline before judging spikes against it
        has_baseline = now - self._week[0][0] >= timedelta(days=1)
        if not has_baseline or self._hour_high < self._min_high_risk or now - self._last_alert < timedelta(hours=6):
            return []
        hour_share = self._hour_high / len(self._hour)
        week_share = self._week_high / len(self._week)
        if hour_share < self._multiplier * week_share:
            return []
        self._last_alert = now
        return [
            Alert(
                timestamp_utc=now,
                rule="high_risk_share_spike",
                severity="high",
                subject="all markets",
                message=f"{hour_share:.0%} of bookings in the last hour are high risk vs {week_share:.0%} "
                f"over 7 days ({self._hour_high} of {len(self._hour)})",
            )
        ]


class CountryHighValueWatcher:
    """High-value card bookings from one country in 24 h far above that country's normal daily level."""

    def __init__(self, multiplier: float, min_count: int, high_value_usd: float) -> None:
        if multiplier <= 1 or min_count < 1 or high_value_usd <= 0:
            raise ValueError("multiplier must be > 1, min_count >= 1, high_value_usd > 0")
        self._multiplier = multiplier
        self._min_count = min_count
        self._high_value_usd = high_value_usd
        self._events: dict[str, deque[datetime]] = {}
        self._last_alert: dict[str, datetime] = {}

    def observe(self, scored: ScoredTransaction) -> list[Alert]:
        event = scored.transaction
        if event.payment_method != "card" or event.amount_usd < self._high_value_usd:
            return []
        now = event.timestamp_utc
        events = self._events.setdefault(event.billing_country, deque())
        events.append(now)
        while events[0] < now - timedelta(days=8):
            events.popleft()
        last_day = sum(1 for moment in events if moment >= now - timedelta(days=1))
        prior_week_daily = (len(events) - last_day) / 7
        has_baseline = now - events[0] >= timedelta(days=7)
        recently_alerted = now - self._last_alert.get(event.billing_country, datetime.min) < timedelta(days=1)
        if not has_baseline or recently_alerted or last_day < self._min_count:
            return []
        if last_day < self._multiplier * max(prior_week_daily, 1):
            return []
        self._last_alert[event.billing_country] = now
        return [
            Alert(
                timestamp_utc=now,
                rule="country_high_value_burst",
                severity="critical",
                subject=event.billing_country,
                message=f"{last_day} card bookings over ${self._high_value_usd:,.0f} from {event.billing_country} "
                f"in 24 h vs {prior_week_daily:.0f} a day last week",
            )
        ]
