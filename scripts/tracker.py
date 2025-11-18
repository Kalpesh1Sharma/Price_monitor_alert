# tracker.py
import os
import time
import uuid
import sqlite3
import threading
import logging
import re
import requests
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor

# --- config & logging ---
DB_FILE = os.environ.get("DB_FILE", "prices.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 60))
PORT = int(os.environ.get("PORT", 5000))
MAX_WORKERS = 5  # Max concurrent HTTP requests

LOG = logging.getLogger("tracker")
LOG.setLevel(logging.INFO)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
LOG.addHandler(h)

app = Flask(__name__)

# Lock for synchronizing DB writes to prevent "database is locked" errors
DB_LOCK = threading.Lock()

# --- DB helpers ---
def get_db_conn():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    return conn

def init_db():
    with DB_LOCK:
        conn = get_db_conn()
        c = conn.cursor()
        # Enable Write-Ahead Logging for better concurrency
        c.execute('PRAGMA journal_mode=WAL;')
        
        c.execute('''
          CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT
          )
        ''')
        c.execute('''
          CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            checked_at REAL,
            price REAL,
            raw_text TEXT
          )
        ''')
        # Add index for faster history lookups
        c.execute('CREATE INDEX IF NOT EXISTS idx_prices_item ON prices(item_id);')
        conn.commit()
        conn.close()
        LOG.info("DB initialized (%s) with WAL mode", DB_FILE)

def db_write(query, params=()):
    """Thread-safe write operation."""
    with DB_LOCK:
        conn = get_db_conn()
        try:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
        finally:
            conn.close()

def db_read(query, params=(), one=False):
    """Read operation (doesn't strictly need lock in WAL mode, but safe)."""
    conn = get_db_conn()
    try:
        c = conn.cursor()
        c.execute(query, params)
        rv = c.fetchall()
        return (rv[0] if rv else None) if one else rv
    finally:
        conn.close()

# --- simple price extraction heuristic ---
# Note: Regex parsing HTML is fragile. Ideally, use BeautifulSoup.
# This regex looks for currency symbols or standard number formats.
PRICE_RE = re.compile(r'([₹$EUR€£]?\s?[\d,]+\.?\d{0,2})')

def extract_price_from_text(text):
    # 1. clean newlines to make searching easier
    clean_text = " ".join(text.split())
    
    # 2. Try to find matches
    matches = PRICE_RE.findall(clean_text)
    if not matches:
        return None, clean_text[:100] # Return snippet of text for debugging

    # 3. Heuristic: First match is often garbage (e.g. phone number in header).
    # In a real app, you need CSS selectors. For now, we take the first logical match.
    for raw in matches:
        # Remove currency symbols and commas to convert to float
        cleaned = re.sub(r'[^\d.]', '', raw)
        if not cleaned: continue
        try:
            val = float(cleaned)
            return val, raw
        except ValueError:
            continue
            
    return None, matches[0] if matches else "no match"

# --- fetch logic ---
def fetch_price(item_id, url):
    LOG.info("Fetching price for %s -> %s", item_id, url)
    price = None
    raw = ""
    ts = time.time()
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, timeout=10, headers=headers)
        resp.raise_for_status() # Check for 404/500 errors
        
        text = resp.text or ""
        price, raw = extract_price_from_text(text)
        
        # Record success
        db_write('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                   (item_id, ts, price if price is not None else -1.0, raw))
        
        LOG.info("Fetched %s -> price=%s", item_id, price)
        return {"item_id": item_id, "price": price, "raw": raw, "checked_at": ts}

    except Exception as e:
        LOG.error("Fetch failed for %s: %s", url, str(e))
        # Record failure
        db_write('INSERT INTO prices (item_id, checked_at, price, raw_text) VALUES (?,?,?,?)',
                   (item_id, ts, -1.0, f"error: {str(e)}"))
        return {"item_id": item_id, "price": None, "raw": str(e), "checked_at": ts}

# --- background poller ---
poller_stop = threading.Event()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

def poller_loop(interval=POLL_INTERVAL):
    LOG.info("Poller thread started (interval=%s)", interval)
    while not poller_stop.is_set():
        start_time = time.time()
        try:
            items = db_read('SELECT id, url FROM items')
            if items:
                # Use thread pool to avoid spawning 1000s of threads if many items exist
                for row in items:
                    executor.submit(fetch_price, row['id'], row['url'])
        except Exception:
            LOG.exception("Poller loop error")
        
        # Calculate remaining time to sleep so the interval is consistent
        elapsed = time.time() - start_time
        sleep_time = max(0, interval - elapsed)
        poller_stop.wait(sleep_time)

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
    db_write('INSERT INTO items (id, name, url) VALUES (?,?,?)', (item_id, name, url))
    
    LOG.info("Added track item %s -> %s", item_id, url)
    # Trigger immediate fetch via pool
    executor.submit(fetch_price, item_id, url)
    
    return jsonify({"id": item_id, "name": name, "url": url}), 201

@app.route("/prices", methods=["GET"])
def list_prices():
    # OPTIMIZED: Single query instead of N+1 queries
    query = '''
        SELECT i.id, i.name, i.url, p.price, p.checked_at 
        FROM items i
        LEFT JOIN (
            SELECT item_id, price, checked_at
            FROM prices
            WHERE id IN (
                SELECT MAX(id) FROM prices GROUP BY item_id
            )
        ) p ON i.id = p.item_id
    '''
    rows = db_read(query)
    
    out = []
    for row in rows:
        out.append({
            "id": row['id'],
            "name": row['name'],
            "url": row['url'],
            "last_price": row['price'],
            "last_checked": row['checked_at']
        })
    return jsonify(out)

@app.route("/prices/<item_id>", methods=["GET"])
def get_price_history(item_id):
    rows = db_read('SELECT checked_at, price, raw_text FROM prices WHERE item_id=? ORDER BY checked_at DESC LIMIT 50', (item_id,))
    hist = [{"checked_at": r['checked_at'], "price": (None if r['price'] == -1.0 else r['price']), "raw": r['raw_text']} for r in rows]
    return jsonify({"id": item_id, "history": hist})

@app.route("/fetch/<item_id>", methods=["POST"])
def trigger_fetch(item_id):
    item = db_read('SELECT url FROM items WHERE id=?', (item_id,), one=True)
    if not item:
        return jsonify({"error": "not found"}), 404
    
    executor.submit(fetch_price, item_id, item['url'])
    return jsonify({"status": "fetch_queued"}), 202

# --- startup ---
if __name__ == "__main__":
    init_db()
    
    # Start poller thread
    t = threading.Thread(target=poller_loop, daemon=True)
    t.start()
    
    LOG.info("Starting Flask on 0.0.0.0:%d", PORT)
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        LOG.info("Shutting down...")
        poller_stop.set()
        executor.shutdown(wait=False)
