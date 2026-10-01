"""DuckDB store, file readers, and the console notifier."""

from pathlib import Path

import duckdb
import pandas as pd

from fraud_intel.domain.alerts import Alert
from fraud_intel.domain.schema import validate_transactions


class DuckDbStore:
    """One DuckDB file holds raw, scored, and alert tables; each call opens and closes its own connection."""

    def __init__(self, path: Path | str, read_only: bool) -> None:
        if path is None:
            raise ValueError("path is required")
        self._path = str(path)
        self._read_only = read_only
        # a fresh checkout or a new config may point at a folder that does not exist yet
        if not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def append_raw(self, frame: pd.DataFrame) -> int:
        validated = validate_transactions(frame=frame)
        with duckdb.connect(self._path, read_only=self._read_only) as connection:
            connection.register("batch", validated)
            connection.execute("CREATE TABLE IF NOT EXISTS raw_transactions AS SELECT * FROM batch WHERE false")
            before = connection.execute("SELECT count(*) FROM raw_transactions").fetchone()
            # replaying the same batch twice must not double-count bookings
            connection.execute(
                "INSERT INTO raw_transactions SELECT * FROM batch "
                "WHERE transaction_id NOT IN (SELECT transaction_id FROM raw_transactions)"
            )
            after = connection.execute("SELECT count(*) FROM raw_transactions").fetchone()
        return int(after[0]) - int(before[0]) if before and after else 0

    def load_raw(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw_transactions ORDER BY timestamp_utc, transaction_id")

    def replace_scored(self, frame: pd.DataFrame) -> None:
        self._replace(table="scored_transactions", frame=frame)

    def load_scored(self) -> pd.DataFrame:
        return self._query("SELECT * FROM scored_transactions ORDER BY timestamp_utc, transaction_id")

    def replace_alerts(self, frame: pd.DataFrame) -> None:
        self._replace(table="alerts", frame=frame)

    def load_alerts(self) -> pd.DataFrame:
        return self._query("SELECT * FROM alerts ORDER BY timestamp_utc")

    def _replace(self, table: str, frame: pd.DataFrame) -> None:
        with duckdb.connect(self._path, read_only=self._read_only) as connection:
            connection.register("frame", frame)
            connection.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM frame")

    def _query(self, sql: str) -> pd.DataFrame:
        with duckdb.connect(self._path, read_only=self._read_only) as connection:
            return connection.execute(sql).df()


def read_transactions_file(path: Path) -> pd.DataFrame:
    """Read a CSV (optionally gzipped) or JSON batch; BINs stay text so leading digits survive."""
    if path is None or not path.is_file():
        raise FileNotFoundError(f"transactions file not found: {path}")
    if path.suffix == ".json":
        return pd.read_json(path, orient="records", dtype={"card_bin": str})
    return pd.read_csv(path, dtype={"card_bin": str})


class ConsoleNotifier:
    """Prints alerts; a Slack, PagerDuty, or email notifier would implement the same notify method."""

    def notify(self, alert: Alert) -> None:
        print(f"[{alert.severity.upper()}] {alert.timestamp_utc:%Y-%m-%d %H:%M} {alert.rule}: {alert.message}")
