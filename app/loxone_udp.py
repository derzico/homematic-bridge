# SPDX-License-Identifier: Apache-2.0

# loxone_udp.py
# Sendet HmIP- und Shelly-Zustände per UDP an den Loxone Miniserver.
# Format: "variablename@wert\r\n" pro Wert (Loxone Virtual Input UDP)
# Variablenname: hmip_<DEVICE_ID>_ch<N>_<field>

import logging
import math
import re
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Iterator, Optional, Set, Union

from app.utils import _event_sort_key

log = logging.getLogger("bridge-ws")

MAX_DATAGRAM_BYTES = 1200
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_UNSAFE_TEXT = re.compile(r"[@\x00-\x1f\x7f-\x9f\u2028\u2029]")

# Numerische Felder die direkt als Zahl übertragen werden
_NUMERIC_FIELDS: Set[str] = {
    "dimLevel", "saturationLevel", "hue", "colorTemperature",
    "actualTemperature", "setPointTemperature", "humidity", "co2Concentration",
    "illumination", "windSpeed", "shutterLevel", "slatsLevel",
    "currentPowerConsumption", "energyCounter", "ventilationLevel",
    "valvePosition", "rssiDeviceValue",
}

# Bool-Felder die als 0/1 übertragen werden
_BOOL_FIELDS: Set[str] = {
    "on", "motionDetected", "presenceDetected",
    "lowBat", "unreach", "dutyCycle", "sabotage",
    "smokeDetectorActive", "moistureDetected", "waterlevelDetected",
}

# String-Felder als Klartext (z.B. als Triggerwert in Loxone Virtual Input)
_STRING_FIELDS: Set[str] = {
    "smokeDetectorAlarmType", "windowState", "alarmSignalType",
    "opticalAlarmSignal", "acousticAlarmSignal",
}

# Connect API 1.0.1: getSystemState, HOME_CHANGED/GROUP_CHANGED,
# heating/setControlMode, profile/setProfileMode und vorhandene View-Felder.
# Keine Metadaten, Labels, beliebigen verschachtelten Objekte oder Geräteklassen.
_GROUP_NUMERIC_FIELDS = _NUMERIC_FIELDS | {"onTime", "minTemperature", "maxTemperature"}
_GROUP_BOOL_FIELDS = _BOOL_FIELDS | {
    "active", "silent", "configPending", "boostMode", "acousticFeedbackEnabled",
}
_GROUP_STRING_FIELDS = _STRING_FIELDS | {
    "controlMode", "profileMode", "profileIndex", "zoneAssignmentIndex",
    "signalAcoustic", "signalOptical",
}
_HOME_NUMERIC_FIELDS = {"dutyCycle", "carrierSense"}
_HOME_BOOL_FIELDS = {"connected"}
_HOME_STRING_FIELDS = {"updateState"}
_SECURITY_NUMERIC_FIELDS = {"zoneActivationDelay", "alarmEventTimestamp"}
_SECURITY_BOOL_FIELDS = {
    "active", "alarmActive", "safetyAlarmActive", "intrusionAlarmActive",
    "activationInProgress", "intrusionAlertThroughSmokeDetectors",
}
_SECURITY_STRING_FIELDS = {"securityZoneActivationMode", "alarmSecurityJournalEntryType"}
_WEATHER_NUMERIC_FIELDS = {
    "temperature", "minTemperature", "maxTemperature", "humidity", "windSpeed", "windDirection",
}
_WEATHER_STRING_FIELDS = {"weatherCondition", "weatherDayTime"}
_SHELLY_NUMERIC_FIELDS = {"power_w", "import_kwh", "export_kwh"}


