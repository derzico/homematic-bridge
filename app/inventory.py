# SPDX-License-Identifier: Apache-2.0
"""Read-only Inventar und Loxone-Variablen-Mapping aus dem vorhandenen Snapshot.

Gedacht für externe Werkzeuge (z.B. einen Loxone-Config-MCP), die HmIP-Räume,
Geräte, Kanäle und Gruppen mit den Loxone Virtual-UDP-Eingängen abgleichen.
Keine Geräteabfragen, keine Befehle. Ausgegeben werden nur Felder aus einer
Allowlist – keine Tokens, Rohsnapshots oder beliebige verschachtelte Objekte.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import app.loxone_udp as lox
from app.controls import indexed

log = logging.getLogger("bridge-ws")

# Beschreibende Metadaten, die zusätzlich zu den Loxone-Wertfeldern erlaubt sind
_DEVICE_META = ("label", "type", "modelType", "modelId", "firmwareVersion",
                "connectionType", "permanentlyReachable", "lastStatusUpdate")
_CHANNEL_META = ("label", "functionalChannelType")
_GROUP_META = ("label", "type")
_SHELLY_META = ("ip", "id", "name", "model", "gen", "fw", "last_seen")

_CHANNEL_VALUE_FIELDS = lox._NUMERIC_FIELDS | lox._BOOL_FIELDS | lox._STRING_FIELDS
_GROUP_VALUE_FIELDS = (lox._GROUP_NUMERIC_FIELDS | lox._GROUP_BOOL_FIELDS
                       | lox._GROUP_STRING_FIELDS)


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _pick(obj: Any, keys: Iterable[str]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    return {k: obj[k] for k in keys if k in obj and _scalar(obj[k])}


def _member_refs(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs = []
    for ref in group.get("channels") or []:
        if isinstance(ref, dict) and isinstance(ref.get("deviceId"), str):
            index = ref.get("channelIndex")
            refs.append({"deviceId": ref["deviceId"],
                         "channelIndex": index if isinstance(index, int) else None})
    return refs


def _rooms(groups: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """META-Gruppen = Räume. Liefert (Raumliste, {device_id: Raumlabel})."""
    rooms, room_of = [], {}
    for group_id, group in groups.items():
        if not isinstance(group, dict) or group.get("type") != "META":
            continue
        label = str(group.get("label") or group_id)
        device_ids = []
        for ref in _member_refs(group):
            if ref["deviceId"] not in device_ids:
                device_ids.append(ref["deviceId"])
            room_of.setdefault(ref["deviceId"], label)
        rooms.append({"id": group_id, "label": label, "devices": device_ids})
    rooms.sort(key=lambda r: r["label"].lower())
    return rooms, room_of


def _channels(device: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """functionalChannels als {index_str: channel}, egal ob Dict oder Liste."""
    raw = device.get("functionalChannels")
    result = {}
    for key, channel in lox._indexed_objects(raw):
        index = channel.get("index", key) if isinstance(raw, list) else key
        result[str(index)] = channel
    return result


def _system_parts(snapshot: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(devices, groups) mit denselben Snapshot-Layouts wie loxone_udp._system_lines.

    Unterstützt raw getSystemState, {body: state}, HCU-Response body.body sowie
    devices/groups unterhalb von home.
    """
    if not isinstance(snapshot, dict) or snapshot.get("type") not in (None, "HMIP_SYSTEM_RESPONSE"):
        return {}, {}
    current: Any = snapshot
    for _ in range(3):
        if not isinstance(current, dict):
            return {}, {}
        if "code" in current and current["code"] != 200:
            return {}, {}
        if any(key in current for key in ("devices", "groups", "home")):
            break
        current = current.get("body")
    else:
        return {}, {}
    home = current.get("home")
    nested = home if isinstance(home, dict) else {}
    return (indexed(current.get("devices", nested.get("devices"))),
            indexed(current.get("groups", nested.get("groups"))))


def _variables_by_prefix(snapshot: Any) -> Dict[str, List[str]]:
    """Loxone-Variablennamen aus exakt derselben Logik wie der UDP-Push."""
    by_prefix: Dict[str, List[str]] = {}
    for line in lox._system_lines(snapshot):
        name = line.split("@", 1)[0]
        by_prefix.setdefault(_entity_prefix(name), []).append(name)
    return by_prefix


def _entity_prefix(name: str) -> str:
    """hmip_DEV_ch1_on -> hmip_DEV_ch1; hmip_DEV_available -> hmip_DEV."""
    head, _, _field = name.rpartition("_")
    return head


