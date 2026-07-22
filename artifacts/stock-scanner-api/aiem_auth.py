"""
AIEM Authentication Module — Phase 3 Remediation
=================================================
Roles (ordered by privilege level):
  viewer=1, institutional_ddv=1, trader=2, analyst=3,
  risk_manager=4, auditor=5, administrator=10

Session: sha256-hashed UUID token in HttpOnly cookie (aiem_session).
CSRF:     double-submit cookie (aiem_csrf); X-CSRF-Token header required
          on all state-changing (POST/PUT/PATCH/DELETE) routes.
Brute-force: 5 failures in 15 minutes → 15-minute lockout.
Backward compat: X-Admin-Token header still accepted (maps to administrator).
"""

import hashlib
import hmac as _hmac
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps

import psycopg2
from flask import Blueprint, jsonify, request, make_response
from werkzeug.security import generate_password_hash, check_password_hash

# ── Constants ────────────────────────────────────────────────────────────────

ROLE_LEVELS = {
    "viewer": 1,
    "institutional_ddv": 1,
    "trader": 2,
    "analyst": 3,
    "risk_manager": 4,
    "auditor": 5,
    "administrator": 10,
}
ALL_ROLES = list(ROLE_LEVELS.keys())
SESSION_TTL_SECONDS = 8 * 3600       # 8 hours
LOCKOUT_WINDOW_SECONDS = 15 * 60     # 15 minutes
LOCKOUT_THRESHOLD = 5                # failures before lockout
LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes

_DB_URL: str = ""  # set by init_auth_module()

# In-memory brute-force tracker: {key: {"count": N, "window_start": ts, "lockout_until": ts}}
_lockout_lock = threading.Lock()
_lockout_table: dict = {}

# ── Blueprint ────────────────────────────────────────────────────────────────

auth_bp = Blueprint("aiem_auth", __name__)


# ── DB helpers ───────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(_DB_URL, connect_timeout=5,
                            options="-c statement_timeout=5000")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_secure() -> bool:
    """Return True when the request came over HTTPS (behind proxy or direct)."""
    return (request.is_secure
            or request.headers.get("X-Forwarded-Proto", "http") == "https")


