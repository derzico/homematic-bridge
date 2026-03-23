# SPDX-License-Identifier: Apache-2.0
# tests/test_adapters.py – Tests für Device-Modell, Registry, und Adapter-Konvertierung

from app.adapters.base import BaseAdapter, Device, DeviceCapability, DeviceChannel
from app.adapters.registry import AdapterRegistry
from app.adapters.shelly_adapter import ShellyAdapter
from app.adapters.hmip_adapter import HmIPAdapter


# ── Device-Modell ────────────────────────────────────────────────────────────

class TestDevice:
    def test_to_dict(self):
        dev = Device(
            id="d1", name="Lampe", adapter="test",
            model="Switch", ip="192.168.1.10",
            capabilities=[DeviceCapability.SWITCH],
            channels=[DeviceChannel(index=0, on=True, power_w=45.0)],
        )
        d = dev.to_dict()
        assert d["id"] == "d1"
        assert d["adapter"] == "test"
        assert d["capabilities"] == ["switch"]
        assert len(d["channels"]) == 1
        assert d["channels"][0]["on"] is True
        assert d["channels"][0]["power_w"] == 45.0

    def test_defaults(self):
        dev = Device(id="d2", name="Test", adapter="test")
        assert dev.online is True
        assert dev.channels == []
        assert dev.capabilities == []
        assert dev.raw == {}

    def test_capability_enum(self):
        assert DeviceCapability.SWITCH.value == "switch"
        assert DeviceCapability.ENERGY_METER.value == "energy_meter"


# ── AdapterRegistry ─────────────────────────────────────────────────────────

class _DummyAdapter(BaseAdapter):
    """Minimal-Adapter für Tests."""
    def __init__(self, n: str, devices=None):
        self._name = n
        self._devices = devices or []
        self._started = False

    @property
    def name(self): return self._name
    @property
    def display_name(self): return self._name.upper()

    def start(self): self._started = True
    def stop(self): self._started = False
    def is_connected(self): return self._started
    def get_devices(self): return self._devices
    def control(self, device_id, action, params=None): return True


class TestAdapterRegistry:
    def test_register_and_get(self):
        reg = AdapterRegistry()
        a = _DummyAdapter("test")
        reg.register(a)
        assert reg.get("test") is a
        assert reg.get("nope") is None

    def test_all(self):
        reg = AdapterRegistry()
        reg.register(_DummyAdapter("a"))
        reg.register(_DummyAdapter("b"))
        assert len(reg.all()) == 2

    def test_start_all(self):
        reg = AdapterRegistry()
        a = _DummyAdapter("a")
        b = _DummyAdapter("b")
        reg.register(a)
        reg.register(b)
        reg.start_all()
        assert a._started is True
        assert b._started is True

    def test_stop_all(self):
        reg = AdapterRegistry()
        a = _DummyAdapter("a")
        reg.register(a)
        reg.start_all()
        reg.stop_all()
        assert a._started is False

    def test_health(self):
        reg = AdapterRegistry()
        a = _DummyAdapter("a")
        reg.register(a)
        assert reg.health() == {"a": False}
        reg.start_all()
        assert reg.health() == {"a": True}

    def test_get_all_devices(self):
        d1 = Device(id="d1", name="A", adapter="a")
        d2 = Device(id="d2", name="B", adapter="b")
        reg = AdapterRegistry()
        reg.register(_DummyAdapter("a", [d1]))
        reg.register(_DummyAdapter("b", [d2]))
        all_devs = reg.get_all_devices()
        assert len(all_devs) == 2
        ids = {d.id for d in all_devs}
        assert ids == {"d1", "d2"}

    def test_find_device(self):
        d1 = Device(id="d1", name="A", adapter="a")
        reg = AdapterRegistry()
        reg.register(_DummyAdapter("a", [d1]))
        assert reg.find_device("d1") is d1
        assert reg.find_device("d99") is None


# ── ShellyAdapter._to_device ────────────────────────────────────────────────

