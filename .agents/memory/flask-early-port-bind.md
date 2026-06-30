---
name: Flask early port bind
description: How to open port 5050 within ~2s of startup so deployment healthchecks pass during the 20-30s route loading phase
---

## The rule
Start `make_server()` in a thread near the TOP of main.py (right after `app = Flask(...)`, security init, and CORS), BEFORE the 46K lines of `@app.route` decorators.

## Why
Flask's `app.run()` is at line ~46K. In prod, Python takes 20-30s to load that far. The deployment platform's port detection times out, sends SIGTERM, and the process restarts in a loop forever.

## How to apply
```python
# After _init_security(app) and CORS(app):
PORT = int(os.environ.get("STOCK_API_PORT", 5050))

@app.route("/stock-api/")
@app.route("/stock-api")
def health_root():
    return jsonify({"status": "ok"}), 200

import threading as _early_bind_thr
from werkzeug.serving import make_server as _wz_make_server
_wz_srv = _wz_make_server("0.0.0.0", PORT, app, threaded=True)
_wz_srv_thr = _early_bind_thr.Thread(target=_wz_srv.serve_forever, daemon=False, name="flask-main")
_wz_srv_thr.start()
# Flask 2.x guard: raises AssertionError on @app.route after first request — patch it out
app._check_setup_finished = lambda f_name: None

# At the bottom, replace app.run() with:
# if __name__ == "__main__":
#     _wz_srv_thr.join()
```

## Flask 2.x gotcha
`_check_setup_finished` raises `AssertionError` when any route is decorated after the first request is served. The fix is to monkey-patch it to a no-op. Flask's `url_map` IS updated correctly by each `@app.route` decorator — the check is a developer-convenience assertion only, not part of routing.

**Why this works:** Flask routes through `app.url_map` which is mutable. Routes registered at any time (even mid-serving) are immediately available to the running server. The `_check_setup_finished` guard is only a consistency warning.

## PORT definition
`PORT` must be moved UP to the early bind block (from its old location near line 10898). Remove it from mid-file to avoid duplicate definition. `_BOOT_TIME` and preload imports stay at mid-file.
