# SPDX-License-Identifier: Apache-2.0
# app/adapters/shelly_adapter.py – Shelly-Adapter (wrappt app/shelly.py)

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import app.shelly as shelly_mod
import app.state as state
from app.adapters.base import BaseAdapter, Device, DeviceCapability, DeviceChannel

log = logging.getLogger("bridge-ws")


class ShellyAdapter(BaseAdapter):
    """Adapter für Shelly-Geräte (Gen1 + Gen2) via HTTP/mDNS."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._scan_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "shelly"

    @property
    def display_name(self) -> str:
        return "Shelly"

    def start(self) -> None:
        cfg = self._config
        if not cfg.get("enabled"):
            return

        subnet = cfg.get("subnet", "")
        timeout = float(cfg.get("timeout_sec", 1.5))
        if not subnet:
            return

        shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))

        if cfg.get("scan_on_startup", False):
            log.info("Shelly: Startup-Scan gestartet (%s)", subnet)
            shelly_mod.start_scan(subnet, timeout_sec=timeout)

        interval_h = float(cfg.get("scan_interval_hours", 0))
        if interval_h > 0:
            self._scan_thread = threading.Thread(
                target=self._periodic_scan, args=(subnet, timeout, interval_h),
                daemon=True,
            )
            self._scan_thread.start()

    def _periodic_scan(self, subnet: str, timeout: float, interval_h: float) -> None:
        while True:
            time.sleep(interval_h * 3600)
            with state.config_lock:
                cfg = dict(state.config.get("shelly") or {})
            shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
            log.info("Shelly: Zyklischer Scan gestartet (%s)", subnet)
            shelly_mod.start_scan(subnet, timeout_sec=timeout)

    def stop(self) -> None:
        pass  # Scan-Threads sind Daemons, beenden sich mit dem Prozess

    def is_connected(self) -> bool:
        return self._config.get("enabled", False)

    def get_devices(self) -> List[Device]:
        raw_devices = shelly_mod.load_cached()
        return [self._to_device(d) for d in raw_devices]

    def get_device(self, device_id: str) -> Optional[Device]:
        for d in shelly_mod.load_cached():
            if d.get("id") == device_id or d.get("ip") == device_id:
                return self._to_device(d)
        return None

    def control(self, device_id: str, action: str, params: Optional[Dict[str, Any]] = None) -> bool:
        params = params or {}
        cached = {d["ip"]: d for d in shelly_mod.load_cached()}

        # device_id kann IP oder Shelly-ID sein
        device = cached.get(device_id)
        if not device:
            for d in cached.values():
                if d.get("id") == device_id:
                    device = d
                    break
        if not device:
            return False

        ip = device["ip"]
        gen = device.get("gen", 1)

        # Credentials setzen
        with state.config_lock:
            cfg = dict(state.config.get("shelly") or {})
        shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))

        if action == "switch":
            channel = params.get("channel", 0)
            on = params.get("on", True)
            ok = shelly_mod.set_relay(ip, gen, channel, on)
            if ok:
                shelly_mod.refresh_device(ip, gen)
            return ok

        if action == "update":
            return shelly_mod.trigger_update(ip, gen)

        log.warning("Shelly: Unbekannte Action '%s' für %s", action, device_id)
        return False

    @staticmethod
    def _to_device(raw: Dict[str, Any]) -> Device:
        """Konvertiert ein Shelly-Cache-Dict in ein einheitliches Device."""
        channels: List[DeviceChannel] = []
        capabilities: List[DeviceCapability] = []

        # Schalt-Kanäle
        raw_channels = raw.get("channels") or {}
        if raw_channels:
            capabilities.append(DeviceCapability.SWITCH)
            for idx_str, ch in sorted(raw_channels.items(), key=lambda x: x[0]):
                channels.append(DeviceChannel(
                    index=int(idx_str),
                    on=ch.get("on"),
                    power_w=ch.get("power_w"),
                    total_kwh=ch.get("total_kwh"),
                ))

        # Energie-Monitor (SHEM / SHEM-3)
        raw_emeters = raw.get("emeters") or {}
        if raw_emeters:
            capabilities.append(DeviceCapability.ENERGY_METER)
            for idx_str, em in sorted(raw_emeters.items(), key=lambda x: x[0]):
                channels.append(DeviceChannel(
                    index=100 + int(idx_str),  # Offset um Kollision mit Relays zu vermeiden
                    power_w=em.get("power_w"),
                    total_kwh=em.get("total_kwh"),
                    voltage=em.get("voltage"),
                    current=em.get("current"),
                    extra={k: v for k, v in em.items()
                           if k not in ("power_w", "total_kwh", "voltage", "current")},
                ))

        return Device(
            id=raw.get("id") or raw.get("ip", ""),
            name=raw.get("name") or raw.get("id") or "Shelly",
            adapter="shelly",
            model=raw.get("model", ""),
            ip=raw.get("ip"),
            mac=raw.get("mac"),
            firmware=raw.get("fw", ""),
            online=raw.get("online", True),
            rssi=raw.get("rssi"),
            capabilities=capabilities,
            channels=channels,
            raw=raw,
        )
