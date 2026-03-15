# SPDX-License-Identifier: Apache-2.0
# auth.py – API-Key-Verwaltung und Auth-Decorators

import logging
import os
import secrets
from functools import wraps

from flask import request, jsonify, Response, session, redirect, url_for

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


def require_api_key(f):
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


def require_web_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not state.REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(f"/login?next={request.path}")
        return f(*args, **kwargs)
    return wrapper
