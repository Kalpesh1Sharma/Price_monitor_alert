# app/dashboard.py
# Safe, robust Streamlit dashboard for price check + simple tracking (SQLite).
# Paste this whole file and redeploy.

import re
import os
import sqlite3
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st

# -----------------------
# CONFIG
# -----------------------
st.set_page_config(page_title="Price Monitor", layout="centered")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
# DB file (use env var if provided)
DB_PATH = os.environ.get("TRACKER_DB", "tracked.db")
REQUEST_TIMEOUT = 12  # seconds

# -----------------------
# HELPERS / SCRAPERS (defined before UI)
# -----------------------
def parse_price_string(price_str: str):
    """Return float or None from strings like '₹ 12,345.00' or '12,345'."""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.,]", "", price_str).strip()
    if "." in cleaned and "," in cleaned:
        # assume commas are thousand separators
        cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except Exception:
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
    price_text = None
    for sel in price_selectors:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            break

    img_el = soup.select_one("#landingImage") or soup.select_one("#imgTagWrapperId img")
    img = img_el.get("src") if img_el and img_el.has_attr("src") else None

    return title, price_text, img

def get_flipkart_data(html):
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("span.B_NuCI") or soup.select_one("._35KyD6")
    title = title_el.get_text(strip=True) if title_el else None

    price_el = soup.select_one("div._30jeq3._16Jk6d") or soup.select_one("div._30jeq3")
    price_text = price_el.get_text(strip=True) if price_el else None

    img_el = soup.select_one("img._2r_T1I") or soup.select_one("img._396cs4")
    img = img_el.get("src") if img_el and img_el.has_attr("src") else None

    return title, price_text, img

def fetch_product(url: str):
    """
    Safe fetch wrapper. Returns (title, price_float_or_None, img_url_or_None, raw_price_text_or_None, error_or_None)
    """
    if not url or not url.startswith("http"):
        return None, None, None, None, "Please provide a full URL (starting with http/https)."

    domain = urlparse(url).netloc.lower()
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return None, None, None, None, f"Request error: {e}"

    html = r.text

    try:
        if "amazon." in domain:
            title, raw_price, img = get_amazon_data(html)
        elif "flipkart." in domain:
            title, raw_price, img = get_flipkart_data(html)
        else:
            soup = BeautifulSoup(html, "lxml")
            t = soup.select_one("meta[property='og:title']") or soup.select_one("title")
            title = t.get("content") if (t and t.has_attr("content")) else (t.get_text() if t else None)
            p = soup.select_one("meta[property='product:price:amount']") or soup.select_one("[class*='price']")
            raw_price = p.get("content") if (p and p.has_attr("content")) else (p.get_text() if p else None)
            img_tag = soup.select_one("meta[property='og:image']")
            img = img_tag.get("content") if (img_tag and img_tag.has_attr("content")) else None
    except Exception as e:
        return None, None, None, None, f"Parsing error: {e}"

    price = parse_price_string(raw_price) if raw_price else None
    return title, price, img, raw_price, None

# -----------------------
# SQLITE DB HELPERS
# -----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tracked_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        title TEXT,
        target_price REAL,
        last_price REAL,
        last_checked TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def add_tracked_product(url, title, target_price, last_price=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tracked_products (url, title, target_price, last_price, last_checked) VALUES (?, ?, ?, ?, ?)",
        (url, title, target_price, last_price, None),
    )
    conn.commit()
    conn.close()

def list_tracked_products():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, url, title, target_price, last_price, last_checked FROM tracked_products ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_tracked_product(item_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tracked_products WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

# Initialize DB quickly (fast operation)
init_db()

# -----------------------
# STREAMLIT UI
# -----------------------
st.title("🔎 Price Checker & Tracker")

# small health check visible at top so blank-screen issues are obvious
st.markdown("*Health:* App loaded. If you see nothing below, check logs.")

col1, col2 = st.columns([8,2])
with col1:
    url = st.text_input("Product URL", placeholder="https://www.amazon.in/... or https://www.flipkart.com/...")
with col2:
    st.write("")  # spacer

target_price_str = st.text_input("Target price (₹) — numbers only", value="")
check_now = st.button("Check price")
track_now = st.button("Track this product")

# Check Price handler (only runs on button click)
if check_now:
    with st.spinner("Fetching product... (this runs once)"):
        title, price, img, raw_price_text, error = fetch_product(url)
    if error:
        st.error(error)
    else:
        if title:
            st.subheader(title)
        if img:
            try:
                st.image(img, width=320)
            except Exception:
                st.write("Image found but failed to display.")
        if price is not None:
            st.metric(label="Current price (approx.)", value=f"₹ {price:,.2f}")
            st.write("Raw extracted price text:", raw_price_text)
        else:
            st.warning("Price not detected automatically. Raw text (if any):")
            st.write(raw_price_text)

# Track handler (stores in SQLite)
if track_now:
    if not url:
        st.error("Paste a product URL first.")
    else:
        with st.spinner("Fetching product to save..."):
            title, price, img, raw_price_text, error = fetch_product(url)
        if error:
            st.error(f"Cannot fetch product: {error}")
        else:
            # validate target price
            try:
                tp = float(target_price_str.replace(",", "").strip())
            except Exception:
                st.error("Enter a valid target price — numbers only, e.g., 1299.50")
            else:
                add_tracked_product(url, title or "", tp, price)
                st.success(f"Saved tracking: {title or url}\nTarget: ₹ {tp:,.2f}")

st.markdown("---")
st.header("Active tracked products")
rows = list_tracked_products()
if not rows:
    st.info("No products tracked yet. Add one above.")
else:
    for r in rows:
        item_id, r_url, r_title, r_target, r_last_price, r_last_checked = r
        st.write(f"{r_title or r_url}")
        st.write(r_url)
        st.write(f"Target: ₹ {r_target:,.2f} — Last price: {('₹ %.2f' % r_last_price) if r_last_price else 'N/A'}")
        if st.button("Delete", key=f"del-{item_id}"):
            delete_tracked_product(item_id)
            st.experimental_rerun()

st.caption("Tip: The background tracker scripts/tracker.py handles periodic checks & Telegram notifications. This UI only adds/removes tracked items.")
