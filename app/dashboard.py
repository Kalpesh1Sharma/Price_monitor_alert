st.markdown("---")
st.header("🔧 Telegram & Debug")

st.write("Bot token set?:", bool(TELEGRAM_BOT_TOKEN))
st.write("Chat id set?:", bool(TELEGRAM_CHAT_ID))

if st.button("Test: call getUpdates"):
    if not TELEGRAM_BOT_TOKEN:
        st.error("Set TELEGRAM_BOT_TOKEN first")
    else:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", timeout=8)
            st.json(r.json())
        except Exception as e:
            st.error(f"getUpdates failed: {e}")

test_msg = st.text_input("Test message text", value="Hello from app (test)")
if st.button("Send test Telegram message"):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        st.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    else:
        try:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": test_msg, "parse_mode":"HTML"}
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)
            st.write("Status:", r.status_code)
            st.json(r.json())
            if r.status_code == 200 and r.json().get("ok"):
                st.success("Message sent (check Telegram)")
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
        st.dataframe(dfp[['item_id','time','price']])
except Exception as e:
    st.write("Error reading prices:", e)

# Show items last_alert_at
st.markdown("**Tracked items (last_alert_at):**")
try:
    conn = get_db_connection()
    dfi = pd.read_sql("SELECT id, name, target_price, last_alert_at FROM items", conn)
    conn.close()
    if dfi.empty:
        st.write("No items.")
    else:
        dfi['last_alert_at_human'] = dfi['last_alert_at'].apply(lambda v: datetime.fromtimestamp(v).strftime('%Y-%m-%d %H:%M') if v and v>0 else "Never")
        st.dataframe(dfi[['id','name','target_price','last_alert_at_human']])
except Exception as e:
    st.write("Error reading items:", e)
