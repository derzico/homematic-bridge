# SPDX-License-Identifier: Apache-2.0
"""Steuerziele über Raum und Label statt ID auflösen (überlebt Gerätetausch).

Unterstützt im JSON-Body der Steuerbefehle:
  - Geräte:  "label": "Licht Decke"  optional  "room": "Wohnzimmer 2OG"
  - Gruppen: "groupLabel": "Heizung Bad"  oder (Heizung) nur "room": "Bad"
Eine explizite "device"/"group" hat immer Vorrang. Mehrdeutige Treffer werden mit
409 und Kandidatenliste abgelehnt, nie geraten. Nur Snapshot, keine Gerätezugriffe.
"""

from typing import Any, Dict, List, Optional, Tuple

from app.controls import indexed, system_body

DEVICE_PATHS = {"/hmipSwitch", "/hmipDimmer", "/hmipRGB", "/hmipIrrigation", "/hmipShutter", "/hmipLight"}
HEATING_PATHS = {"/hmipThermostat", "/hmipHeating"}
GROUP_TYPES = {
    "/hmipThermostat": {"HEATING"},
    "/hmipHeating": {"HEATING"},
    "/hmipAlarm": {"ALARM_SWITCHING"},
    "/hmipLight": {"SWITCHING", "LINKED_SWITCHING", "EXTENDED_LINKED_SWITCHING"},
}
RESOLVABLE_PATHS = DEVICE_PATHS | set(GROUP_TYPES)

Error = Tuple[Dict[str, Any], int]


def _norm(value: Any) -> str:
    return str(value).strip().casefold() if isinstance(value, str) else ""


def _room_members(groups: Dict[str, Any]) -> Dict[str, set]:
    rooms: Dict[str, set] = {}
    for group in groups.values():
        if isinstance(group, dict) and group.get("type") == "META":
            members = rooms.setdefault(_norm(group.get("label")), set())
            for ref in group.get("channels") or []:
                if isinstance(ref, dict) and isinstance(ref.get("deviceId"), str):
                    members.add(ref["deviceId"])
    return rooms


def _pick(candidates: List[Tuple[str, Any]], what: str, query: str) -> Tuple[Optional[str], Optional[Error]]:
    if not candidates:
        return None, ({"error": f"{what} '{query}' nicht gefunden"}, 404)
    if len(candidates) > 1:
        return None, ({"error": f"{what} '{query}' ist mehrdeutig – bitte ID oder Raum angeben",
                       "candidates": [{"id": cid, "label": obj.get("label")} for cid, obj in candidates]}, 409)
    return candidates[0][0], None


def _wanted(path: str, data: Dict[str, Any]) -> Tuple[bool, bool]:
    wants_device = path in DEVICE_PATHS and not data.get("device") and not data.get("group") \
        and isinstance(data.get("label"), str)
    wants_group = path in GROUP_TYPES and not data.get("group") and not data.get("groupId") \
        and not data.get("device") and (isinstance(data.get("groupLabel"), str)
                                        or (path in HEATING_PATHS and isinstance(data.get("room"), str)))
    return wants_device, wants_group


def needs_resolution(path: str, data: Any) -> bool:
    """True, wenn der Body Label/Raum statt ID nutzt (nur dann Snapshot laden)."""
    return isinstance(data, dict) and any(_wanted(path, data))


def resolve_targets(path: str, data: Dict[str, Any], snapshot: Any) -> Optional[Error]:
    """Ergänzt data["device"]/data["group"] aus Label/Raum; liefert (Fehler, Status) oder None."""
    wants_device, wants_group = _wanted(path, data)
    if not wants_device and not wants_group:
        return None
    system = system_body(snapshot)
    if not isinstance(system.get("devices"), (dict, list)):
        return {"error": "Kein gültiger HCU-Snapshot für die Namensauflösung"}, 503
    devices, groups = indexed(system.get("devices")), indexed(system.get("groups"))
    rooms = _room_members(groups)
    room = _norm(data.get("room"))
    if room and room not in rooms:
        return {"error": f"Raum '{data.get('room')}' nicht gefunden"}, 404
    in_room = rooms.get(room) if room else None

    if wants_device:
        label = _norm(data["label"])
        candidates = [(did, dev) for did, dev in devices.items()
                      if isinstance(dev, dict) and _norm(dev.get("label")) == label
                      and (in_room is None or did in in_room)]
        device_id, error = _pick(candidates, "Gerät", data["label"])
        if error:
            return error
        data["device"] = device_id
        return None

    types = GROUP_TYPES[path]
    typed = [(gid, g) for gid, g in groups.items() if isinstance(g, dict) and g.get("type") in types]
    if isinstance(data.get("groupLabel"), str):
        label = _norm(data["groupLabel"])
        candidates = [(gid, g) for gid, g in typed if _norm(g.get("label")) == label]
        query = data["groupLabel"]
    else:
        # Heizgruppe eines Raums: alle Mitglieder im Raum, sonst gleichnamige Gruppe
        def members(group: Dict[str, Any]) -> set:
            return {ref.get("deviceId") for ref in group.get("channels") or [] if isinstance(ref, dict)}
        candidates = [(gid, g) for gid, g in typed if members(g) and members(g) <= in_room]
        if not candidates:
            candidates = [(gid, g) for gid, g in typed if _norm(g.get("label")) == room]
        query = data["room"]
    group_id, error = _pick(candidates, "Gruppe", query)
    if error:
        return error
    data["group"] = group_id
    return None


def loxone_rgb(value: Any) -> Optional[Dict[str, Any]]:
    """Loxone-Lichtwert dekodieren.

    BBBGGGRRR (je 0..100 %)          → {"rgb": (r, g, b)} als Anteile 0..1
    20BBBTTTT (Lumitech, ≥200000000) → {"brightness": 0..100, "kelvin": 2700..6500}
    Ungültig → None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not (text.isascii() and text.isdigit()) or len(text) > 9:
            return None
        value = int(text)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 0:
        return None
    if value >= 200_000_000:
        brightness, kelvin = (value // 10_000) % 1000, value % 10_000
        if value // 10_000_000 != 20 or brightness > 100 or not 1000 <= kelvin <= 10000:
            return None
        return {"brightness": brightness, "kelvin": kelvin}
    red, green, blue = value % 1000, (value // 1000) % 1000, value // 1_000_000
    if max(red, green, blue) > 100:
        return None
    return {"rgb": (red / 100, green / 100, blue / 100)}
