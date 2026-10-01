"""The stream: score each transaction in time order using customer memory and settled history.

Each step maps onto an Apache Beam transform: read events, key by customer, score with per-key state,
then fan the scored events out to the sink and to the alert watchers.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from fraud_intel.application.ports import Notifier, TransactionStore
from fraud_intel.config import AppConfig, RiskConfig
from fraud_intel.domain.alerts import Alert, AlertWatcher, alerts_to_frame, default_watchers
from fraud_intel.domain.memory import CustomerMemory
from fraud_intel.domain.model import ScoredTransaction, Signals, Transaction, scored_to_frame, transactions_from_frame
from fraud_intel.domain.rules import evaluate_rules, recommend_action, score_hits
from fraud_intel.domain.settled import SettledRates, build_settled_rates, scored_window_start


class StreamProcessor:
    """Scores events one at a time; memory carries over between calls, so batches can arrive in pieces."""

    def __init__(
        self, rates: SettledRates, scored_from: datetime, risk: RiskConfig, utc_offset_hours: dict[str, int]
    ) -> None:
        if rates is None or scored_from is None or risk is None or utc_offset_hours is None:
            raise ValueError("rates, scored_from, risk and utc_offset_hours are required")
        self._rates = rates
        self._scored_from = scored_from
        self._risk = risk
        self._utc_offset_hours = utc_offset_hours
        self._window = timedelta(minutes=risk.velocity_window_minutes)
        self._memory: dict[str, CustomerMemory] = {}
        self._last_seen = datetime.min

    def process(self, transactions: Iterable[Transaction]) -> Iterator[ScoredTransaction]:
        for transaction in transactions:
            if transaction.timestamp_utc < self._last_seen:
                raise ValueError(f"events must arrive in time order: {transaction.transaction_id} is late")
            self._last_seen = transaction.timestamp_utc
            memory = self._memory.setdefault(transaction.customer_id, CustomerMemory())
            memory.forget_before(transaction.timestamp_utc - self._window)
            scored = self._score(transaction=transaction, memory=memory)
            memory.remember(transaction)
            yield scored

    def _score(self, transaction: Transaction, memory: CustomerMemory) -> ScoredTransaction:
        signals = self._signals(transaction=transaction, memory=memory)
        # history rows only build memory; scoring them with rates learned from themselves would leak
        if transaction.timestamp_utc < self._scored_from:
            return ScoredTransaction(
                transaction, False, signals.is_returning, signals.local_hour, None, None, (), (), None
            )
        hits = evaluate_rules(transaction=transaction, signals=signals, risk=self._risk)
        score, level = score_hits(hits=hits, risk=self._risk)
        rules_fired = tuple(hit.name for hit in hits)
        return ScoredTransaction(
            transaction=transaction,
            in_scored_window=True,
            is_returning=signals.is_returning,
            local_hour=signals.local_hour,
            risk_score=score,
            risk_level=level,
            rules_fired=rules_fired,
            reasons=tuple(hit.reason for hit in hits),
            recommended_action=recommend_action(
                transaction=transaction, risk_level=level, rules_fired=set(rules_fired)
            ),
        )

    def _signals(self, transaction: Transaction, memory: CustomerMemory) -> Signals:
        earlier = memory.recent
        bins = {card_bin for _, _, card_bin in earlier if card_bin} | (
            {transaction.card_bin} if transaction.card_bin else set()
        )
        first_seen = earlier[0][0] if earlier else transaction.timestamp_utc
        offset = timedelta(hours=self._utc_offset_hours.get(transaction.billing_country, 0))
        # departure_date has no time, so assume a midday departure
        departure = transaction.departure_date + timedelta(hours=12)
        segment = (transaction.billing_country, transaction.payment_method)
        return Signals(
            attempts_in_window=len(earlier) + 1,
            declines_in_window=sum(1 for _, approved, _ in earlier if not approved),
            distinct_bins_in_window=len(bins),
            minutes_since_first_in_window=(transaction.timestamp_utc - first_seen).total_seconds() / 60,
            is_returning=memory.has_approved_booking,
            local_hour=(transaction.timestamp_utc + offset).hour,
            hours_to_departure=(departure - transaction.timestamp_utc).total_seconds() / 3600,
            overall_rate=self._rates.overall_rate,
            segment_rate=self._rates.segment_rate(*segment),
            bin_rate=self._rates.bin_rate(transaction.card_bin),
            amount_p99=self._rates.amount_p99.get(segment),
        )


@dataclass(frozen=True)
class PipelineResult:
    events: int
    scored: int
    high_risk: int
    alerts: int


def run_pipeline(store: TransactionStore, config: AppConfig, notifier: Notifier) -> PipelineResult:
    """Replay every stored transaction through the stream and save scores and alerts."""
    if store is None or config is None or notifier is None:
        raise ValueError("store, config and notifier are required")
    raw = store.load_raw()
    scored_from = scored_window_start(frame=raw, scored_window_days=config.scored_window_days)
    # slow path: rates come only from the settled history, computed once before the stream starts
    rates = build_settled_rates(
        history=raw[raw["timestamp_utc"] < scored_from],
        prior_strength=config.risk.settled_prior_strength,
        outlier_quantile=config.risk.value_outlier_quantile,
    )
    processor = StreamProcessor(
        rates=rates, scored_from=scored_from, risk=config.risk, utc_offset_hours=config.utc_offset_hours
    )
    watchers = default_watchers(alerts=config.alerts, risk=config.risk)
    scored, alerts = [], []
    for item in processor.process(transactions=transactions_from_frame(frame=raw)):
        scored.append(item)
        alerts.extend(_watch(item=item, watchers=watchers, notifier=notifier))
    store.replace_scored(frame=scored_to_frame(scored=scored))
    store.replace_alerts(frame=alerts_to_frame(alerts=alerts))
    return PipelineResult(
        events=len(scored),
        scored=sum(item.in_scored_window for item in scored),
        high_risk=sum(item.risk_level == "high" for item in scored),
        alerts=len(alerts),
    )


def _watch(item: ScoredTransaction, watchers: list[AlertWatcher], notifier: Notifier) -> list[Alert]:
    fired = [alert for watcher in watchers for alert in watcher.observe(scored=item)]
    for alert in fired:
        notifier.notify(alert=alert)
    return fired
