# SPDX-License-Identifier: Apache-2.0

# loxone_udp.py
# Sendet HmIP-Gerätezustände per UDP an den Loxone Miniserver.
# Format: "variablename@wert\r\n" pro Wert (Loxone Virtual Input UDP)
# Variablenname: hmip_<DEVICE_ID>_ch<N>_<field>

import logging
import socket
from typing import Any, Dict, Set, Union

log = logging.getLogger("bridge-ws")

# Numerische Felder die direkt als Zahl übertragen werden
_NUMERIC_FIELDS: Set[str] = {
    "dimLevel", "saturationLevel", "hue", "colorTemperature",
    "actualTemperature", "humidity", "co2Concentration",
    "illumination", "windSpeed", "shutterLevel", "slatsLevel",
    "currentPowerConsumption", "energyCounter", "ventilationLevel",
}


def push_channel_state(host: str, port: int, device_id: str, channel_index: Union[str, int], channel: Dict[str, Any]) -> None:
    """Sendet alle relevanten Werte eines functionalChannel per UDP an Loxone."""
    if not host or not channel:
        return

    prefix = f"hmip_{device_id}_ch{channel_index}"
    lines = []

    for key, val in channel.items():
        if key == "on" and isinstance(val, bool):
            lines.append(f"{prefix}_on@{1 if val else 0}\r\n")
        elif key in _NUMERIC_FIELDS and isinstance(val, (int, float)):
            lines.append(f"{prefix}_{key}@{val}\r\n")

    if not lines:
        return

    payload = "".join(lines).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, (host, int(port)))
        log.debug("UDP → Loxone %s:%s | %s | %d Werte", host, port, device_id, len(lines))
    except Exception as e:
        log.warning("UDP Push zu Loxone fehlgeschlagen (%s:%s): %s", host, port, e)


def push_event_devices(host: str, port: int, msg_data: Dict[str, Any]) -> None:
    """Extrahiert alle Device-Channels aus einem HMIP_SYSTEM_EVENT und pushed sie."""
    if not host:
        return

    tx = (msg_data.get("body") or {}).get("eventTransaction") or {}
    events = tx.get("events") or {}

    for _, ev in sorted(events.items(), key=lambda kv: kv[0]):
        if not isinstance(ev, dict):
            continue
        dev = ev.get("device")
        if not isinstance(dev, dict):
            continue
        dev_id = dev.get("id")
        if not dev_id:
            continue
        channels = dev.get("functionalChannels") or {}
        for ch_idx, ch_state in channels.items():
            if isinstance(ch_state, dict):
                push_channel_state(host, port, dev_id, ch_idx, ch_state)
