# SPDX-License-Identifier: Apache-2.0
# main.py – Einstiegspunkt: Konfiguration, Logging, Flask-App, WS-Thread starten

import logging
import os
import secrets
import sys
import threading
from datetime import timedelta
from logging.handlers import TimedRotatingFileHandler

from flask import Flask

import app.state as state
from app.auth import _ensure_api_key
from app.routes import bp as routes_bp
from app.websocket_handler import ws_loop
from config.loader import load_config, load_internal_config

# ── Konfiguration laden ───────────────────────────────────────────────────────
config          = load_config()
config_internal = load_internal_config()

# ── Shared State initialisieren ───────────────────────────────────────────────
state.config          = config
state.config_internal = config_internal
state.STALE_SEC       = float(config_internal.get("health_stale_seconds", 60.0))
state.PENDING_TTL     = float(config_internal.get("pending_ttl_seconds", 60.0))
state.REQUIRE_API_KEY = bool(config_internal.get("require_api_key", config.get("require_api_key", True)))
state.API_KEY_FILE    = config_internal.get("api_key_file", "data/api_key.txt")
state.API_KEY         = os.getenv("BRIDGE_API_KEY") or config_internal.get("api_key") or config.get("api_key")

_loxone_cfg        = config.get("loxone") or {}
state.LOXONE_HOST  = _loxone_cfg.get("miniserver_ip") or ""
state.LOXONE_UDP_PORT = int(_loxone_cfg.get("udp_port") or 7777)

_ensure_api_key()

if not config.get("homematic_token"):
    logging.getLogger("bridge-ws").error(
        "Kein homematic_token in config.yaml! "
        "Bitte zuerst 'python app/request_token.py' ausführen um einen Token zu generieren."
    )
    sys.exit(1)

# ── Logging konfigurieren ─────────────────────────────────────────────────────
log = logging.getLogger("bridge-ws")
log.setLevel(getattr(logging, config_internal.get("log_level", "INFO").upper(), logging.INFO))
log.propagate = False

formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)

if config_internal.get("log_file"):
    log_path = config_internal["log_file"]
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    except Exception as e:
        log.warning("Log-Verzeichnis konnte nicht angelegt werden: %s", e)
    file_handler = TimedRotatingFileHandler(log_path, when="midnight", backupCount=7, encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

# ── Flask-App erstellen ───────────────────────────────────────────────────────
def _load_or_create_secret_key(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(key)
        return key

app = Flask(__name__)
app.secret_key = _load_or_create_secret_key("data/secret_key.bin")
app.permanent_session_lifetime = timedelta(hours=8)
app.register_blueprint(routes_bp)

# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=ws_loop, daemon=True).start()
    host, port = "0.0.0.0", 8080
    try:
        from waitress import serve
        log.info("Starte HTTP Server mit Waitress auf Port %s", port)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        log.info("Waitress nicht installiert – nutze Flask-Dev-Server")
        app.run(host=host, port=port)
