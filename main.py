# SPDX-License-Identifier: Apache-2.0
# main.py

import json
import sys
import threading
import time
import logging
import yaml
import websocket
import ssl
import os
import random
import certifi
import secrets
from functools import wraps
from typing import Optional, Dict, Any
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, send_file, Response
from config.loader import load_config, load_internal_config
from app.messages import send_plugin_state, send_hmip_set_switch, send_get_system_state
from app.utils import save_system_state
from app.generate_html import generate_device_overview, generate_device_detail_html
from threading import Lock

# Konfiguration laden (inkl. Token sicherstellen)
config = load_config()
config_internal = load_internal_config()
PLUGIN_ID = config.get("plugin_id")

# Health-/Pending-Konfiguration
STALE_SEC = float(config_internal.get("health_stale_seconds", 60.0))       # Snapshot gilt nach X Sekunden als alt
PENDING_TTL = float(config_internal.get("pending_ttl_seconds", 60.0))      # wie lange wir auf ACKs warten

# --- API-Key laden / generieren ---
API_KEY = os.getenv("BRIDGE_API_KEY") or config_internal.get("api_key") or config.get("api_key")
REQUIRE_API_KEY = bool(config_internal.get("require_api_key", config.get("require_api_key", True)))
API_KEY_FILE = config_internal.get("api_key_file", "data/api_key.txt")

def _ensure_api_key():
    """Sorgt dafür, dass ein API-Key vorhanden ist.
       Reihenfolge: ENV -> config -> api_key_file -> automatisch generieren (falls Pflicht)."""
    global API_KEY
    if API_KEY:
        return
    # 1) Aus Datei laden
    try:
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            API_KEY = f.read().strip()
    except FileNotFoundError:
        API_KEY = None
    # 2) Generieren, wenn Pflicht und noch None
    if REQUIRE_API_KEY and not API_KEY:
        try:
            os.makedirs(os.path.dirname(API_KEY_FILE) or ".", exist_ok=True)
            API_KEY = secrets.token_urlsafe(32)
            with open(API_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(API_KEY)
            try:
                os.chmod(API_KEY_FILE, 0o600)  # unter Windows harmless
            except Exception:
                pass
            logging.getLogger("bridge-ws").info("Neuen API-Key generiert und gespeichert (%s).", API_KEY_FILE)
        except Exception as e:
            logging.getLogger("bridge-ws").error("API-Key konnte nicht gespeichert werden: %s", e)

_ensure_api_key()

if not config.get("homematic_token"):
    logging.getLogger("bridge-ws").error(
        "Kein homematic_token in config.yaml! "
        "Bitte zuerst 'python app/request_token.py' ausführen um einen Token zu generieren."
    )
    sys.exit(1)

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not REQUIRE_API_KEY:
            return f(*args, **kwargs)
        if not API_KEY:
            return jsonify({"error": "server_misconfigured: API key required but not set"}), 503
        if request.headers.get("X-API-Key") != API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def require_web_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not REQUIRE_API_KEY:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.password != API_KEY:
            return Response(
                "Authentifizierung erforderlich.",
                401,
                {"WWW-Authenticate": 'Basic realm="Homematic Bridge"'}
            )
        return f(*args, **kwargs)
    return wrapper

# Logging konfigurieren
log = logging.getLogger("bridge-ws")
log.setLevel(getattr(logging, config_internal.get("log_level", "INFO").upper(), logging.INFO))
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

# Konsole
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)
log.propagate = False

# Datei mit Rotation (+ Ordner automatisch anlegen)
if config_internal.get("log_file"):
    log_path = config_internal["log_file"]
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)  # <— Ordner (z. B. logs/) anlegen
    except Exception as e:
        log.warning(f"Log-Verzeichnis konnte nicht angelegt werden: {e}")

    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

# Globale WebSocket-Verbindung + Locks
conn = None
send_lock = Lock()

# Pending-Registry: id -> {"path": str, "ts": float}
pending_lock = Lock()
pending: Dict[str, Dict[str, Any]] = {}

def _register_pending(req_id: str, path: str) -> None:
    if not req_id:
        return
    with pending_lock:
        pending[req_id] = {"path": path, "ts": time.time()}

