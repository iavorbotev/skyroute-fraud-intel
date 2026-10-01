import streamlit as st

from fraud_intel.dashboard import pages

pages.payment_methods(context=st.session_state["context"])
