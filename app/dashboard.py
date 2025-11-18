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
st.set_page_config(page_title="Amazon Price Monitor", layout="wide")
DB_FILE = os.environ.get("DB_FILE", "prices.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 3600))      # seconds (default 1 hour)
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", 86400))  # seconds (default 24 hours)
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 15))

# Read secrets from st.secrets (support both naming styles)
def load_secrets():
    # Support `telegram_bot_token` or `TELEGRAM_BOT_TOKEN` in secrets.toml
    bot = None
    chat = None
    try:
        bot = st.secrets.get("telegram_bot_token") or st.secrets.get("TELEGRAM_BOT_TOKEN")
        chat = st.secrets.get("telegram_chat_id") or st.secrets.get("TELEGRAM_CHAT_ID")
    except Exception:
        bot = None
        chat = None
    # If chat is numeric in secrets, convert to str
    if chat is not None:
        chat = str(chat)
    return bot, chat

TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = load_secrets()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
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
    # try migration safely (if columns missing)
    cols = [row[1] for row in c.execute("PRAGMA table_info(items)")]
    if 'target_price' not in cols:
        try:
            c.execute('ALTER TABLE items ADD COLUMN target_price REAL DEFAULT 0')
        except Exception:
            pass
    if 'last_alert_at' not in cols:
        try:
            c.execute('ALTER TABLE items ADD COLUMN last_alert_at REAL DEFAULT 0')
        except Exception:
            pass
    conn.commit()
    conn.close()

# -----------------------
# TELEGRAM
# -----------------------
def escape_html(s: str):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_telegram_alert(message: str):
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    # reload secrets if missing (handy if changed in UI)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = load_secrets()
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing.")
        return False, "Missing secrets"

    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(api, json=payload, timeout=10)
        data = r.json() if r.content else {"ok": False, "status_code": r.status_code}
        if r.status_code == 200 and data.get("ok"):
            return True, data
        else:
            # return message to UI/logs for debugging
            print("Telegram send failed:", r.status_code, data)
            return False, data
    except Exception as e:
        print("Telegram request exception:", e)
        return False, str(e)

# -----------------------
# SCRAPING
# -----------------------
def extract_price(html_text: str):
    """Extract a float price from text/html. Returns float or None."""
    if not html_text:
        return None
    # Remove script/style blocks and tags
    html_text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_text)
    text = re.sub(r"<[^>]+>", " ", html_text)
    # Look for patterns with rupee symbol or plain numbers with commas/decimal
    # Matches ₹ 1,234.56 or 1,234 or 1234.56
    matches = re.findall(r'([₹\u20B9]?\s?[\d{1,3},]+(?:\.\d{1,2})?)', text)
    if not matches:
        matches = re.findall(r'(\d{1,3}(?:[,\d]{0,})?(?:\.\d{1,2})?)', text)
    for m in matches:
        cleaned = re.sub(r'[^\d.]', '', m)
        if not cleaned:
            continue
        try:
            val = float(cleaned)
            # ignore unrealistic small numbers or years
            if val > 10:
                return val
        except:
            continue
    return None

