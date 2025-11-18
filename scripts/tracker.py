# scripts/tracker.py
import os
import time
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Config from environment
DB_PATH = os.environ.get("TRACKER_DB", "tracked.db")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 300))  # seconds, default 5 minutes
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch_product(url):
    # copy the same fetch_product logic as in dashboard
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return None, None, f"Request failed: {e}"

    html = r.text
    domain = urlparse(url).netloc.lower()
    # reuse selectors; minimal robust attempt:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    title = None
    price_text = None
    img = None

    if "amazon." in domain:
        t = soup.select_one("#productTitle")
        title = t.get_text(strip=True) if t else None
        price_selectors = ["#priceblock_ourprice", "#priceblock_dealprice", "span.a-price > span.a-offscreen"]
        for sel in price_selectors:
            el = soup.select_one(sel)
            if el:
                price_text = el.get_text(strip=True)
                break
        img_el = soup.select_one("#landingImage") or soup.select_one("#imgTagWrapperId img")
        img = img_el.get("src") if img_el and img_el.has_attr("src") else None

    elif "flipkart." in domain:
        t = soup.select_one("span.B_NuCI") or soup.select_one("._35KyD6")
        title = t.get_text(strip=True) if t else None
        p = soup.select_one("div._30jeq3._16Jk6d") or soup.select_one("div._30jeq3")
        price_text = p.get_text(strip=True) if p else None
        img_el = soup.select_one("img._2r_T1I") or soup.select_one("img._396cs4")
        img = img_el.get("src") if img_el and img_el.has_attr("src") else None
    else:
        title_tag = soup.select_one("meta[property='og:title']") or soup.select_one("title")
        if title_tag:
            title = title_tag.get("content") if title_tag.has_attr("content") else title_tag.get_text()
        ptag = soup.select_one("meta[property='product:price:amount']") or soup.select_one(".price") or soup.select_one("[class*='price']")
        if ptag:
            price_text = ptag.get("content") if ptag.has_attr("content") else ptag.get_text()
        img_tag = soup.select_one("meta[property='og:image']")
        img = img_tag.get("content") if img_tag and img_tag.has_attr("content") else None

    price = parse_price_string(price_text) if price_text else None
    return title, price, None

def parse_price_string(price_str):
    if not price_str:
        return None
    import re
    cleaned = re.sub(r"[^\d.,]", "", price_str).strip()
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except:
        return None

# DB helpers
def get_all_tracked():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, url, title, target_price, last_price FROM tracked_products")
    rows = c.fetchall()
    conn.close()
    return rows

def update_last_price(item_id, price):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE tracked_products SET last_price = ?, last_checked = ? WHERE id = ?", (price, datetime.utcnow(), item_id))
    conn.commit()
    conn.close()

def delete_by_id(item_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tracked_products WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

# Telegram helper
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing; cannot send message.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        r = requests.post(url, data=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print("Failed to send telegram:", e)
        return False

# Main loop
if _name_ == "_main_":
    print("Starting price tracker. Poll every", CHECK_INTERVAL, "seconds.")
    while True:
        try:
            items = get_all_tracked()
            if not items:
                print("No items tracked. Sleeping...")
            for item in items:
                item_id, url, title, target, last_price = item
                print("Checking:", title or url, "target:", target)
                t, price, err = None, None, None
                try:
                    t, price, e = fetch_product(url)
                    if price is None:
                        print("Could not parse price for", url)
                        # update last checked but keep the item in DB
                        update_last_price(item_id, last_price)
                        continue
                except Exception as ex:
                    print("Error fetching:", ex)
                    continue

                update_last_price(item_id, price)
                # Trigger notification if price <= target
                if price is not None and target is not None and price <= target:
                    title_text = t or title or url
                    message = f"🔥 Price Alert!\n{title_text}\nCurrent price: ₹{price:,.2f}\nTarget price: ₹{target:,.2f}\n{url}"
                    ok = send_telegram_message(message)
                    if ok:
                        print("Sent telegram for", url)
                        # Option A: delete entry after notifying once (uncomment if you prefer)
                        # delete_by_id(item_id)
                        # Option B: keep it but you may want to set new flag; for simplicity we delete:
                        delete_by_id(item_id)
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("Stopping tracker.")
            break
        except Exception as e:
            print("Tracker loop error:", e)
            time.sleep(30)