def _resolve_pending(req_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not req_id:
        return None
    with pending_lock:
        return pending.pop(req_id, None)

def _cleanup_pending() -> None:
    now = time.time()
    removed = 0
    with pending_lock:
        to_del = [rid for rid, meta in pending.items() if now - meta.get("ts", 0) > PENDING_TTL]
        for rid in to_del:
            pending.pop(rid, None)
            removed += 1
    if removed:
        log.debug("Pending-Requests bereinigt: %d", removed)

# Snapshot/Health Helpers
def _load_snapshot() -> Optional[Dict[str, Any]]:
    try:
        with open(config_internal["system_state_path"], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _get_nested(d, keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def _devices_count_from_snapshot(snap: Optional[Dict[str, Any]]) -> int:
    if not isinstance(snap, dict):
        return 0
    candidates = [
        ("body", "devices"),
        ("body", "home", "devices"),
        ("body", "body", "devices"),
        ("body", "body", "home", "devices"),
    ]
    for path in candidates:
        devs = _get_nested(snap, path)
        if isinstance(devs, dict):
            return len(devs)
        if isinstance(devs, list):
            return sum(1 for x in devs if isinstance(x, dict))
    return 0

def _snapshot_age_ms(path: str) -> Optional[int]:
    try:
        mtime = os.path.getmtime(path)
        return int((time.time() - mtime) * 1000)
    except Exception:
        return None

def ws_loop():
    global conn
    headers = {
        "authtoken": config["homematic_token"],
        "plugin-id": PLUGIN_ID,
        "hmip-system-events": "true"
    }
    url = f"wss://{config['homematic_hcu']}:9001"

    ssl_verify = config.get("ssl_verify", False)
    cert_path = config.get("ssl_cert_path")

    if ssl_verify and cert_path:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": cert_path}
        log.info("[SSL] Zertifikatspfad verwendet: %s", cert_path)
    elif ssl_verify:
        sslopt = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "ca_certs": certifi.where()
        }
        log.info("[SSL] Certifi CA-Bundle wird verwendet.")
    else:
        sslopt = {"cert_reqs": ssl.CERT_NONE}
        log.warning("[SSL] Verbindung ohne Zertifikatsprüfung (unsicher)")

    backoff = 1.0  # Sekunden
    while True:
        try:
            log.info("Verbinde zu WebSocket (Port 9001)...")
            conn = websocket.create_connection(url, header=headers, sslopt=sslopt, timeout=10)
            conn.settimeout(30)  # recv-Timeout: 30s, danach Keepalive-Ping
            log.info("WebSocket-Verbindung hergestellt.")
            backoff = 1.0  # Backoff bei Erfolg zurücksetzen

            # Initialen Zustand anfordern (PluginState + Vollsnapshot) UND registrieren
            with send_lock:
                send_plugin_state(conn)
                rid = send_get_system_state(conn)
            _register_pending(rid, "/hmip/home/getSystemState")

            while True:
                try:
                    msg = conn.recv()
                except websocket.WebSocketTimeoutException:
                    # Keine Daten für 30s → Ping senden um Verbindung zu prüfen
                    try:
                        with send_lock:
                            conn.ping()
                        log.debug("Keepalive-Ping gesendet.")
                    except Exception as ping_err:
                        log.warning("Keepalive-Ping fehlgeschlagen: %s", ping_err)
                        raise
                    continue
                log.debug(f"Nachricht empfangen: {msg}")
                try:
                    msg_data = json.loads(msg)
                    msg_type = msg_data.get("type")

                    if msg_type == "PLUGIN_STATE_REQUEST":
                        msg_id = msg_data.get("id")
                        with send_lock:
                            send_plugin_state(conn, msg_id=msg_id)

                    elif msg_type == "HMIP_SYSTEM_RESPONSE":
                        # Response korrelieren und gezielt handeln
                        rid = msg_data.get("id")
                        meta = _resolve_pending(rid)
                        code = (msg_data.get("body") or {}).get("code")

                        if meta:
                            path = meta.get("path")
                            if path == "/hmip/home/getSystemState":
                                # Vollsnapshot -> speichern
                                save_system_state(msg_data)
                                log.info("getSystemState → Snapshot gespeichert (code=%s)", code)
                            else:
                                # ACK zu Steuerbefehlen
                                if code == 200:
                                    log.info("ACK %s OK (id=%s)", path, rid)
                                else:
                                    log.warning("ACK %s Fehler (code=%s, id=%s)", path, code, rid)
                        else:
                            # Unkorrelierte Response: zur Sicherheit nur loggen
                            log.debug("Unkorrelierte HMIP_SYSTEM_RESPONSE (id=%s, code=%s) ignoriert.", rid, code)

                        _cleanup_pending()

                    elif msg_type == "HMIP_SYSTEM_EVENT":
                        # Delta-Event -> in bestehenden Snapshot mergen
                        save_system_state(msg_data)

                    else:
                        log.debug("Unbehandelter Nachrichtentyp: %r", msg_type)

                except Exception as e:
                    log.error(f"Fehler beim Verarbeiten der Nachricht: {e}")

        except Exception as e:
            log.error(f"WebSocket Fehler: {e}")
            # Verbindung zurücksetzen
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            finally:
                conn = None
            # Pending leeren (neue Session, alte IDs sind wertlos)
            with pending_lock:
                pending.clear()
            # Exponentielles Backoff + Jitter
            sleep_for = backoff + random.uniform(0, 0.3 * backoff)
            log.info("Reconnect in %.1fs (Backoff: %.1fs)", sleep_for, backoff)
            time.sleep(sleep_for)
            backoff = min(backoff * 2.0, 60.0)

# Flask HTTP-Server
app = Flask(__name__)

@app.route("/devices/html")
@require_web_auth
def serve_html_overview():
    generate_device_overview(config_internal["system_state_path"], "static/device_overview.html")
    return send_file("static/device_overview.html")

@app.route("/devices/<device_id>", methods=["GET"])
@require_web_auth
def serve_device_detail(device_id):
    html_str = generate_device_detail_html(config_internal["system_state_path"], device_id)
    return Response(html_str, mimetype="text/html; charset=utf-8")

# --- Sichere POST-Route mit API-Key ---
@app.post("/hmipSwitch")
@require_api_key
def hmip_switch_post():
    global conn
    if conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    data = request.get_json(silent=True, force=True) or {}
    device_id = data.get("device")
    state = data.get("on")
    channel_index = data.get("channelIndex", 0)

    if not device_id or not isinstance(state, bool):
        return jsonify({"error": "Ungültige Parameter: device (str), on (bool), optional channelIndex (int)"}), 400

    with send_lock:
        rid = send_hmip_set_switch(conn, device_id, state, channel_index)
    _register_pending(rid, "/hmip/device/control/setSwitchState")

    return jsonify({"status": f"{device_id}: {'ON' if state else 'OFF'}", "request_id": rid}), 200

# --- GET-Route: lokal ohne Key, extern mit X-API-Key (z.B. Loxone) ---
@app.get("/hmipSwitch")
def hmip_switch_get():
    local = request.remote_addr in {"127.0.0.1", "::1"}
    if not local:
        if not REQUIRE_API_KEY or not API_KEY:
            return jsonify({"error": "Nur lokal erlaubt oder X-API-Key erforderlich"}), 403
        if request.headers.get("X-API-Key") != API_KEY:
            return jsonify({"error": "unauthorized"}), 401

    global conn
    if conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    device_id = request.args.get("device")
    on_param = request.args.get("on")
    try:
        channel_index = int(request.args.get("channelIndex", "0"))
    except ValueError:
        return jsonify({"error": "channelIndex muss eine Zahl sein"}), 400

    if not device_id or on_param not in {"true", "false"}:
        return jsonify({"error": "Ungültige Parameter"}), 400

    state = (on_param == "true")
    with send_lock:
        rid = send_hmip_set_switch(conn, device_id, state, channel_index)
    _register_pending(rid, "/hmip/device/control/setSwitchState")

    return jsonify({"status": f"{device_id}: {'ON' if state else 'OFF'}", "request_id": rid}), 200

@app.route("/healthz", methods=["GET"])
def healthz():
    """
    Einfache Health-Checks für Monitoring/Orchestrierung.
    - ws_connected: True/False
    - snapshot_age_ms: Alter der gespeicherten system_state.json (mtime)
    - devices_count: Anzahl erkannter Geräte im Snapshot
    - pending_requests: Anzahl offener Requests ohne Response
    - status: ok | degraded | unhealthy
    """
    ws_connected = conn is not None
    path = config_internal["system_state_path"]
    age_ms = _snapshot_age_ms(path)
    snap = _load_snapshot()
    devices_count = _devices_count_from_snapshot(snap)
    with pending_lock:
        pending_count = len(pending)

    status = "ok"
    if not ws_connected:
        status = "degraded"
    if age_ms is None:
        status = "degraded" if ws_connected else "unhealthy"
    elif age_ms > STALE_SEC * 1000:
        status = "degraded" if ws_connected else "unhealthy"

    return jsonify({
        "ws_connected": ws_connected,
        "snapshot_age_ms": age_ms,
        "devices_count": devices_count,
        "pending_requests": pending_count,
        "status": status
    }), 200

if __name__ == '__main__':
    threading.Thread(target=ws_loop, daemon=True).start()
    host, port = "0.0.0.0", 8080
    try:
        from waitress import serve
        log.info("Starte HTTP Server mit Waitress auf Port %s", port)
        serve(app, host=host, port=port, threads=8)  # threads anpassen (z. B. 8–16)
    except ImportError:
        log.info("Waitress nicht installiert – nutze Flask-Dev-Server")
        app.run(host=host, port=port)
