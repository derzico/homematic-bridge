# homematic_bridge.py

import yaml
import json
import logging
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from base64 import b64decode
import websocket

# --- Load config ---

def load_config(path='config.yaml'):
    with open(path, 'r') as f:
        cfg = yaml.safe_load(f)
    required = ['homematic_url', 'homematic_token', 'bridge_user', 'bridge_pass']
    missing = [k for k in required if k not in cfg or not cfg[k]]
    if missing:
        raise ValueError(f"Fehlende Konfigurationswerte: {', '.join(missing)}")
    return cfg

config = load_config()

# --- Setup Logging ---

log = logging.getLogger("bridge")
log.setLevel(getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO))

if config.get("log_file"):
    handler = TimedRotatingFileHandler(config["log_file"], when="midnight", backupCount=7)
    handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s'))
    log.addHandler(handler)
else:
    logging.basicConfig(level=log.level, format='[%(asctime)s] [%(levelname)s] %(message)s')

# --- WebSocket Handler ---

ws = None

def send_plugin_state():
    msg = {
        "type": "PluginStateResponse",
        "pluginState": {
            "pluginReadinessStatus": "READY",
            "message": "Plugin bereit"
        }
    }
    if ws:
        ws.send(json.dumps(msg))


def ws_loop():
    global ws
    headers = {"Authorization": f"Bearer {config['homematic_token']}"}
    while True:
        try:
            ws = websocket.create_connection(config['homematic_url'], header=headers)
            log.info("WebSocket verbunden")
            send_plugin_state()
            while True:
                msg = ws.recv()
                log.debug(f"Empfangen: {msg}")
        except Exception as e:
            log.error(f"WebSocket Fehler: {e}")
            time.sleep(5)

# --- HTTP Handler ---

class SimpleHandler(BaseHTTPRequestHandler):
    def _auth_failed(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Bridge"')
        self.end_headers()

    def _parse_auth(self):
        auth = self.headers.get('Authorization')
        if not auth or not auth.startswith('Basic '):
            return False
        raw = b64decode(auth[6:]).decode('utf-8')
        user, pw = raw.split(':', 1)
        return user == config['bridge_user'] and pw == config['bridge_pass']

    def do_GET(self):
        if not self._parse_auth():
            log.warning(f"Unauthorized access from {self.client_address[0]}")
            self._auth_failed()
            return

        parsed = urlparse(self.path)
        if parsed.path != "/setSwitch":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        device = params.get("device", [None])[0]
        state = params.get("state", [None])[0]

        if not device or state not in ("on", "off"):
            self.send_error(400, "Fehlende oder ungültige Parameter")
            return

        if not ws:
            self.send_error(503, "WebSocket nicht verbunden")
            return

        try:
            msg = {
                "type": "ControlResponse",
                "control": {
                    "deviceId": device,
                    "property": {
                        "type": "SwitchState",
                        "value": state.upper()
                    }
                }
            }
            ws.send(json.dumps(msg))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Befehl an {device} gesendet: {state}".encode())
        except Exception as e:
            log.error(f"Fehler beim Senden: {e}")
            self.send_error(500)

# --- Start Threads ---

if __name__ == "__main__":
    threading.Thread(target=ws_loop, daemon=True).start()
    log.info("Starte HTTP Server auf Port 8080")
    server = HTTPServer(("0.0.0.0", 8080), SimpleHandler)
    server.serve_forever()
