import streamlit as st
import sqlite3
import pandas as pd
import time
from datetime import datetime

DB_FILE = "prices.db"

def db_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS items(
        id TEXT PRIMARY KEY,
        name TEXT,
        url TEXT,
        target_price REAL,
        last_alert_at REAL DEFAULT 0
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS prices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT,
        checked_at REAL,
        price REAL
    )""")
    conn.commit()
    conn.close()

def add_item(name, url, target):
    import uuid
    conn = db_conn()
    conn.execute("INSERT INTO items VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), name, url, target, 0))
    conn.commit()
    conn.close()

def get_dashboard_data():
    conn = db_conn()
    df = pd.read_sql("""
        SELECT i.*, p.price, p.checked_at
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, checked_at FROM prices
            WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
        ) p ON i.id = p.item_id
    """, conn)
    conn.close()
    return df

def main():
    st.set_page_config(page_title="Price Monitor", layout="wide")
    init_db()

    st.title("📊 Price Monitor (UI Only)")

    with st.sidebar:
        st.header("Add Item")
        name = st.text_input("Product Name")
        url = st.text_input("Product URL")
        target = st.number_input("Target Price (₹)", min_value=0.0)
        if st.button("Start Tracking"):
            add_item(name or url, url, target)
            st.success("Added!")
            st.rerun()

    df = get_dashboard_data()

    if df.empty:
        st.info("No items tracked.")
        return

    for _, row in df.iterrows():
        st.subheader(row["name"])
        st.write("URL:", row["url"])
        st.write("Target:", row["target_price"])
        if pd.notna(row["price"]):
            st.write("Latest Price:", row["price"])
            st.write("Last Checked:", datetime.fromtimestamp(row["checked_at"]))
        else:
            st.write("No price fetched yet.")

if __name__ == "__main__":
    main()
