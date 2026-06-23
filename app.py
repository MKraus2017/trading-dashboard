"""Flask-Dashboard für Trading-Bot."""
import json
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, session, redirect, url_for

import config
from analyzer import auto_trader, portfolio, scheduler_tasks, signals

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.DASHBOARD_PASSWORD:
            session["logged_in"] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            return redirect(url_for("index"))
        error = "Falsches Passwort"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


@app.route("/api/portfolio")
@login_required
def api_portfolio():
    p, alerts = portfolio.evaluate_portfolio()
    return jsonify({"portfolio": p, "alerts": alerts})


@app.route("/api/recommendations", methods=["GET", "POST"])
@login_required
def api_recommendations():
    if request.method == "POST":
        dry_run = request.args.get("dry_run", "false").lower() == "true"
        result = auto_trader.run_auto_trading(dry_run=dry_run)
        return jsonify(result)
    return jsonify(signals.load_recommendations())


@app.route("/api/trade/buy", methods=["POST"])
@login_required
def api_trade_buy():
    data = request.get_json(force=True, silent=True) or {}
    symbol = data.get("symbol")
    amount = data.get("amount_eur")
    price = data.get("price")
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol fehlt"}), 400
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (ValueError, TypeError):
        amount = None
    try:
        price = float(price) if price not in (None, "") else None
    except (ValueError, TypeError):
        price = None

    res = portfolio.buy(symbol, price=price, amount_eur=amount)
    return jsonify(res)


@app.route("/api/trade/sell", methods=["POST"])
@login_required
def api_trade_sell():
    data = request.get_json(force=True, silent=True) or {}
    symbol = data.get("symbol")
    shares = data.get("shares")
    price = data.get("price")
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol fehlt"}), 400
    try:
        shares = float(shares) if shares not in (None, "") else None
    except (ValueError, TypeError):
        shares = None
    try:
        price = float(price) if price not in (None, "") else None
    except (ValueError, TypeError):
        price = None

    res = portfolio.sell(symbol, price=price, shares=shares)
    return jsonify(res)


@app.route("/api/real_trade", methods=["POST"])
@login_required
def api_real_trade():
    """Nutzer meldet eine reale Trade-Ausführung auf Trade Republic."""
    data = request.get_json(force=True, silent=True) or {}
    symbol = data.get("symbol")
    action = data.get("action", "").lower()
    shares = data.get("shares")
    price = data.get("price")
    if not symbol or action not in ("buy", "sell"):
        return jsonify({"ok": False, "error": "Symbol oder Aktion ungültig"}), 400
    try:
        shares = float(shares)
        price = float(price)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "shares und price müssen Zahlen sein"}), 400

    p = portfolio.get_portfolio()
    p.setdefault("real_positions", [])
    p.setdefault("real_trades", [])

    if action == "buy":
        existing = next((x for x in p["real_positions"] if x["symbol"] == symbol), None)
        if existing:
            total_shares = existing["shares"] + shares
            avg = (existing["shares"] * existing["entry_price"] + shares * price) / total_shares
            existing["shares"] = total_shares
            existing["entry_price"] = round(avg, 4)
            existing["invested"] = round(total_shares * avg, 2)
        else:
            p["real_positions"].append({
                "symbol": symbol,
                "shares": shares,
                "entry_price": round(price, 4),
                "invested": round(shares * price, 2),
                "opened_at": datetime.utcnow().isoformat(),
            })
        p["real_trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "action": "BUY",
            "shares": shares,
            "price": round(price, 4),
            "invested": round(shares * price, 2),
        })
    else:  # sell
        existing = next((x for x in p["real_positions"] if x["symbol"] == symbol), None)
        if not existing:
            return jsonify({"ok": False, "error": f"{symbol} nicht in realen Positionen"}), 400
        sell_shares = min(shares, existing["shares"])
        proceeds = sell_shares * price
        cost = sell_shares * existing["entry_price"]
        existing["shares"] -= sell_shares
        existing["invested"] = round(existing["shares"] * existing["entry_price"], 2)
        if existing["shares"] <= 0:
            p["real_positions"] = [x for x in p["real_positions"] if x["symbol"] != symbol]
        p["real_trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "action": "SELL",
            "shares": sell_shares,
            "price": round(price, 4),
            "proceeds": round(proceeds, 2),
            "pnl_eur": round(proceeds - cost, 2),
        })

    portfolio._save(p)
    return jsonify({"ok": True, "real_positions": p["real_positions"]})


@app.route("/api/reset_portfolio", methods=["POST"])
@login_required
def api_reset_portfolio():
    p = portfolio.reset_portfolio()
    return jsonify(p)


def _scheduler_auth():
    """Prüft den API-Key für externe Cron-Dienste."""
    auth_header = request.headers.get("Authorization", "")
    expected = os.environ.get("SCHEDULER_API_KEY", "")
    if not expected:
        return False
    return auth_header == f"Bearer {expected}"


@app.route("/api/scheduler/refresh_prices", methods=["POST"])
def api_scheduler_refresh_prices():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.refresh_prices())


@app.route("/api/scheduler/market_analysis", methods=["POST"])
def api_scheduler_market_analysis():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.market_analysis(notify=True))


@app.route("/api/scheduler/llm_analysis", methods=["POST"])
def api_scheduler_llm_analysis():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    auto = request.args.get("auto_trade", "true").lower() == "true"
    return jsonify(scheduler_tasks.llm_analysis(auto_trade=auto, notify=True))


@app.route("/api/scheduler/daily_summary", methods=["POST"])
def api_scheduler_daily_summary():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.daily_summary())


@app.route("/api/scheduler/portfolio_report", methods=["POST"])
def api_scheduler_portfolio_report():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.portfolio_report(notify=True))


@app.route("/api/scheduler/real_positions_alert", methods=["POST"])
def api_scheduler_real_positions_alert():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.real_positions_report(only_urgent=True))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
