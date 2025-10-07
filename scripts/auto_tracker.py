import csv
import schedule
import time
from scripts.tracker import track_price



def check_all_products():
    print(f"\n🕒 Checking prices at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    with open('app/products.csv', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            name = row['product_name']
            url = row['url']
            target = float(row['target_price'])
            print(f"\n🔎 Checking: {name}")
            track_price(url, target)

# 🔁 Schedule it: every X minutes/hours
schedule.every(2).hours.do(check_all_products)
# You can also try: schedule.every(30).minutes.do(...)

# 🔁 Initial run
check_all_products()

# 🕰️ Keep running forever
while True:
    schedule.run_pending()
    time.sleep(60)
