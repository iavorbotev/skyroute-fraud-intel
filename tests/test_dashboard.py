import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from fraud_intel.application.stream import run_pipeline
from fraud_intel.config import AppConfig
from fraud_intel.dashboard.filters import Filters, apply_filters
from fraud_intel.domain.metrics import segment_scorecard
from fraud_intel.infrastructure.generator import generate_transactions
from fraud_intel.infrastructure.storage import DuckDbStore
from tests.fakes import RecordingNotifier


@pytest.fixture(scope="module")
def database(config: AppConfig, tmp_path_factory: pytest.TempPathFactory) -> Path:
    small = replace(
        config, generator=replace(config.generator, transactions_per_window=4000, customers_per_window=3000)
    )
    path = tmp_path_factory.mktemp("db") / "dashboard.duckdb"
    store = DuckDbStore(path=path, read_only=False)
    store.append_raw(frame=generate_transactions(config=small))
    run_pipeline(store=store, config=small, notifier=RecordingNotifier())
    return path


def _app(database: Path) -> AppTest:
    os.environ["FRAUD_INTEL_DB"] = str(database)
    os.environ["FRAUD_INTEL_CONFIG"] = str(Path(__file__).parents[1] / "config.toml")
    return AppTest.from_file(str(Path(__file__).parents[1] / "src/fraud_intel/dashboard/app.py"), default_timeout=60)


def test_empty_selection_means_all_and_picks_narrow_down(database: Path) -> None:
    scored = DuckDbStore(path=database, read_only=True).load_scored()
    everything = Filters(date(2026, 8, 1), date(2026, 9, 30), (), (), ())
    picked = apply_filters(
        frame=scored,
        filters=replace(everything, countries=("MX",), methods=("card",), risk_levels=("high",)),
        use_dates=True,
    )

    assert len(apply_filters(frame=scored, filters=everything, use_dates=True)) == len(scored)
    assert not picked.empty
    assert set(picked["billing_country"]) == {"MX"}
    assert set(picked["payment_method"]) == {"card"}
    assert set(picked["risk_level"]) == {"high"}


def test_segment_scorecard_compares_with_history() -> None:
    def rows(country: str, statuses: list[str], frauds: list[bool]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "transaction_id": [f"{country}{index}" for index in range(len(statuses))],
                "billing_country": country,
                "status": statuses,
                "is_fraud": frauds,
                "amount_usd": 1000.0,
                "risk_level": ["high"] + ["low"] * (len(statuses) - 1),
            }
        )

    current = pd.concat(
        [
            rows("AR", ["approved", "approved", "declined"], [True, False, False]),
            rows("BR", ["approved"] * 2, [False, False]),
        ]
    )
    history = pd.concat(
        [rows("AR", ["approved"] * 4, [True, False, False, False]), rows("BR", ["approved"] * 2, [False, False])]
    )

    card = segment_scorecard(current=current, history=history, by="billing_country", chargeback_fee_usd=20.0)
    argentina = card.set_index("billing_country").loc["AR"]

    assert list(card["billing_country"]) == ["AR", "BR"]
    assert argentina["fraud_rate"] == pytest.approx(0.5)
    assert argentina["history_fraud_rate"] == pytest.approx(0.25)
    assert argentina["change_pp"] == pytest.approx(25.0)
    assert argentina["chargeback_cost_usd"] == pytest.approx(1020.0)
    assert argentina["high_risk"] == 1


@pytest.mark.parametrize(
    "page",
    ["overview", "countries", "payment_methods", "patterns", "transactions", "daily_report", "alerts", "score_check"],
)
def test_every_page_renders_without_errors(database: Path, page: str) -> None:
    app = _app(database=database)
    app.run()
    if page != "overview":
        app.switch_page(f"views/{page}.py")
        app.run()
    assert not app.exception, app.exception
    assert app.title and (app.title[0].value.endswith("?") or "Top 50" in app.title[0].value)


def test_top_bar_filters_the_transactions_list(database: Path) -> None:
    app = _app(database=database)
    app.run()
    app.switch_page("views/transactions.py")
    app.run()
    app.multiselect(key="countries").set_value(["MX"])
    app.multiselect(key="methods").set_value(["card"])
    app.multiselect(key="risk_levels").set_value(["high"]).run()

    table = app.dataframe[0].value
    assert not app.exception, app.exception
    assert not table.empty
    assert set(table["billing_country"]) == {"MX"}
    assert set(table["payment_method"]) == {"card"}
    assert set(table["risk_level"]) == {"high"}
