# app/dashboard.py
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

st.set_page_config(page_title="Price Monitor", layout="centered")

st.title("🔎 Price Checker")
st.write("Paste a product URL (Amazon / Flipkart) and click Check price")

url = st.text_input("Product URL", placeholder="https://www.amazon.in/.... or https://www.flipkart.com/....")
check_now = st.button("Check price")

def parse_price_string(price_str: str):
    """
    Turn strings like '₹ 1,234.00' or '1,234' into float 1234.0
    """
    if not price_str:
        return None
    # Keep digits and dot
    cleaned = re.sub(r"[^\d.,]", "", price_str).strip()
    # Replace common thousand separators then change comma decimal if needed:
    # If there are both comma and dot, assume dot decimal e.g. '1,234.56' -> remove commas
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(",", "")
    # If only commas and they look like decimal (rare for INR), convert
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except Exception:
        return None

def get_amazon_data(page_html):
    soup = BeautifulSoup(page_html, "lxml")
    # Title
    title = soup.select_one("#productTitle")
    title_text = title.get_text(strip=True) if title else None

    # Price: try common selectors
    price_selectors = [
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.a-price > span.a-offscreen",  # generic
    ]
    price_text = None
    for sel in price_selectors:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            break

    # Image
    img = soup.select_one("#landingImage") or soup.select_one("#imgTagWrapperId img")
    img_src = img["src"] if img and img.has_attr("src") else None

    return title_text, price_text, img_src

def get_flipkart_data(page_html):
    soup = BeautifulSoup(page_html, "lxml")
    # Title
    title = soup.select_one("span.B_NuCI") or soup.select_one("._35KyD6")
    title_text = title.get_text(strip=True) if title else None

    # Price
    price = soup.select_one("div._30jeq3._16Jk6d") or soup.select_one("div._30jeq3")
    price_text = price.get_text(strip=True) if price else None

    # Image
    img = soup.select_one("img._2r_T1I") or soup.select_one("img._396cs4")
    img_src = img["src"] if img and img.has_attr("src") else None

    return title_text, price_text, img_src

def fetch_product(url):
    domain = urlparse(url).netloc.lower()
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return None, None, None, f"Request failed: {e}"

    html = r.text
    if "amazon." in domain:
        title, price_text, img = get_amazon_data(html)
    elif "flipkart." in domain:
        title, price_text, img = get_flipkart_data(html)
    else:
        # Generic attempt: try to find meta tags
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.select_one("meta[property='og:title']") or soup.select_one("title")
        title = title_tag.get("content") if title_tag and title_tag.has_attr("content") else (title_tag.get_text() if title_tag else None)
        price_tag = soup.select_one("meta[property='product:price:amount']") or soup.select_one(".price")
        price_text = price_tag.get("content") if price_tag and price_tag.has_attr("content") else (price_tag.get_text() if price_tag else None)
        img_tag = soup.select_one("meta[property='og:image']")
        img = img_tag.get("content") if img_tag and img_tag.has_attr("content") else None

    price = parse_price_string(price_text) if price_text else None
    return title, price, img, None

if check_now:
    if not url:
        st.error("Please paste a product URL first.")
    else:
        with st.spinner("Fetching product..."):
            title, price, img, error = fetch_product(url)
            time.sleep(0.5)  # small pause so UI feels responsive

        if error:
            st.error(error)
        else:
            if title:
                st.subheader(title)
            if img:
                st.image(img, width=300)
            if price is not None:
                st.metric(label="Current price (approx.)", value=f"₹ {price:,.2f}")
            else:
                st.warning("Couldn't detect price automatically. Try a different product page or send me the URL and I'll help adapt the scraper.")

            # show raw price text (helpful for debugging)
            st.caption("Raw extracted price text shown below (useful for debugging selectors):")
            st.write("price:", price)
