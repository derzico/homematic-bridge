# messages.py

import json
import logging
import uuid
from config import load_config

# Konfiguration laden (inkl. Token sicherstellen)
config = load_config()

log = logging.getLogger("bridge-ws")

PLUGIN_ID = config.get("plugin_id")
FRIENDLY_NAME = config.get("friendly_name")

def send_plugin_state(ws, msg_id=None):
    response = {
        "pluginId": PLUGIN_ID,
        "id": msg_id or str(uuid.uuid4()),
        "type": "PLUGIN_STATE_RESPONSE",
        "body": {
            "pluginReadinessStatus": "READY",
            "friendlyName": FRIENDLY_NAME
        }
    }

    try:
        ws.send(json.dumps(response))
        log.info("PLUGIN_STATE_RESPONSE gesendet.")
    except Exception as e:
        log.error(f"Fehler beim Senden von PLUGIN_STATE_RESPONSE: {e}")