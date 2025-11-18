import time
import sqlite3
import requests
import re
from datetime import datetime

DB_FILE = "prices.db"
POLL_DELAY = 600   # 10 minutes
COOLDOWN = 86400   # 24 hrs cooldown

import os
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

headers = {
    "User-Agent": "Mozilla/5.0"
}

def db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def extract_price(html):
    html = re.sub("<[^>]+>", " ", html)
    m = re.findall(r"₹\s?([\d,]+\.?\d*)", html)
    if not m:
        return None
    return float(m[0].replace(",", ""))

def send_alert(name, price, url, target):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram vars missing")
        return
    
    msg = f"🚨 PRICE DROP!\n\n{name}\nCurrent: ₹{price}\nTarget: ₹{target}\n{url}"
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg}
    )
    print("Sent alert:", r.text)

def check_once():
    conn = db_conn()
    items = conn.execute("SELECT * FROM items").fetchall()

    for it in items:
        print("Checking:", it["name"])
        try:
            html = requests.get(it["url"], headers=headers, timeout=10).text
            price = extract_price(html)

            # record price
            conn.execute("INSERT INTO prices(item_id, checked_at, price) VALUES(?,?,?)",
                         (it["id"], time.time(), price or -1))
            conn.commit()

            # alert logic
            if price and price <= it["target_price"]:
                if time.time() - it["last_alert_at"] > COOLDOWN:
                    send_alert(it["name"], price, it["url"], it["target_price"])
                    conn.execute("UPDATE items SET last_alert_at=? WHERE id=?",
                                 (time.time(), it["id"]))
                    conn.commit()

        except Exception as e:
            print("Error:", e)

    conn.close()

def main():
    print("Worker running...")
    while True:
        check_once()
        time.sleep(POLL_DELAY)

if __name__ == "__main__":
    main()
