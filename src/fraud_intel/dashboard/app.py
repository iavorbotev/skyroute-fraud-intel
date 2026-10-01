"""Streamlit entry point: load scored data once, draw the heading and the shared filter bar, route to the pages."""

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


def _filter_bar(scored: pd.DataFrame) -> Filters:
    """One row of filters above every page; an empty dropdown means "all"."""
    scored_window = scored[scored["in_scored_window"]]
    first_day = scored["timestamp_utc"].min().date()
    last_day = scored["timestamp_utc"].max().date()
    with st.container(border=True):
        dates, country, method, risk = st.columns([1.2, 1, 1, 1])
        picked = dates.date_input(
            "Dates",
            value=(scored_window["timestamp_utc"].min().date(), last_day),
            min_value=first_day,
            max_value=last_day,
            key="date_range",
        )
        chosen_countries = country.multiselect(
            "Country", sorted(scored["billing_country"].unique()), placeholder="All countries", key="countries"
        )
        chosen_methods = method.multiselect(
            "Payment method", sorted(scored["payment_method"].unique()), placeholder="All methods", key="methods"
        )
        chosen_levels = risk.multiselect(
            "Risk level", ["high", "medium", "low"], placeholder="All levels", key="risk_levels"
        )
    # while the user is mid-way through picking a range, Streamlit returns a single date
    start, end = (picked[0], picked[-1]) if isinstance(picked, tuple) and picked else (first_day, last_day)
    return Filters(
        start=start,
        end=end,
        countries=tuple(chosen_countries),
        methods=tuple(chosen_methods),
        risk_levels=tuple(chosen_levels),
    )


def _headings() -> dict[str, str]:
    # each page answers one question, so the question is the heading
    return {
        "Overview": "What is happening with fraud right now?",
        "Countries": "How does each country compare?",
        "Payment methods": "How does each payment method compare?",
        "Patterns": "Which patterns go with fraud?",
        "Transactions": "Which bookings should we look at first?",
        "Daily report": "Daily Top 50 high-risk transactions",
        "Alerts": "What needs attention now?",
        "Score check": "Does the risk score find fraud?",
    }


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
    navigation = st_navigation(
        [
            st.Page("views/overview.py", title="Overview", icon="📊", default=True),
            st.Page("views/countries.py", title="Countries", icon="🌎"),
            st.Page("views/payment_methods.py", title="Payment methods", icon="💳"),
            st.Page("views/patterns.py", title="Patterns", icon="🔎"),
            st.Page("views/transactions.py", title="Transactions", icon="🧾"),
            st.Page("views/daily_report.py", title="Daily report", icon="📄"),
            st.Page("views/alerts.py", title="Alerts", icon="🚨"),
            st.Page("views/score_check.py", title="Score check", icon="🎯"),
        ]
    )
    st.sidebar.caption(
        "Fraud rate = chargebacks / approved bookings. Scores cover the last 30 days; "
        "the 30 days before are the settled history the segment rates come from."
    )
    # heading and filters live here, not in the pages: widgets in the entry script keep their values across pages
    st.title(_headings()[navigation.title])
    filters = _filter_bar(scored=scored)
    # pages are separate files so each has its own URL; they read the shared context from the session
    st.session_state["context"] = pages.PageContext(scored=scored, alerts=alerts, filters=filters, config=config)
    navigation.run()


main()
