"""Transaction schema: the columns every batch must carry, and the checks that guard data quality."""

import pandas as pd


class SchemaError(ValueError):
    """A batch of transactions does not match the expected schema."""


def transaction_columns() -> tuple[str, ...]:
    return (
        "transaction_id",
        "timestamp_utc",
        "customer_id",
        "customer_email",
        "billing_country",
        "ip_country",
        "payment_method",
        "card_bin",
        "amount_usd",
        "status",
        "is_fraud",
        "booking_type",
        "destination_country",
        "departure_date",
    )


def validate_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Check a raw batch and return it with normalized types; raise SchemaError listing every problem."""
    if frame is None:
        raise SchemaError("no transactions given")
    missing = [column for column in transaction_columns() if column not in frame.columns]
    if missing:
        raise SchemaError(f"missing columns: {', '.join(missing)}")

    clean = frame.loc[:, list(transaction_columns())].copy()
    clean["timestamp_utc"] = pd.to_datetime(clean["timestamp_utc"], errors="coerce")
    clean["departure_date"] = pd.to_datetime(clean["departure_date"], errors="coerce")
    clean["amount_usd"] = pd.to_numeric(clean["amount_usd"], errors="coerce")
    clean["is_fraud"] = clean["is_fraud"].astype(str).str.lower().map({"true": True, "false": False})
    # BINs are identifiers, so keep leading digits as text; non-card rows have none
    clean["card_bin"] = clean["card_bin"].astype("string").str.replace(r"\.0$", "", regex=True)
    for column in ("transaction_id", "customer_id", "billing_country", "ip_country", "payment_method", "status"):
        clean[column] = clean[column].astype("string")

    problems = []
    checks = {
        "unparseable timestamp_utc": clean["timestamp_utc"].isna(),
        "unparseable departure_date": clean["departure_date"].isna(),
        "amount_usd missing or not positive": ~(clean["amount_usd"] > 0),
        "status not approved/declined": ~clean["status"].isin(["approved", "declined"]),
        "is_fraud not true/false": clean["is_fraud"].isna(),
        "missing transaction_id or customer_id": clean["transaction_id"].isna() | clean["customer_id"].isna(),
    }
    for label, bad_rows in checks.items():
        count = int(bad_rows.fillna(True).sum())
        if count:
            problems.append(f"{count} rows with {label}")
    if problems:
        raise SchemaError("; ".join(problems))

    # a chargeback can only follow an approved payment, so a fraud flag on a decline is bad data
    fraud_on_decline = int((clean["is_fraud"] & (clean["status"] == "declined")).sum())
    if fraud_on_decline:
        raise SchemaError(f"{fraud_on_decline} declined rows are flagged as fraud")
    clean["is_fraud"] = clean["is_fraud"].astype(bool)
    return clean.sort_values(["timestamp_utc", "transaction_id"]).reset_index(drop=True)
