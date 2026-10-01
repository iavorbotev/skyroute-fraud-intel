from dataclasses import replace

import pandas as pd
import pytest

from fraud_intel.config import AppConfig
from fraud_intel.infrastructure.generator import generate_transactions


@pytest.fixture(scope="module")
def transactions(config: AppConfig) -> pd.DataFrame:
    return generate_transactions(config=config)


def test_same_seed_gives_same_data(config: AppConfig) -> None:
    small = replace(config, generator=replace(config.generator, transactions_per_window=2000))
    first = generate_transactions(config=small)
    second = generate_transactions(config=small)
    pd.testing.assert_frame_equal(first, second)


def test_mixes_match_the_brief(transactions: pd.DataFrame) -> None:
    country_share = transactions["billing_country"].value_counts(normalize=True)
    method_share = transactions["payment_method"].value_counts(normalize=True)
    approved = transactions["status"] == "approved"
    scored = transactions["timestamp_utc"] >= transactions["timestamp_utc"].max() - pd.Timedelta(days=30)

    assert country_share["BR"] == pytest.approx(0.35, abs=0.02)
    assert country_share["MX"] == pytest.approx(0.25, abs=0.02)
    assert method_share["card"] == pytest.approx(0.60, abs=0.03)
    assert method_share["pix"] == pytest.approx(0.20, abs=0.02)
    assert approved.mean() == pytest.approx(0.83, abs=0.02)
    assert transactions.loc[approved & ~scored, "is_fraud"].mean() == pytest.approx(0.008, abs=0.003)
    assert transactions.loc[approved & scored, "is_fraud"].mean() == pytest.approx(0.03, abs=0.007)
    assert not transactions.loc[~approved, "is_fraud"].any()


def test_planted_patterns_are_present(transactions: pd.DataFrame) -> None:
    ar_high_value_fraud = transactions[
        (transactions["billing_country"] == "AR")
        & (transactions["payment_method"] == "card")
        & (transactions["amount_usd"] >= 800)
        & transactions["is_fraud"]
    ]
    busiest_three_days = ar_high_value_fraud.set_index("timestamp_utc").resample("3D")["amount_usd"].count().max()
    assert busiest_three_days >= 80

    hourly_attempts = (
        transactions.set_index("timestamp_utc").groupby("customer_id")["amount_usd"].rolling("60min").count()
    )
    assert (hourly_attempts >= 3).groupby(level="customer_id").any().sum() >= 50
    assert (hourly_attempts >= 5).groupby(level="customer_id").any().sum() >= 10

    legit_high_value = transactions[(transactions["amount_usd"] >= 1500) & ~transactions["is_fraud"]]
    assert len(legit_high_value) >= 100
    assert (transactions["ip_country"] != transactions["billing_country"]).mean() > 0.02
