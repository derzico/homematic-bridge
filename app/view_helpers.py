# SPDX-License-Identifier: Apache-2.0

"""Datenvorbereitung für Jinja2-Templates.

Extrahiert Rohdaten aus dem HmIP-Snapshot und bereitet sie als
einfache Dicts/Listen für render_template() auf.
"""

import datetime
import html
import json
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils import _get_nested, _locate_devices_container, _find_device_in_list


# ── Wetter-Icons ──────────────────────────────────────────────────────────────

_WEATHER_ICON = {
    "CLEAR": "☀️", "PARTLY_CLOUDY": "🌤️", "CLOUDY": "☁️", "HEAVILY_CLOUDY": "☁️",
    "FOGGY": "🌫️", "STRONG_WIND": "💨", "RAINY": "🌧️", "HEAVY_RAIN": "⛈️",
    "LIGHT_RAIN": "🌦️", "SNOWY": "❄️", "SNOWY_RAINY": "🌨️", "THUNDERSTORM": "⛈️",
}


# ── JSON-Navigation ──────────────────────────────────────────────────────────

def _build_room_map(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Gibt {device_id: room_label} zurück, basierend auf META-Gruppen."""
    room_map: Dict[str, str] = {}
    candidates = [
        ("body", "groups"),
        ("body", "body", "groups"),
    ]
    groups = None
    for path in candidates:
        groups = _get_nested(snapshot, path)
        if isinstance(groups, dict):
            break
    if not isinstance(groups, dict):
        return room_map
    for g in groups.values():
        if not isinstance(g, dict) or g.get("type") != "META":
            continue
        room_label = str(g.get("label") or "–")
        for ch in (g.get("channels") or []):
            if isinstance(ch, dict) and ch.get("deviceId"):
                room_map[ch["deviceId"]] = room_label
    return room_map


def _iter_devices(snapshot: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Iteriert über alle Devices im Snapshot (Dict oder Liste)."""
    devices, _ = _locate_devices_container(snapshot)
    if isinstance(devices, dict):
        for dev_id, dev in devices.items():
            if isinstance(dev, dict):
                yield str(dev_id), dev
    elif isinstance(devices, list):
        for dev in devices:
            if isinstance(dev, dict):
                yield str(dev.get("id", "")), dev


def _find_device(snapshot: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
    """Findet ein einzelnes Device im Snapshot."""
    devices, _ = _locate_devices_container(snapshot)
    if isinstance(devices, list):
        dev, _ = _find_device_in_list(devices, device_id)
        return dev
    if isinstance(devices, dict):
        return devices.get(device_id)
    return None


def _get_home_and_groups(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Gibt (home_dict, groups_dict) zurück."""
    body = data.get("body", {}).get("body", {}) or data.get("body", {})
    home = body.get("home", {})
    groups = body.get("groups", {})
    return home, groups


def _wind_dir(deg: Any) -> str:
    """Windrichtung aus Grad-Wert."""
    if not isinstance(deg, (int, float)):
        return "–"
    dirs = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
    return dirs[round(int(deg) / 45) % 8]


def _ts_format(ms: Any) -> str:
    """Timestamp (ms) als deutsches Datumsformat."""
    if not ms:
        return "–"
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "–"


def _age_str(last_seen: Any) -> str:
    """Zeitdifferenz zu 'last_seen' als kompakter String."""
    if not last_seen:
        return "–"
    age = int(time.time() - last_seen)
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    return f"{age // 3600}h"


# ── Datenvorbereitung pro Seite ──────────────────────────────────────────────

def prepare_device_overview(system_state_path: str) -> Dict[str, Any]:
    """Daten für die Geräteübersicht."""
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    room_map = _build_room_map(data)
    devices = []
    for dev_id, dev in _iter_devices(data):
        devices.append({
            "id": dev_id,
            "label": str(dev.get("label", "–")),
            "type": str(dev.get("type", "–")),
            "model": str(dev.get("modelType", "–")),
            "room": room_map.get(dev_id, "–"),
        })
    return {"devices": devices, "device_count": len(devices), "active_nav": "devices"}


def prepare_device_detail(system_state_path: str, device_id: str) -> Dict[str, Any]:
    """Daten für die Gerätedetail-Seite."""
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dev_raw = _find_device(data, device_id)
    if not isinstance(dev_raw, dict):
        return {"dev": None, "device_id": device_id, "active_nav": "devices"}

    room_map = _build_room_map(data)
    fch = dev_raw.get("functionalChannels", {})
    channels = []
    if isinstance(fch, dict):
        for ch_idx in sorted(fch.keys(), key=str):
            ch = fch.get(ch_idx, {})
            if isinstance(ch, dict):
                channels.append((str(ch_idx), ch))

    return {
        "dev": {
            "id": device_id,
            "label": str(dev_raw.get("label", "–")),
            "type": str(dev_raw.get("type", "–")),
            "model": str(dev_raw.get("modelType", "–")),
            "room": room_map.get(device_id, "–"),
            "raw": dev_raw,
            "raw_json": html.escape(json.dumps(dev_raw, ensure_ascii=False, indent=2)),
            "channels": channels,
        },
        "meta_keys": ["id", "label", "type", "modelType", "homeId",
                       "permanentlyReachable", "firmwareVersion"],
        "active_nav": "devices",
    }


def prepare_device_status(system_state_path: str) -> Dict[str, Any]:
    """Daten für die Gerätestatus-Seite."""
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    room_map = _build_room_map(data)
    entries = []
    for dev_id, dev in _iter_devices(data):
        ch0 = dev.get("functionalChannels", {}).get("0", {})
        low_bat = ch0.get("lowBat")
        unreach = ch0.get("unreach")
        duty = ch0.get("dutyCycle")
        sabotage = ch0.get("sabotage")
        rssi = ch0.get("rssiDeviceValue")
        sev = 0
        if low_bat or unreach or duty or sabotage:
            sev = 2
        elif isinstance(rssi, (int, float)) and rssi != 128 and rssi < -85:
            sev = 1
        entries.append({
            "sev": sev,
            "label": dev.get("label", "–"),
            "id": dev_id,
            "type": dev.get("type", "–"),
            "room": room_map.get(dev_id, "–"),
            "low_bat": low_bat,
            "unreach": unreach,
            "duty": duty,
            "sabotage": sabotage,
            "rssi": rssi,
        })
    entries.sort(key=lambda e: (-e["sev"], str(e["label"]).lower()))
    warn_count = sum(1 for e in entries if e["sev"] >= 2)
    return {
        "entries": entries,
        "warn_count": warn_count,
        "device_count": len(entries),
        "active_nav": "status",
    }


def prepare_dashboard(system_state_path: str) -> Dict[str, Any]:
    """Daten für das Dashboard."""
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    home, groups = _get_home_and_groups(data)
    weather_raw = home.get("weather") or {}

    # Wetter
    condition = weather_raw.get("weatherCondition", "–")
    temp = weather_raw.get("temperature")
    hum = weather_raw.get("humidity")
    wind = weather_raw.get("windSpeed")
    temp_min = weather_raw.get("minTemperature")
    temp_max = weather_raw.get("maxTemperature")
    weather = {
        "condition": condition,
        "icon": _WEATHER_ICON.get(condition, "🌡️"),
        "temp_str": f"{temp:.1f}" if isinstance(temp, (int, float)) else "–",
        "humidity": hum,
        "wind_str": f"{wind:.1f} km/h {_wind_dir(weather_raw.get('windDirection'))}"
                    if isinstance(wind, (int, float)) else "–",
        "minmax": f"{temp_min:.1f}° / {temp_max:.1f}°"
                  if isinstance(temp_min, (int, float)) else "",
    }

    # Alarm
    sec_fh = None
    for fh in (home.get("functionalHomes") or {}).values():
        if isinstance(fh, dict) and fh.get("solution") == "SECURITY_AND_ALARM":
            sec_fh = fh
            break
    alarm_active = sec_fh.get("alarmActive", False) if sec_fh else None
    safety_active = sec_fh.get("safetyAlarmActive", False) if sec_fh else None
    intrusion_active = sec_fh.get("intrusionAlarmActive", False) if sec_fh else None
    alarm = {
        "any_active": alarm_active or safety_active or intrusion_active,
        "intrusion": intrusion_active,
        "safety": safety_active,
        "last_event_type": sec_fh.get("alarmSecurityJournalEntryType", "") if sec_fh else "",
        "last_event_ts": _ts_format(sec_fh.get("alarmEventTimestamp") if sec_fh else None),
    }

    # System
    system = {
        "connected": home.get("connected"),
        "update_state": str(home.get("updateState", "–")),
        "duty": home.get("dutyCycle") or 0,
    }

    # Heizung
    heating_groups_raw = []
    for g in groups.values():
        if isinstance(g, dict) and g.get("type") == "HEATING":
            heating_groups_raw.append(g)
    heating_groups_raw.sort(key=lambda g: str(g.get("label", "")))
    heating_groups = []
    for hg in heating_groups_raw:
        actual = hg.get("actualTemperature")
        setp = hg.get("setPointTemperature")
        valve = hg.get("valvePosition")
        mode = hg.get("controlMode", "")
        heating_groups.append({
            "label": str(hg.get("label", "–")),
            "actual_str": f"{actual:.1f}°" if isinstance(actual, (int, float)) else "–",
            "setp_str": f"Soll: {setp:.1f}°" if isinstance(setp, (int, float)) else "",
            "valve_str": f"Ventil: {valve * 100:.0f}%" if isinstance(valve, (int, float)) else "",
            "mode_str": mode.replace("_", " ").title() if mode else "",
        })

    # Gerätewarnungen
    warn_devs: List[str] = []
    for dev_id, dev in _iter_devices(data):
        ch0 = dev.get("functionalChannels", {}).get("0", {})
        if ch0.get("lowBat") or ch0.get("unreach") or ch0.get("dutyCycle") or ch0.get("sabotage"):
            warn_devs.append(dev.get("label", dev_id))

    return {
        "weather": weather,
        "alarm": alarm,
        "system": system,
        "heating_groups": heating_groups,
        "warn_devs": warn_devs,
        "active_nav": "dashboard",
    }


def prepare_heating(system_state_path: str) -> Dict[str, Any]:
    """Daten für die Heizungsseite."""
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    home, groups = _get_home_and_groups(data)

    # Abwesenheitsmodus
    absence = "–"
    for fh in (home.get("functionalHomes") or {}).values():
        if isinstance(fh, dict) and fh.get("solution") == "INDOOR_CLIMATE":
            absence = str(fh.get("absenceType", "–")).replace("_", " ").title()

    # Heizgruppen
    heating_groups_raw = []
    for g in groups.values():
        if isinstance(g, dict) and g.get("type") == "HEATING":
            heating_groups_raw.append(g)
    heating_groups_raw.sort(key=lambda g: str(g.get("label", "")))
    heating_groups = []
    for hg in heating_groups_raw:
        actual = hg.get("actualTemperature")
        setp = hg.get("setPointTemperature")
        hum_val = hg.get("humidity")
        valve = hg.get("valvePosition")
        mode = hg.get("controlMode", "–")
        heating_groups.append({
            "label": str(hg.get("label", "–")),
            "actual_str": f"{actual:.1f} °C" if isinstance(actual, (int, float)) else "–",
            "setp_str": f"{setp:.1f} °C" if isinstance(setp, (int, float)) else "–",
            "hum_str": f"{hum_val} %" if isinstance(hum_val, (int, float)) else "–",
            "valve_str": f"{valve * 100:.0f} %" if isinstance(valve, (int, float)) else "–",
            "mode_str": str(mode).replace("_", " ").title(),
            "boost": hg.get("boostMode", False),
            "party": hg.get("partyMode", False),
        })
    return {
        "absence": absence,
        "heating_groups": heating_groups,
        "active_nav": "heating",
    }


def prepare_shelly() -> Dict[str, Any]:
    """Daten für die Shelly-Seite."""
    import app.adapters.shelly_adapter as shelly_mod
    import app.state as state

    devices_raw = shelly_mod.load_cached()
    status = shelly_mod.scan_status()
    cfg = state.config.get("shelly", {})
    enabled = cfg.get("enabled", False)
    subnet = cfg.get("subnet", "–")

    if not enabled:
        return {"enabled": False, "active_nav": "shelly"}

    scan_info = ""
    if status["running"]:
        scan_info = "Scan läuft…"
    elif status["error"]:
        scan_info = f"Fehler: {status['error']}"

    devices = []
    for dev in devices_raw:
        devices.append({
            "ip": dev.get("ip", ""),
            "name": dev.get("name") or dev.get("id") or "–",
            "model": dev.get("model", "–"),
            "gen": dev.get("gen", 1),
            "mac": dev.get("mac", "–"),
            "fw": dev.get("fw", "–"),
            "new_fw": dev.get("new_fw", ""),
            "update_available": dev.get("update_available", False),
            "online": dev.get("online", True),
            "channels": dev.get("channels", {}),
            "emeters": dev.get("emeters", {}),
            "rssi": dev.get("rssi"),
            "age_str": _age_str(dev.get("last_seen")),
        })

    online_count = sum(1 for d in devices if d["online"])
    offline_devs = [d for d in devices if not d["online"]]
    offline_names = ", ".join(d["name"] for d in offline_devs) if offline_devs else ""
    updates_count = sum(1 for d in devices if d["update_available"])

    return {
        "enabled": True,
        "subnet": subnet,
        "devices": devices,
        "online_count": online_count,
        "offline_count": len(offline_devs),
        "offline_names": offline_names,
        "updates_count": updates_count,
        "scan_info": scan_info,
        "active_nav": "shelly",
    }