# ── Abgeleitete Zahlenwerte (Loxone kann Text nicht per UDP-Analogeingang auswerten) ──
_WINDOW_STATE_CODES = {"CLOSED": 0, "OPEN": 1, "TILTED": 2}
# Reihenfolge = Code; nicht ändern, nur hinten ergänzen (Loxone-Logik hängt daran)
_WEATHER_CONDITIONS = (
    "CLEAR", "LIGHT_CLOUDY", "CLOUDY", "CLOUDY_WITH_RAIN", "CLOUDY_WITH_SNOW_RAIN",
    "HEAVILY_CLOUDY", "HEAVILY_CLOUDY_WITH_RAIN", "HEAVILY_CLOUDY_WITH_STRONG_RAIN",
    "HEAVILY_CLOUDY_WITH_SNOW", "HEAVILY_CLOUDY_WITH_SNOW_RAIN", "HEAVILY_CLOUDY_WITH_THUNDER",
    "HEAVILY_CLOUDY_WITH_RAIN_AND_THUNDER", "FOGGY", "STRONG_WIND",
)
_WEATHER_CODES = {name: index for index, name in enumerate(_WEATHER_CONDITIONS)}
# 0..1-Felder, die zusätzlich als <feld>Pct (0..100) gesendet werden
PERCENT_SOURCE_FIELDS: FrozenSet[str] = frozenset({
    "dimLevel", "shutterLevel", "slatsLevel", "valvePosition", "saturationLevel",
})
AGGREGATE_FIELDS: FrozenSet[str] = frozenset({
    "deviceCount", "unreachCount", "lowBatCount", "sabotageCount",
})
DERIVED_NUMERIC_FIELDS: FrozenSet[str] = frozenset(
    {"windowOpen", "weatherConditionCode", "lastCmdCode", "heartbeat"}
    | {f + "Pct" for f in PERCENT_SOURCE_FIELDS} | AGGREGATE_FIELDS
)
DERIVED_BOOL_FIELDS: FrozenSet[str] = frozenset({
    "smokeAlarm", "controlModeManual", "absenceActive", "lastCmdOk", "hcuConnected",
})
# Diagnosewerte pro Gerät/Gruppe, per loxone.filter.diagnostics abschaltbar
DIAGNOSTIC_FIELDS: FrozenSet[str] = frozenset({"rssiDeviceValue", "dutyCycle", "configPending"})


@dataclass(frozen=True)
class LoxoneOptions:
    """Laufzeitoptionen aus config.loxone (siehe configure())."""
    numeric_states: bool = True
    percent_values: bool = True
    aggregates: bool = True
    groups: bool = True
    diagnostics: bool = True
    include_fields: FrozenSet[str] = frozenset()
    exclude_fields: FrozenSet[str] = frozenset()
    include_rooms: FrozenSet[str] = frozenset()
    exclude_rooms: FrozenSet[str] = frozenset()
    include_devices: FrozenSet[str] = frozenset()
    exclude_devices: FrozenSet[str] = frozenset()

    @property
    def filtered(self) -> bool:
        return bool(not self.groups or not self.diagnostics or self.include_fields
                    or self.exclude_fields or self.include_rooms or self.exclude_rooms
                    or self.include_devices or self.exclude_devices)


@dataclass
class _Topology:
    room_of: Dict[str, str] = field(default_factory=dict)   # device_id -> raum (lower)
    label_of: Dict[str, str] = field(default_factory=dict)  # device_id -> label (lower)


_options = LoxoneOptions()
_topology = _Topology()
_runtime_lock = threading.Lock()
_last_aggregates: Optional[Dict[str, int]] = None


def _lower_set(values: Any) -> FrozenSet[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(v).strip().lower() for v in values if str(v).strip())


def _plain_set(values: Any) -> FrozenSet[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(v).strip() for v in values if str(v).strip())


def configure(loxone_cfg: Optional[Dict[str, Any]]) -> LoxoneOptions:
    """Übernimmt percent_values/aggregates und loxone.filter aus der Konfiguration."""
    global _options
    cfg = loxone_cfg if isinstance(loxone_cfg, dict) else {}
    filt = cfg.get("filter") if isinstance(cfg.get("filter"), dict) else {}
    options = LoxoneOptions(
        numeric_states=cfg.get("numeric_states", True) is not False,
        percent_values=cfg.get("percent_values", True) is not False,
        aggregates=cfg.get("aggregates", True) is not False,
        groups=filt.get("groups", True) is not False,
        diagnostics=filt.get("diagnostics", True) is not False,
        include_fields=_plain_set(filt.get("include_fields")),
        exclude_fields=_plain_set(filt.get("exclude_fields")),
        include_rooms=_lower_set(filt.get("include_rooms")),
        exclude_rooms=_lower_set(filt.get("exclude_rooms")),
        include_devices=_lower_set(filt.get("include_devices")),
        exclude_devices=_lower_set(filt.get("exclude_devices")),
    )
    with _runtime_lock:
        _options = options
    return options


