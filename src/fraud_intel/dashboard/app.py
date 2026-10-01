"""Streamlit entry point: load scored data once, draw the shared sidebar filters, route to the pages."""

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.commands.navigation import navigation as st_navigation

from fraud_intel.config import AppConfig, load_config
from fraud_intel.dashboard import pages
from fraud_intel.dashboard.filters import Filters
from fraud_intel.domain.metrics import add_dimensions
from fraud_intel.infrastructure.storage import DuckDbStore


@st.cache_resource
def _config(config_path: str) -> AppConfig:
    return load_config(path=Path(config_path))


@st.cache_data
def _load(database_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    store = DuckDbStore(path=database_path, read_only=True)
    return add_dimensions(frame=store.load_scored()), store.load_alerts()


def _sidebar(scored: pd.DataFrame) -> Filters:
    scored_window = scored[scored["in_scored_window"]]
    first_day = scored["timestamp_utc"].min().date()
    last_day = scored["timestamp_utc"].max().date()
    st.sidebar.header("Filters")
    picked = st.sidebar.date_input(
        "Date range",
        value=(scored_window["timestamp_utc"].min().date(), last_day),
        min_value=first_day,
        max_value=last_day,
        key="date_range",
    )
    # while the user is mid-way through picking a range, Streamlit returns a single date
    start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (first_day, last_day)
    countries = sorted(scored["billing_country"].unique())
    methods = sorted(scored["payment_method"].unique())
    chosen_countries = st.sidebar.multiselect("Country", countries, default=countries, key="countries")
    chosen_methods = st.sidebar.multiselect("Payment method", methods, default=methods, key="methods")
    chosen_levels = st.sidebar.multiselect(
        "Risk level", ["high", "medium", "low"], default=["high", "medium", "low"], key="risk_levels"
    )
    st.sidebar.caption(
        "Fraud rate = chargebacks / approved bookings. Scores cover the last 30 days; "
        "the 30 days before are the settled history the segment rates come from."
    )
    return Filters(
        start=start,
        end=end,
        countries=tuple(chosen_countries),
        methods=tuple(chosen_methods),
        risk_levels=tuple(chosen_levels),
    )


def main() -> None:
    st.set_page_config(page_title="SkyRoute Fraud Intelligence", page_icon="🛡️", layout="wide")
    # Streamlit has no theme option for the page links, and the default 14px reads small next to the filters
    st.html(
        """
        <style>
        [data-testid="stSidebarNavLink"] p { font-size: 1.15rem; line-height: 1.4; }
        [data-testid="stSidebarNavLink"] [data-testid="stIconEmoji"] { font-size: 1.2rem; }
        </style>
        """
    )
    config = _config(config_path=os.environ.get("FRAUD_INTEL_CONFIG", "config.toml"))
    database_path = os.environ.get("FRAUD_INTEL_DB", str(config.database_path))
    scored, alerts = _load(database_path=database_path)
    filters = _sidebar(scored=scored)
    # pages are separate files so each has its own URL; they read the shared context from the session
    st.session_state["context"] = pages.PageContext(scored=scored, alerts=alerts, filters=filters, config=config)
    navigation = st_navigation(
        [
            st.Page("views/overview.py", title="Overview", icon="📊", default=True),
            st.Page("views/patterns.py", title="Patterns", icon="🔎"),
            st.Page("views/transactions.py", title="Transactions", icon="🧾"),
            st.Page("views/daily_report.py", title="Daily report", icon="📄"),
            st.Page("views/alerts.py", title="Alerts", icon="🚨"),
            st.Page("views/score_check.py", title="Score check", icon="🎯"),
        ]
    )
    navigation.run()


main()
