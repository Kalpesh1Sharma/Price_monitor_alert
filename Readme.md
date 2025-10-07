# 📉 Real-Time Price Monitoring & Alert System

A full-stack Python project that tracks product prices (e.g., Flipkart/Amazon), alerts users via Telegram when deals are found, stores price history in a database, and displays a beautiful web dashboard — all deployed to the cloud using Railway.

---

## 🚀 Features

- ✅ Real-time price scraping using Selenium
- ✅ Multi-product tracking via CSV
- ✅ Deal alerts via Telegram bot
- ✅ Scheduled scraping using `schedule` + Railway Cron Jobs
- ✅ Streamlit dashboard with current prices and alert status
- ✅ SQLite-based price history tracking
- ✅ Secure `.env` file for tokens and credentials
- ✅ Cloud deployment using [Railway.app](https://railway.app)

---

## 📸 Preview

<!-- Optional: Add screenshot of your dashboard -->
<!-- ![Dashboard Preview](https://your-screenshot-url.png) -->

---

## 🔧 Technologies Used

| Layer        | Tools & Tech                        |
|--------------|-------------------------------------|
| Scraping     | `Selenium`, `lxml`, `BeautifulSoup` |
| Automation   | `schedule`, `cron jobs`             |
| Alerts       | `Telegram Bot API`                  |
| Database     | `SQLite`                            |
| Dashboard    | `Streamlit`                         |
| Deployment   | `Railway.app`                       |
| Secrets Mgmt | `python-dotenv`, `.env` files       |

---

## 🛠️ How It Works

1. Add products to `products.csv` with:
   - `product_name`, `url`, `target_price`
2. Run `auto_tracker.py` to check prices periodically (scheduled with Railway)
3. If a product price drops below target, a **Telegram alert** is sent
4. All prices are saved to `price_tracker.db`
5. Streamlit dashboard (`dashboard.py`) shows current status

---

## 📁 Folder Structure

📁 Price_Monitor/
├── dashboard.py # Streamlit web dashboard
├── auto_tracker.py # Periodic price checker (cron)
├── tracker.py # Main logic to scrape, compare, log
├── scraper.py # Selenium-based scraper
├── telegram_alert.py # Sends Telegram messages
├── products.csv # Product URLs + target prices
├── price_tracker.db # SQLite DB for price history
├── .env # Secret tokens (excluded via .gitignore)
├── Procfile # For Railway deployment
└── requirements.txt # Python dependencies


---

## 📦 Deployment (Railway)

> This app is fully deployed to [Railway.app](https://railway.app)

- 📊 Dashboard runs via `dashboard.py`
- 🔁 Background job (every X hours) runs `auto_tracker.py`
- 📥 `.env` variables are stored in Railway’s Secrets tab:
  - `BOT_TOKEN`
  - `CHAT_ID`

---

## 📬 Setup Guide (Local)

1. **Clone the repo**
```bash
git clone https://github.com/your-username/price-monitor.git
cd price-monitor
```

2. **Install dependencies** 
```bash
pip install -r requirements.txt
```
3. **Create .env**
```bash
BOT_TOKEN=your_telegram_bot_token
CHAT_ID=your_telegram_chat_id
```
4. **Add products to products.csv**

5. **Run Tracker**
```bash
python auto_tracker.py
```
6. **Run Dashboard**
```bash
streamlit run dashboard.py
```
## 🛡️ Security Notes

- 🔒 Never commit your `.env` file to GitHub. It contains sensitive data like your Telegram bot token and chat ID.
- ✅ Add `.env` to your `.gitignore` file to prevent accidental upload.
- 🔑 Use Railway’s **"Environment Variables"** feature to securely store secrets in the cloud.
- 🧪 Rotate your bot token if it's ever exposed.

## 💡 Future Improvements

- [ ] 📧 Add Email alert support (Gmail or Outlook)
- [ ] 📊 Show price trend graphs using Plotly/Matplotlib
- [ ] 📤 Export CSV reports from dashboard
- [ ] 📥 Connect to Google Sheets for product input
- [ ] 🧑‍💻 Allow users to add/remove products from dashboard
- [ ] 🔁 Add retry logic + proxy rotation for scraping
- [ ] 🐳 Dockerize the app for containerized deployment
- [ ] 🌍 Add currency and region selection support

## 👨‍💻 Author

**Kalpesh Sharma**  
_Data Engineer & Automation Enthusiast_

- 🔗 [GitHub](https://github.com/Kalpesh1Sharma)
- 🔗 [LinkedIn](https://www.linkedin.com/in/KalpeshSharma862/)

---

> If you liked this project, don’t forget to ⭐ star the repo and share it with others!

