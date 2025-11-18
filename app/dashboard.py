import streamlit as st
import sqlite3
import time
import threading
import uuid
import requests
import re
import pandas as pd
from datetime import datetime

# --- Config ---
DB_FILE = "prices.db"
POLL_INTERVAL = 300  # Check every 5 minutes

# --- Database Functions ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;') # Better concurrency
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            checked_at REAL,
            price REAL,
            raw_text TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_item(name, url):
    conn = get_db_connection()
    item_id = str(uuid.uuid4())
    conn.execute('INSERT INTO items (id, name, url) VALUES (?,?,?)', (item_id, name, url))
    conn.commit()
    conn.close()
    # Trigger immediate fetch
    fetch_price(item_id, url)
    return item_id

def get_all_items_with_latest_price():
    conn = get_db_connection()
    # Complex query to get items + their most recent price
    query = '''
        SELECT i.id, i.name, i.url, p.price, p.checked_at 
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, checked_at
            FROM prices
            WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
        ) p ON i.id = p.item_id
    '''
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def get_history(item_id):
    conn = get_db_connection()
    df = pd.read_sql('SELECT checked_at, price FROM prices WHERE item_id=? ORDER BY checked_at ASC', 
                     conn, params=(item_id,))
    conn.close()
    if not df.empty:
        # Convert timestamp to readable date
        df['date'] = pd.to_datetime(df['checked_at'], unit='s')
    return df

# --- Scraping Logic ---
def extract_price(text):
    # Simple heuristic: remove html tags, look for numbers
    # Note: For production, use BeautifulSoup
    clean_text = re.sub(r'<[^>]+>', ' ', text) 
    matches = re.findall(r'([₹$€£]?\s?[\d,]+\.?\d{0,2})', clean_text)
    
    for m in matches:
        cleaned = re.sub(r'[^\d.]', '', m)
        if cleaned:
            try:
                return float(cleaned)
            except:
                continue
    return None

def fetch_price(item_id, url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        price = extract_price(r.text)
        
        conn = get_db_connection()
        conn.execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                     (item_id, time.time(), price if price else -1, "fetched"))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error fetching {url}: {e}")

# --- Background Worker (The Magic Part) ---
# @st.cache_resource ensures this runs only ONCE when the server starts
# and stays alive across user reloads.
@st.cache_resource
def start_background_poller():
    def poll_loop():
        while True:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            items = conn.execute('SELECT id, url FROM items').fetchall()
            conn.close()
            
            for item_id, url in items:
                fetch_price(item_id, url)
                
            time.sleep(POLL_INTERVAL)

    # Daemon thread dies when the main program exits
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    return t

# --- Main UI ---
def main():
    st.set_page_config(page_title="Price Tracker", layout="wide")
    init_db()
    
    # Start the background thread (singleton)
    start_background_poller()

    st.title("🛒 Live Price Tracker")

    # Sidebar: Add New Item
    with st.sidebar:
        st.header("Track New Item")
        with st.form("add_form"):
            new_name = st.text_input("Item Name")
            new_url = st.text_input("URL")
            submitted = st.form_submit_button("Start Tracking")
            
            if submitted and new_url:
                add_item(new_name, new_url)
                st.success(f"Added {new_name}!")
                time.sleep(1)
                st.rerun()

    # Main Content: Dashboard
    df = get_all_items_with_latest_price()

    if df.empty:
        st.info("No items tracked yet. Add one in the sidebar!")
    else:
        # 1. Overview Metrics
        col1, col2 = st.columns(2)
        col1.metric("Items Tracked", len(df))
        avg_price = df[df['price'] > 0]['price'].mean()
        col2.metric("Avg Price", f"${avg_price:.2f}" if not pd.isna(avg_price) else "-")

        st.divider()

        # 2. Main Data Table
        st.subheader("Current Prices")
        
        # Formatting for display
        display_df = df.copy()
        display_df['price'] = display_df['price'].apply(lambda x: f"${x:.2f}" if x > 0 else "Error/N/A")
        display_df['last_checked'] = pd.to_datetime(display_df['checked_at'], unit='s').dt.strftime('%Y-%m-%d %H:%M')
        
        st.dataframe(
            display_df[['name', 'price', 'last_checked', 'url']],
            use_container_width=True,
            column_config={
                "url": st.column_config.LinkColumn("Link"),
            }
        )

        st.divider()

        # 3. Drill Down (Charts)
        st.subheader("Price History Analysis")
        selected_item_name = st.selectbox("Select Item to View History", df['name'].unique())
        
        if selected_item_name:
            # Get ID for the name
            item_row = df[df['name'] == selected_item_name].iloc[0]
            hist_df = get_history(item_row['id'])
            
            if not hist_df.empty:
                # Clean -1 errors for the chart
                chart_df = hist_df[hist_df['price'] > 0]
                
                if not chart_df.empty:
                    st.line_chart(chart_df, x='date', y='price')
                    
                    min_price = chart_df['price'].min()
                    max_price = chart_df['price'].max()
                    curr_price = chart_df.iloc[-1]['price']
                    
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Lowest Price", f"${min_price}")
                    c2.metric("Highest Price", f"${max_price}")
                    c3.metric("Current", f"${curr_price}")
                else:
                    st.warning("Price data contains errors (scraped -1). Check the URL.")
            else:
                st.write("No history yet.")

        # Manual Refresh Button
        if st.button("Refresh Data Now"):
            st.rerun()

if __name__ == "__main__":
    main()
