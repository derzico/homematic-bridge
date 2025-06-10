# main.py

import json
import threading
import time
import logging
import yaml
import websocket
import ssl
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, send_file
from config.loader import load_config, load_internal_config
from app.messages import send_plugin_state, send_hmip_set_switch, send_get_system_state
from app.utils import save_system_state
from app.generate_html import generate_device_overview

# Konfiguration laden (inkl. Token sicherstellen)
config = load_config()
config_internal = load_internal_config()
PLUGIN_ID = config.get("plugin_id")

# Logging konfigurieren
log = logging.getLogger("bridge-ws")
log.setLevel(getattr(logging, config_internal.get("log_level", "INFO").upper(), logging.INFO))
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

# Konsole
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)

# Datei mit Rotation
if config_internal.get("log_file"):
    file_handler = TimedRotatingFileHandler(
        filename=config_internal["log_file"],
        when="midnight",
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

# Globale WebSocket-Verbindung
conn = None

def ws_loop():
    global conn
    headers = {
        "authtoken": config["homematic_token"],
        "plugin-id": PLUGIN_ID
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
    elif ssl_verify:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
        log.info("[SSL] Systemweit vertrauenswürdige Zertifikate werden verwendet.")
    else:
        sslopt = {"cert_reqs": ssl.CERT_NONE}
        log.warning("[SSL] Verbindung ohne Zertifikatsprüfung (unsicher)")

    while True:
        try:
            log.info("Verbinde zu WebSocket (Port 9001)...")
            conn = websocket.create_connection(url, header=headers, sslopt=sslopt)
            log.info("WebSocket-Verbindung hergestellt.")
            send_plugin_state(conn)
            send_get_system_state(conn)

            while True:
                msg = conn.recv()
                log.info(f"Nachricht empfangen: {msg}")
                try:
                    msg_data = json.loads(msg)
                    if msg_data.get("type") == "PluginStateRequest":
                        msg_id = msg_data.get("id")
                        send_plugin_state(conn, msg_id=msg_id)
                    elif msg_data.get("type") == "HMIP_SYSTEM_RESPONSE":
                        save_system_state(msg_data)
                except Exception as e:
                    log.error(f"Fehler beim Verarbeiten der Nachricht: {e}")
        except Exception as e:
            log.error(f"WebSocket Fehler: {e}")
            time.sleep(5)

# Flask HTTP-Server
app = Flask(__name__)

@app.route("/devices/html")
def serve_html_overview():
    generate_device_overview(config_internal["system_state_path"], "static/device_overview.html")
    return send_file("static/device_overview.html")

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
    send_hmip_set_switch(conn, device_id, state)
    return jsonify({"status": f"Befehl an {device_id} gesendet: {'ON' if state else 'OFF'}"}), 200

if __name__ == '__main__':
    # Starte WebSocket-Thread
    threading.Thread(target=ws_loop, daemon=True).start()
    log.info("Starte HTTP Server auf Port 8080")
    app.run(host="0.0.0.0", port=8080)