# SPDX-License-Identifier: Apache-2.0
# auth.py – API-Key-Verwaltung, Auth-Decorators, CSRF-Schutz

import logging
import os
import secrets
from functools import wraps
from typing import Callable, Optional

from flask import abort, jsonify, redirect, request, session

import app.state as state

log = logging.getLogger("bridge-ws")


def _ensure_api_key() -> None:
    """Sorgt dafür, dass ein API-Key vorhanden ist.
    Reihenfolge: ENV → config → api_key_file → automatisch generieren."""
    if state.API_KEY:
        return
    try:
        with open(state.API_KEY_FILE, "r", encoding="utf-8") as f:
            state.API_KEY = f.read().strip()
    except FileNotFoundError:
        state.API_KEY = None
    if state.REQUIRE_API_KEY and not state.API_KEY:
        try:
            os.makedirs(os.path.dirname(state.API_KEY_FILE) or ".", exist_ok=True)
            state.API_KEY = secrets.token_urlsafe(32)
            with open(state.API_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(state.API_KEY)
            try:
                os.chmod(state.API_KEY_FILE, 0o600)
            except Exception:
                pass
            log.info("Neuen API-Key generiert und gespeichert (%s).", state.API_KEY_FILE)
        except Exception as e:
            log.error("API-Key konnte nicht gespeichert werden: %s", e)


def require_api_key(f: Callable) -> Callable:
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not state.REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not state.API_KEY:
            return jsonify({"error": "server_misconfigured: API key required but not set"}), 503
        if request.headers.get("X-API-Key") != state.API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def require_web_auth(f: Callable) -> Callable:
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not state.REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(f"/login?next={request.path}")
        return f(*args, **kwargs)
    return wrapper


# ── CSRF-Schutz ──────────────────────────────────────────────────────────────

def generate_csrf_token() -> str:
    """Erzeugt ein CSRF-Token und speichert es in der Session."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf_token() -> bool:
    """Prüft ob das übermittelte CSRF-Token mit der Session übereinstimmt."""
    token = session.get("csrf_token")
    if not token:
        return False
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    return secrets.compare_digest(token, submitted or "")


def require_csrf(f: Callable) -> Callable:
    """Decorator: prüft CSRF-Token bei POST/PUT/DELETE-Requests."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE"):
            if not validate_csrf_token():
                log.warning("CSRF-Token ungültig oder fehlend: %s %s", request.method, request.path)
                abort(403)
        return f(*args, **kwargs)
    return wrapper
