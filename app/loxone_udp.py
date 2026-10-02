# SPDX-License-Identifier: Apache-2.0

# loxone_udp.py
# Sendet HmIP- und Shelly-Zustände per UDP an den Loxone Miniserver.
# Format: "variablename@wert\r\n" pro Wert (Loxone Virtual Input UDP)
# Variablenname: hmip_<DEVICE_ID>_ch<N>_<field>

import logging
import math
import re
import socket
from typing import Any, Dict, Iterable, Iterator, Set, Union

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
    "on", "motionDetected", "presenceDetected", "windowOpen",
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


def _send_lines(host: str, port: int, lines: Iterable[str]) -> None:
    if not host:
        return
    packets = iter(_pack_lines(lines))
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


def _system_lines(snapshot: Any) -> Iterator[str]:
    if not isinstance(snapshot, dict):
        return
    if snapshot.get("type") not in (None, "HMIP_SYSTEM_RESPONSE"):
        return
    current = snapshot
    # Raw state, body state und HCU response body.body; niemals rekursiv beliebig.
    for _ in range(3):
        if not isinstance(current, dict):
            return
        if "code" in current and current["code"] != 200:
            return
        if any(key in current for key in ("devices", "groups", "home")):
            break
        current = current.get("body")
    else:
        return
    home = current.get("home")
    nested = home if isinstance(home, dict) else {}
    for key, device in _indexed_objects(current.get("devices", nested.get("devices"))):
        yield from _device_lines(device, key)
    for key, group in _indexed_objects(current.get("groups", nested.get("groups"))):
        yield from _group_lines(group.get("id", key), group)
    yield from _home_lines(home)


def push_system_state(host: str, port: int, snapshot: Dict[str, Any]) -> None:
    """Initial-/Resync-Push aller Kanäle, Gruppen und Home-Felder.

    Akzeptiert raw getSystemState, {body: state} oder erfolgreiche HCU-Response
    {type: HMIP_SYSTEM_RESPONSE, body: {code: 200, body: state}}. Container dürfen
    ID-Mappings oder Listen sein. Stateless: Initial-, Periodic- und HTTP-Resync
    werden vom Aufrufer ausgelöst; keine Cache-/Datei-/Gerätezugriffe hier.
    Fehlendes unreach bedeutet available@1; bool True bedeutet available@0.
    """
    _send_lines(host, port, _system_lines(snapshot))


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
