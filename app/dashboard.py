import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def parse_price_string(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.,]", "", price_str)
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except:
        return None

def fetch_product(url):
    domain = urlparse(url).netloc.lower()

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return None, None, None, f"Request failed: {e}"

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    # AMAZON
    if "amazon" in domain:
        title = soup.select_one("#productTitle")
        title = title.get_text(strip=True) if title else None

        price = soup.select_one("span.a-price span.a-offscreen")
        price = price.get_text(strip=True) if price else None

        img = soup.select_one("#landingImage")
        img = img["src"] if img else None

        return title, parse_price_string(price), img, None

    # FLIPKART
    if "flipkart" in domain:
        title = soup.select_one("span.B_NuCI")
        title = title.get_text(strip=True) if title else None

        price = soup.select_one("div._30jeq3._16Jk6d")
        price = price.get_text(strip=True) if price else None

        img = soup.select_one("img._2r_T1I")
        img = img["src"] if img else None

        return title, parse_price_string(price), img, None

    # GENERIC WEBSITE
    title = soup.select_one("meta[property='og:title']") or soup.select_one("title")
    if title:
        title = title.get("content") if title.has_attr("content") else title.get_text()

    price = soup.select_one("meta[property='product:price:amount']")

    price = price.get("content") if price and price.has_attr("content") else None

    img = soup.select_one("meta[property='og:image']")
    img = img.get("content") if img and img.has_attr("content") else None

    return title, parse_price_string(price), img, None
