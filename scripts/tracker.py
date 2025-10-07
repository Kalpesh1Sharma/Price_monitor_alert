from scraper import get_flipkart_price
import sqlite3
import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("✅ Telegram alert sent!")
    else:
        print("❌ Failed to send alert:", response.text)

# 🔁 Test it
send_telegram_alert("🚨 Test: Price drop detected!")

def track_price(url, target_price):
    # Step 1: Get product title and current price
    title, current_price = get_flipkart_price(url)

    if title is None or current_price is None:
        print("❌ Could not retrieve product info.")
        return

    # Step 2: Save to SQLite
    conn = sqlite3.connect('price_tracker.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            url TEXT NOT NULL,
            current_price REAL NOT NULL,
            target_price REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        INSERT INTO price_history (product_name, url, current_price, target_price)
        VALUES (?, ?, ?, ?)
    ''', (title, url, current_price, target_price))

    conn.commit()
    conn.close()

    # Step 3: Compare prices and notify
    if current_price <= target_price:
        print(f"🎉 DEAL! {title} is now ₹{current_price} (Target: ₹{target_price})")
        message = f"🎯 DEAL FOUND!\n\n{title}\nPrice: ₹{current_price}\nTarget: ₹{target_price}\n\n{url}"
        send_telegram_alert(message)
    else:
        print(f"📊 {title} is ₹{current_price} (Target: ₹{target_price})")