def get_options() -> LoxoneOptions:
    with _runtime_lock:
        return _options


def update_topology(snapshot: Any) -> None:
    """Raum-/Labelzuordnung für Filter aus einem Vollsnapshot übernehmen."""
    devices, groups, _ = _system_containers(snapshot)
    if devices is None:
        return
    room_of, label_of = {}, {}
    for _, group in _indexed_objects(groups):
        if group.get("type") != "META":
            continue
        room = str(group.get("label") or "").strip().lower()
        for ref in group.get("channels") or []:
            if isinstance(ref, dict) and isinstance(ref.get("deviceId"), str) and room:
                room_of.setdefault(ref["deviceId"], room)
    for key, device in _indexed_objects(devices):
        device_id = device.get("id", key)
        if isinstance(device_id, str) and isinstance(device.get("label"), str):
            label_of[device_id] = device["label"].strip().lower()
    with _runtime_lock:
        _topology.room_of, _topology.label_of = room_of, label_of


def parse_variable(name: str) -> Dict[str, Optional[str]]:
    """Zerlegt einen Variablennamen in entity/id/channel/field (Felder ohne Unterstrich)."""
    head, _, leaf = name.rpartition("_")
    if name.startswith("bridge_"):
        return {"entity": "bridge", "id": None, "channel": None, "field": leaf}
    if name.startswith("shelly_"):
        return {"entity": "shelly", "id": None, "channel": None, "field": leaf}
    if name.startswith("hmip_home_"):
        return {"entity": "home", "id": None, "channel": None, "field": leaf}
    if name.startswith("hmip_group_"):
        return {"entity": "group", "id": head[len("hmip_group_"):], "channel": None, "field": leaf}
    device_part = head[len("hmip_"):]
    match = re.fullmatch(r"(.+)_ch(\d+)", device_part)
    if match:
        return {"entity": "device", "id": match.group(1), "channel": match.group(2), "field": leaf}
    return {"entity": "device", "id": device_part, "channel": None, "field": leaf}


def is_sent(name: str, options: Optional[LoxoneOptions] = None) -> bool:
    """True, wenn die Variable nach loxone.filter an den Miniserver geht."""
    opts = options or get_options()
    if not opts.filtered:
        return True
    parts = parse_variable(name)
    entity, leaf = parts["entity"], parts["field"] or ""
    if entity == "bridge":
        return True
    if opts.include_fields and leaf not in opts.include_fields:
        return False
    if leaf in opts.exclude_fields:
        return False
    if entity == "group" and not opts.groups:
        return False
    if entity in ("device", "group") and not opts.diagnostics and leaf in DIAGNOSTIC_FIELDS:
        return False
    if entity == "device" and (opts.include_rooms or opts.exclude_rooms
                               or opts.include_devices or opts.exclude_devices):
        device_id = parts["id"] or ""
        with _runtime_lock:
            room = _topology.room_of.get(device_id)
            label = _topology.label_of.get(device_id)
        keys = {device_id.lower()} | ({label} if label else set())
        if opts.include_rooms and room not in opts.include_rooms:
            return False
        if room and room in opts.exclude_rooms:
            return False
        if opts.include_devices and not keys & opts.include_devices:
            return False
        if keys & opts.exclude_devices:
            return False
    return True


def _derived_values(obj: Dict[str, Any], states: bool, percent: bool) -> Dict[str, Union[int, float]]:
    """Zahlen-Varianten für Textzustände und Prozentwerte (nur wenn Quelle vorhanden)."""
    out: Dict[str, Union[int, float]] = {}
    if states:
        out.update(_state_codes(obj))
    if percent:
        for key in sorted(PERCENT_SOURCE_FIELDS):
            value = obj.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    if math.isfinite(value):
                        out[key + "Pct"] = round(value * 100, 1)
                except (OverflowError, ValueError):
                    pass
    return out


