from pathlib import Path

import pandas as pd
import pytest

from fraud_intel.domain.schema import SchemaError, validate_transactions
from fraud_intel.infrastructure.storage import DuckDbStore


def _row(transaction_id: str, status: str) -> dict:
    return {
        "transaction_id": transaction_id,
        "timestamp_utc": "2026-09-10 12:00:00",
        "customer_id": "c_1",
        "customer_email": "c_1@gmail.com",
        "billing_country": "BR",
        "ip_country": "BR",
        "payment_method": "card",
        "card_bin": "012345",
        "amount_usd": 420.0,
        "status": status,
        "is_fraud": False,
        "booking_type": "flight",
        "destination_country": "BR",
        "departure_date": "2026-09-20",
    }


def test_bad_batches_are_rejected_with_a_clear_message() -> None:
    with pytest.raises(SchemaError, match="status not approved/declined"):
        validate_transactions(frame=pd.DataFrame([_row("tx_1", "pending")]))
    with pytest.raises(SchemaError, match="missing columns: ip_country"):
        validate_transactions(frame=pd.DataFrame([_row("tx_1", "approved")]).drop(columns="ip_country"))


def test_duckdb_round_trip_dedupes_and_keeps_types(tmp_path: Path) -> None:
    store = DuckDbStore(path=tmp_path / "not_created_yet" / "test.duckdb", read_only=False)
    batch = pd.DataFrame([_row("tx_1", "approved"), _row("tx_2", "declined")])

    assert store.append_raw(frame=batch) == 2
    assert store.append_raw(frame=batch) == 0
    loaded = store.load_raw()
    assert list(loaded["transaction_id"]) == ["tx_1", "tx_2"]
    assert loaded.loc[0, "card_bin"] == "012345"
    assert pd.api.types.is_datetime64_any_dtype(loaded["timestamp_utc"])


def test_batch_with_only_the_fields_the_brief_lists_is_accepted() -> None:
    brief_fields = ["transaction_id", "timestamp_utc", "customer_id", "billing_country", "ip_country"]
    brief_fields += ["payment_method", "amount_usd", "status", "is_fraud"]
    batch = pd.DataFrame([_row("tx_1", "approved"), _row("tx_2", "declined")]).loc[:, brief_fields]

    clean = validate_transactions(frame=batch)

    assert list(clean["transaction_id"]) == ["tx_1", "tx_2"]
    assert clean["departure_date"].isna().all()
    assert set(clean["booking_type"]) == {"unknown"}
    assert clean["card_bin"].isna().all()
