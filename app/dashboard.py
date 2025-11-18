# app/dashboard.py  — HEALTH CHECK (temporary)
import streamlit as st
import os
import requests

st.set_page_config(page_title="Health Check", layout="centered")
st.title("Streamlit Health Check ✅")
st.write("If you see this, Streamlit is running and imports succeed.")

# Show a tiny secrets check (safe)
bot = None
chat = None
try:
    bot = st.secrets.get("telegram_bot_token") or st.secrets.get("TELEGRAM_BOT_TOKEN")
    chat = st.secrets.get("telegram_chat_id") or st.secrets.get("TELEGRAM_CHAT_ID")
except Exception:
    bot = None
    chat = None

st.write("Bot token present?:", bool(bot))
st.write("Chat id present?:", bool(chat))

if st.button("Call Telegram getUpdates (debug)"):
    if not bot:
        st.error("Bot token not set in secrets.")
    else:
        try:
            r = requests.get(f"https://api.telegram.org/bot{bot}/getUpdates", timeout=6)
            try:
                r.raise_for_status()
                st.json(r.json())
            except Exception:
                st.write("Response:", r.text[:2000])
        except Exception as e:
            st.error("Request error: " + str(e))

st.write("---")
st.write("When this page loads, we know the environment is fine. Reply here after it loads.")
