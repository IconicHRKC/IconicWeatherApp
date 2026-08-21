"""
app.py
Single service that does two jobs:
  1. Every POLL_INTERVAL_MINUTES, checks NOAA/SPC for new storm reports
     within 30 miles of Kansas City. When a genuinely new report shows
     up, it fires the SMS/WhatsApp alert via notifier.py.
  2. Serves the live dashboard (/) plus a small JSON API (/api/reports)
     that the dashboard's own JS polls every 60s to refresh in place -
     so anyone with the page open (including embedded via Wix iframe)
     always sees current data without reloading.

Run locally:
    pip install -r requirements.txt
    export TWILIO_ACCOUNT_SID=...   (see notifier.py / README.md)
    export TWILIO_AUTH_TOKEN=...
    export TWILIO_SMS_FROM=+1...
    export DASHBOARD_URL=http://localhost:10000
    python app.py

Deploy: see README.md (Render one-click instructions).
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template
from apscheduler.schedulers.background import BackgroundScheduler

from storm_data import fetch_and_filter_reports
from notifier import send_storm_alert

app = Flask(__name__, template_folder=".")

STATE_FILE = Path(__file__).parent / "storm_state.json"
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "10"))

_lock = threading.Lock()


def _load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_ids": [], "reports": [], "last_checked": None}


def _save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def poll_once():
    """Fetch current reports, diff against what we've already alerted
    on, send alerts for anything new, and persist the latest snapshot
    for the dashboard to read."""
    with _lock:
        state = _load_state()
        seen = set(state.get("seen_ids", []))

        try:
            reports = fetch_and_filter_reports()
        except Exception as exc:
            print(f"[app] poll failed: {exc}")
            return

        new_reports = [r for r in reports if r["id"] not in seen]

        if new_reports:
            print(f"[app] {len(new_reports)} new report(s) in radius - sending alert")
            try:
                send_storm_alert()
            except Exception as exc:
                print(f"[app] failed to send alert: {exc}")
            seen.update(r["id"] for r in new_reports)

        state["reports"] = reports
        state["seen_ids"] = list(seen)
        state["last_checked"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)


@app.route("/")
def dashboard():
    state = _load_state()
    return render_template(
        "dashboard.html",
        reports=state.get("reports", []),
        last_checked=state.get("last_checked"),
    )


@app.route("/api/reports")
def api_reports():
    state = _load_state()
    return jsonify({
        "reports": state.get("reports", []),
        "last_checked": state.get("last_checked"),
    })


@app.route("/api/poll-now", methods=["GET", "POST"])
def api_poll_now():
    """Manual trigger - useful for testing without waiting for the
    scheduler, or for wiring a 'refresh now' button later."""
    poll_once()
    return jsonify({"status": "ok"})


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(poll_once, "interval", minutes=POLL_INTERVAL_MINUTES, next_run_time=datetime.now())
    scheduler.start()


if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
else:
    # Also start the scheduler under a production WSGI server (gunicorn).
    start_scheduler()
