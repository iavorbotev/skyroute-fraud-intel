import streamlit as st

from fraud_intel.dashboard import pages

pages.daily_report(context=st.session_state["context"])