def get_amazon_data(html):
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el else None
    # pick common price selectors
    price_selectors = [
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "#priceblock_saleprice",
        "span.a-price > span.a-offscreen"
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

def fetch_price_and_title(url: str):
    """Return (title, price_float_or_None, raw_price_text_or_None, error_or_None)"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return None, None, None, f"Request failed: {e}"

    html = r.text
    domain = urlparse(url).netloc.lower()
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
        # update stored name if better title found
        if title:
            conn = get_db_connection()
            conn.execute("UPDATE items SET name = ? WHERE id = ?", (title, item_id))
            conn.commit()
            conn.close()

        # alert logic (safe checks)
        if price is not None and price > 0 and target_price and target_price > 0:
            if price <= target_price and (now - (last_alert_at or 0) > ALERT_COOLDOWN):
                msg = f"🚨 *PRICE DROP!* \n\n*{name or title or url}*\nPrice: ₹{price:,.2f}\nTarget: ₹{target_price:,.2f}\n{url}"
                ok, resp = send_telegram_alert(msg)
                if ok:
                    conn = get_db_connection()
                    conn.execute("UPDATE items SET last_alert_at = ? WHERE id = ?", (now, item_id))
                    conn.commit()
                    conn.close()
                else:
                    print("Telegram not sent. Resp:", resp)
        return True
    except Exception as e:
        print("Error in fetch_and_analyze:", e)
        return False

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
# BACKGROUND POLLER
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
    # initial fetch in background (non-blocking)
    Thread(target=fetch_and_analyze, args=(item_id, url, final_name, float(target or 0), 0), daemon=True).start()
    return item_id

def main():
    init_db()
    start_background_poller()

    st.title("⚡ Amazon Price Monitor")
    st.markdown("**Health:** App loaded. Background poller running.")

    # sidebar: add tracker + telegram test + debug secrets
    with st.sidebar:
        st.header("Controls")
        with st.form("add_form", clear_on_submit=True):
            st.subheader("Add Tracker")
            name = st.text_input("Product Name (optional)")
            url = st.text_input("URL")
            target = st.number_input("Target Price (₹)", min_value=0.0)
            if st.form_submit_button("Start Tracking"):
                if url:
                    add_item(url, name, target)
                    st.success("Added and initial fetch started.")
                    time.sleep(0.4)
                    st.rerun()

        st.divider()
        st.subheader("Telegram Setup / Debug")
        # Show whether secrets are present
        bot, chat = load_secrets()
        st.write("Bot token present?:", bool(bot))
        st.write("Chat id present?:", bool(chat))
        if bot:
            st.write("Bot token starts with:", (bot[:8] + "..."))
        if chat:
            st.write("Chat id:", chat)

        if st.button("Test Telegram Connection"):
            ok, resp = send_telegram_alert("✅ Test message from Price Monitor")
            if ok:
                st.success("Message Sent! Check Telegram.")
            else:
                st.error("Failed. See logs and response below.")
                st.write(resp)

    # main: watchlist
    df = get_dashboard_data()
    if df.empty:
        st.info("No items tracked. Add one in the sidebar.")
    else:
        for _, row in df.iterrows():
            item_id = row['id']
            name = row['name'] or row['url']
            url = row['url']
            price = row['price'] if 'price' in row and not pd.isna(row['price']) else None
            target = row['target_price'] if 'target_price' in row else 0
            last_checked = row['checked_at'] if 'checked_at' in row and not pd.isna(row['checked_at']) else None

            status = "⚪ ERROR / PENDING"
            if price is not None and price > 0:
                if target > 0 and price <= target:
                    status = f"🟢 DEAL (₹{price:,.2f})"
                else:
                    status = f"🔴 HIGH (₹{price:,.2f})"

            header = f"{status}  **{name}**"
            with st.expander(header, expanded=False):
                c1, c2 = st.columns([1.5, 1])
                with c1:
                    st.markdown(f"**Target:** ₹{target:,.2f} | **Current:** {('₹{:.2f}'.format(price)) if price else '...'}")
                    st.markdown(f"**Last Checked:** {pretty_time(last_checked)}")
                    st.markdown(f"[Open Product]({url})")
                    if st.button("Check Now", key=f"fetch_{item_id}"):
                        with st.spinner("Fetching..."):
                            success = fetch_and_analyze(item_id, url, name, target, 0)
                            if success:
                                st.success("Updated!")
                            else:
                                st.error("Failed to fetch. Check logs or product page.")
                        time.sleep(0.6)
                        st.rerun()
                    if st.button("Delete", key=f"del_{item_id}"):
                        conn = get_db_connection()
                        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
                        conn.execute("DELETE FROM prices WHERE item_id = ?", (item_id,))
                        conn.commit()
                        conn.close()
                        st.rerun()
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
