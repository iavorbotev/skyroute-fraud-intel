import streamlit as st

from fraud_intel.dashboard import pages

pages.transactions(context=st.session_state["context"])
