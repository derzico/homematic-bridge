# SPDX-License-Identifier: Apache-2.0
# app/adapters/base.py – Adapter-Interface und einheitliches Device-Modell

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DeviceCapability(str, Enum):
    """Fähigkeiten, die ein Gerät haben kann."""
    SWITCH = "switch"
    DIMMER = "dimmer"
    COLOR = "color"
    SHUTTER = "shutter"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    ENERGY_METER = "energy_meter"
    MOTION = "motion"
    CONTACT = "contact"
    SMOKE = "smoke"
    WATER = "water"


@dataclass
class DeviceChannel:
    """Ein Kanal eines Geräts (z.B. Relay 0, Dimmer 1)."""
    index: int
    on: Optional[bool] = None
    dim_level: Optional[float] = None
    power_w: Optional[float] = None
    total_kwh: Optional[float] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Device:
    """Einheitliches Geräte-Modell, adapter-übergreifend."""
    id: str
    name: str
    adapter: str                # z.B. "hmip", "shelly"
    model: str = ""
    ip: Optional[str] = None
    mac: Optional[str] = None
    firmware: str = ""
    online: bool = True
    rssi: Optional[int] = None
    capabilities: List[DeviceCapability] = field(default_factory=list)
    channels: List[DeviceChannel] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model,
            "ip": self.ip,
            "mac": self.mac,
            "firmware": self.firmware,
            "online": self.online,
            "rssi": self.rssi,
            "capabilities": [c.value for c in self.capabilities],
            "channels": [
                {
                    "index": ch.index,
                    "on": ch.on,
                    "dim_level": ch.dim_level,
                    "power_w": ch.power_w,
                    "total_kwh": ch.total_kwh,
                    "voltage": ch.voltage,
                    "current": ch.current,
                    **ch.extra,
                }
                for ch in self.channels
            ],
        }


class BaseAdapter(ABC):
    """Abstrakte Basisklasse für alle Geräte-Adapter.

    Jeder Adapter (HmIP, Shelly, Zigbee, …) implementiert diese Methoden,
    damit main.py alle Systeme einheitlich starten, abfragen und steuern kann.

    Lifecycle:  __init__(config) → start() → ... → stop()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Kurzname des Adapters (z.B. 'hmip', 'shelly')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Anzeigename (z.B. 'Homematic IP', 'Shelly')."""

    @abstractmethod
    def start(self) -> None:
        """Adapter starten (Threads, Verbindungen, initialer Scan, …)."""

    @abstractmethod
    def stop(self) -> None:
        """Adapter sauber beenden."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Gibt True zurück wenn der Adapter betriebsbereit ist."""

    @abstractmethod
    def get_devices(self) -> List[Device]:
        """Gibt alle bekannten Geräte als einheitliche Device-Objekte zurück."""

    def get_device(self, device_id: str) -> Optional[Device]:
        """Einzelnes Gerät nach ID. Default: linearer Scan über get_devices()."""
        for dev in self.get_devices():
            if dev.id == device_id:
                return dev
        return None

    @abstractmethod
    def control(self, device_id: str, action: str, params: Optional[Dict[str, Any]] = None) -> bool:
        """Steuert ein Gerät. Gibt True bei Erfolg zurück.

        Typische Actions: 'switch', 'dim', 'color', 'update'
        Params hängen von der Action ab, z.B. {'on': True} für 'switch'.
        """
