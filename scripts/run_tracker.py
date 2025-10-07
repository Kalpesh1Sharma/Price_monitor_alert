import csv
from tracker import track_price

# Flipkart product URL
with open('app/products.csv', newline='', encoding='utf-8') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        name = row['product_name']
        url = row['url']
        target = float(row['target_price'])
        print(f"\n🔎 Checking: {name}")
        track_price(url, target)


