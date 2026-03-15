# SPDX-License-Identifier: Apache-2.0

# messages.py

import json
import logging
import uuid
from config.loader import load_config, load_internal_config

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

def _build_hmip_request(path: str, body: dict | None = None) -> tuple[str, dict]:
    rid = str(uuid.uuid4())
    payload = {
        "pluginId": PLUGIN_ID,
        "id": rid,
        "type": "HMIP_SYSTEM_REQUEST",
        "body": {
            "path": path,
            "body": body or {}
        }
    }
    return rid, payload

def send_get_system_state(ws) -> str:
    rid, payload = _build_hmip_request("/hmip/home/getSystemState", {})
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → getSystemState gesendet.")
        return rid
    except Exception as e:
        log.error(f"Fehler beim Senden von getSystemState: {e}")
        return rid  # trotzdem zurückgeben, damit caller ggf. aufräumt

def send_config_template_response(ws, msg_id: str, current_log_level: str) -> None:
    response = {
        "pluginId": PLUGIN_ID,
        "id": msg_id or str(uuid.uuid4()),
        "type": "CONFIG_TEMPLATE_RESPONSE",
        "body": {
            "properties": {
                "log_level": {
                    "dataType":     "ENUM",
                    "friendlyName": "Log-Level",
                    "description":  "Detailgrad der Protokollierung (debug / info / warning / error)",
                    "currentValue": current_log_level,
                    "values":       ["debug", "info", "warning", "error"],
                    "required":     True,
                    "order":        1,
                },
            },
        },
    }
    try:
        ws.send(json.dumps(response))
        log.info("CONFIG_TEMPLATE_RESPONSE gesendet.")
    except Exception as e:
        log.error(f"Fehler beim Senden von CONFIG_TEMPLATE_RESPONSE: {e}")


def send_config_update_response(ws, msg_id: str, status: str = "APPLIED", message: str = None) -> None:
    body = {"status": status}
    if message:
        body["message"] = message
    response = {
        "pluginId": PLUGIN_ID,
        "id": msg_id,
        "type": "CONFIG_UPDATE_RESPONSE",
        "body": body,
    }
    try:
        ws.send(json.dumps(response))
        log.info(f"CONFIG_UPDATE_RESPONSE gesendet (status={status}).")
    except Exception as e:
        log.error(f"Fehler beim Senden von CONFIG_UPDATE_RESPONSE: {e}")


def send_hmip_set_dim_level(ws, device_id: str, dim_level: float, channel_index: int = 1) -> str:
    body = {
        "dimLevel": dim_level,
        "channelIndex": channel_index,
        "deviceId": device_id
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setDimLevel", body)
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → dimLevel={dim_level} (id={rid})")
        return rid
    except Exception as e:
        log.error(f"Fehler beim Senden von HMIP_SYSTEM_REQUEST (setDimLevel): {e}")
        return rid


def send_hmip_set_switch(ws, device_id: str, state: bool, channel_index: int = 0) -> str:
    body = {
        "on": state,
        "channelIndex": channel_index,
        "deviceId": device_id
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setSwitchState", body)
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → {'ON' if state else 'OFF'} (id={rid})")
        return rid
    except Exception as e:
        log.error(f"Fehler beim Senden von HMIP_SYSTEM_REQUEST: {e}")
        return rid