# ── Schema bootstrap ─────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aiem_users (
    id          SERIAL PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT,
    password_hash TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('viewer','institutional_ddv','trader',
                                    'analyst','risk_manager','auditor','administrator')),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES aiem_users(id),
    role           TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ NOT NULL,
    revoked_at     TIMESTAMPTZ,
    remote_addr    TEXT,
    user_agent     TEXT
);
CREATE INDEX IF NOT EXISTS ix_aiem_sessions_expires
    ON aiem_sessions (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS aiem_auth_events (
    id          SERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    username    TEXT,
    role        TEXT,
    remote_addr TEXT,
    user_agent  TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_login_attempts (
    id          SERIAL PRIMARY KEY,
    lookup_key  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_aiem_login_attempts_key_ts
    ON aiem_login_attempts (lookup_key, created_at);
"""


def init_auth_module(db_url: str) -> None:
    """Call once at startup. Creates tables and default admin user."""
    global _DB_URL
    _DB_URL = db_url
    try:
        with _db() as c, c.cursor() as cu:
            for stmt in SCHEMA_SQL.strip().split(";"):
                s = stmt.strip()
                if s:
                    cu.execute(s)
            c.commit()
            # Create default administrator if no users exist
            cu.execute("SELECT COUNT(*) FROM aiem_users")
            if cu.fetchone()[0] == 0:
                _default_pw = os.environ.get("AIEM_DEFAULT_ADMIN_PW", "ChangeMe123!")
                _ph = generate_password_hash(_default_pw, method="pbkdf2:sha256", salt_length=16)
                cu.execute(
                    "INSERT INTO aiem_users (username, email, password_hash, role) "
                    "VALUES (%s, %s, %s, %s)",
                    ("admin", "admin@aiem.local", _ph, "administrator"),
                )
                c.commit()
                print("[aiem_auth] Default admin user created (username=admin)")
    except Exception as exc:
        print(f"[aiem_auth] Schema init error: {exc}")


# ── Brute-force protection ────────────────────────────────────────────────────

def _lockout_key(username: str, remote_addr: str) -> str:
    return f"{username}|{remote_addr}"


def _check_lockout(username: str, remote_addr: str) -> bool:
    """Return True if this username+IP is currently locked out."""
    key = _lockout_key(username, remote_addr)
    now = time.time()
    with _lockout_lock:
        entry = _lockout_table.get(key)
        if entry and entry.get("lockout_until", 0) > now:
            return True
    return False


def _record_failure(username: str, remote_addr: str) -> None:
    key = _lockout_key(username, remote_addr)
    now = time.time()
    with _lockout_lock:
        entry = _lockout_table.get(key, {"count": 0, "window_start": now, "lockout_until": 0})
        # Reset window if older than LOCKOUT_WINDOW_SECONDS
        if now - entry["window_start"] > LOCKOUT_WINDOW_SECONDS:
            entry = {"count": 0, "window_start": now, "lockout_until": 0}
        entry["count"] += 1
        if entry["count"] >= LOCKOUT_THRESHOLD:
            entry["lockout_until"] = now + LOCKOUT_DURATION_SECONDS
        _lockout_table[key] = entry


def _clear_failure(username: str, remote_addr: str) -> None:
    key = _lockout_key(username, remote_addr)
    with _lockout_lock:
        _lockout_table.pop(key, None)


# ── Auth event logger ────────────────────────────────────────────────────────

def _log_auth_event(event_type: str, username: str = None, role: str = None,
                    detail: str = None) -> None:
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "INSERT INTO aiem_auth_events "
                "(event_type, username, role, remote_addr, user_agent, detail) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (event_type, username, role,
                 request.remote_addr, request.headers.get("User-Agent", "")[:512],
                 detail),
            )
            c.commit()
    except Exception:
        pass


# ── Session helpers ───────────────────────────────────────────────────────────

def _create_session(user_id: int, role: str) -> str:
    """Create session row, return raw token (to set in cookie)."""
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw)
    expires = datetime.now(timezone.utc).timestamp() + SESSION_TTL_SECONDS
    with _db() as c, c.cursor() as cu:
        cu.execute(
            "INSERT INTO aiem_sessions "
            "(session_id, user_id, role, expires_at, remote_addr, user_agent) "
            "VALUES (%s,%s,%s, to_timestamp(%s),%s,%s)",
            (token_hash, user_id, role, expires,
             request.remote_addr, request.headers.get("User-Agent", "")[:512]),
        )
        c.commit()
    return raw


def _resolve_session(raw_token: str) -> dict | None:
    """Return {user_id, username, role} if session valid, else None."""
    token_hash = _hash_token(raw_token)
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT s.user_id, u.username, s.role, s.expires_at "
                "FROM aiem_sessions s JOIN aiem_users u ON u.id=s.user_id "
                "WHERE s.session_id=%s AND s.revoked_at IS NULL "
                "  AND s.expires_at > now() AND u.is_active=TRUE",
                (token_hash,),
            )
            row = cu.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return {"user_id": row[0], "username": row[1], "role": row[2]}


def _revoke_session(raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "UPDATE aiem_sessions SET revoked_at=now() WHERE session_id=%s",
                (token_hash,),
            )
            c.commit()
    except Exception:
        pass


def _set_session_cookie(resp: "make_response", raw_token: str) -> None:
    resp.set_cookie(
        "aiem_session",
        raw_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_is_secure(),
        samesite="Strict",
        path="/",
    )


def _set_csrf_cookie(resp: "make_response") -> str:
    """Set and return a CSRF token (non-HttpOnly so JS can read it)."""
    existing = request.cookies.get("aiem_csrf")
    if existing and len(existing) >= 32:
        return existing
    csrf_token = secrets.token_urlsafe(24)
    resp.set_cookie(
        "aiem_csrf",
        csrf_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=False,
        secure=_is_secure(),
        samesite="Strict",
        path="/",
    )
    return csrf_token


def _clear_auth_cookies(resp: "make_response") -> None:
    resp.delete_cookie("aiem_session", path="/")
    resp.delete_cookie("aiem_csrf", path="/")


# ── CSRF validation ───────────────────────────────────────────────────────────

def _csrf_ok() -> bool:
    """
    On state-changing methods (POST/PUT/PATCH/DELETE):
      - If caller uses X-Admin-Token, skip CSRF (backward compat internal callers).
      - Otherwise require X-CSRF-Token == aiem_csrf cookie.
    GET/HEAD/OPTIONS are always exempt.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.headers.get("X-Admin-Token"):
        return True  # internal callers use admin token, not cookies
    header_tok = request.headers.get("X-CSRF-Token", "")
    cookie_tok = request.cookies.get("aiem_csrf", "")
    if not header_tok or not cookie_tok:
        return False
    return _hmac.compare_digest(header_tok, cookie_tok)


# ── Current-user resolution ───────────────────────────────────────────────────

def _admin_token_ok() -> bool:
    """Legacy X-Admin-Token check (backward compat)."""
    want = os.environ.get("ADMIN_TOKEN", "")
    got  = request.headers.get("X-Admin-Token", "")
    return bool(want) and bool(got) and _hmac.compare_digest(got, want)


def get_current_user() -> dict | None:
    """
    Resolve who is making this request. Returns dict with user_id/username/role
    or None if unauthenticated.

    Priority:
    1. Valid aiem_session cookie → full user record from DB.
    2. X-Admin-Token match → synthetic administrator identity.
    """
    # 1. Cookie-based session
    raw = request.cookies.get("aiem_session", "")
    if raw:
        user = _resolve_session(raw)
        if user:
            return user

    # 2. X-Admin-Token header backward compat
    if _admin_token_ok():
        return {"user_id": 0, "username": "admin_token", "role": "administrator"}

    # 3. Query-param token — GET only (EventSource cannot send custom headers)
    if request.method == "GET":
        qp = request.args.get("token", "")
        want = os.environ.get("ADMIN_TOKEN", "")
        if qp and want and _hmac.compare_digest(qp, want):
            return {"user_id": 0, "username": "admin_token_qp", "role": "administrator"}

    return None


# ── require_role decorator ────────────────────────────────────────────────────

def require_role(min_role: str):
    """
    Decorator that enforces minimum role on a Flask route.

    Usage:
        @app.route(...)
        @require_role("analyst")
        def my_endpoint():
            ...

    Returns:
        401 — no valid session or token (not authenticated).
        403 — authenticated but role too low.
    """
    min_level = ROLE_LEVELS.get(min_role, 999)

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return jsonify({"error": "unauthorized", "detail": "authentication required"}), 401
            user_level = ROLE_LEVELS.get(user["role"], 0)
            if user_level < min_level:
                return jsonify({"error": "forbidden",
                                "detail": f"requires {min_role} or higher"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── /auth/* endpoints ─────────────────────────────────────────────────────────

@auth_bp.route("/stock-api/auth/login", methods=["POST"])
def auth_login():
    """
    POST /stock-api/auth/login
    Body: {"username": "...", "password": "..."}
    Response: sets aiem_session + aiem_csrf cookies; returns {username, role}
    """
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip().lower()
    password  = (body.get("password") or "")

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    remote = request.remote_addr or "0.0.0.0"

    # Brute-force gate
    if _check_lockout(username, remote):
        _log_auth_event("lockout_blocked", username=username,
                        detail=f"IP {remote} locked out")
        return jsonify({"error": "too many failed attempts — try again in 15 minutes"}), 429

    # Fetch user
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT id, password_hash, role, is_active FROM aiem_users WHERE username=%s",
                (username,),
            )
            row = cu.fetchone()
    except Exception as exc:
        return jsonify({"error": "database error"}), 503

    if row is None or not row[3]:  # not found or inactive
        _record_failure(username, remote)
        _log_auth_event("login_failure", username=username,
                        detail="unknown user or inactive")
        return jsonify({"error": "invalid credentials"}), 401

    user_id, pw_hash, role, _ = row

    if not check_password_hash(pw_hash, password):
        _record_failure(username, remote)
        _log_auth_event("login_failure", username=username,
                        detail="wrong password")
        return jsonify({"error": "invalid credentials"}), 401

    # Success — clear failure counter, create session
    _clear_failure(username, remote)
    raw_token = _create_session(user_id, role)
    _log_auth_event("login_success", username=username, role=role)

    resp = make_response(jsonify({"username": username, "role": role, "status": "ok"}))
    _set_session_cookie(resp, raw_token)
    _set_csrf_cookie(resp)
    return resp


@auth_bp.route("/stock-api/auth/logout", methods=["POST"])
def auth_logout():
    """POST /stock-api/auth/logout — revokes the server-side session.
    Requires CSRF token (X-CSRF-Token header) or X-Admin-Token for backward compat."""
    if not _csrf_ok():
        return jsonify({"error": "CSRF validation failed"}), 403
    raw = request.cookies.get("aiem_session", "")
    if raw:
        _revoke_session(raw)
        _log_auth_event("logout")
    resp = make_response(jsonify({"status": "logged_out"}))
    _clear_auth_cookies(resp)
    return resp


@auth_bp.route("/stock-api/auth/me", methods=["GET"])
def auth_me():
    """GET /stock-api/auth/me — returns current user info or 401."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "unauthenticated"}), 401
    return jsonify({
        "user_id":  user["user_id"],
        "username": user["username"],
        "role":     user["role"],
        "role_level": ROLE_LEVELS.get(user["role"], 0),
        "all_roles": ALL_ROLES,
    })


@auth_bp.route("/stock-api/auth/users", methods=["GET"])
@require_role("administrator")
def auth_list_users():
    """GET /stock-api/auth/users — list all users (administrator only)."""
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT id, username, email, role, is_active, created_at FROM aiem_users ORDER BY id"
            )
            rows = cu.fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503
    users = [
        {"id": r[0], "username": r[1], "email": r[2], "role": r[3],
         "is_active": r[4], "created_at": r[5].isoformat() if r[5] else None}
        for r in rows
    ]
    return jsonify({"users": users, "count": len(users)})


@auth_bp.route("/stock-api/auth/users", methods=["POST"])
@require_role("administrator")
def auth_create_user():
    """POST /stock-api/auth/users — create a new user (administrator only)."""
    if not _csrf_ok():
        return jsonify({"error": "invalid or missing CSRF token"}), 403

    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip().lower()
    email    = (body.get("email") or "").strip()
    password = (body.get("password") or "")
    role     = (body.get("role") or "viewer").strip().lower()

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    if role not in ROLE_LEVELS:
        return jsonify({"error": f"invalid role; must be one of {ALL_ROLES}"}), 400
    if len(password) < 12:
        return jsonify({"error": "password must be at least 12 characters"}), 400

    pw_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "INSERT INTO aiem_users (username, email, password_hash, role) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (username, email or None, pw_hash, role),
            )
            new_id = cu.fetchone()[0]
            c.commit()
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "username already exists"}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503

    _log_auth_event("user_created", username=username, role=role,
                    detail=f"id={new_id}")
    return jsonify({"id": new_id, "username": username, "role": role, "status": "created"}), 201


@auth_bp.route("/stock-api/auth/users/<int:user_id>", methods=["PATCH"])
@require_role("administrator")
def auth_update_user(user_id: int):
    """PATCH /stock-api/auth/users/<id> — update role/password/active status."""
    if not _csrf_ok():
        return jsonify({"error": "invalid or missing CSRF token"}), 403

    body = request.get_json(silent=True) or {}
    updates, params = [], []

    if "role" in body:
        r = body["role"]
        if r not in ROLE_LEVELS:
            return jsonify({"error": f"invalid role {r!r}"}), 400
        updates.append("role=%s"); params.append(r)

    if "is_active" in body:
        updates.append("is_active=%s"); params.append(bool(body["is_active"]))

    if "password" in body:
        pw = body["password"]
        if len(pw) < 12:
            return jsonify({"error": "password must be at least 12 characters"}), 400
        updates.append("password_hash=%s")
        params.append(generate_password_hash(pw, method="pbkdf2:sha256", salt_length=16))

    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    params.append(user_id)
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(f"UPDATE aiem_users SET {', '.join(updates)} WHERE id=%s", params)
            c.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503

    _log_auth_event("user_updated", detail=f"id={user_id} fields={list(body.keys())}")
    return jsonify({"status": "updated", "user_id": user_id})


@auth_bp.route("/stock-api/auth/sessions", methods=["GET"])
@require_role("administrator")
def auth_list_sessions():
    """GET /stock-api/auth/sessions — active sessions (administrator only)."""
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT s.user_id, u.username, s.role, s.created_at, s.expires_at, s.remote_addr "
                "FROM aiem_sessions s JOIN aiem_users u ON u.id=s.user_id "
                "WHERE s.revoked_at IS NULL AND s.expires_at>now() ORDER BY s.created_at DESC"
            )
            rows = cu.fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"sessions": [
        {"user_id": r[0], "username": r[1], "role": r[2],
         "created_at": r[3].isoformat(), "expires_at": r[4].isoformat(),
         "remote_addr": r[5]}
        for r in rows
    ]})


@auth_bp.route("/stock-api/auth/revoke-all-sessions", methods=["POST"])
@require_role("administrator")
def auth_revoke_all():
    """POST /stock-api/auth/revoke-all-sessions — forcibly invalidate all active sessions."""
    if not _csrf_ok():
        return jsonify({"error": "invalid or missing CSRF token"}), 403
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute("UPDATE aiem_sessions SET revoked_at=now() WHERE revoked_at IS NULL")
            count = cu.rowcount
            c.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503
    _log_auth_event("revoke_all_sessions", detail=f"{count} sessions revoked")
    return jsonify({"status": "ok", "revoked": count})


@auth_bp.route("/stock-api/auth/events", methods=["GET"])
@require_role("auditor")
def auth_events():
    """GET /stock-api/auth/events — auth event log (auditor+)."""
    limit = min(int(request.args.get("limit", 100)), 1000)
    try:
        with _db() as c, c.cursor() as cu:
            cu.execute(
                "SELECT id, event_type, username, role, remote_addr, detail, created_at "
                "FROM aiem_auth_events ORDER BY id DESC LIMIT %s", (limit,)
            )
            rows = cu.fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"events": [
        {"id": r[0], "event_type": r[1], "username": r[2], "role": r[3],
         "remote_addr": r[4], "detail": r[5],
         "created_at": r[6].isoformat() if r[6] else None}
        for r in rows
    ]})
