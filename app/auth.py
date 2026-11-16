# SPDX-License-Identifier: Apache-2.0
# auth.py – API-Key-Verwaltung, Auth-Decorators, CSRF-Schutz

import ipaddress
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


def _ensure_readonly_key() -> None:
    """Nur-Lese-Key: ENV → config → Datei → automatisch generieren (wie der Haupt-Key)."""
    if state.API_KEY_READONLY:
        return
    try:
        with open(state.API_KEY_READONLY_FILE, "r", encoding="utf-8") as f:
            state.API_KEY_READONLY = f.read().strip() or None
    except FileNotFoundError:
        state.API_KEY_READONLY = None
    except OSError:
        log.warning("Nur-Lese-Key konnte nicht gelesen werden (%s).", state.API_KEY_READONLY_FILE)
        return
    if state.REQUIRE_API_KEY and not state.API_KEY_READONLY:
        try:
            os.makedirs(os.path.dirname(state.API_KEY_READONLY_FILE) or ".", exist_ok=True)
            key = secrets.token_urlsafe(32)
            with open(state.API_KEY_READONLY_FILE, "w", encoding="utf-8") as f:
                f.write(key)
            try:
                os.chmod(state.API_KEY_READONLY_FILE, 0o600)
            except OSError:
                pass
            state.API_KEY_READONLY = key
            log.info("Neuen Nur-Lese-Key generiert und gespeichert (%s).", state.API_KEY_READONLY_FILE)
        except OSError:
            log.exception("Nur-Lese-Key konnte nicht gespeichert werden")


def apply_security_config(config: dict, config_internal: dict) -> None:
    """Nur-Lese-Key-Quelle und IP-Freigabe aus der Konfiguration übernehmen (Aufrufer hält config_lock)."""
    state.API_KEY_READONLY = (os.getenv("BRIDGE_API_KEY_READONLY") or config_internal.get("api_key_readonly")
                              or config.get("api_key_readonly") or None)
    default_file = os.path.join(os.path.dirname(state.API_KEY_FILE) or ".", "api_key_readonly.txt")
    state.API_KEY_READONLY_FILE = (config_internal.get("api_key_readonly_file")
                                   or config.get("api_key_readonly_file") or default_file)
    state.CONTROL_ALLOWED_NETS = parse_allowed_nets(config.get("control_allowed_ips"))


def parse_allowed_nets(values) -> list:
    """control_allowed_ips → Liste von Netzen; ungültige Einträge werden ignoriert (Validierung vorher)."""
    nets = []
    for value in values or []:
        try:
            nets.append(ipaddress.ip_network(str(value), strict=False))
        except ValueError:
            log.warning("Ungültiger Eintrag in control_allowed_ips ignoriert: %r", value)
    return nets


def _client_allowed() -> bool:
    nets = state.CONTROL_ALLOWED_NETS
    if not nets:
        return True
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    return any(address in net for net in nets)


def _key_matches(expected: Optional[str]) -> bool:
    supplied = request.headers.get("X-API-Key") or ""
    return bool(expected) and secrets.compare_digest(supplied.encode(), expected.encode())


def require_api_key(f: Callable) -> Callable:
    """Voller Zugriff (Steuern): Haupt-Key und – falls gesetzt – erlaubte Quell-IP."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _client_allowed():
            log.warning("Steuerbefehl von nicht freigegebener Adresse %s abgelehnt: %s",
                        request.remote_addr, request.path)
            return jsonify({"error": "forbidden: Quelladresse nicht in control_allowed_ips"}), 403
        if not state.REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not state.API_KEY:
            return jsonify({"error": "server_misconfigured: API key required but not set"}), 503
        if not _key_matches(state.API_KEY):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def require_read_key(f: Callable) -> Callable:
    """Lesender Zugriff: Haupt-Key oder Nur-Lese-Key; keine IP-Einschränkung."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not state.REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not state.API_KEY and not state.API_KEY_READONLY:
            return jsonify({"error": "server_misconfigured: API key required but not set"}), 503
        if not (_key_matches(state.API_KEY) or _key_matches(state.API_KEY_READONLY)):
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
