from flask import Flask, jsonify, render_template, request
import json, os
from datetime import datetime

app = Flask(__name__)

DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "OKX2024secure!")
CACHE_FILE = "/tmp/dashboard_cache.json"

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/update", methods=["POST"])
def api_update():
    """Push-Endpunkt: Server schickt aktuelle Daten hierher."""
    secret = request.headers.get("X-API-Secret", "")
    if secret != DASHBOARD_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    return jsonify({"ok": True})

@app.route("/api/data")
def api_data():
    """Frontend ruft diese Route ab."""
    cache = load_cache()
    if not cache:
        return jsonify({"error": "Keine Daten — Push-Script noch nicht gelaufen"}), 503
    return jsonify(cache)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
