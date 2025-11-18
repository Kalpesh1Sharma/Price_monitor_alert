import streamlit as st
import sqlite3
import time
import threading
import uuid
import requests
import os
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# --- CRITICAL CONCURRENCY LOCK ---
# This ensures that only one thread (poller or UI) can access the database file at a time.
DB_LOCK = threading.Lock()

# --- CONFIGURATION ---
POLL_INTERVAL = 1800  # 30 minutes
ALERT_COOLDOWN = 43200 # 12 hours

# --- SECRETS MANAGEMENT (Crash-Proof) ---
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

try:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not TELEGRAM_BOT_TOKEN:
        if "telegram_bot_token" in st.secrets:
            TELEGRAM_BOT_TOKEN = st.secrets["telegram_bot_token"]
        if "telegram_chat_id" in st.secrets:
            TELEGRAM_CHAT_ID = st.secrets["telegram_chat_id"]
except Exception:
    pass

# --- Database Path Management ---
def get_db_path():
    """Returns a reliable path for the SQLite file in the OS temporary directory (/tmp)."""
    return os.path.join("/tmp", "prices.db") 

def get_db_connection():
    """Returns a new SQLite connection using the reliable path."""
    conn = sqlite3.connect(get_db_path(), check_same_thread=False) 
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes DB, handles corruption by deleting/recreating the file."""
    db_path = get_db_path()
    
    # 1. Check for Corruption/Locking on existing file
    if os.path.exists(db_path):
        try:
            with DB_LOCK:
                conn = get_db_connection()
                conn.execute("SELECT name FROM items LIMIT 1").fetchone()
                conn.close()
        except Exception:
            print("DB file found but appears corrupted/locked. Deleting and recreating.")
            os.remove(db_path)
            
    # 2. Create the tables (or ensure they exist)
    try:
        with DB_LOCK:
            conn = get_db_connection()
            c = conn.cursor()
            
            c.execute('''CREATE TABLE IF NOT EXISTS items 
                         (id TEXT PRIMARY KEY, name TEXT, url TEXT, target_price REAL DEFAULT 0, last_alert_at REAL DEFAULT 0)''')
            c.execute('''CREATE TABLE IF NOT EXISTS prices 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, checked_at REAL, price REAL, status TEXT)''')
            
            # Safe migration using IF NOT EXISTS
            c.execute('ALTER TABLE items ADD COLUMN IF NOT EXISTS target_price REAL DEFAULT 0')
            c.execute('ALTER TABLE items ADD COLUMN IF NOT EXISTS last_alert_at REAL DEFAULT 0')
            
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"FATAL: Database Initialization Failed: {e}")

# --- Scraping & Telegram Logic ---

def get_random_headers():
    try:
        ua = UserAgent()
        user_agent = ua.random
    except:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    return {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
    }

def parse_price_amazon(soup):
    try:
        price_element = soup.select_one('.a-price-whole')
        if price_element:
            return float(price_element.text.replace(',', '').replace('.', ''))
        price_element = soup.select_one('.a-offscreen')
        if price_element:
            clean = price_element.text.replace('₹', '').replace('$', '').replace(',', '')
            return float(clean)
    except:
        pass
    return None

def fetch_price_data(url):
    try:
        session = requests.Session()
        response = session.get(url, headers=get_random_headers(), timeout=15)
        if response.status_code != 200: return None, f"Blocked ({response.status_code})"
        
        soup = BeautifulSoup(response.content, "html.parser")
        price = None
        
        if "amazon" in url:
            price = parse_price_amazon(soup)
        else:
            # Generic Fallback
            import re
            text = soup.get_text()
            matches = re.findall(r'[₹$]\s?([\d,]+)', text)
            if matches: price = float(matches[0].replace(',', ''))
            
        if price: return price, "Success"
        return None, "Parse Fail"
    except Exception as e:
        return None, f"Request Error: {e}"

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return False, "No Config"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
        return True, "Sent"
    except Exception as e:
        return False, str(e)

# --- Worker Logic ---
def check_item_logic(item_id, name, url, target_price, last_alert):
    price, status = fetch_price_data(url)
    now = time.time()
    
    # Database Write/Commit (Must use lock!)
    conn = get_db_connection()
    try:
        with DB_LOCK:
            # 1. Insert Price Snapshot
            conn.execute('INSERT INTO prices (item_id, checked_at, price, status) VALUES (?,?,?,?)',
                         (item_id, now, price if price else -1, status))
            
            # 2. Alerting Logic
            if price and price > 0 and target_price > 0 and price <= target_price:
                if (now - last_alert) > ALERT_COOLDOWN:
                    msg = f"🚨 **DEAL ALERT!**\n\n📦 {name}\n💰 **Current:** ₹{price}\n🎯 **Target:** ₹{target_price}\n\n[Link]({url})"
                    success, err = send_telegram_message(msg)
                    if success:
                        conn.execute('UPDATE items SET last_alert_at = ? WHERE id = ?', (now, item_id))
                    else:
                        print(f"Alert failed for {name}: {err}")
            
            conn.commit()
    except Exception as e:
        print(f"DB Write Error in Poller for {name}: {e}")
    finally:
        conn.close()
    return price, status

# --- Background Thread ---
@st.cache_resource
def start_poller():
    def run():
        while True:
            try:
                # Read items (must use lock as well)
                with DB_LOCK:
                    conn = get_db_connection()
                    conn.row_factory = sqlite3.Row
                    items = conn.execute('SELECT * FROM items').fetchall()
                    conn.close()
                
                for row in items:
                    check_item_logic(row['id'], row['name'], row['url'], row['target_price'], row['last_alert_at'])
                    time.sleep(10) # Wait between items
            except Exception as e:
                print(f"Poller Loop Failed: {e}")
                
            time.sleep(POLL_INTERVAL)
            
    t = threading.Thread(target=run, daemon=True)
    t.start()

# --- MAIN UI ---
def main():
    st.set_page_config(page_title="Price Tracker", layout="wide")
    init_db()
    start_poller()
    
    st.title("☁️ Cloud Price Tracker")

    # --- Sidebar ---
    with st.sidebar:
        st.header("Add Item")
        with st.form("new_item"):
            name = st.text_input("Product Name (Optional)")
            url = st.text_input("URL")
            target = st.number_input("Target Price (₹)", min_value=0.0)
            
            if st.form_submit_button("Track"):
                if url:
                    item_name = name or url[:30]
                    try:
                        # Manual Item Insert (Must use lock!)
                        with DB_LOCK:
                            conn = get_db_connection()
                            uid = str(uuid.uuid4())
                            conn.execute("INSERT INTO items (id, name, url, target_price) VALUES (?,?,?,?)", 
                                         (uid, item_name, url, target))
                            conn.commit()
                            conn.close()
                        st.success(f"Added: {item_name}")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to insert item (DB Write Error): {e}")
        
        st.divider()
        if st.button("Test Telegram"):
            ok, msg = send_telegram_message("✅ Test message from App")
            if ok: st.success("Sent!")
            else: st.error(f"Failed: {msg}")

    # --- Dashboard Data Loading ---
    df = pd.DataFrame()
    try:
        # Load data (Must use lock!)
        with DB_LOCK:
            conn = get_db_connection()
            df = pd.read_sql('''
                SELECT i.id, i.name, i.url, i.target_price, 
                       p.price as current_price, p.status, p.checked_at
                FROM items i
                LEFT JOIN (
                    SELECT item_id, price, status, checked_at FROM prices 
                    WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
                ) p ON i.id = p.item_id
            ''', conn)
            conn.close()
    except Exception as e:
        print(f"Main data load failed: {e}")
        st.warning("Database is initializing or empty. Add an item to start.")
        df = pd.DataFrame() 

    # --- Display ---
    if not df.empty:
        for _, row in df.iterrows():
            price = row['current_price']
            target = row['target_price']
            status = row['status'] if row['status'] else "Pending"
            last_checked_ts = row['checked_at']

            # 1. Safely determine display values and status (FIX FOR TYPE ERROR)
            if price is None or price <= 0:
                price_display = "Pending/Error"
                price_float = 0.0
                icon = "⚠️"
                lbl = "Status: Pending"
            else:
                # Use safe string formatting only when price is a valid number
                price_display = f"₹{price:.2f}"
                price_float = price
                if price <= target and target > 0:
                    icon = "🔥"
                    lbl = f"DEAL! ({price_display})"
                else:
                    icon = "📈"
                    lbl = f"Current: {price_display}"
            
            last_checked_str = datetime.fromtimestamp(last_checked_ts).strftime('%Y-%m-%d %H:%M') if last_checked_ts else 'Never'

            # 2. Expander Display (Using the safe strings)
            with st.expander(f"{icon} {lbl} | Target: ₹{target:.2f} | {row['url'][:40]}...", expanded=True):
                
                # --- FIX APPLIED HERE: Safe string output ---
                st.markdown(f"**Current Price:** {price_display} (Checked: {last_checked_str})")
                st.markdown(f"**Target Price:** ₹{target:.2f}")
                st.markdown(f"**Status:** {status}")
                
                c1, c2 = st.columns(2)
                c1.markdown(f"[Link]({row['url']})")
                
                # Manual Check
                if c1.button("Check Now", key=f"chk_{row['id']}"):
                    with st.spinner("Checking..."):
                        check_item_logic(row['id'], row['name'], row['url'], row['target_price'], 0)
                    st.rerun()
                
                # Delete Item
                if c2.button("Delete", key=f"del_{row['id']}"):
                    try:
                        with DB_LOCK:
                            conn = get_db_connection()
                            conn.execute("DELETE FROM items WHERE id=?", (row['id'],))
                            conn.execute("DELETE FROM prices WHERE item_id=?", (row['id'],))
                            conn.commit()
                            conn.close()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Deletion Failed: {e}")
    else:
        st.info("Watchlist is empty. Add an item in the sidebar.")

if __name__ == "__main__":
    main()