def build_inventory(snapshot: Any, shelly_devices: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Strukturierte Übersicht aller HmIP-Räume, -Geräte, -Kanäle, -Gruppen und Shellys."""
    devices, groups = _system_parts(snapshot)
    rooms, room_of = _rooms(groups)
    variables = _variables_by_prefix(snapshot)

    device_list = []
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        channels = []
        for index, channel in _channels(device).items():
            prefix = f"hmip_{device_id}_ch{index}"
            channels.append({
                "index": index,
                **_pick(channel, _CHANNEL_META),
                "values": _pick(channel, sorted(_CHANNEL_VALUE_FIELDS)),
                "loxone_variables": variables.get(prefix, []),
            })
        channels.sort(key=lambda c: int(c["index"]) if c["index"].isdecimal() else 0)
        device_list.append({
            "id": device_id,
            **_pick(device, _DEVICE_META),
            "room": room_of.get(device_id),
            "channels": channels,
            "loxone_variables": variables.get(f"hmip_{device_id}", []),
        })
    device_list.sort(key=lambda d: ((d.get("room") or "~").lower(), str(d.get("label") or d["id"]).lower()))

    group_list = []
    for group_id, group in groups.items():
        if not isinstance(group, dict) or group.get("type") == "META":
            continue
        group_list.append({
            "id": group_id,
            **_pick(group, _GROUP_META),
            "members": _member_refs(group),
            "values": _pick(group, sorted(_GROUP_VALUE_FIELDS)),
            "loxone_variables": variables.get(f"hmip_group_{group_id}", []),
        })
    group_list.sort(key=lambda g: (str(g.get("type")), str(g.get("label") or g["id"]).lower()))

    shelly_list = []
    for dev in shelly_devices or []:
        if not isinstance(dev, dict):
            continue
        # Adapter-Cache speichert channels als {index: status}; Listen ebenfalls zulassen
        outputs = dev.get("channels")
        count = len(outputs) if isinstance(outputs, (dict, list)) else 0
        shelly_list.append({**_pick(dev, _SHELLY_META), "outputs": count})

    return {
        "rooms": rooms,
        "devices": device_list,
        "groups": group_list,
        "shelly": shelly_list,
        "counts": {"rooms": len(rooms), "devices": len(device_list),
                   "groups": len(group_list), "shelly": len(shelly_list)},
    }


def _kind(field: str, numeric: Iterable[str], boolean: Iterable[str]) -> str:
    if field in lox.DERIVED_BOOL_FIELDS:
        return "bool"
    if field in lox.DERIVED_NUMERIC_FIELDS:
        return "number"
    if field == "available" or field in boolean:
        return "bool"
    if field in numeric:
        return "number"
    return "text"


def _classify(entity: str, field: str) -> str:
    """bool/number/text mit den Feldsätzen der jeweiligen Entität (wie im UDP-Export).

    Gleiche Feldnamen können je Entität verschieden typisiert sein, z.B.
    dutyCycle: Kanal = bool, Home = Zahl.
    """
    if entity == "device":
        return _kind(field, lox._NUMERIC_FIELDS, lox._BOOL_FIELDS)
    if entity == "group":
        return _kind(field, lox._GROUP_NUMERIC_FIELDS, lox._GROUP_BOOL_FIELDS)
    if field.startswith("weather_"):
        return _kind(field[8:], lox._WEATHER_NUMERIC_FIELDS, ())
    if field.startswith("security_zone_"):
        return "text"
    if field.startswith("security_"):
        return _kind(field[9:], lox._SECURITY_NUMERIC_FIELDS, lox._SECURITY_BOOL_FIELDS)
    if field.startswith("climate_"):
        return _kind(field[8:], (), {"active", "cooling"})
    return _kind(field, lox._HOME_NUMERIC_FIELDS, lox._HOME_BOOL_FIELDS)


def build_loxone_mapping(snapshot: Any) -> List[Dict[str, Any]]:
    """Jede Loxone-UDP-Variable mit Herkunft (Gerät/Kanal/Gruppe/Home, Raum, Label).

    "command" ist die Befehlserkennung für einen Loxone Virtual UDP Eingang.
    Textfelder lassen sich nur über einen Virtuellen Text-Eingang / Vergleich
    auswerten, daher "command": null.
    """
    devices, groups = _system_parts(snapshot)
    _, room_of = _rooms(groups)
    lox.update_topology(snapshot)
    # Längste IDs zuerst, damit IDs mit Unterstrich eindeutig zugeordnet werden
    device_ids = sorted((d for d in devices if isinstance(d, str)), key=len, reverse=True)
    group_ids = sorted((g for g in groups if isinstance(g, str)), key=len, reverse=True)

    result = []
    for line in lox._system_lines(snapshot):
        name, _, value = line.rstrip("\r\n").partition("@")
        entry: Dict[str, Any] = {"variable": name, "value": value}
        rest = name[len("hmip_"):]
        if rest.startswith("group_") and any(rest[6:].startswith(g + "_") for g in group_ids):
            gid = next(g for g in group_ids if rest[6:].startswith(g + "_"))
            group = groups.get(gid) or {}
            entry.update(entity="group", id=gid, label=group.get("label"),
                         group_type=group.get("type"), field=rest[6 + len(gid) + 1:])
        elif rest.startswith("home_"):
            entry.update(entity="home", id=None, label=None, field=rest[5:])
        else:
            did = next((d for d in device_ids if rest.startswith(d + "_")), None)
            if did is None:
                continue
            device = devices.get(did) or {}
            tail = rest[len(did) + 1:]
            channel = None
            if tail.startswith("ch") and "_" in tail:
                head, _, field = tail.partition("_")
                if head[2:].isdecimal():
                    channel, tail = head[2:], field
            ch_obj = _channels(device).get(channel) if channel else None
            entry.update(entity="device", id=did, label=device.get("label"),
                         device_type=device.get("type"), room=room_of.get(did),
                         channel=channel, field=tail,
                         channel_type=ch_obj.get("functionalChannelType") if isinstance(ch_obj, dict) else None,
                         channel_label=ch_obj.get("label") if isinstance(ch_obj, dict) else None)
        entry["kind"] = _classify(entry["entity"], entry["field"])
        entry["unit"] = _unit(entry["field"])
        entry["command"] = f"{name}@\\v" if entry["kind"] != "text" else None
        entry["sent"] = lox.is_sent(name)
        result.append(entry)
    return result


_UNITS = {"actualTemperature": "°C", "setPointTemperature": "°C", "temperature": "°C",
          "minTemperature": "°C", "maxTemperature": "°C", "humidity": "%",
          "co2Concentration": "ppm", "illumination": "lx", "windSpeed": "km/h",
          "currentPowerConsumption": "W", "energyCounter": "kWh", "rssiDeviceValue": "dBm",
          "colorTemperature": "K", "hue": "°", "windDirection": "°", "onTime": "s",
          "zoneActivationDelay": "s"}


def _unit(field: str) -> Optional[str]:
    leaf = field.rsplit("_", 1)[-1]
    if leaf.endswith("Pct"):
        return "%"
    return _UNITS.get(leaf)


def filter_mapping(variables: List[Dict[str, Any]], *, room: str = "", entity: str = "",
                   field: str = "", target: str = "", kind: str = "",
                   sent_only: bool = False) -> List[Dict[str, Any]]:
    """Filter für Mapping/Vorlage: Raum, Entität, Feld, Gerät/Gruppe (ID oder Label), Typ."""
    room, target = room.strip().lower(), target.strip().lower()
    fields = {f.strip() for f in field.split(",") if f.strip()}
    result = []
    for v in variables:
        if sent_only and not v.get("sent"):
            continue
        if room and str(v.get("room") or "").lower() != room:
            continue
        if entity and v.get("entity") != entity:
            continue
        if fields and v.get("field") not in fields and str(v.get("field")).rsplit("_", 1)[-1] not in fields:
            continue
        if target and target not in {str(v.get("id") or "").lower(), str(v.get("label") or "").lower()}:
            continue
        if kind and v.get("kind") != kind:
            continue
        result.append(v)
    return result


def build_loxone_template(variables: List[Dict[str, Any]], *, title: str, port: int,
                          address: str = "") -> str:
    """Loxone-Config-Vorlage (VirtualInUdp) für alle Zahlen/Ja-Nein-Variablen.

    Textvariablen werden übersprungen (kein Analogwert). Import in Loxone Config:
    Virtuelle Eingänge → Vorlage importieren / Datei in den Vorlagenordner kopieren.
    """
    from xml.etree import ElementTree as ET
    root = ET.Element("VirtualInUdp", {"Title": title, "Comment": "Homematic Bridge",
                                         "Address": address, "Port": str(int(port))})
    ET.SubElement(root, "Info", {"templateType": "1", "minVersion": "10020326"})
    for v in variables:
        if v.get("kind") not in ("bool", "number"):
            continue
        analog = v["kind"] == "number"
        unit = v.get("unit") or ""
        parts = [v.get("label") or v.get("id") or v.get("entity")]
        if v.get("channel") is not None:
            parts.append(v.get("channel_label") or f"ch{v['channel']}")
        parts.append(v.get("field"))
        cmd_title = " ".join(str(p) for p in parts if p)[:64]
        ET.SubElement(root, "VirtualInUdpCmd", {
            "Title": cmd_title,
            "Comment": " / ".join(str(p) for p in (v.get("room"), v["variable"]) if p),
            "Address": "", "Check": f"{v['variable']}@\\v",
            "Signed": "true", "Analog": "true" if analog else "false",
            "SourceValLow": "0", "DestValLow": "0", "SourceValHigh": "100", "DestValHigh": "100",
            "DefVal": "0", "MinVal": "-2147483648", "MaxVal": "2147483647",
            "Unit": (f"<v.1> {unit}".strip() if analog else "<v>"),
            "HintText": "",
        })
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
