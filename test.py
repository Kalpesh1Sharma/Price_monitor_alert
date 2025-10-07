from scraper import get_flipkart_price

url = "https://www.flipkart.com/realme-narzo-60x-5g-stellar-green-128-gb/p/itm906a3c913a97f"
title, price = get_flipkart_price(url)

if title and price:
    print(f"✅ {title} - ₹{price}")
else:
    print("❌ Could not fetch product info.")
