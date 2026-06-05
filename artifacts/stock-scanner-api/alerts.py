import json
import os
from datetime import datetime

ALERTS_FILE = "alerts.json"


def load_alerts() -> list:
    if not os.path.exists(ALERTS_FILE):
        return []
    with open(ALERTS_FILE, "r") as f:
        return json.load(f)


def save_alerts(alerts: list):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)


def get_alerts():
    return load_alerts()


def add_alert(ticker: str, alert_type: str, value: float, direction: str = "above") -> dict:
    alerts = load_alerts()
    alert = {
        "id": int(datetime.utcnow().timestamp() * 1000),
        "ticker": ticker.upper(),
        "type": alert_type,
        "value": value,
        "direction": direction,
        "triggered": False,
        "created": datetime.utcnow().isoformat(),
    }
    alerts.append(alert)
    save_alerts(alerts)
    return {"success": True, "alert": alert}


def delete_alert(alert_id: int) -> dict:
    alerts = load_alerts()
    alerts = [a for a in alerts if a["id"] != alert_id]
    save_alerts(alerts)
    return {"success": True}


def check_alerts(current_data: dict) -> list:
    alerts = load_alerts()
    triggered = []

    for alert in alerts:
        if alert["triggered"]:
            continue
        ticker = alert["ticker"]
        if ticker not in current_data:
            continue

        data = current_data[ticker]
        alert_type = alert["type"]
        target = alert["value"]
        direction = alert["direction"]

        current_val = None
        if alert_type == "price":
            current_val = data.get("price")
        elif alert_type == "rsi":
            current_val = data.get("rsi")
        elif alert_type == "score":
            current_val = data.get("score")

        if current_val is None:
            continue

        hit = (direction == "above" and current_val >= target) or \
              (direction == "below" and current_val <= target)

        if hit:
            alert["triggered"] = True
            alert["triggered_at"] = datetime.utcnow().isoformat()
            alert["triggered_value"] = current_val
            triggered.append(alert)

    save_alerts(alerts)
    return triggered
