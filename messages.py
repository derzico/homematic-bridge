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

def send_get_system_state(ws):
    payload = {
        "pluginId": PLUGIN_ID,
        "id": str(uuid.uuid4()),
        "type": "HMIP_SYSTEM_REQUEST",
        "body": {
            "path": "/hmip/home/getSystemState",
            "body": {}
        }
    }

    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → getSystemState gesendet.")
    except Exception as e:
        log.error(f"Fehler beim Senden von getSystemState: {e}")

def send_hmip_set_switch(ws, device_id: str, state: bool, channel_index: int = 0):
    payload = {
        "pluginId": PLUGIN_ID,
        "id": str(uuid.uuid4()),
        "type": "HMIP_SYSTEM_REQUEST",
        "body": {
            "path": "/hmip/device/control/setSwitchState",
            "body": {
                "on": state,
                "channelIndex": channel_index,
                "deviceId": device_id
            }
        }
    }

    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → {'ON' if state else 'OFF'}")
    except Exception as e:
        log.error(f"Fehler beim Senden von HMIP_SYSTEM_REQUEST: {e}")