# app/test_streamlit.py
import streamlit as st

st.set_page_config(page_title="Health Check")
st.title("Streamlit Health Check ✅")
st.write("If you see this, Streamlit is running fine.")
st.write("Time:", st.experimental_get_query_params())
