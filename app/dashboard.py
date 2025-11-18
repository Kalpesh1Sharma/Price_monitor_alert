import streamlit as st
import sqlite3
import time
import threading
import uuid
import requests
import re
import pandas as pd
from datetime import datetime

# --- CONFIGURATION ---
DB_FILE = "prices.db"
POLL_INTERVAL = 300  # Check every 5 minutes (300 seconds)
ALERT_COOLDOWN = 86400 # 24 Hours (in seconds) before alerting again for the same item

# --- TELEGRAM CONFIG ---
# Replace these with your actual details
TELEGRAM_BOT_TOKEN = "AAGCJSbtJrNp0j4YG6-vSFuV5pYtMSz_yPo" 
TELEGRAM_CHAT_ID = "8231783905"

# --- Database Engine ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize DB and perform migrations if columns are missing"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    
    # Create tables if they don't exist
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            target_price REAL DEFAULT 0,
            last_alert_at REAL DEFAULT 0
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
    
    # --- MIGRATION CHECK (For your existing DB) ---
    # Check if 'target_price' exists, if not, add it
    columns = [row[1] for row in c.execute("PRAGMA table_info(items)")]
    if 'target_price' not in columns:
        print("Migrating DB: Adding target_price column...")
        c.execute('ALTER TABLE items ADD COLUMN target_price REAL DEFAULT 0')
    if 'last_alert_at' not in columns:
        print("Migrating DB: Adding last_alert_at column...")
        c.execute('ALTER TABLE items ADD COLUMN last_alert_at REAL DEFAULT 0')
        
    conn.commit()
    conn.close()

# --- Telegram Logic ---
def send_telegram_alert(item_name, price, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token not set.")
        return

    msg = f"🚨 **Price Drop Alert!** \n\nItem: {item_name}\nNew Price: ${price}\n\n[Buy Now]({url})"
    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    try:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        }
        requests.post(send_url, json=payload, timeout=5)
        print(f"Telegram sent for {item_name}")
    except Exception as e:
        print(f"Failed to send Telegram: {e}")

# --- Scraping & Logic ---
def extract_price(text):
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    matches = re.findall(r'([₹$€£]?\s?[\d,]+\.?\d{0,2})', clean_text)
    for m in matches:
        cleaned = re.sub(r'[^\d.]', '', m)
        if cleaned:
            try: return float(cleaned)
            except: continue
    return None

