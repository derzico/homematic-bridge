# SPDX-License-Identifier: Apache-2.0
# app/adapters/hmip_messages.py – HmIP WebSocket-Nachrichten

import json
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from websocket import WebSocket

from config.loader import load_config

# Konfiguration laden (inkl. Token sicherstellen)
config: Dict[str, Any] = load_config()
log = logging.getLogger("bridge-ws")

PLUGIN_ID: Optional[str] = config.get("plugin_id")
FRIENDLY_NAME: Optional[Dict[str, str]] = config.get("friendly_name")


def send_plugin_state(ws: WebSocket, msg_id: Optional[str] = None) -> None:
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
        log.exception("Fehler beim Senden von PLUGIN_STATE_RESPONSE")

def _build_hmip_request(path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
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

def send_get_system_state(ws: WebSocket) -> str:
    rid, payload = _build_hmip_request("/hmip/home/getSystemState", {})
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → getSystemState gesendet.")
        return rid
    except Exception as e:
        log.exception("Fehler beim Senden von getSystemState")
        return rid  # trotzdem zurückgeben, damit caller ggf. aufräumt

def send_config_template_response(ws: WebSocket, msg_id: str, current_log_level: str) -> None:
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
        log.exception("Fehler beim Senden von CONFIG_TEMPLATE_RESPONSE")


def send_config_update_response(ws: WebSocket, msg_id: str, status: str = "APPLIED", message: Optional[str] = None) -> None:
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
        log.exception("Fehler beim Senden von CONFIG_UPDATE_RESPONSE")


def send_hmip_set_dim_level(ws: WebSocket, device_id: str, dim_level: float, channel_index: int = 1) -> str:
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
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST (setDimLevel)")
        return rid


def send_hmip_set_hue_saturation_dim_level(ws: WebSocket, device_id: str, hue: int, saturation_level: float, dim_level: float, channel_index: int = 1) -> str:
    body = {
        "hue": hue,
        "saturationLevel": saturation_level,
        "dimLevel": dim_level,
        "channelIndex": channel_index,
        "deviceId": device_id,
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setHueSaturationDimLevel", body)
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → hue={hue}° sat={saturation_level:.2f} dim={dim_level:.2f} (id={rid})")
        return rid
    except Exception as e:
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST (setHueSaturationDimLevel)")
        return rid


def send_hmip_set_switch(ws: WebSocket, device_id: str, state: bool, channel_index: int = 0) -> str:
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
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST")
        return rid


def send_hmip_set_alarm_signal_optical(ws: WebSocket, device_id: str, signal: str, channel_index: int = 2) -> str:
    """Optisches Alarmsignal eines Rauchmelders / Alarmsirene setzen.

    signal: "FULL_ALARM" | "INTRUSION_ALARM" | "PRE_ALARM" | "NO_ALARM"
    """
    body = {
        "opticalAlarmSignal": signal,
        "channelIndex": channel_index,
        "deviceId": device_id,
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setAlarmSignalOptical", body)
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → setAlarmSignalOptical device=%s signal=%s (id=%s)", device_id, signal, rid)
        return rid
    except Exception:
        log.exception("Fehler beim Senden von setAlarmSignalOptical")
        return rid


def send_hmip_set_alarm_signal_acoustic(ws: WebSocket, device_id: str, signal: str, channel_index: int = 2) -> str:
    """Akustisches Alarmsignal eines Rauchmelders / Alarmsirene setzen.

    signal: "FULL_ALARM" | "INTRUSION_ALARM" | "PRE_ALARM" | "NO_ALARM"
    """
    body = {
        "acousticAlarmSignal": signal,
        "channelIndex": channel_index,
        "deviceId": device_id,
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setAlarmSignalAcoustic", body)
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → setAlarmSignalAcoustic device=%s signal=%s (id=%s)", device_id, signal, rid)
        return rid
    except Exception:
        log.exception("Fehler beim Senden von setAlarmSignalAcoustic")
        return rid


def send_hmip_set_point_temperature(ws: WebSocket, device_id: str, temperature: float, channel_index: int = 1) -> str:
    """Solltemperatur eines Heizkörperthermostats oder Wandthermostats setzen."""
    body = {
        "setPointTemperature": round(float(temperature), 1),
        "channelIndex": channel_index,
        "deviceId": device_id,
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setSetPointTemperature", body)
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → setSetPointTemperature device=%s temp=%.1f (id=%s)", device_id, temperature, rid)
        return rid
    except Exception:
        log.exception("Fehler beim Senden von setSetPointTemperature")
        return rid