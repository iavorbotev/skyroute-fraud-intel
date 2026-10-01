import streamlit as st

from fraud_intel.dashboard import pages

pages.patterns(context=st.session_state["context"])
