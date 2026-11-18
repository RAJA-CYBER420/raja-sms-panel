import os
import sqlite3
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "raja_sms_panel_secret_998877")

# ==========================================
# CONFIGURATION (5sim.net Live API)
# ==========================================
# Put your 5sim JWT token here or in Admin Panel
FIVESIM_API_KEY = os.environ.get("FIVESIM_API_KEY", "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE4MjY1Mjg1NTMsImlhdCI6MTc5NDk5MjU1MywicmF5IjoiZjA3YjExMmJhZmNkYjEwMDFjYjcwZDg1MjZhMzM2NmEiLCJzdWIiOjQ3MzY5ODh9.H6LoWXe3xrnvriw7fwvJAmEM61HdKt9PBd36Zoa0nLVpjknGkEMEEh0yYx-zQUPLOZT8nvREABCF_mSA_r_v7JbjpXYFQOD-fhU_L_iYmzD0PF7kRceWuREFdYx9Z1O6i2RrZMjRzPSmvOCkI2ZAd2feL0vRah7dVBHHF3l0sETNS0FISYPFijWEQ2xtZwzHCZUSHx-h3IGrAoLJ6TK9oiuy96bNk6IpI5DPEHXZs8a6U9k6U2UYPphh6_CDeOZrN3o8bv3_temuARV0YVIWzKdUSdJg0vmG-W4Wqh4hqckXq0Oana0xFFJ6i12EiuitV3XZl3ki-tFXG7Vl_xzjJQ")
FIVESIM_BASE_URL = "https://5sim.net/v1"
ADMIN_USERNAME = "admin"
DB_NAME = "otpfast.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    # Users Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0
        )
    """)
    # Settings Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Orders Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            fivesim_id TEXT,
            service TEXT,
            country TEXT,
            operator TEXT,
            phone TEXT,
            cost_price REAL,
            sell_price REAL,
            status TEXT DEFAULT 'PENDING',
            otp_code TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    # Deposits Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            amount REAL,
            trx_id TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )
    """)

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('markup_percent', '50')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('fivesim_api_key', ?)", (FIVESIM_API_KEY,))

    # Default Admin (admin / admin123)
    admin = c.execute("SELECT * FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
    if not admin:
        c.execute(
            "INSERT INTO users (username, password_hash, balance, is_admin) VALUES (?, ?, ?, 1)",
            (ADMIN_USERNAME, generate_password_hash("admin123"), 50.0)
        )
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def fivesim_headers():
    api_key = get_setting("fivesim_api_key", FIVESIM_API_KEY)
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }


@app.route("/")
def index():
    if "user_id" not in session:
        return render_template("index.html", logged_in=False)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    if not user:
        session.clear()
        return render_template("index.html", logged_in=False)
    return render_template(
        "index.html",
        logged_in=True,
        username=user["username"],
        balance=round(user["balance"], 2),
        is_admin=bool(user["is_admin"])
    )


