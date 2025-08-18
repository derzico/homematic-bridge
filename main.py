# main.py

import json
import threading
import time
import logging
import yaml
import websocket
import ssl
import os
import random
import certifi
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

# Health-Konfiguration
STALE_SEC = float(config_internal.get("health_stale_seconds", 60.0))  # Snapshot gilt nach X Sekunden als alt

# Logging konfigurieren
log = logging.getLogger("bridge-ws")
log.setLevel(getattr(logging, config_internal.get("log_level", "INFO").upper(), logging.INFO))
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

# Konsole
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)

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

# Globale WebSocket-Verbindung + Sende-Lock
conn = None
send_lock = Lock()

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
    # alle gängigen Pfade prüfen (Dict oder Liste)
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
            # nur valide Device-Dicts zählen
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
        sslopt = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "ca_certs": cert_path
        }
        log.info("[SSL] Zertifikatspfad verwendet: %s", cert_path)
    # im ws_loop() bei ssl_verify==True und ohne cert_path:
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
            log.info("WebSocket-Verbindung hergestellt.")
            backoff = 1.0  # Backoff bei Erfolg zurücksetzen

            # Initialen Zustand anfordern (PluginState + Vollsnapshot)
            with send_lock:
                send_plugin_state(conn)
                send_get_system_state(conn)

            while True:
                msg = conn.recv()
                # Eingehende Payload nur auf DEBUG loggen (kann sensible Daten enthalten)
                log.debug(f"Nachricht empfangen: {msg}")
                try:
                    msg_data = json.loads(msg)
                    msg_type = msg_data.get("type")

                    if msg_type == "PluginStateRequest":
                        msg_id = msg_data.get("id")
                        with send_lock:
                            send_plugin_state(conn, msg_id=msg_id)

                    elif msg_type == "HMIP_SYSTEM_RESPONSE":
                        # Vollzustand -> direkt speichern (komplette JSON)
                        save_system_state(msg_data)

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
            # Exponentielles Backoff + Jitter
            sleep_for = backoff + random.uniform(0, 0.3 * backoff)
            log.info("Reconnect in %.1fs (Backoff: %.1fs)", sleep_for, backoff)
            time.sleep(sleep_for)
            backoff = min(backoff * 2.0, 60.0)

# Flask HTTP-Server
app = Flask(__name__)

@app.route("/devices/html")
def serve_html_overview():
    generate_device_overview(config_internal["system_state_path"], "static/device_overview.html")
    return send_file("static/device_overview.html")

@app.route("/devices/<device_id>", methods=["GET"])
def serve_device_detail(device_id):
    html_str = generate_device_detail_html(config_internal["system_state_path"], device_id)
    return Response(html_str, mimetype="text/html; charset=utf-8")

@app.route("/hmipSwitch", methods=["GET"])
def hmip_switch():
    global conn
    if conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    device_id = request.args.get("device")
    on_param = request.args.get("on")

    if not device_id or on_param not in ["true", "false"]:
        return jsonify({"error": "Ungültige Parameter"}), 400

    state = on_param == "true"
    # Thread-sicher senden
    with send_lock:
        send_hmip_set_switch(conn, device_id, state)

    return jsonify({"status": f"Befehl an {device_id} gesendet: {'ON' if state else 'OFF'}"}), 200

@app.route("/healthz", methods=["GET"])
def healthz():
    """
    Einfache Health-Checks für Monitoring/Orchestrierung.
    - ws_connected: True/False
    - snapshot_age_ms: Alter der gespeicherten system_state.json (mtime)
    - devices_count: Anzahl erkannter Geräte im Snapshot
    - status: ok | degraded | unhealthy
    """
    ws_connected = conn is not None
    path = config_internal["system_state_path"]
    age_ms = _snapshot_age_ms(path)
    snap = _load_snapshot()
    devices_count = _devices_count_from_snapshot(snap)

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