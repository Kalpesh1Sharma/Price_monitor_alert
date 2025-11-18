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
POLL_INTERVAL = 300  # Check every 5 minutes
ALERT_COOLDOWN = 86400 # 24 Hours before alerting again for the same item

# --- TELEGRAM CONFIG ---
# Replace with your details
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

# --- Database Engine ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize DB and perform migrations"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    
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
    
    # Migration check for older DB files
    columns = [row[1] for row in c.execute("PRAGMA table_info(items)")]
    if 'target_price' not in columns:
        c.execute('ALTER TABLE items ADD COLUMN target_price REAL DEFAULT 0')
    if 'last_alert_at' not in columns:
        c.execute('ALTER TABLE items ADD COLUMN last_alert_at REAL DEFAULT 0')
        
    conn.commit()
    conn.close()

# --- Telegram Logic (INR Updated) ---
def send_telegram_alert(item_name, price, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Updated message with ₹ symbol
    msg = f"🚨 **Price Drop Alert!** \n\nItem: {item_name}\nNew Price: ₹{price:,.2f}\n\n[Buy Now]({url})"
    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(send_url, json=payload, timeout=5)
    except Exception as e:
        print(f"Failed to send Telegram: {e}")

# --- Scraping & Logic ---
def extract_price(text):
    # Updated regex to handle unicode ₹ and HTML entities
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    
    # Look for patterns like ₹ 1,499 or 1,499.00
    matches = re.findall(r'([₹$€£]?\s?[\d,]+\.?\d{0,2})', clean_text)
    
    for m in matches:
        # Remove non-numeric characters except dot
        cleaned = re.sub(r'[^\d.]', '', m)
        if cleaned:
            try: 
                return float(cleaned)
            except: 
                continue
    return None

def fetch_and_analyze(item_id, url, name, target_price, last_alert_at):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=10)
        price = extract_price(r.text)
        now = time.time()
        
        conn = get_db_connection()
        conn.execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                     (item_id, now, price if price else -1, "fetched"))
        
        # Alert Logic
        if price and price > 0 and target_price > 0:
            if price <= target_price:
                if (now - last_alert_at) > ALERT_COOLDOWN:
                    send_telegram_alert(name, price, url)
                    conn.execute('UPDATE items SET last_alert_at = ? WHERE id = ?', (now, item_id))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error fetching {name}: {e}")

def add_item_logic(url, name, target):
    conn = get_db_connection()
    item_id = str(uuid.uuid4())
    final_name = name if name else url
    conn.execute('INSERT INTO items (id, name, url, target_price) VALUES (?,?,?,?)', 
                 (item_id, final_name, url, target))
    conn.commit()
    conn.close()
    fetch_and_analyze(item_id, url, final_name, target, 0) 
    return item_id

def get_dashboard_data():
    conn = get_db_connection()
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
                items = conn.execute('SELECT id, url, name, target_price, last_alert_at FROM items').fetchall()
                for row in items:
                    fetch_and_analyze(row['id'], row['url'], row['name'], row['target_price'], row['last_alert_at'])
                    time.sleep(2)
            except
