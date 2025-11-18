# --- Telegram & debug panel (paste inside your Streamlit app) ---
import os
import re
import time
import uuid
import sqlite3
import requests
import pandas as pd

from datetime import datetime
from urllib.parse import urlparse
from threading import Thread

import streamlit as st
from bs4 import BeautifulSoup


# reload secrets (so updates in secrets.toml are reflected without restart)
def _load_secrets_safe():
    try:
        bot = st.secrets.get("telegram_bot_token") or st.secrets.get("TELEGRAM_BOT_TOKEN")
        chat = st.secrets.get("telegram_chat_id") or st.secrets.get("TELEGRAM_CHAT_ID")
    except Exception:
        bot, chat = None, None
    if chat is not None:
        chat = str(chat)
    return bot, chat

BOT, CHAT = _load_secrets_safe()

st.write("Bot token present?:", bool(BOT))
st.write("Chat id present?:", bool(CHAT))
if BOT:
    st.write("Bot token starts with:", (BOT[:8] + "..."))
if CHAT:
    st.write("Chat id:", CHAT)

# getUpdates button
if st.button("Test: call getUpdates"):
    if not BOT:
        st.error("Set TELEGRAM_BOT_TOKEN (secrets) first")
    else:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT}/getUpdates", timeout=8)
            # raise for status so HTTP errors are surfaced
            try:
                r.raise_for_status()
                # try to show pretty JSON, but catch decode errors
                try:
                    st.json(r.json())
                except Exception:
                    st.write("Response (non-JSON):", r.text[:2000])
            except requests.HTTPError as he:
                # Show full response text for debugging
                st.error(f"HTTP error: {he}")
                st.write("Response body:", r.text[:2000])
        except Exception as e:
            st.error(f"getUpdates failed: {e}")

# Send test message
test_msg = st.text_input("Test message text", value="Hello from app (test)")
if st.button("Send test Telegram message"):
    BOT, CHAT = _load_secrets_safe()  # reload in case secrets changed
    if not (BOT and CHAT):
        st.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in secrets.")
    else:
        payload = {"chat_id": CHAT, "text": test_msg, "parse_mode": "HTML"}
        try:
            r = requests.post(f"https://api.telegram.org/bot{BOT}/sendMessage", json=payload, timeout=8)
            # show response status & JSON (or text)
            st.write("Status:", r.status_code)
            try:
                st.json(r.json())
                ok = r.json().get("ok", False)
            except Exception:
                st.write("Response text:", r.text[:2000])
                ok = (r.status_code == 200)
            if ok:
                st.success("Message sent (check Telegram).")
            else:
                st.error("Failed to send. See response above.")
        except Exception as e:
            st.error(f"Send failed: {e}")

# Show recent prices for debugging
st.markdown("**Latest prices (debug):**")
try:
    conn = get_db_connection()
    dfp = pd.read_sql("SELECT item_id, checked_at, price FROM prices ORDER BY checked_at DESC LIMIT 20", conn)
    conn.close()
    if dfp.empty:
        st.write("No price records yet.")
    else:
        dfp['time'] = pd.to_datetime(dfp['checked_at'], unit='s')
        # show a truncated dataframe to avoid overwhelming UI
        st.dataframe(dfp[['item_id','time','price']].head(50))
except Exception as e:
    st.write("Error reading prices:", e)

# Show items and last_alert_at
st.markdown("**Tracked items (last_alert_at):**")
try:
    conn = get_db_connection()
    dfi = pd.read_sql("SELECT id, name, target_price, last_alert_at FROM items", conn)
    conn.close()
    if dfi.empty:
        st.write("No items.")
    else:
        # safely convert last_alert_at to human readable, handling nulls and zeros
        def _fmt_last(ts):
            try:
                if not ts or pd.isna(ts) or float(ts) <= 0:
                    return "Never"
                return datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                return "Invalid"
        dfi['last_alert_at_human'] = dfi['last_alert_at'].apply(_fmt_last)
        st.dataframe(dfi[['id','name','target_price','last_alert_at_human']])
except Exception as e:
    st.write("Error reading items:", e)
