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

# --- CONFIGURATION ---
DB_FILE = "prices.db"
# Check more frequently (every 30 mins) so we don't sleep too long, 
# but not so fast we get banned.
POLL_INTERVAL = 1800 
ALERT_COOLDOWN = 43200 # 12 Hours

# --- SECRETS (Render Compatible) ---
# On Render, we read from os.environ. On Local, we try st.secrets
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or st.secrets.get("telegram_bot_token")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or st.secrets.get("telegram_chat_id")

# --- Database ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    c.execute('''CREATE TABLE IF NOT EXISTS items 
                 (id TEXT PRIMARY KEY, name TEXT, url TEXT, target_price REAL DEFAULT 0, last_alert_at REAL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS prices 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, checked_at REAL, price REAL, status TEXT)''')
    
    # Migration safety
    cols = [row[1] for row in c.execute("PRAGMA table_info(items)")]
    if 'target_price' not in cols: c.execute('ALTER TABLE items ADD COLUMN target_price REAL DEFAULT 0')
    conn.commit()
    conn.close()

# --- Advanced Scraping ---
def get_random_headers():
    # Rotates User-Agents to look like different real computers
    try:
        ua = UserAgent()
        user_agent = ua.random
    except:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        
    return {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
    }

def parse_price_amazon(soup):
    # Logic specifically for Amazon's CSS classes
    try:
        # Try 1: The standard big price
        price_element = soup.select_one('.a-price-whole')
        if price_element:
            return float(price_element.text.replace(',', '').replace('.', ''))
        
        # Try 2: The hidden offscreen price
        price_element = soup.select_one('.a-offscreen')
        if price_element:
            clean = price_element.text.replace('₹', '').replace('$', '').replace(',', '')
            return float(clean)
            
    except Exception as e:
        print(f"Parsing Error: {e}")
    return None

def fetch_price_data(url):
    try:
        # Request with headers
        session = requests.Session()
        response = session.get(url, headers=get_random_headers(), timeout=15)
        
        if response.status_code != 200:
            return None, f"Blocked ({response.status_code})"

        soup = BeautifulSoup(response.content, "html.parser")
        
        # Detect Amazon vs others
        if "amazon" in url:
            price = parse_price_amazon(soup)
        else:
            # Fallback for other sites (Flipkart etc)
            # Try to find any text that looks like ₹1,200 inside a likely class
            import re
            text = soup.get_text()
            matches = re.findall(r'[₹$]\s?([\d,]+)', text)
            if matches:
                price = float(matches[0].replace(',', ''))
            else:
                price = None

        if price:
            return price, "Success"
        else:
            # If we got the page but couldn't find price, Amazon might have served a CAPTCHA page
            if "captcha" in response.text.lower():
                return None, "Captcha Block"
            return None, "Parse Fail"
            
    except Exception as e:
        return None, str(e)

# --- Telegram ---
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Config Missing")
        return False, "Config Missing"
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True, "Sent"
        else:
            return False, f"API Error {r.status_code}: {r.text}"
    except Exception as e:
        return False, str(e)

# --- Worker Logic ---
def check_item_logic(item_id, name, url, target_price, last_alert):
    price, status = fetch_price_data(url)
    now = time.time()
    
    conn = get_db_connection()
    conn.execute('INSERT INTO prices (item_id, checked_at, price, status) VALUES (?,?,?,?)',
                 (item_id, now, price if price else -1, status))
    
    # Alerting
    if price and price > 0 and target_price > 0:
        if price <= target_price:
            # Cooldown check
            if (now - last_alert) > ALERT_COOLDOWN:
                msg = f"🚨 **DEAL ALERT!**\n\n📦 {name}\n💰 **Current:** ₹{price}\n🎯 **Target:** ₹{target_price}\n\n[Link]({url})"
                success, err = send_telegram_message(msg)
                if success:
                    conn.execute('UPDATE items SET last_alert_at = ? WHERE id = ?', (now, item_id))
                    print(f"Alert sent for {name}")
                else:
                    print(f"Alert failed: {err}")

    conn.commit()
    conn.close()
    return price, status

# --- Background Thread ---
@st.cache_resource
def start_poller():
    def run():
        while True:
            try:
                conn = sqlite3.connect(DB_FILE, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                items = conn.execute('SELECT * FROM items').fetchall()
                conn.close()
                
                for row in items:
                    check_item_logic(row['id'], row['name'], row['url'], row['target_price'], row['last_alert_at'])
                    time.sleep(10) # Wait between items
            except Exception as e:
                print(f"Poller Error: {e}")
                
            time.sleep(POLL_INTERVAL)
            
    t = threading.Thread(target=run, daemon=True)
    t.start()

# --- UI ---
def main():
    st.set_page_config(page_title="Price Tracker", layout="wide")
    init_db()
    start_poller()
    
    st.title("☁️ Cloud Price Tracker")
    
    # Warning for Render Users
    st.warning("⚠️ If hosting on free Render: Data wipes on restart. Use an external DB for persistence.")

    # Sidebar
    with st.sidebar:
        st.header("Add Item")
        with st.form("new_item"):
            url = st.text_input("URL")
            target = st.number_input("Target Price", min_value=0.0)
            if st.form_submit_button("Track"):
                if url:
                    conn = get_db_connection()
                    uid = str(uuid.uuid4())
                    conn.execute("INSERT INTO items (id, name, url, target_price) VALUES (?,?,?,?)", 
                                 (uid, "New Item", url, target))
                    conn.commit()
                    conn.close()
                    st.success("Added")
                    time.sleep(1)
                    st.rerun()
        
        st.divider()
        if st.button("Test Telegram"):
            ok, msg = send_telegram_message("✅ Test from Render App")
            if ok: st.success("Sent!")
            else: st.error(f"Failed: {msg}")

    # Dashboard
    conn = get_db_connection()
    # Get items and latest status
    df = pd.read_sql('''
        SELECT i.*, p.price as current_price, p.status, p.checked_at
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, status, checked_at FROM prices 
            WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
        ) p ON i.id = p.item_id
    ''', conn)
    conn.close()

    if not df.empty:
        for _, row in df.iterrows():
            price = row['current_price']
            target = row['target_price']
            
            # Color coding
            if row['status'] != "Success":
                color = "grey"
                icon = "⚠️"
                lbl = f"{row['status']}"
            elif price <= target and target > 0:
                color = "green"
                icon = "🔥"
                lbl = f"₹{price}"
            else:
                color = "red"
                icon = "📈"
                lbl = f"₹{price}"

            with st.expander(f"{icon} {lbl} | Target: ₹{target} | {row['url'][:40]}...", expanded=True):
                c1, c2 = st.columns(2)
                c1.write(f"**Status:** {row['status']}")
                c1.markdown(f"[Link]({row['url']})")
                if c1.button("Check Now", key=f"chk_{row['id']}"):
                    with st.spinner("Checking..."):
                        p, s = check_item_logic(row['id'], row['name'], row['url'], row['target_price'], 0)
                    st.rerun()
                
                if c2.button("Delete", key=f"del_{row['id']}"):
                    conn = get_db_connection()
                    conn.execute("DELETE FROM items WHERE id=?", (row['id'],))
                    conn.commit()
                    conn.close()
                    st.rerun()

    else:
        st.info("Add an item to start.")

if __name__ == "__main__":
    main()
