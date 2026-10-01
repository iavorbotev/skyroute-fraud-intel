"""Interfaces the pipeline depends on; infrastructure provides the real ones, tests provide fakes."""

from typing import Protocol

import pandas as pd

from fraud_intel.domain.alerts import Alert


class TransactionStore(Protocol):
    def append_raw(self, frame: pd.DataFrame) -> int: ...

    def load_raw(self) -> pd.DataFrame: ...

    def replace_scored(self, frame: pd.DataFrame) -> None: ...

    def load_scored(self) -> pd.DataFrame: ...

    def replace_alerts(self, frame: pd.DataFrame) -> None: ...

    def load_alerts(self) -> pd.DataFrame: ...


class Notifier(Protocol):
    def notify(self, alert: Alert) -> None: ...
