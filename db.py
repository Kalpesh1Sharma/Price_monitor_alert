import sqlite3

def create_db():
    conn = sqlite3.connect('app/price_tracker.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            url TEXT NOT NULL,
            current_price REAL NOT NULL,
            target_price REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
