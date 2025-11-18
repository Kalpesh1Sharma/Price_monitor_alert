# app/dashboard.py
import os
import re
import time
import uuid
import sqlite3
import requests
import pandas as pd

from datetime import datetime
from urllib.parse import urlparse
from threading import Thread

import streamlit as st
from bs4 import BeautifulSoup

# -----------------------
# CONFIG
# -----------------------
st.set_page_config(page_title="INR Price Monitor", layout="wide")
DB_FILE = os.environ.get("DB_FILE", "prices.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))      # seconds
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", 86400)) # seconds (default 24h)
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 12))

# Telegram from env (do NOT hardcode production tokens)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# -----------------------
# DB HELPERS
# -----------------------
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
    conn.commit()
    conn.close()

# -----------------------
# TELEGRAM
# -----------------------
def send_telegram_alert(name, price, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing; skipping telegram alert.")
        return False

    msg_html = (
        f"<b>🚨 Price Drop Alert!</b>\n\n"
        f"<b>Item:</b> {escape_html(name)}\n"
        f"<b>New Price:</b> ₹{price:,.2f}\n\n"
        f'<a href="{escape_html(url)}">Buy Now</a>'
    )
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(api, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print("Failed to send telegram:", e, getattr(e, "response", ""))
        return False

def escape_html(s: str):
    # Minimal escaping for HTML parse mode
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# -----------------------
# SCRAPING
# -----------------------
def extract_price(html_text: str):
    """Extract float price from HTML/text. Returns float or None."""
    if not html_text:
        return None
    text = re.sub(r"<script.?>.?</script>", " ", html_text, flags=re.S|re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    # Find patterns with currency symbols or plain numbers with separators
    # Examples matched: ₹ 1,234.00, 1,234, 1234.50, ₹1234
    matches = re.findall(r'([₹\u20B9$\€£]?\s?[\d{1,3},]+(?:\.\d{1,2})?)', text)
    if not matches:
        # As fallback, find simple numbers
        matches = re.findall(r'(\d{1,3}(?:[,\d]{0,})?(?:\.\d{1,2})?)', text)
    for m in matches:
        cleaned = re.sub(r'[^\d.]', '', m)
        if cleaned:
            try:
                return float(cleaned)
            except:
                continue
    return None

def get_amazon_data(html):
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else None

    price_selectors = [
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.a-price > span.a-offscreen",
    ]
    raw_price = None
    for sel in price_selectors:
        el = soup.select_one(sel)
        if el:
            raw_price = el.get_text(strip=True)
            break
    return title, raw_price

def get_flipkart_data(html):
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("span.B_NuCI") or soup.select_one("._35KyD6")
    title = title_el.get_text(strip=True) if title_el else None
    price_el = soup.select_one("div._30jeq3._16Jk6d") or soup.select_one("div._30jeq3")
    raw_price = price_el.get_text(strip=True) if price_el else None
    return title, raw_price

def fetch_price_and_title(url):
    """Return (title, price_float_or_None, raw_price_text_or_None, error_or_None)"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return None, None, None, f"Request failed: {e}"

    domain = urlparse(url).netloc.lower()
    html = r.text

    try:
        if "amazon." in domain:
            title, raw_price = get_amazon_data(html)
        elif "flipkart." in domain:
            title, raw_price = get_flipkart_data(html)
        else:
            soup = BeautifulSoup(html, "lxml")
            t = soup.select_one("meta[property='og:title']") or soup.select_one("title")
            title = t.get("content") if (t and t.has_attr("content")) else (t.get_text() if t else None)
            p = soup.select_one("meta[property='product:price:amount']") or soup.select_one("[class*='price']")
            raw_price = p.get("content") if (p and p.has_attr("content")) else (p.get_text() if p else None)
    except Exception as e:
        return None, None, None, f"Parse error: {e}"

    price = extract_price(raw_price) if raw_price else None
    return title, price, raw_price, None

# -----------------------
# ANALYSIS & TRACKING
# -----------------------
def record_price(item_id, price, raw_text="fetched"):
    conn = get_db_connection()
    conn.execute("INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?, ?, ?, ?)",
                 (item_id, time.time(), price if price is not None else -1, raw_text))
    conn.commit()
    conn.close()

def fetch_and_analyze(item_id, url, name, target_price, last_alert_at):
    try:
        title, price, raw_price_text, error = fetch_price_and_title(url)
        now = time.time()
        record_price(item_id, price, raw_price_text or "")
        # Update item name if we got better title
        if title:
            conn = get_db_connection()
            conn.execute("UPDATE items SET name = ? WHERE id = ?", (title, item_id))
            conn.commit()
            conn.close()

        # Alerting logic
        if price and price > 0 and target_price and target_price > 0:
            if price <= target_price and (now - (last_alert_at or 0) > ALERT_COOLDOWN):
                ok = send_telegram_alert(name or (title or url), price, url)
                if ok:
                    conn = get_db_connection()
                    conn.execute("UPDATE items SET last_alert_at = ? WHERE id = ?", (now, item_id))
                    conn.commit()
                    conn.close()
    except Exception as e:
        print("Error in fetch_and_analyze:", e)

# -----------------------
# DASHBOARD QUERIES
# -----------------------
def get_dashboard_data():
    conn = get_db_connection()
    query = """
        SELECT i.id, i.name, i.url, i.target_price,
               p.price, p.checked_at
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, checked_at
            FROM prices
            WHERE id IN (SELECT MAX(id) FROM prices GROUP BY item_id)
        ) p ON i.id = p.item_id
        ORDER BY i.rowid DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def get_item_history(item_id, limit=50):
    conn = get_db_connection()
    df = pd.read_sql("SELECT checked_at, price FROM prices WHERE item_id=? ORDER BY checked_at DESC LIMIT ?",
                     conn, params=(item_id, limit))
    conn.close()
    return df

# -----------------------
# BACKGROUND POLLER (single thread)
# -----------------------
_poller_started = False

def start_background_poller():
    global _poller_started
    if _poller_started:
        return
    _poller_started = True

    def poll_loop():
        print("Background poller started. Interval:", POLL_INTERVAL)
        while True:
            try:
                conn = get_db_connection()
                rows = conn.execute("SELECT id, url, name, target_price, last_alert_at FROM items").fetchall()
                conn.close()
                for r in rows:
                    fetch_and_analyze(r["id"], r["url"], r["name"], r["target_price"], r["last_alert_at"])
                    # small polite delay between item fetches
                    time.sleep(1.5)
            except Exception as e:
                print("Poller loop error:", e)
            time.sleep(POLL_INTERVAL)

    t = Thread(target=poll_loop, daemon=True)
    t.start()

# -----------------------
# UI HELPERS
# -----------------------
def pretty_time(ts):
    if not ts or pd.isna(ts): 
        return "Never"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")

# -----------------------
# STREAMLIT APP
# -----------------------
def add_item(url, name, target):
    if not url:
        return None
    conn = get_db_connection()
    item_id = str(uuid.uuid4())
    final_name = name.strip() if name else url
    conn.execute("INSERT INTO items (id, name, url, target_price, last_alert_at) VALUES (?, ?, ?, ?, ?)",
                 (item_id, final_name, url, float(target or 0), 0))
    conn.commit()
    conn.close()
    # fetch initial price in background so UI isn't blocked
    Thread(target=fetch_and_analyze, args=(item_id, url, final_name, float(target or 0), 0), daemon=True).start()
    return item_id

def main():
    init_db()
    start_background_poller()

    st.title("🇮🇳 INR Price Monitor & Alerts")
    st.markdown("*Health:* App loaded. Background poller running in the background.")

    with st.sidebar.form("add_form", clear_on_submit=True):
        name = st.text_input("Product Name (optional)")
        url = st.text_input("Product URL (full link)")
        target = st.number_input("Target Price (₹)", min_value=0.0, step=1.0)
        submitted = st.form_submit_button("Start Tracking")
        if submitted:
            if not url:
                st.warning("Please provide a URL.")
            else:
                add_item(url, name, target)
                st.success("Added to watchlist. Initial fetch launched.")

    # Top summary
    df = get_dashboard_data()
    if not df.empty:
        deals = df[(df['price'].notna()) & (df['price'] > 0) & (df['target_price'] > 0) & (df['price'] <= df['target_price'])]
        if not deals.empty:
            st.success(f"🎉 {len(deals)} item(s) are currently at or below your target!")

    st.subheader("Watchlist")
    if df.empty:
        st.info("No tracked items yet.")
    else:
        for _, row in df.iterrows():
            item_id = row['id']
            name = row['name'] or row['url']
            url = row['url']
            price = row['price'] if 'price' in row and not pd.isna(row['price']) else None
            target = row['target_price'] if 'target_price' in row else 0
            last_checked = row['checked_at'] if 'checked_at' in row and not pd.isna(row['checked_at']) else None

            status = "⚪"
            if price and price > 0:
                if target > 0 and price <= target:
                    status = "🟢 DEAL"
                elif target > 0:
                    status = "🔴"

            price_fmt = f"₹{price:,.2f}" if price and price > 0 else "..."
            target_fmt = f"₹{target:,.2f}" if target and target > 0 else "No target"

            header = f"{status}  *{name}* — {price_fmt} (Target: {target_fmt})"
            with st.expander(header, expanded=False):
                c1, c2 = st.columns([1,2])
                with c1:
                    st.markdown(f"*URL:* [Open link]({url})")
                    st.markdown(f"*Last Checked:* {pretty_time(last_checked)}")
                    # update target
                    new_target = st.number_input("Update Target (₹)", value=float(target or 0.0), key=f"t_{item_id}")
                    if st.button("Update Target", key=f"u_{item_id}"):
                        conn = get_db_connection()
                        conn.execute("UPDATE items SET target_price = ? WHERE id = ?", (new_target, item_id))
                        conn.commit()
                        conn.close()
                        st.experimental_rerun()
                    if st.button("Delete", key=f"d_{item_id}"):
                        conn = get_db_connection()
                        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
                        conn.execute("DELETE FROM prices WHERE item_id = ?", (item_id,))
                        conn.commit()
                        conn.close()
                        st.experimental_rerun()
                with c2:
                    hist = get_item_history(item_id)
                    if not hist.empty:
                        hist = hist[hist['price'] > 0].copy()
                        if not hist.empty:
                            hist['date'] = pd.to_datetime(hist['checked_at'], unit='s')
                            display = hist.sort_values('date')
                            st.line_chart(display.set_index('date')['price'])
                            st.caption(f"Min recorded: ₹{display['price'].min():,.2f}")
                    else:
                        st.write("No history yet.")

if __name__ == "__main__":
    main()
