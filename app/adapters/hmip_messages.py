# SPDX-License-Identifier: Apache-2.0
# app/adapters/hmip_messages.py – HmIP WebSocket-Nachrichten

import json
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from websocket import WebSocket

import app.state as state

log = logging.getLogger("bridge-ws")


def _plugin_id() -> Optional[str]:
    """Aktuelle plugin_id aus dem Shared State (thread-safe gelesen)."""
    # Command senders already hold send_lock. Avoid acquiring config_lock for
    # active connections (the connection publisher uses config -> send order).
    if state.conn is not None and state.WS_PLUGIN_ID is not None:
        return state.WS_PLUGIN_ID
    with state.config_lock:
        return state.config.get("plugin_id")


def _friendly_name() -> Dict[str, str]:
    with state.config_lock:
        return state.config.get("friendly_name") or {
            "de": "Homematic Bridge",
            "en": "Homematic Bridge",
        }


def send_plugin_state(ws: WebSocket, msg_id: Optional[str] = None) -> None:
    response = {
        "pluginId": _plugin_id(),
        "id": msg_id or str(uuid.uuid4()),
        "type": "PLUGIN_STATE_RESPONSE",
        "body": {
            "pluginReadinessStatus": "READY",
            "friendlyName": _friendly_name()
        }
    }

    try:
        ws.send(json.dumps(response))
        log.info("PLUGIN_STATE_RESPONSE gesendet.")
    except Exception as e:
        log.exception("Fehler beim Senden von PLUGIN_STATE_RESPONSE")

def _build_hmip_request(
    path: str,
    body: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    rid = request_id or str(uuid.uuid4())
    payload = {
        "pluginId": _plugin_id(),
        "id": rid,
        "type": "HMIP_SYSTEM_REQUEST",
        "body": {
            "path": path,
            "body": body or {}
        }
    }
    return rid, payload

def send_get_system_state(ws: WebSocket, request_id: Optional[str] = None) -> str:
    rid, payload = _build_hmip_request("/hmip/home/getSystemState", {}, request_id)
    try:
        ws.send(json.dumps(payload))
        log.info("HMIP_SYSTEM_REQUEST → getSystemState gesendet.")
        return rid
    except Exception:
        log.exception("Fehler beim Senden von getSystemState")
        raise


def send_hmip_request(ws: WebSocket, path: str, body: Dict[str, Any], request_id: Optional[str] = None) -> str:
    """Interner Versand; HTTP-Zugriff erhält niemals einen frei wählbaren Pfad."""
    rid, payload = _build_hmip_request(path, body, request_id)
    ws.send(json.dumps(payload))
    return rid

def send_config_template_response(ws: WebSocket, msg_id: str, current_log_level: str) -> None:
    response = {
        "pluginId": _plugin_id(),
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
        "pluginId": _plugin_id(),
        "id": msg_id,
        "type": "CONFIG_UPDATE_RESPONSE",
        "body": body,
    }
    try:
        ws.send(json.dumps(response))
        log.info(f"CONFIG_UPDATE_RESPONSE gesendet (status={status}).")
    except Exception as e:
        log.exception("Fehler beim Senden von CONFIG_UPDATE_RESPONSE")


def send_hmip_set_dim_level(
    ws: WebSocket, device_id: str, dim_level: float, channel_index: int = 1,
    request_id: Optional[str] = None,
) -> str:
    body = {
        "dimLevel": dim_level,
        "channelIndex": channel_index,
        "deviceId": device_id
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setDimLevel", body, request_id)
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → dimLevel={dim_level} (id={rid})")
        return rid
    except Exception:
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST (setDimLevel)")
        raise


def send_hmip_set_hue_saturation_dim_level(
    ws: WebSocket, device_id: str, hue: int, saturation_level: float,
    dim_level: float, channel_index: int = 1, request_id: Optional[str] = None,
) -> str:
    body = {
        "hue": hue,
        "saturationLevel": saturation_level,
        "dimLevel": dim_level,
        "channelIndex": channel_index,
        "deviceId": device_id,
    }
    rid, payload = _build_hmip_request(
        "/hmip/device/control/setHueSaturationDimLevel", body, request_id
    )
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → hue={hue}° sat={saturation_level:.2f} dim={dim_level:.2f} (id={rid})")
        return rid
    except Exception:
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST (setHueSaturationDimLevel)")
        raise


def send_hmip_set_switch(
    ws: WebSocket, device_id: str, on: bool, channel_index: int = 0,
    request_id: Optional[str] = None,
) -> str:
    body = {
        "on": on,
        "channelIndex": channel_index,
        "deviceId": device_id
    }
    rid, payload = _build_hmip_request("/hmip/device/control/setSwitchState", body, request_id)
    try:
        ws.send(json.dumps(payload))
        log.info(f"HMIP_SYSTEM_REQUEST gesendet für device {device_id} → {'ON' if on else 'OFF'} (id={rid})")
        return rid
    except Exception:
        log.exception("Fehler beim Senden von HMIP_SYSTEM_REQUEST")
        raise


def send_hmip_set_watering_switch(
    ws: WebSocket, device_id: str, on: bool, channel_index: int = 1,
    request_id: Optional[str] = None,
) -> str:
    rid, payload = _build_hmip_request(
        "/hmip/device/control/setWateringSwitchState",
        {"deviceId": device_id, "channelIndex": channel_index, "wateringActive": on},
        request_id,
    )
    ws.send(json.dumps(payload))
    return rid


def send_hmip_set_group_state(
    ws: WebSocket, group_id: str, on: bool,
    request_id: Optional[str] = None,
) -> str:
    """Schaltzustand einer Homematic-IP-Gruppe setzen."""
    body = {"on": on, "groupId": group_id}
    rid, payload = _build_hmip_request(
        "/hmip/group/switching/setState", body, request_id
    )
    try:
        ws.send(json.dumps(payload))
        log.info(
            "HMIP_SYSTEM_REQUEST → group setState group=%s on=%s (id=%s)",
            group_id, on, rid,
        )
        return rid
    except Exception:
        log.exception("Fehler beim Senden von group setState")
        raise


def send_hmip_set_point_temperature(
    ws: WebSocket, group_id: str, temperature: float,
    request_id: Optional[str] = None,
) -> str:
    """Solltemperatur einer Homematic-IP-Heizgruppe setzen."""
    body = {
        "setPointTemperature": round(float(temperature), 1),
        "groupId": group_id,
    }
    rid, payload = _build_hmip_request(
        "/hmip/group/heating/setSetPointTemperature", body, request_id
    )
    try:
        ws.send(json.dumps(payload))
        log.info(
            "HMIP_SYSTEM_REQUEST → setSetPointTemperature group=%s temp=%.1f (id=%s)",
            group_id, temperature, rid,
        )
        return rid
    except Exception:
        log.exception("Fehler beim Senden von setSetPointTemperature")
        raise
