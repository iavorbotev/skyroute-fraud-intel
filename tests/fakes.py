import pandas as pd

from fraud_intel.domain.alerts import Alert
from fraud_intel.domain.schema import validate_transactions


class FakeStore:
    """In-memory TransactionStore with the same dedupe rule as the DuckDB one."""

    def __init__(self) -> None:
        self.raw = pd.DataFrame()
        self.scored = pd.DataFrame()
        self.alerts = pd.DataFrame()

    def append_raw(self, frame: pd.DataFrame) -> int:
        batch = validate_transactions(frame=frame)
        if not self.raw.empty:
            batch = batch[~batch["transaction_id"].isin(self.raw["transaction_id"])]
        self.raw = pd.concat([self.raw, batch], ignore_index=True).sort_values(["timestamp_utc", "transaction_id"])
        return len(batch)

    def load_raw(self) -> pd.DataFrame:
        return self.raw.reset_index(drop=True)

    def replace_scored(self, frame: pd.DataFrame) -> None:
        self.scored = frame

    def load_scored(self) -> pd.DataFrame:
        return self.scored

    def replace_alerts(self, frame: pd.DataFrame) -> None:
        self.alerts = frame

    def load_alerts(self) -> pd.DataFrame:
        return self.alerts


class RecordingNotifier:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)
