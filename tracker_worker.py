# tracker_worker.py  — safe drop-in for Render
import os
import time
import sqlite3
import requests
import re

DB_FILE = os.environ.get("DB_FILE", "prices.db")
POLL_DELAY = int(os.environ.get("POLL_DELAY", 600))   # seconds
COOLDOWN = int(os.environ.get("COOLDOWN", 86400))    # seconds

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------- DB helpers ----------
def db_conn():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist (safe to run every start)."""
    conn = db_conn()
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            target_price REAL,
            last_alert_at REAL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            checked_at REAL,
            price REAL
        )
    """)
    conn.commit()
    conn.close()

# ---------- scraping and alert ----------
def extract_price(html):
    html = re.sub("<[^>]+>", " ", html)
    m = re.findall(r"₹\s?([\d,]+\.?\d*)", html)
    if not m:
        return None
    try:
        return float(m[0].replace(",", ""))
    except:
        return None

def send_alert(name, price, url, target):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram vars missing")
        return
    msg = f"🚨 PRICE DROP!\n\n{name}\nCurrent: ₹{price}\nTarget: ₹{target}\n{url}"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
        print("Alert response:", r.status_code, r.text[:500])
    except Exception as e:
        print("Failed to send alert:", e)

def check_once():
    conn = db_conn()
    try:
        items = conn.execute("SELECT * FROM items").fetchall()
    except Exception as e:
        print("DB read error in check_once:", e)
        conn.close()
        return

    for it in items:
        print("Checking:", it["name"])
        try:
            r = requests.get(it["url"], headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"Fetch failed {r.status_code} for {it['url']}")
                continue
            price = extract_price(r.text)
            conn.execute("INSERT INTO prices(item_id, checked_at, price) VALUES(?,?,?)",
                         (it["id"], time.time(), price if price is not None else -1))
            conn.commit()

            if price is not None and price <= (it["target_price"] or 0) and (time.time() - (it["last_alert_at"] or 0) > COOLDOWN):
                send_alert(it["name"], price, it["url"], it["target_price"])
                conn.execute("UPDATE items SET last_alert_at=? WHERE id=?", (time.time(), it["id"]))
                conn.commit()
        except Exception as e:
            print("Error checking item:", e)
    conn.close()

# ---------- main ----------
def main():
    print("Worker starting — DB_FILE:", DB_FILE)
    init_db()    # <<--- ensure tables exist
    while True:
        try:
            check_once()
        except Exception as e:
            print("Worker loop top-level error:", e)
        time.sleep(POLL_DELAY)

if __name__ == "__main__":
    main()
