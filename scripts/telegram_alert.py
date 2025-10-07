import requests

BOT_TOKEN = "8231783905:AAGCJSbtJrNp0j4YG6-vSFuV5pYtMSz_yPo"
CHAT_ID = "2096012658"

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
