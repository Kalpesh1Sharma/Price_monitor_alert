import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="Price Tracker Dashboard", layout="wide")

st.title("🛒 Real-Time Price Tracker")
st.caption("Track products, price drops, and alerts.")

# Connect to SQLite DB
conn = sqlite3.connect("app/price_tracker.db")
query = """
SELECT 
    product_name,
    url,
    current_price,
    target_price,
    timestamp
FROM price_history
ORDER BY timestamp DESC
"""

df = pd.read_sql_query(query, conn)

if df.empty:
    st.warning("No price data found yet. Run tracker first.")
else:
    # Keep latest entry per product
    latest = df.groupby("product_name").first().reset_index()

    # Highlight deals
    def get_status(row):
        return "🔥 Deal!" if row.current_price <= row.target_price else "❌ No Deal"

    latest["Status"] = latest.apply(get_status, axis=1)

    st.dataframe(
        latest[["product_name", "current_price", "target_price", "Status", "timestamp"]],
        use_container_width=True
    )

    if st.checkbox("📉 Show price history"):
        st.dataframe(df)

conn.close()
