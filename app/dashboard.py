# app/dashboard.py  (add or integrate into your existing file)
import sqlite3
import os
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st

DB_PATH = os.environ.get("TRACKER_DB", "tracked.db")

# --- DB helper (simple SQLite) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tracked_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        title TEXT,
        target_price REAL,
        last_price REAL,
        last_checked TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def add_tracked_product(url, title, target_price, last_price=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO tracked_products (url, title, target_price, last_price, last_checked) VALUES (?, ?, ?, ?, ?)",
              (url, title, target_price, last_price, None))
    conn.commit()
    conn.close()

# --- existing fetch_product / parse functions here ---
# re-use your existing fetch_product implementation from earlier message

# Initialize DB
init_db()

st.title("🔎 Price Checker & Tracker")

url = st.text_input("Product URL", placeholder="https://www.amazon.in/.... or https://www.flipkart.com/....")
target_price = st.text_input("Target price (₹) — enter numbers only", value="")
check_now = st.button("Check price")
track_now = st.button("Track this product")

if check_now:
    if not url:
        st.error("Paste a product URL first.")
    else:
        title, price, img, error = fetch_product(url)
        if error:
            st.error(error)
        else:
            if title:
                st.subheader(title)
            if img:
                st.image(img, width=300)
            if price is not None:
                st.metric(label="Current price (approx.)", value=f"₹ {price:,.2f}")
            else:
                st.warning("Couldn't detect price automatically. Try another URL.")

# When user clicks "Track this product"
if track_now:
    if not url:
        st.error("Paste a product URL first.")
    else:
        # fetch to get title/price
        title, price, img, error = fetch_product(url)
        if error:
            st.error(f"Cannot fetch product: {error}")
        else:
            # validate target_price as float
            try:
                tp = float(target_price.replace(",", "").strip())
            except Exception:
                st.error("Enter a valid target price (just numbers, e.g., 1299.50).")
            else:
                add_tracked_product(url, title or "", tp, price)
                st.success(f"Tracking saved for: {title or url}\nTarget price: ₹ {tp:,.2f}")
