# SPDX-License-Identifier: Apache-2.0
# app/adapters/hmip_adapter.py – Homematic IP Adapter (wrappt websocket_handler + utils)

import json
import logging
import threading
from typing import Any, Dict, List, Optional

import app.state as state
from app.adapters.base import BaseAdapter, Device, DeviceCapability, DeviceChannel
from app.utils import _locate_devices_container
from app.websocket_handler import ws_loop

log = logging.getLogger("bridge-ws")

# HmIP functionalChannelType → DeviceCapability Mapping
_CHANNEL_TYPE_MAP: Dict[str, DeviceCapability] = {
    "SWITCH_CHANNEL": DeviceCapability.SWITCH,
    "SWITCH_MEASURING_CHANNEL": DeviceCapability.SWITCH,
    "DIMMER_CHANNEL": DeviceCapability.DIMMER,
    "SHUTTER_CHANNEL": DeviceCapability.SHUTTER,
    "BLIND_CHANNEL": DeviceCapability.SHUTTER,
    "TEMPERATURE_SENSOR_2_OUTDOOR_CHANNEL": DeviceCapability.TEMPERATURE,
    "WALL_MOUNTED_THERMOSTAT_WITHOUT_DISPLAY_CHANNEL": DeviceCapability.TEMPERATURE,
    "WALL_MOUNTED_THERMOSTAT_PRO_CHANNEL": DeviceCapability.TEMPERATURE,
    "CLIMATE_SENSOR_CHANNEL": DeviceCapability.TEMPERATURE,
    "HUMIDITY_SENSOR_CHANNEL": DeviceCapability.HUMIDITY,
    "SMOKE_DETECTOR_CHANNEL": DeviceCapability.SMOKE,
    "WATER_SENSOR_CHANNEL": DeviceCapability.WATER,
    "MOTION_DETECTION_CHANNEL": DeviceCapability.MOTION,
    "CONTACT_INTERFACE_CHANNEL": DeviceCapability.CONTACT,
    "SHUTTER_CONTACT_CHANNEL": DeviceCapability.CONTACT,
    "MULTI_MODE_INPUT_SWITCH_CHANNEL": DeviceCapability.SWITCH,
    "NOTIFICATION_LIGHT_CHANNEL": DeviceCapability.COLOR,
}


class HmIPAdapter(BaseAdapter):
    """Adapter für Homematic IP via WebSocket (Port 9001)."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._ws_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "hmip"

    @property
    def display_name(self) -> str:
        return "Homematic IP"

    def start(self) -> None:
        self._ws_thread = threading.Thread(target=ws_loop, daemon=True)
        self._ws_thread.start()
        log.info("HmIP-Adapter: WebSocket-Thread gestartet")

    def stop(self) -> None:
        if state.conn:
            try:
                state.conn.close()
            except Exception:
                pass
            state.conn = None

    def is_connected(self) -> bool:
        return state.conn is not None

    def get_devices(self) -> List[Device]:
        snap = self._load_snapshot()
        if not snap:
            return []
        container, _ = _locate_devices_container(snap)
        if container is None:
            return []

        devices: List[Device] = []
        if isinstance(container, dict):
            for dev_id, dev_data in container.items():
                if isinstance(dev_data, dict):
                    devices.append(self._to_device(str(dev_id), dev_data))
        elif isinstance(container, list):
            for dev_data in container:
                if isinstance(dev_data, dict):
                    dev_id = dev_data.get("id", "")
                    devices.append(self._to_device(str(dev_id), dev_data))
        return devices

    def get_device(self, device_id: str) -> Optional[Device]:
        snap = self._load_snapshot()
        if not snap:
            return None
        container, _ = _locate_devices_container(snap)
        if container is None:
            return None

        if isinstance(container, dict):
            dev = container.get(device_id)
            if isinstance(dev, dict):
                return self._to_device(device_id, dev)
        elif isinstance(container, list):
            for d in container:
                if isinstance(d, dict) and d.get("id") == device_id:
                    return self._to_device(device_id, d)
        return None

    def control(self, device_id: str, action: str, params: Optional[Dict[str, Any]] = None) -> bool:
        from app.messages import send_hmip_set_switch, send_hmip_set_dim_level
        from app.websocket_handler import _register_pending

        params = params or {}
        if not state.conn:
            log.warning("HmIP: Kein WebSocket verbunden, kann %s nicht ausführen", action)
            return False

        if action == "switch":
            on = params.get("on", True)
            channel = params.get("channel", 0)
            with state.send_lock:
                rid = send_hmip_set_switch(state.conn, device_id, on, channel_index=channel)
            _register_pending(rid, "/hmip/device/control/setSwitchState")
            return True

        if action == "dim":
            level = params.get("level", 1.0)
            channel = params.get("channel", 1)
            with state.send_lock:
                rid = send_hmip_set_dim_level(state.conn, device_id, level, channel_index=channel)
            _register_pending(rid, "/hmip/device/control/setDimLevel")
            return True

        log.warning("HmIP: Unbekannte Action '%s' für %s", action, device_id)
        return False

    @staticmethod
    def _load_snapshot() -> Optional[Dict[str, Any]]:
        try:
            path = state.config_internal.get("system_state_path", "data/system_state.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _to_device(dev_id: str, raw: Dict[str, Any]) -> Device:
        """Konvertiert ein HmIP-Device-Dict in ein einheitliches Device."""
        label = raw.get("label") or raw.get("type") or dev_id
        model = raw.get("modelType") or raw.get("type") or ""
        fw = raw.get("firmwareVersion") or ""

        channels: List[DeviceChannel] = []
        capabilities: List[DeviceCapability] = []
        seen_caps: set = set()

        func_channels = raw.get("functionalChannels") or {}
        for ch_idx_str, ch_data in func_channels.items():
            if not isinstance(ch_data, dict):
                continue
            ch_type = ch_data.get("functionalChannelType", "")

            # Capability ableiten
            cap = _CHANNEL_TYPE_MAP.get(ch_type)
            if cap and cap not in seen_caps:
                capabilities.append(cap)
                seen_caps.add(cap)

            # Channel-Daten extrahieren
            ch = DeviceChannel(index=int(ch_idx_str))
            if "on" in ch_data:
                ch.on = ch_data["on"]
            if "dimLevel" in ch_data:
                ch.dim_level = ch_data["dimLevel"]
            if "currentPowerConsumption" in ch_data:
                ch.power_w = ch_data["currentPowerConsumption"]
            if "energyCounter" in ch_data:
                ch.total_kwh = ch_data["energyCounter"]
            channels.append(ch)

        return Device(
            id=dev_id,
            name=label,
            adapter="hmip",
            model=model,
            firmware=fw,
            online=raw.get("permanentlyReachable", True),
            rssi=raw.get("rssiDeviceValue"),
            capabilities=capabilities,
            channels=channels,
            raw=raw,
        )
