import streamlit as st

from fraud_intel.dashboard import pages

pages.score_check(context=st.session_state["context"])
