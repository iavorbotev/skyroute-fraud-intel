import streamlit as st

from fraud_intel.dashboard import pages

pages.overview(context=st.session_state["context"])
