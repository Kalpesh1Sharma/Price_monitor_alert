from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import random

def get_flipkart_price(url):
    try:
        # Setup Chrome options
        options = Options()
        # Uncomment below line to run without opening browser window
        # options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument('--disable-blink-features=AutomationControlled')

        # Start Chrome driver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(url)

        # 💤 Wait like a human before scraping
        sleep_time = random.uniform(3, 6)
        print(f"⏳ Sleeping for {round(sleep_time, 2)} seconds to avoid detection...")
        time.sleep(sleep_time)

        # Wait for the title to appear
        wait = WebDriverWait(driver, 10)

        # 🆕 Updated Flipkart class names
        title_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "VU-ZEz")))
        title = title_elem.text.strip()

        price_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "Nx9bqj")))
        price_text = price_elem.text.strip()

        # Clean ₹ symbol and commas
        price = float(price_text.replace("₹", "").replace(",", ""))

        driver.quit()
        return title, price

    except Exception as e:
        print("⚠️ Selenium scraping error:", e)
        driver.quit()
        return None, None
