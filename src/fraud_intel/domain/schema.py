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


def required_columns() -> tuple[str, ...]:
    """The fields the challenge brief asks every transaction to carry."""
    return (
        "transaction_id",
        "timestamp_utc",
        "customer_id",
        "billing_country",
        "ip_country",
        "payment_method",
        "amount_usd",
        "status",
        "is_fraud",
    )


def optional_defaults() -> dict[str, object]:
    # extra fields our generator adds; a batch without them still loads, and the rules that need them stay silent
    return {
        "customer_email": "",
        "card_bin": None,
        "booking_type": "unknown",
        "destination_country": "unknown",
        "departure_date": None,
    }


def validate_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Check a raw batch and return it with normalized types; raise SchemaError listing every problem."""
    if frame is None:
        raise SchemaError("no transactions given")
    missing = [column for column in required_columns() if column not in frame.columns]
    if missing:
        raise SchemaError(f"missing columns: {', '.join(missing)}")

    clean = frame.copy()
    for column, default in optional_defaults().items():
        if column not in clean.columns:
            clean[column] = default
    clean = clean.loc[:, list(transaction_columns())]
    # an empty cell is a missing value, not a bad one
    given_departure = clean["departure_date"].replace("", None).notna()
    clean["timestamp_utc"] = pd.to_datetime(clean["timestamp_utc"], errors="coerce")
    clean["departure_date"] = pd.to_datetime(clean["departure_date"].replace("", None), errors="coerce")
    clean["amount_usd"] = pd.to_numeric(clean["amount_usd"], errors="coerce")
    clean["is_fraud"] = (
        clean["is_fraud"].astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
    )
    # BINs are identifiers, so keep leading digits as text; non-card rows have none
    clean["card_bin"] = clean["card_bin"].astype("string").str.replace(r"\.0$", "", regex=True).replace("", None)
    for column in ("transaction_id", "customer_id", "billing_country", "ip_country", "payment_method", "status"):
        clean[column] = clean[column].astype("string")
    for column in ("customer_email", "booking_type", "destination_country"):
        clean[column] = clean[column].astype("string").fillna(str(optional_defaults()[column]))

    problems = []
    checks = {
        "unparseable timestamp_utc": clean["timestamp_utc"].isna(),
        "unparseable departure_date": given_departure & clean["departure_date"].isna(),
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