class TestShellyToDevice:
    def test_switch_device(self):
        raw = {
            "ip": "192.168.1.10", "gen": 1, "id": "SHSW-1-abc",
            "name": "Flurschalter", "model": "SHSW-1",
            "mac": "AABBCCDDEEFF", "fw": "1.14.0",
            "rssi": -55, "online": True,
            "channels": {"0": {"on": True, "power_w": 42.5, "total_kwh": 1.23}},
            "emeters": {},
        }
        dev = ShellyAdapter._to_device(raw)
        assert dev.id == "SHSW-1-abc"
        assert dev.adapter == "shelly"
        assert dev.name == "Flurschalter"
        assert DeviceCapability.SWITCH in dev.capabilities
        assert len(dev.channels) == 1
        assert dev.channels[0].on is True
        assert dev.channels[0].power_w == 42.5

    def test_energy_meter(self):
        raw = {
            "ip": "192.168.1.20", "gen": 1, "id": "SHEM-3-xyz",
            "name": "Stromzähler", "model": "SHEM-3",
            "mac": "112233445566", "fw": "1.14.0",
            "rssi": -70, "online": True,
            "channels": {},
            "emeters": {
                "0": {"power_w": 100, "total_kwh": 50.0, "voltage": 230.1, "current": 0.43},
                "1": {"power_w": 200, "total_kwh": 80.0, "voltage": 231.0, "current": 0.87},
            },
        }
        dev = ShellyAdapter._to_device(raw)
        assert DeviceCapability.ENERGY_METER in dev.capabilities
        assert len(dev.channels) == 2
        # Emeter-Channels haben Offset 100+
        assert dev.channels[0].index == 100
        assert dev.channels[0].voltage == 230.1

    def test_offline_device(self):
        raw = {
            "ip": "192.168.1.30", "id": "shelly-off",
            "name": "Offline", "model": "SHSW-1",
            "online": False, "channels": {}, "emeters": {},
        }
        dev = ShellyAdapter._to_device(raw)
        assert dev.online is False


# ── HmIPAdapter._to_device ──────────────────────────────────────────────────

class TestHmIPToDevice:
    def test_switch_device(self):
        raw = {
            "id": "hmip-001",
            "label": "Küchenlicht",
            "modelType": "HMIP-PS",
            "type": "PLUGABLE_SWITCH_MEASURING",
            "firmwareVersion": "2.12.6",
            "permanentlyReachable": True,
            "rssiDeviceValue": -68,
            "functionalChannels": {
                "0": {"functionalChannelType": "DEVICE_BASE"},
                "1": {
                    "functionalChannelType": "SWITCH_MEASURING_CHANNEL",
                    "on": True,
                    "currentPowerConsumption": 55.3,
                    "energyCounter": 123.45,
                },
            },
        }
        dev = HmIPAdapter._to_device("hmip-001", raw)
        assert dev.id == "hmip-001"
        assert dev.adapter == "hmip"
        assert dev.name == "Küchenlicht"
        assert dev.model == "HMIP-PS"
        assert DeviceCapability.SWITCH in dev.capabilities
        assert dev.channels[1].on is True
        assert dev.channels[1].power_w == 55.3
        assert dev.channels[1].total_kwh == 123.45

    def test_dimmer_device(self):
        raw = {
            "id": "hmip-002",
            "label": "Wohnzimmer Dimmer",
            "modelType": "HMIP-BDT",
            "firmwareVersion": "1.8.2",
            "functionalChannels": {
                "0": {"functionalChannelType": "DEVICE_BASE"},
                "1": {
                    "functionalChannelType": "DIMMER_CHANNEL",
                    "on": True, "dimLevel": 0.75,
                },
            },
        }
        dev = HmIPAdapter._to_device("hmip-002", raw)
        assert DeviceCapability.DIMMER in dev.capabilities
        assert dev.channels[1].dim_level == 0.75

    def test_minimal_device(self):
        raw = {"id": "hmip-003"}
        dev = HmIPAdapter._to_device("hmip-003", raw)
        assert dev.id == "hmip-003"
        assert dev.adapter == "hmip"
        assert dev.channels == []
