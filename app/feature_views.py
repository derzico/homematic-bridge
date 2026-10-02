# SPDX-License-Identifier: Apache-2.0
"""Ansichten nur aus dem vorhandenen Snapshot, ohne Geräteabfragen."""
import app.state as state
from app.controls import indexed, light_capabilities, system_body
from app.view_helpers import _common_context, _load_view_snapshot


def current_snapshot():
    return _load_view_snapshot(state.config_internal.get("system_state_path", "data/system_state.json"))


def prepare_controls():
    system = system_body(current_snapshot())
    devices = indexed(system.get("devices"))
    shutters, lights, light_groups = [], [], []
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        for index, channel in (device.get("functionalChannels") or {}).items():
            if not isinstance(channel, dict):
                continue
            base = {"id": device_id, "channel": index, "label": device.get("label") or device_id}
            if channel.get("functionalChannelType") == "SHUTTER_CHANNEL":
                level = channel.get("shutterLevel")
                shutters.append({**base, "level": round(level * 100) if isinstance(level, (int, float)) else None})
            capabilities = light_capabilities(channel)
            if capabilities:
                lights.append({**base, "capabilities": capabilities,
                               "level": round((channel.get("dimLevel") or 0) * 100),
                               "hue": channel.get("hue") or 0, "saturation": round((channel.get("saturationLevel") or 0) * 100),
                               "minimum_kelvin": channel.get("minimalColorTemperature") or 1000,
                               "maximum_kelvin": channel.get("maximumColorTemperature") or 10000,
                               "kelvin": channel.get("colorTemperature")})
    for group_id, group in indexed(system.get("groups")).items():
        if not isinstance(group, dict) or group.get("type") not in ("SWITCHING", "LINKED_SWITCHING", "EXTENDED_LINKED_SWITCHING"):
            continue
        capabilities = set()
        for ref in group.get("channels") or []:
            if isinstance(ref, dict):
                dev = devices.get(ref.get("deviceId"), {})
                ch = (dev.get("functionalChannels") or {}).get(str(ref.get("channelIndex")), {})
                capabilities.update(light_capabilities(ch))
        if capabilities:
            light_groups.append({"id": group_id, "label": group.get("label") or group_id, "capabilities": capabilities})
    return {"shutters": shutters, "lights": lights, "light_groups": light_groups,
            "active_nav": "controls", **_common_context(state.config_internal.get("system_state_path", ""))}


def security_data(snapshot):
    system = system_body(snapshot)
    home = system.get("home") or {}
    functional = home.get("functionalHomes") or {}
    security = next((fh for fh in functional.values() if isinstance(fh, dict)
                     and fh.get("solution") == "SECURITY_AND_ALARM"), {})
    groups = indexed(system.get("groups"))
    zone_ids = security.get("securityZones") or {}
    zones = []
    if isinstance(zone_ids, dict):
        for name, group_id in zone_ids.items():
            group = groups.get(group_id, {})
            if not isinstance(group, dict) or group.get("type") != "SECURITY_ZONE":
                group = {}
            zones.append({"name": name, "label": group.get("label") or name,
                          "active": group.get("active") if isinstance(group.get("active"), bool) else None,
                          "windowState": group.get("windowState"), "presenceDetected": group.get("presenceDetected"),
                          "motionDetected": group.get("motionDetected"), "unreach": group.get("unreach")})
    statuses = [zone["active"] for zone in zones]
    if not statuses or any(value is None for value in statuses):
        armed = "unknown"
    elif all(statuses):
        armed = "armed"
    elif any(statuses):
        armed = "partly_armed"
    else:
        armed = "disarmed"
    sensors = []
    fields = ("windowState", "presenceDetected", "motionDetected", "smokeDetectorAlarmType",
              "smokeDetectorActive", "sabotage", "lowBat", "unreach")
    for device_id, device in indexed(system.get("devices")).items():
        if not isinstance(device, dict):
            continue
        values = {}
        for channel in (device.get("functionalChannels") or {}).values():
            if isinstance(channel, dict):
                values.update({key: channel[key] for key in fields if key in channel})
        if any(key in values for key in fields[:5]):
            sensors.append({"id": device_id, "label": device.get("label") or device_id, "values": values})
    return {"armed_state": armed, "zones": zones, "sensors": sensors,
            "alarm_active": security.get("alarmActive"), "safety_alarm_active": security.get("safetyAlarmActive"),
            "intrusion_alarm_active": security.get("intrusionAlarmActive"),
            "activation_in_progress": security.get("activationInProgress"),
            "snapshot_ready": state.WS_READY, "ws_connected": state.conn is not None}


def prepare_security():
    return {**security_data(current_snapshot()), "active_nav": "security",
            **_common_context(state.config_internal.get("system_state_path", ""))}
