import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fraud_intel.application.stream import run_pipeline
from fraud_intel.config import AppConfig
from fraud_intel.dashboard.filters import Filters, apply_filters
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


def test_high_risk_card_mexico_filter_keeps_only_matching_rows(database: Path) -> None:
    scored = DuckDbStore(path=database, read_only=True).load_scored()
    filters = Filters(date(2026, 9, 1), date(2026, 9, 30), ("MX",), ("card",), ("high",))
    picked = apply_filters(frame=scored, filters=filters, use_dates=True)

    assert not picked.empty
    assert set(picked["billing_country"]) == {"MX"}
    assert set(picked["payment_method"]) == {"card"}
    assert set(picked["risk_level"]) == {"high"}


@pytest.mark.parametrize("page", ["overview", "patterns", "transactions", "daily_report", "alerts", "score_check"])
def test_every_page_renders_without_errors(database: Path, page: str) -> None:
    os.environ["FRAUD_INTEL_DB"] = str(database)
    os.environ["FRAUD_INTEL_CONFIG"] = str(Path(__file__).parents[1] / "config.toml")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/fraud_intel/dashboard/app.py"), default_timeout=60)
    app.run()
    if page != "overview":
        app.switch_page(f"views/{page}.py")
        app.run()
    assert not app.exception, app.exception
    assert app.title and app.title[0].value.endswith("?") or "Top 50" in app.title[0].value
