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
POLL_INTERVAL = 3600  # Check every 1 HOUR (3600 seconds)
ALERT_COOLDOWN = 86400 # 24 Hours wait before repeating an alert for the same item

# --- SECRETS MANAGEMENT ---
try:
    TELEGRAM_BOT_TOKEN = st.secrets["telegram_bot_token"]
    TELEGRAM_CHAT_ID = st.secrets["telegram_chat_id"]
except Exception:
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None

# --- Database Engine ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, name TEXT, url TEXT, target_price REAL DEFAULT 0, last_alert_at REAL DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, checked_at REAL, price REAL, raw_text TEXT
        )
    ''')
    # Migration check
    cols = [row[1] for row in c.execute("PRAGMA table_info(items)")]
    if 'target_price' not in cols: c.execute('ALTER TABLE items ADD COLUMN target_price REAL DEFAULT 0')
    if 'last_alert_at' not in cols: c.execute('ALTER TABLE items ADD COLUMN last_alert_at REAL DEFAULT 0')
    conn.commit()
    conn.close()

# --- UTILS ---
def get_headers():
    # This mocks a real Chrome browser on Windows to fool Amazon
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception as e:
        print(f"Telegram Error: {e}")
        return False

def extract_price(text):
    # Remove HTML tags to clean the mess
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    # Regex to find currency-like numbers (Supports ₹, $, commas)
    matches = re.findall(r'([₹$€£]?\s?[\d,]+\.?\d{0,2})', clean_text)
    
    for m in matches:
        # Clean specific currency symbols and commas
        cleaned = re.sub(r'[^\d.]', '', m)
        if cleaned:
            try:
                val = float(cleaned)
                # Filter out year numbers (2023, 2024) or small junk
                if val > 10: 
                    return val
            except:
                continue
    return None

def fetch_and_analyze(item_id, url, name, target_price, last_alert_at):
    try:
        # Use the strong headers
        r = requests.get(url, headers=get_headers(), timeout=15)
        
        if r.status_code != 200:
            print(f"Failed to fetch {url} - Status: {r.status_code}")
            return False
            
        price = extract_price(r.text)
        now = time.time()
        
        # Save result to DB
        conn = get_db_connection()
        conn.execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                     (item_id, now, price if price else -1, "fetched"))
        
        # ALERT LOGIC
        if price and price > 0 and target_price > 0:
            if price <= target_price:
                # Check if we alerted recently (Cool down)
                if (now - last_alert_at) > ALERT_COOLDOWN:
                    msg = f"🚨 **PRICE DROP ALERT!**\n\n📦 **{name}**\n💰 Price: ₹{price}\n🎯 Target: ₹{target_price}\n\n[👉 Buy Now]({url})"
                    send_telegram_alert(msg)
                    conn.execute('UPDATE items SET last_alert_at = ? WHERE id = ?', (now, item_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error scraping {name}: {e}")
        return False

# --- Background Worker ---
@st.cache_resource
def start_background_poller():
    def poll_loop():
        while True:
            # Loop through items and check prices
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                items = conn.execute('SELECT id, url, name, target_price, last_alert_at FROM items').fetchall()
                for row in items:
                    fetch_and_analyze(row['id'], row['url'], row['name'], row['target_price'], row['last_alert_at'])
                    # Sleep 5 seconds between items to be polite to Amazon
                    time.sleep(5) 
            except Exception as e:
                print(f"Poller Crash: {e}")
            finally:
                conn.close()
            
            # Wait 1 HOUR before starting the next cycle
            time.sleep(POLL_INTERVAL)
            
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    return t

# --- UI ---
def pretty_time(ts):
    if not ts or pd.isna(ts): return "Never"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

def main():
    st.set_page_config(page_title="Amazon Price Monitor", layout="wide")
    init_db()
    start_background_poller()

    st.title("⚡ Amazon Price Monitor")
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.header("Controls")
        
        # Add Item
        with st.form("add_form", clear_on_submit=True):
            st.subheader("Add Tracker")
            name = st.text_input("Product Name")
            url = st.text_input("URL")
            target = st.number_input("Target Price (₹)", min_value=0.0)
            if st.form_submit_button("Start Tracking"):
                if url:
                    conn = get_db_connection()
                    item_id = str(uuid.uuid4())
                    conn.execute('INSERT INTO items (id, name, url, target_price) VALUES (?,?,?,?)', 
                                 (item_id, name if name else url, url, target))
                    conn.commit()
                    conn.close()
                    st.success("Added!")
                    time.sleep(0.5)
                    st.rerun()
        
        st.divider()
        
        # Telegram Tester
        st.subheader("Telegram Setup")
        if st.button("Test Telegram Connection"):
            if send_telegram_alert("✅ This is a test message from your Price Monitor."):
                st.success("Message Sent! Check your Telegram.")
            else:
                st.error("Failed. Check secrets.toml")

    # --- MAIN DASHBOARD ---
    conn = get_db_connection()
    df = pd.read_sql('''
        SELECT i.id, i.name, i.url, i.target_price, p.price, p.checked_at 
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, checked_at
            FROM prices
            WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
        ) p ON i.id = p.item_id
    ''', conn)
    conn.close()

    if df.empty:
        st.info("No items tracked. Add one in the sidebar.")
    else:
        for _, row in df.iterrows():
            item_id, name, url = row['id'], row['name'], row['url']
            price, target, last_checked = row['price'], row['target_price'], row['checked_at']
            
            # Status Logic
            if not price or price <= 0:
                status = "⚪ ERROR / PENDING"
                color = "grey"
            elif target > 0 and price <= target:
                status = f"🟢 DEAL (₹{price})"
                color = "green"
            else:
                status = f"🔴 HIGH (₹{price})"
                color = "red"

            with st.expander(f"{status} | {name}", expanded=False):
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.markdown(f"**Target:** ₹{target} | **Current:** {('₹'+str(price)) if price > 0 else 'Failed'}")
                    st.markdown(f"**Last Checked:** {pretty_time(last_checked)}")
                    st.markdown(f"[Open Product]({url})")
                    
                    if st.button("Check Now", key=f"fetch_{item_id}"):
                        with st.spinner("Fetching..."):
                            success = fetch_and_analyze(item_id, url, name, target, 0)
                            if success: st.success("Updated!")
                            else: st.error("Failed to fetch (Amazon blocked connection)")
                        time.sleep(1)
                        st.rerun()
                        
                    if st.button("Delete", key=f"del_{item_id}"):
                        conn = get_db_connection()
                        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
                        conn.execute("DELETE FROM prices WHERE item_id=?", (item_id,))
                        conn.commit()
                        conn.close()
                        st.rerun()

if __name__ == "__main__":
    main()
