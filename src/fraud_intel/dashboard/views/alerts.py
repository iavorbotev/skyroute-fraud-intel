import streamlit as st

from fraud_intel.dashboard import pages

pages.alerts(context=st.session_state["context"])