@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    if len(username) < 3 or len(password) < 4:
        return jsonify({"ok": False, "error": "Username (3+ chars) and password (4+ chars) required."})
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, balance) VALUES (?, ?, 0.0)",
            (username, generate_password_hash(password))
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        session["user_id"] = user["id"]
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "Username already exists."})
    finally:
        conn.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["id"]
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid username or password."})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/prices")
def get_prices():
    country = request.args.get("country", "usa")
    product = request.args.get("product", "whatsapp")
    markup = float(get_setting("markup_percent", "50"))

    try:
        url = f"{FIVESIM_BASE_URL}/guest/prices?country={country}&product={product}"
        res = requests.get(url, timeout=10)
        data = res.json()
        operators_data = data.get(country, {}).get(product, {})

        result = []
        for op_name, info in operators_data.items():
            count = info.get("count", 0)
            base_cost = float(info.get("cost", 0))
            if count > 0 and base_cost > 0:
                usd_base = round(base_cost / 90.0, 3)
                sell_price = round(usd_base * (1 + markup / 100.0), 2)
                if sell_price < 0.05:
                    sell_price = 0.05
                result.append({
                    "operator": op_name,
                    "count": count,
                    "sell_price": sell_price,
                    "rate": info.get("rate", 95)
                })
        result.sort(key=lambda x: x["sell_price"])
        return jsonify({"ok": True, "operators": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/buy", methods=["POST"])
def buy_number():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Please login first."})

    data = request.json
    country = data.get("country", "usa")
    operator = data.get("operator", "any")
    product = data.get("product", "whatsapp")
    sell_price = float(data.get("sell_price", 0.25))

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if user["balance"] < sell_price:
        conn.close()
        return jsonify({"ok": False, "error": "Insufficient wallet balance. Please deposit funds."})

    try:
        url = f"{FIVESIM_BASE_URL}/user/buy/activation/{country}/{operator}/{product}"
        res = requests.get(url, headers=fivesim_headers(), timeout=15)
        if res.status_code != 200:
            conn.close()
            return jsonify({"ok": False, "error": f"Provider Error: {res.text}"})

        order_data = res.json()
        fivesim_id = str(order_data["id"])
        phone = order_data["phone"]
        raw_cost = round(float(order_data.get("price", 0)) / 90.0, 3)

        new_balance = round(user["balance"] - sell_price, 2)
        conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))
        conn.execute("""
            INSERT INTO orders (user_id, fivesim_id, service, country, operator, phone, cost_price, sell_price, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """, (user["id"], fivesim_id, product, country, operator, phone, raw_cost, sell_price, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

        return jsonify({"ok": True, "phone": phone, "fivesim_id": fivesim_id, "balance": new_balance})
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/check_sms/<fivesim_id>")
def check_sms(fivesim_id):
    if "user_id" not in session:
        return jsonify({"ok": False})

    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE fivesim_id = ? AND user_id = ?", (fivesim_id, session["user_id"])).fetchone()
    if not order:
        conn.close()
        return jsonify({"ok": False, "error": "Order not found"})

    if order["status"] == "RECEIVED":
        conn.close()
        return jsonify({"ok": True, "status": "RECEIVED", "code": order["otp_code"]})

    try:
        url = f"{FIVESIM_BASE_URL}/user/check/{fivesim_id}"
        res = requests.get(url, headers=fivesim_headers(), timeout=10)
        data = res.json()
        sms_list = data.get("sms", [])
        status = data.get("status", "PENDING")

        if sms_list:
            code = sms_list[-1].get("code") or sms_list[-1].get("text")
            conn.execute("UPDATE orders SET status = 'RECEIVED', otp_code = ? WHERE id = ?", (code, order["id"]))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "status": "RECEIVED", "code": code})

        conn.close()
        return jsonify({"ok": True, "status": status, "code": ""})
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cancel/<fivesim_id>", methods=["POST"])
def cancel_order(fivesim_id):
    if "user_id" not in session:
        return jsonify({"ok": False})

    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE fivesim_id = ? AND user_id = ?", (fivesim_id, session["user_id"])).fetchone()
    if not order or order["status"] != "PENDING":
        conn.close()
        return jsonify({"ok": False, "error": "Cannot cancel this order."})

    try:
        url = f"{FIVESIM_BASE_URL}/user/cancel/{fivesim_id}"
        requests.get(url, headers=fivesim_headers(), timeout=10)

        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        new_balance = round(user["balance"] + order["sell_price"], 2)
        conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))
        conn.execute("UPDATE orders SET status = 'CANCELED' WHERE id = ?", (order["id"],))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "balance": new_balance})
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/my_orders")
def my_orders():
    if "user_id" not in session:
        return jsonify({"ok": False, "orders": []})
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 30", (session["user_id"],)).fetchall()
    user = conn.execute("SELECT balance FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return jsonify({
        "ok": True,
        "balance": round(user["balance"], 2),
        "orders": [dict(r) for r in rows]
    })


@app.route("/api/deposit", methods=["POST"])
def submit_deposit():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Login required"})
    data = request.json
    method = data.get("method", "JazzCash")
    amount = float(data.get("amount", 0))
    trx_id = data.get("trx_id", "").strip()
    if amount <= 0 or not trx_id:
        return jsonify({"ok": False, "error": "Valid amount and Transaction ID required."})

    conn = get_db()
    conn.execute(
        "INSERT INTO deposits (user_id, method, amount, trx_id, status, created_at) VALUES (?, ?, ?, ?, 'PENDING', ?)",
        (session["user_id"], method, amount, trx_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/data")
def admin_data():
    if "user_id" not in session:
        return jsonify({"ok": False})
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not user or not user["is_admin"]:
        conn.close()
        return jsonify({"ok": False, "error": "Unauthorized"})

    deposits = conn.execute("""
        SELECT d.*, u.username FROM deposits d
        JOIN users u ON d.user_id = u.id
        ORDER BY d.id DESC LIMIT 30
    """).fetchall()
    markup = get_setting("markup_percent", "50")
    api_key = get_setting("fivesim_api_key", "")
    conn.close()

    provider_balance = "N/A"
    try:
        res = requests.get(f"{FIVESIM_BASE_URL}/user/profile", headers=fivesim_headers(), timeout=8)
        if res.status_code == 200:
            provider_balance = f"{res.json().get('balance', 0)} RUB"
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "deposits": [dict(d) for d in deposits],
        "markup": markup,
        "api_key": api_key,
        "provider_balance": provider_balance
    })


@app.route("/api/admin/approve_deposit/<int:dep_id>", methods=["POST"])
def approve_deposit(dep_id):
    if "user_id" not in session:
        return jsonify({"ok": False})
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not admin or not admin["is_admin"]:
        conn.close()
        return jsonify({"ok": False})

    dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,)).fetchone()
    if dep and dep["status"] == "PENDING":
        conn.execute("UPDATE deposits SET status = 'APPROVED' WHERE id = ?", (dep_id,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (dep["amount"], dep["user_id"]))
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/save_settings", methods=["POST"])
def save_settings():
    if "user_id" not in session:
        return jsonify({"ok": False})
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not admin or not admin["is_admin"]:
        conn.close()
        return jsonify({"ok": False})

    data = request.json
    conn.execute("REPLACE INTO settings (key, value) VALUES ('markup_percent', ?)", (str(data.get("markup", 50)),))
    conn.execute("REPLACE INTO settings (key, value) VALUES ('fivesim_api_key', ?)", (data.get("api_key", "").strip(),))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    print("==================================================")
    print("   RAJA SMS PANEL IS LIVE & RUNNING SUCCESSFULLY!   ")
    print("   Local Link: http://127.0.0.1:8010               ")
    print("==================================================")
    app.run(host="0.0.0.0", port=8010, debug=True)
