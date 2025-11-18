# tracker.py
# Simple Price Tracker backend (Flask + SQLite + background poller)
# Endpoints:
#  GET  /health
#  POST /track            -> {"url": "...", "name": "..."}  returns id
#  GET  /prices           -> list of tracked items (latest snapshot included)
#  GET  /prices/<id>      -> detail and history
#  POST /fetch/<id>       -> trigger immediate fetch for id

import os
import time
import uuid
import sqlite3
import threading
import logging
import requests
import re
from flask import Flask, request, jsonify

# --- config & logging ---
DB_FILE = os.environ.get("DB_FILE", "prices.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 60))  # seconds between polls
PORT = int(os.environ.get("PORT", 5000))

LOG = logging.getLogger("tracker")
LOG.setLevel(logging.INFO)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
LOG.addHandler(h)

app = Flask(_name_)

# --- DB helpers ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # tracked items: id, name, url
    c.execute('''
      CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY,
        name TEXT,
        url TEXT
      )
    ''')
    # prices history: id, item_id, checked_at (epoch), price (float), raw_text
    c.execute('''
      CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT,
        checked_at REAL,
        price REAL,
        raw_text TEXT
      )
    ''')
    conn.commit()
    conn.close()
    LOG.info("DB initialized (%s)", DB_FILE)

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, params)
    if fetch:
        rows = c.fetchall()
        conn.close()
        return rows
    conn.commit()
    conn.close()
    return None

# --- simple price extraction heuristic ---
PRICE_RE = re.compile(r'([₹$EUR€£]?\s?[\d{1,3},]*\d+(?:\.\d{1,2})?)')

def extract_price_from_text(text):
    # Try to find first numeric-looking token
    matches = PRICE_RE.findall(text.replace('\n', ' '))
    if not matches:
        return None, text[:400]
    # pick first match, strip currency symbols and commas
    raw = matches[0]
    cleaned = re.sub(r'[^\d.]', '', raw)
    try:
        return float(cleaned), raw
    except Exception:
        return None, raw

# --- fetch logic ---
def fetch_price(item_id, url):
    LOG.info("Fetching price for %s -> %s", item_id, url)
    try:
        headers = {"User-Agent": "price-monitor/1.0 (+https://example.com)"}
        resp = requests.get(url, timeout=10, headers=headers)
        text = resp.text or ""
        price, raw = extract_price_from_text(text)
        ts = time.time()
        db_execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                   (item_id, ts, price if price is not None else -1.0, raw))
        LOG.info("Fetched %s -> price=%s", item_id, price)
        return {"item_id": item_id, "price": price, "raw": raw, "checked_at": ts}
    except Exception as e:
        LOG.exception("Fetch failed for %s: %s", url, e)
        ts = time.time()
        db_execute('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                   (item_id, ts, -1.0, f"error: {e}"))
        return {"item_id": item_id, "price": None, "raw": str(e), "checked_at": ts}

# --- background poller ---
poller_stop = threading.Event()

def poller_loop(interval=POLL_INTERVAL):
    LOG.info("Poller thread started (interval=%s)", interval)
    while not poller_stop.is_set():
        try:
            items = db_execute('SELECT id, url FROM items', fetch=True)
            for row in items:
                item_id, url = row
                # perform fetch in a separate thread so one slow URL doesn't block others
                threading.Thread(target=fetch_price, args=(item_id, url), daemon=True).start()
        except Exception:
            LOG.exception("Poller loop error")
        poller_stop.wait(interval)

# --- API endpoints ---
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/track", methods=["POST"])
def track():
    data = request.json or {}
    url = data.get("url")
    name = data.get("name", url)
    if not url:
        return jsonify({"error": "missing url"}), 400
    item_id = str(uuid.uuid4())
    db_execute('INSERT INTO items (id, name, url) VALUES (?,?,?)', (item_id, name, url))
    LOG.info("Added track item %s -> %s", item_id, url)
    # trigger immediate fetch in background
    threading.Thread(target=fetch_price, args=(item_id, url), daemon=True).start()
    return jsonify({"id": item_id, "name": name, "url": url}), 201

@app.route("/prices", methods=["GET"])
def list_prices():
    # For each item, return last known price
    items = db_execute('SELECT id, name, url FROM items', fetch=True)
    out = []
    for item in items:
        item_id, name, url = item
        row = db_execute('SELECT price, checked_at FROM prices WHERE item_id=? ORDER BY checked_at DESC LIMIT 1', (item_id,), fetch=True)
        last = row[0] if row else None
        out.append({
            "id": item_id,
            "name": name,
            "url": url,
            "last_price": last[0] if last else None,
            "last_checked": last[1] if last else None
        })
    return jsonify(out)

@app.route("/prices/<item_id>", methods=["GET"])
def get_price_history(item_id):
    rows = db_execute('SELECT checked_at, price, raw_text FROM prices WHERE item_id=? ORDER BY checked_at DESC LIMIT 50', (item_id,), fetch=True)
    hist = [{"checked_at": r[0], "price": (None if r[1] == -1.0 else r[1]), "raw": r[2]} for r in rows]
    return jsonify({"id": item_id, "history": hist})

@app.route("/fetch/<item_id>", methods=["POST"])
def trigger_fetch(item_id):
    item = db_execute('SELECT url FROM items WHERE id=?', (item_id,), fetch=True)
    if not item:
        return jsonify({"error": "not found"}), 404
    url = item[0][0]
    threading.Thread(target=fetch_price, args=(item_id, url), daemon=True).start()
    return jsonify({"ok": True}), 202

# --- startup ---
if _name_ == "_main_":
    init_db()
    # start poller thread
    t = threading.Thread(target=poller_loop, daemon=True)
    t.start()
    LOG.info("Starting Flask on 0.0.0.0:%d", PORT)
    app.run(host="0.0.0.0", port=PORT)
