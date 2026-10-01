import streamlit as st

from fraud_intel.dashboard import pages

pages.countries(context=st.session_state["context"])
