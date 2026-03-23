# SPDX-License-Identifier: Apache-2.0
# app/adapters/registry.py – Zentrales Adapter-Management

import logging
from typing import Any, Dict, List, Optional

from app.adapters.base import BaseAdapter, Device

log = logging.getLogger("bridge-ws")


class AdapterRegistry:
    """Registry für alle registrierten Adapter.

    Verwendet von main.py um Adapter zu starten und
    ggf. systemübergreifende Abfragen zu ermöglichen.
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.name] = adapter
        log.info("Adapter registriert: %s (%s)", adapter.name, adapter.display_name)

    def get(self, name: str) -> Optional[BaseAdapter]:
        return self._adapters.get(name)

    def all(self) -> List[BaseAdapter]:
        return list(self._adapters.values())

    def start_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.start()
                log.info("Adapter gestartet: %s", adapter.name)
            except Exception:
                log.exception("Adapter %s konnte nicht gestartet werden", adapter.name)

    def stop_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.stop()
            except Exception:
                log.exception("Adapter %s konnte nicht gestoppt werden", adapter.name)

    def get_all_devices(self) -> List[Device]:
        """Gibt alle Geräte aller Adapter zurück."""
        devices: List[Device] = []
        for adapter in self._adapters.values():
            try:
                devices.extend(adapter.get_devices())
            except Exception:
                log.exception("Fehler beim Abrufen der Geräte von %s", adapter.name)
        return devices

    def find_device(self, device_id: str) -> Optional[Device]:
        """Sucht ein Gerät über alle Adapter."""
        for adapter in self._adapters.values():
            dev = adapter.get_device(device_id)
            if dev is not None:
                return dev
        return None

    def health(self) -> Dict[str, bool]:
        """Gibt den Verbindungsstatus aller Adapter zurück."""
        return {a.name: a.is_connected() for a in self._adapters.values()}
