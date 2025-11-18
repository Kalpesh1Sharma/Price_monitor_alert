# dashboard.py - Streamlit front-end for the tracker backend
import streamlit as st
import requests
import time
from datetime import datetime

# --- config / startup guard ---
def safe_api_base():
    try:
        val = st.secrets.get("API_BASE", None)
    except Exception:
        val = None
    # allow manual override in session as well
    if "override_api" in st.session_state and st.session_state.override_api:
        return st.session_state.override_api.rstrip("/")
    if val:
        return val.rstrip("/")
    return "http://127.0.0.1:5000"  # default for local dev

API_BASE = safe_api_base()

st.set_page_config(page_title="Price Monitor Dashboard", layout="wide")
st.title("Price Monitor Dashboard")
st.markdown(f"*Backend:* {API_BASE}")

# helpers
def pretty_time(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"

def api_get(path, timeout=8):
    return requests.get(f"{API_BASE}{path}", timeout=timeout)

def api_post(path, json=None, timeout=10):
    return requests.post(f"{API_BASE}{path}", json=json, timeout=timeout)

st.sidebar.header("Settings")
st.sidebar.text_input("Override backend URL (leave empty to use secrets)", key="override_api")

# add new track
st.sidebar.header("Add product to track")
with st.sidebar.form("add_form", clear_on_submit=True):
    name = st.text_input("Name (optional)")
    url = st.text_input("Product URL")
    submitted = st.form_submit_button("Add to tracker")
    if submitted:
        if not url:
            st.warning("Provide a URL")
        else:
            try:
                resp = api_post("/track", json={"url": url, "name": name or url})
                resp.raise_for_status()
                data = resp.json()
                st.success(f"Tracking added: {data.get('id')}")
            except Exception as e:
                st.error(f"Failed to add: {e}")

st.write("---")
# top row
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("Health")
    try:
        r = api_get("/health", timeout=4)
        if r.status_code == 200:
            st.success("OK")
        else:
            st.warning(f"Status {r.status_code}")
    except Exception as e:
        st.error(f"Backend unreachable: {e}")

with col2:
    if st.button("Refresh list"):
        st.experimental_rerun()  # Streamlit cloud supports st.rerun(); this still works for many versions

st.write("## Tracked Items")
items = []
try:
    r = api_get("/prices")
    r.raise_for_status()
    items = r.json()
except Exception as e:
    st.error(f"Could not fetch tracked items: {e}")

if not items:
    st.info("No items tracked. Add one via the sidebar.")
else:
    for it in items:
        item_id = it.get("id")
        name = it.get("name")
        url = it.get("url")
        last = it.get("last_price")
        last_checked = it.get("last_checked")
        cols = st.columns([3, 2, 1, 1])
        with st.expander(f"{name} — {url}", expanded=False):
            st.markdown(f"- *Latest price:* {last}")
            st.markdown(f"- *Last checked:* {pretty_time(last_checked)}")
            col_a, col_b, col_c = st.columns([1,1,1])
            if col_a.button("Fetch now", key=f"fetch_{item_id}"):
                try:
                    resp = api_post(f"/fetch/{item_id}")
                    resp.raise_for_status()
                    st.success("Fetch triggered (check logs or refresh)")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")
            if col_b.button("History", key=f"hist_{item_id}"):
                try:
                    hr = api_get(f"/prices/{item_id}")
                    hr.raise_for_status()
                    hist = hr.json().get("history", [])
                    st.write(hist)
                except Exception as e:
                    st.error(f"History load failed: {e}")
            if col_c.button("Open URL", key=f"url_{item_id}"):
                st.write(f"[Open product page]({url})")