def fetch_and_analyze(item_id, url, name, target_price, last_alert_at):
    """Fetches price, saves to DB, and handles alerts"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        price = extract_price(r.text)
        now = time.time()
        
        conn = get_db_connection()
        conn.execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                     (item_id, now, price if price else -1, "fetched"))
        
        # --- ALERT LOGIC ---
        if price and price > 0 and target_price > 0:
            if price <= target_price:
                # Check cooldown (prevent spamming)
                if (now - last_alert_at) > ALERT_COOLDOWN:
                    send_telegram_alert(name, price, url)
                    # Update last_alert_at
                    conn.execute('UPDATE items SET last_alert_at = ? WHERE id = ?', (now, item_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error fetching {name}: {e}")
        return False

def add_item_logic(url, name, target):
    conn = get_db_connection()
    item_id = str(uuid.uuid4())
    final_name = name if name else url
    conn.execute('INSERT INTO items (id, name, url, target_price) VALUES (?,?,?,?)', 
                 (item_id, final_name, url, target))
    conn.commit()
    conn.close()
    # Immediate fetch (passing 0 as last alert so it doesn't crash)
    fetch_and_analyze(item_id, url, final_name, target, 0) 
    return item_id

def get_dashboard_data():
    conn = get_db_connection()
    # Get items + latest price
    query = '''
        SELECT i.id, i.name, i.url, i.target_price, p.price, p.checked_at 
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

def get_item_history(item_id):
    conn = get_db_connection()
    df = pd.read_sql('SELECT checked_at, price FROM prices WHERE item_id=? ORDER BY checked_at DESC LIMIT 50', 
                     conn, params=(item_id,))
    conn.close()
    return df

# --- Background Poller ---
@st.cache_resource
def start_background_poller():
    def poll_loop():
        while True:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                # Select needed columns including target and last_alert
                items = conn.execute('SELECT id, url, name, target_price, last_alert_at FROM items').fetchall()
                
                for row in items:
                    # We pass the data to the analyzer logic
                    fetch_and_analyze(row['id'], row['url'], row['name'], row['target_price'], row['last_alert_at'])
                    time.sleep(2) # Small delay between items to be polite to servers
            except Exception as e:
                print(f"Poller Error: {e}")
            finally:
                conn.close()
            
            time.sleep(POLL_INTERVAL)
            
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    return t

# --- UI Helpers ---
def pretty_time(ts):
    if not ts or pd.isna(ts): return "Never"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

# --- Main Streamlit App ---
def main():
    st.set_page_config(page_title="Price Monitor", layout="wide")
    init_db()
    start_background_poller()

    st.title("⚡ Price Monitor & Telegram Alerts")

    # --- Sidebar ---
    st.sidebar.header("Add New Tracker")
    with st.sidebar.form("add_form", clear_on_submit=True):
        name = st.text_input("Product Name")
        url = st.text_input("URL")
        target = st.number_input("Target Price ($)", min_value=0.0, step=0.1)
        
        submitted = st.form_submit_button("Start Tracking")
        if submitted:
            if not url:
                st.warning("URL is required")
            else:
                add_item_logic(url, name, target)
                st.success("Added! Fetching initial price...")
                time.sleep(1)
                st.rerun()

    # --- Top Stats ---
    df = get_dashboard_data()
    
    if not df.empty:
        deals = df[(df['price'] > 0) & (df['price'] <= df['target_price']) & (df['target_price'] > 0)]
        if not deals.empty:
            st.success(f"🎉 **{len(deals)} items are currently below target price!**")

    # --- Data Display ---
    st.subheader("Your Watchlist")

    if df.empty:
        st.info("No items yet. Add one in the sidebar.")
    else:
        for index, row in df.iterrows():
            item_id = row['id']
            name = row['name']
            url = row['url']
            price = row['price']
            target = row['target_price']
            last_checked = row['checked_at']

            # Logic for status color
            status_color = "grey"
            status_emoji = "⚪"
            
            if price and price > 0:
                if target > 0 and price <= target:
                    status_color = "green"
                    status_emoji = "🟢 DEAL"
                elif target > 0:
                    status_color = "red"
                    status_emoji = "🔴 HIGH"
            
            price_fmt = f"${price:.2f}" if price and price > 0 else "Wait..."
            target_fmt = f"${target:.2f}" if target > 0 else "No Target"

            # Expander Header
            header = f"{status_emoji} **{name}** | Current: **{price_fmt}** (Target: {target_fmt})"
            
            with st.expander(header, expanded=False):
                c1, c2 = st.columns([1, 2])
                
                with c1:
                    st.markdown(f"**URL:** [Link]({url})")
                    st.markdown(f"**Last Checked:** {pretty_time(last_checked)}")
                    
                    # Update Target Price Logic (Mini Form)
                    with st.form(key=f"upd_{item_id}"):
                        new_target = st.number_input("Update Target Price", value=float(target))
                        if st.form_submit_button("Update Target"):
                            conn = get_db_connection()
                            conn.execute("UPDATE items SET target_price = ? WHERE id = ?", (new_target, item_id))
                            conn.commit()
                            conn.close()
                            st.rerun()
                    
                    if st.button("Delete Item", key=f"del_{item_id}"):
                        conn = get_db_connection()
                        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
                        conn.execute("DELETE FROM prices WHERE item_id=?", (item_id,))
                        conn.commit()
                        conn.close()
                        st.rerun()

                with c2:
                    hist = get_item_history(item_id)
                    if not hist.empty:
                        chart_data = hist[hist['price'] > 0].copy()
                        if not chart_data.empty:
                            chart_data['date'] = pd.to_datetime(chart_data['checked_at'], unit='s')
                            
                            # Render Chart
                            st.line_chart(chart_data, x='date', y='price', height=200)
                            
                            # Show min/max
                            st.caption(f"Lowest recorded: ${chart_data['price'].min()}")
                    else:
                        st.write("No history yet.")

if __name__ == "__main__":
    main()