def _state_codes(obj: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    window = obj.get("windowState")
    if "windowOpen" not in obj and isinstance(window, str) and window in _WINDOW_STATE_CODES:
        out["windowOpen"] = _WINDOW_STATE_CODES[window]
    smoke = obj.get("smokeDetectorAlarmType")
    if isinstance(smoke, str) and smoke:
        out["smokeAlarm"] = 0 if smoke == "IDLE_OFF" else 1
    mode = obj.get("controlMode")
    if isinstance(mode, str) and mode:
        out["controlModeManual"] = 1 if mode == "MANUAL" else 0
    absence = obj.get("absenceType")
    if isinstance(absence, str) and absence:
        out["absenceActive"] = 0 if absence == "NOT_ABSENT" else 1
    condition = obj.get("weatherCondition")
    if isinstance(condition, str) and condition in _WEATHER_CODES:
        out["weatherConditionCode"] = _WEATHER_CODES[condition]
    return out


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _valid_channel(value: Any) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return 0 <= value <= 2147483647
    return (isinstance(value, str) and 0 < len(value) <= 10
            and value.isascii() and value.isdecimal() and int(value) <= 2147483647)


def _field_lines(
    prefix: str, obj: Any, numeric: Set[str], boolean: Set[str], strings: Set[str],
) -> Iterator[str]:
    if not isinstance(obj, dict):
        return
    for key, val in obj.items():
        text = None
        if key in boolean and isinstance(val, bool):
            text = "1" if val else "0"
        elif key in numeric and isinstance(val, (int, float)) and not isinstance(val, bool):
            try:
                if math.isfinite(val):
                    text = str(val)
            except (OverflowError, ValueError):
                pass
        elif (key in strings and isinstance(val, str) and len(val) <= MAX_DATAGRAM_BYTES
              and not _UNSAFE_TEXT.search(val)):
            text = val
        if text is not None:
            yield f"{prefix}_{key}@{text}\r\n"
    options = get_options()
    for key, value in _derived_values(obj, options.numeric_states, options.percent_values).items():
        yield f"{prefix}_{key}@{value}\r\n"


def _pack_lines(lines: Iterable[str]) -> Iterator[bytes]:
    """Byte-Limit (auch UTF-8); Feldzeilen niemals auf Datagramme aufteilen."""
    payload = bytearray()
    for line in lines:
        try:
            encoded = line.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if len(encoded) > MAX_DATAGRAM_BYTES:
            log.debug("Überlange Loxone-Feldzeile verworfen.")
            continue
        if len(payload) + len(encoded) > MAX_DATAGRAM_BYTES:
            yield bytes(payload)
            payload.clear()
        payload.extend(encoded)
    if payload:
        yield bytes(payload)


def _filtered(lines: Iterable[str]) -> Iterator[str]:
    options = get_options()
    if not options.filtered:
        yield from lines
        return
    for line in lines:
        if is_sent(line.split("@", 1)[0], options):
            yield line


def _send_lines(host: str, port: int, lines: Iterable[str]) -> None:
    if not host:
        return
    packets = iter(_pack_lines(_filtered(lines)))
    first = next(packets, None)
    if first is None:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            target = (host, int(port))
            sock.sendto(first, target)
            for packet in packets:
                sock.sendto(packet, target)
    except Exception as e:
        # UDP ist best effort; ein Fehler darf weder WS noch Poll-Callback stoppen.
        log.warning("UDP Push zu Loxone fehlgeschlagen (%s:%s): %s", host, port, e)


def _indexed_objects(container: Any) -> Iterator[tuple]:
    if isinstance(container, dict):
        items = container.items()
    elif isinstance(container, list):
        items = enumerate(container)
    else:
        return
    for key, obj in items:
        if isinstance(obj, dict):
            yield key, obj


def _channel_lines(device_id: str, channel_index: Any, channel: Any) -> Iterator[str]:
    if not _valid_id(device_id) or not _valid_channel(channel_index):
        return
    yield from _field_lines(
        f"hmip_{device_id}_ch{channel_index}", channel,
        _NUMERIC_FIELDS, _BOOL_FIELDS, _STRING_FIELDS,
    )


def _device_lines(device: Any, fallback_id: Any = None) -> Iterator[str]:
    if not isinstance(device, dict):
        return
    device_id = device.get("id", fallback_id)
    if not _valid_id(device_id):
        return
    channels = device.get("functionalChannels")
    # DEVICE_BASE (i.d.R. ch0) trägt unreach für das gesamte Gerät.
    unreachable = device.get("unreach") is True or any(
        channel.get("unreach") is True for _, channel in _indexed_objects(channels)
    )
    available = 0 if unreachable else 1
    yield f"hmip_{device_id}_available@{available}\r\n"
    for key, channel in _indexed_objects(channels):
        index = channel.get("index", key) if isinstance(channels, list) else key
        if _valid_channel(index):
            yield from _channel_lines(device_id, index, channel)
            yield f"hmip_{device_id}_ch{index}_available@{available}\r\n"


def _group_lines(group_id: str, group: Any) -> Iterator[str]:
    if not _valid_id(group_id) or not isinstance(group, dict):
        return
    prefix = f"hmip_group_{group_id}"
    yield from _field_lines(
        prefix, group, _GROUP_NUMERIC_FIELDS, _GROUP_BOOL_FIELDS, _GROUP_STRING_FIELDS,
    )
    yield f"{prefix}_available@{0 if group.get('unreach') is True else 1}\r\n"


def _security_lines(security: Dict[str, Any], include_solution_active: bool = True) -> Iterator[str]:
    # active hier ist NUR die aktivierte Lösung, niemals ein Armed-Signal.
    boolean = _SECURITY_BOOL_FIELDS if include_solution_active else _SECURITY_BOOL_FIELDS - {"active"}
    yield from _field_lines(
        "hmip_home_security", security, _SECURITY_NUMERIC_FIELDS,
        boolean, _SECURITY_STRING_FIELDS,
    )
    zones = security.get("securityZones")
    if isinstance(zones, dict):
        for zone, group_id in zones.items():
            if _valid_id(zone) and _valid_id(group_id):
                yield f"hmip_home_security_zone_{zone}_groupId@{group_id}\r\n"


def _home_lines(home: Any) -> Iterator[str]:
    if not isinstance(home, dict):
        return
    yield from _field_lines(
        "hmip_home", home, _HOME_NUMERIC_FIELDS, _HOME_BOOL_FIELDS, _HOME_STRING_FIELDS,
    )
    yield from _field_lines(
        "hmip_home_weather", home.get("weather"), _WEATHER_NUMERIC_FIELDS, set(),
        _WEATHER_STRING_FIELDS,
    )
    # Manche Snapshots tragen die Security-Felder unmittelbar am Home.
    yield from _security_lines(home, include_solution_active=False)
    for key, functional_home in _indexed_objects(home.get("functionalHomes")):
        solution = functional_home.get("solution", key)
        if solution in ("SECURITY_AND_ALARM", "SEC_AND_ALARM"):
            yield from _security_lines(functional_home)
        elif solution == "INDOOR_CLIMATE":
            yield from _field_lines(
                "hmip_home_climate", functional_home, set(), {"active", "cooling"},
                {"absenceType"},
            )


def push_channel_state(host: str, port: int, device_id: str, channel_index: Union[str, int], channel: Dict[str, Any]) -> None:
    """Bestehendes hmip_DEVICE_chN_field@value CRLF-Format, ohne Metadaten."""
    _send_lines(host, port, _channel_lines(device_id, channel_index, channel))


def push_group_state(host: str, port: int, group_id: str, group: Dict[str, Any]) -> None:
    """Gruppe als hmip_group_ID_field; available wird aus unreach abgeleitet."""
    _send_lines(host, port, _group_lines(group_id, group))


def push_home_state(host: str, port: int, home: Dict[str, Any]) -> None:
    """Home: hmip_home_FIELD sowie hmip_home_{security,climate,weather}_FIELD.

    Scharfzustand ausschließlich über hmip_group_ID_active der SECURITY_ZONE-
    Gruppen. securityZones liefert hmip_home_security_zone_ZONE_groupId.
    security.active bedeutet aktivierte Lösung, securityZoneActivationMode ist
    Politik, NICHT Scharfzustand. activationInProgress/zoneActivationDelay sind
    Übergang/Verzögerung. Kein abgeleitetes armed/armingState-Signal oder Cache.
    """
    _send_lines(host, port, _home_lines(home))


def _system_containers(snapshot: Any):
    """(devices, groups, home) für raw/{body}/HCU-Response; (None, None, None) wenn ungültig."""
    if not isinstance(snapshot, dict):
        return None, None, None
    if snapshot.get("type") not in (None, "HMIP_SYSTEM_RESPONSE"):
        return None, None, None
    current = snapshot
    # Raw state, body state und HCU response body.body; niemals rekursiv beliebig.
    for _ in range(3):
        if not isinstance(current, dict):
            return None, None, None
        if "code" in current and current["code"] != 200:
            return None, None, None
        if any(key in current for key in ("devices", "groups", "home")):
            break
        current = current.get("body")
    else:
        return None, None, None
    home = current.get("home")
    nested = home if isinstance(home, dict) else {}
    return (current.get("devices", nested.get("devices")),
            current.get("groups", nested.get("groups")), home)


def compute_aggregates(devices: Any) -> Dict[str, int]:
    """Hausweite Zähler: Geräte, nicht erreichbar, Batterie schwach, Sabotage."""
    counts = {"deviceCount": 0, "unreachCount": 0, "lowBatCount": 0, "sabotageCount": 0}
    for _, device in _indexed_objects(devices):
        channels = [ch for _, ch in _indexed_objects(device.get("functionalChannels"))]
        counts["deviceCount"] += 1
        if device.get("unreach") is True or any(ch.get("unreach") is True for ch in channels):
            counts["unreachCount"] += 1
        if any(ch.get("lowBat") is True for ch in channels):
            counts["lowBatCount"] += 1
        if any(ch.get("sabotage") is True for ch in channels):
            counts["sabotageCount"] += 1
    return counts


def _aggregate_lines(counts: Dict[str, int]) -> Iterator[str]:
    for key in sorted(counts):
        yield f"hmip_home_{key}@{counts[key]}\r\n"


def _system_lines(snapshot: Any) -> Iterator[str]:
    devices, groups, home = _system_containers(snapshot)
    if devices is None and groups is None and home is None:
        return
    for key, device in _indexed_objects(devices):
        yield from _device_lines(device, key)
    for key, group in _indexed_objects(groups):
        yield from _group_lines(group.get("id", key), group)
    yield from _home_lines(home)
    if get_options().aggregates and devices is not None:
        yield from _aggregate_lines(compute_aggregates(devices))


def push_system_state(host: str, port: int, snapshot: Dict[str, Any]) -> None:
    """Initial-/Resync-Push aller Kanäle, Gruppen und Home-Felder.

    Akzeptiert raw getSystemState, {body: state} oder erfolgreiche HCU-Response
    {type: HMIP_SYSTEM_RESPONSE, body: {code: 200, body: state}}. Container dürfen
    ID-Mappings oder Listen sein. Stateless: Initial-, Periodic- und HTTP-Resync
    werden vom Aufrufer ausgelöst; keine Cache-/Datei-/Gerätezugriffe hier.
    Fehlendes unreach bedeutet available@1; bool True bedeutet available@0.
    Aktualisiert zusätzlich Raum-/Labelzuordnung für loxone.filter und den
    Stand der Sammelwerte (für push_aggregates_if_changed).
    """
    global _last_aggregates
    update_topology(snapshot)
    devices, _, _ = _system_containers(snapshot)
    if devices is not None:
        with _runtime_lock:
            _last_aggregates = compute_aggregates(devices)
    _send_lines(host, port, _system_lines(snapshot))


def push_aggregates_if_changed(host: str, port: int, snapshot: Any) -> bool:
    """Nach Events: Sammelwerte nur senden, wenn sich ein Zähler geändert hat."""
    global _last_aggregates
    if not get_options().aggregates:
        return False
    devices, _, _ = _system_containers(snapshot)
    if devices is None:
        return False
    counts = compute_aggregates(devices)
    with _runtime_lock:
        changed = counts != _last_aggregates
        _last_aggregates = counts
    if changed:
        _send_lines(host, port, _aggregate_lines(counts))
    return changed


def push_heartbeat(host: str, port: int, counter: int, hcu_connected: bool) -> None:
    """Lebenszeichen: bridge_heartbeat (zählt hoch) und bridge_hcuConnected 0/1.

    In Loxone mit Empfangs-Zeitüberschreitung am Virtual UDP Input überwachen.
    """
    _send_lines(host, port, [f"bridge_heartbeat@{int(counter)}\r\n",
                             f"bridge_hcuConnected@{1 if hcu_connected else 0}\r\n"])


def push_command_feedback(host: str, port: int, target_id: Any, is_group: bool,
                          ok: bool, status: int) -> None:
    """Rückmeldung eines Steuerbefehls: <prefix>_lastCmdOk 0/1 und _lastCmdCode (HTTP-Status)."""
    if not _valid_id(target_id) or isinstance(status, bool) or not isinstance(status, int):
        return
    prefix = f"hmip_group_{target_id}" if is_group else f"hmip_{target_id}"
    _send_lines(host, port, [f"{prefix}_lastCmdOk@{1 if ok else 0}\r\n",
                             f"{prefix}_lastCmdCode@{status}\r\n"])


def push_shelly_energy(host: str, port: int, sample: Dict[str, Any]) -> None:
    """Poll-Callback: {source_id: str, channel: int|str, power_w, import_kwh,
    export_kwh}. Werte bereits in W/kWh; None/fehlend wird nicht als 0 gesendet.
    Nur finite Zahlen (keine bools), keine Einheitenkonvertierung/Netzwerkabfrage.
    Ausgabe: shelly_SOURCE_chN_{power_w,import_kwh,export_kwh}@value CRLF.
    """
    if not isinstance(sample, dict):
        return
    source = sample.get("source_id")
    channel = sample.get("channel")
    if not _valid_id(source) or not _valid_channel(channel):
        return
    _send_lines(host, port, _field_lines(
        f"shelly_{source}_ch{channel}", sample, _SHELLY_NUMERIC_FIELDS, set(), set(),
    ))


def _event_lines(msg_data: Any) -> Iterator[str]:
    if not isinstance(msg_data, dict) or msg_data.get("type") not in (None, "HMIP_SYSTEM_EVENT"):
        return
    body = msg_data.get("body")
    tx = body.get("eventTransaction") if isinstance(body, dict) else None
    events = tx.get("events") if isinstance(tx, dict) else None
    if not isinstance(events, dict):
        return
    for _, ev in sorted(events.items(), key=_event_sort_key):
        if not isinstance(ev, dict):
            continue
        event_type = ev.get("pushEventType")
        if event_type in ("DEVICE_CHANGED", "DEVICE_ADDED"):
            yield from _device_lines(ev.get("device"))
        elif event_type in ("GROUP_CHANGED", "GROUP_ADDED"):
            group = ev.get("group")
            if isinstance(group, dict):
                yield from _group_lines(group.get("id"), group)
        elif event_type == "HOME_CHANGED":
            yield from _home_lines(ev.get("home"))
        elif event_type in ("DEVICE_REMOVED", "GROUP_REMOVED"):
            obj_id = ev.get("id")
            if _valid_id(obj_id):
                prefix = "hmip_group" if event_type == "GROUP_REMOVED" else "hmip"
                yield f"{prefix}_{obj_id}_available@0\r\n"


def push_event_devices(host: str, port: int, msg_data: Dict[str, Any]) -> None:
    """Kompatibler WS-Einstieg: Devices, Gruppen und Home in Event-Reihenfolge.

    REMOVED sendet Entity-available@0 (auch ohne vorigen Snapshot). Loxone muss
    Kanalwerte mit hmip_DEVICE_available verknüpfen: REMOVED enthält keine
    Kanalindizes. Kein Replay veralteter Werte und kein versteckter Kanalcache.
    """
    _send_lines(host, port, _event_lines(msg_data))
