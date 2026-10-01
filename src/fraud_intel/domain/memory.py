"""Per-customer memory the stream keeps between events (the state a Beam stateful DoFn would hold)."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from fraud_intel.domain.model import Transaction


@dataclass
class CustomerMemory:
    # (timestamp, approved, card_bin) for attempts inside the velocity window, oldest first
    recent: deque[tuple[datetime, bool, str | None]] = field(default_factory=deque)
    # kept forever: in production this flag lives in a key-value store, not in process memory
    has_approved_booking: bool = False

    def forget_before(self, cutoff: datetime) -> None:
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()

    def remember(self, transaction: Transaction) -> None:
        self.recent.append((transaction.timestamp_utc, transaction.approved, transaction.card_bin))
        self.has_approved_booking = self.has_approved_booking or transaction.approved
