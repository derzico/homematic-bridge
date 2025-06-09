# homematic_ws_connect.py

import json
import threading
import time
import logging
import yaml
import websocket
import ssl
from logging.handlers import TimedRotatingFileHandler
from request_token import ensure_token

CONFIG_FILE = "config.yaml"
PLUGIN_ID = "de.schnell.niclas.plugin.example"

# Konfiguration laden (inkl. Token sicherstellen)
config = ensure_token()

# Logging konfigurieren
log = logging.getLogger("bridge-ws")
log.setLevel(getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO))
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

# Konsole
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)

# Datei mit Rotation
if config.get("log_file"):
    file_handler = TimedRotatingFileHandler(
        filename=config["log_file"],
        when="midnight",
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

def ws_loop():
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
            ws = websocket.create_connection(url, header=headers, sslopt=sslopt)
            log.info("WebSocket-Verbindung hergestellt.")
            while True:
                msg = ws.recv()
                log.info(f"Nachricht empfangen: {msg}")
        except Exception as e:
            log.error(f"WebSocket Fehler: {e}")
            time.sleep(5)

if __name__ == '__main__':
    threading.Thread(target=ws_loop, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Beendet durch Benutzer.")
