# SPDX-License-Identifier: Apache-2.0
"""Snapshotbasierte Befehlsplanung: keine Netzwerkzugriffe oder Geräteaktionen."""
import math


class ControlError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def system_body(snapshot):
    body = snapshot.get("body", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(body, dict):
        return {}
    body = body.get("body", body)
    return body if isinstance(body, dict) else {}


def indexed(collection):
    if isinstance(collection, dict):
        return collection
    if isinstance(collection, list):
        return {obj["id"]: obj for obj in collection if isinstance(obj, dict) and isinstance(obj.get("id"), str)}
    return {}


def number(value, minimum, maximum, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and minimum <= value <= maximum
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise ControlError(f"{name}: {minimum}..{maximum} erforderlich")
    return value


def integer(value, name, maximum=2147483647):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ControlError(f"{name} muss eine nicht-negative ganze Zahl sein")
    return value


def identifier(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ControlError(f"{key} muss eine nicht-leere ID sein")
    return value


def light_capabilities(channel):
    if not isinstance(channel, dict):
        return set()
    kind = channel.get("functionalChannelType")
    features = channel.get("supportedOptionalFeatures") or {}
    if not isinstance(features, dict):
        features = {}
    if kind == "UNIVERSAL_LIGHT_CHANNEL":
        # channelActive indicates current activity, not configured capability.
        return {action for action, feature in {
            "dim": "IOptionalFeatureDimmerState", "rgb": "IOptionalFeatureHueSaturationValue",
            "temperature": "IOptionalFeatureColorTemperature", "scene": "IOptionalFeatureLightScene",
        }.items() if features.get(feature) is True}
    if kind == "DIMMER_CHANNEL":
        return {"dim"}
    return set()


def plan_control(kind, data, snapshot):
    if not isinstance(data, dict):
        raise ControlError("Body muss ein JSON-Objekt sein")
    system = system_body(snapshot)
    if not isinstance(system.get("devices"), (dict, list)):
        raise ControlError("Kein gültiger HCU-Snapshot vorhanden", 503)
    devices, groups = indexed(system.get("devices")), indexed(system.get("groups"))
    action = data.get("action")
    if kind == "heating":
        group_id = identifier(data, "group")
        group = groups.get(group_id)
        if not isinstance(group, dict) or group.get("type") != "HEATING":
            raise ControlError("Heizgruppe nicht gefunden", 404)
        if group.get("controllable") is False:
            raise ControlError("Heizgruppe ist nicht steuerbar", 409)
        payload = {"groupId": group_id}
        if action == "temperature":
            minimum, maximum = group.get("minTemperature", 5), group.get("maxTemperature", 30)
            minimum = minimum if isinstance(minimum, (int, float)) and math.isfinite(minimum) else 5
            maximum = maximum if isinstance(maximum, (int, float)) and math.isfinite(maximum) else 30
            payload["setPointTemperature"] = number(data.get("temperature"), minimum, maximum, "temperature")
            command = "setSetPointTemperature"
        elif action == "mode":
            if data.get("mode") not in ("MANUAL", "AUTOMATIC"):
                raise ControlError("mode: MANUAL oder AUTOMATIC erforderlich")
            payload["controlMode"] = data["mode"]
            command = "setControlMode"
        elif action == "boost":
            if not isinstance(data.get("boost"), bool):
                raise ControlError("boost muss ein Boolean sein")
            if group.get("boostAllowed") is False:
                raise ControlError("Boost wird von dieser Gruppe nicht unterstützt", 409)
            payload["boost"] = data["boost"]
            command = "setBoost"
        else:
            raise ControlError("Unbekannte Heizaktion")
        return [("/hmip/group/heating/" + command, payload)]

    if kind == "shutter":
        device_id = identifier(data, "device")
        device = devices.get(device_id)
        if not isinstance(device, dict):
            raise ControlError("Gerät nicht gefunden", 404)
        channels = device.get("functionalChannels") or {}
        candidates = [int(index) for index, channel in channels.items()
                      if str(index).isascii() and str(index).isdecimal() and isinstance(channel, dict)
                      and channel.get("functionalChannelType") == "SHUTTER_CHANNEL"]
        channel_index = data.get("channelIndex", candidates[0] if len(candidates) == 1 else None)
        channel_index = integer(channel_index, "channelIndex")
        if channel_index not in candidates:
            raise ControlError("Kein Rollladenkanal an dieser Adresse", 404)
        payload = {"deviceId": device_id, "channelIndex": channel_index}
        if action == "position":
            payload["shutterLevel"] = number(data.get("level"), 0, 100, "level") / 100
            command = "setShutterLevel"
        elif action == "stop":
            command = "stop"
        else:
            raise ControlError("action: position oder stop erforderlich")
        return [("/hmip/device/control/" + command, payload)]

    if kind != "light":
        raise ControlError("Unbekannter Steuerungstyp", 404)
    if action not in ("dim", "rgb", "temperature", "scene"):
        raise ControlError("Unbekannte Lichtaktion")
    if bool(data.get("device")) == bool(data.get("group")):
        raise ControlError("Genau eine device- oder group-ID erforderlich")
    targets = []
    group_id = None
    if "group" in data:
        group_id = identifier(data, "group")
        group = groups.get(group_id)
        if not isinstance(group, dict) or group.get("type") not in ("SWITCHING", "LINKED_SWITCHING", "EXTENDED_LINKED_SWITCHING"):
            raise ControlError("Keine normale Licht-Schaltgruppe", 404)
        for ref in group.get("channels") or []:
            if not isinstance(ref, dict):
                continue
            dev = devices.get(ref.get("deviceId"), {})
            ch = (dev.get("functionalChannels") or {}).get(str(ref.get("channelIndex")), {})
            if action in light_capabilities(ch):
                targets.append((ref.get("deviceId"), ref.get("channelIndex"), ch))
    else:
        device_id = identifier(data, "device")
        channel_index = integer(data.get("channelIndex", 1), "channelIndex")
        dev = devices.get(device_id, {})
        channel = (dev.get("functionalChannels") or {}).get(str(channel_index), {})
        if action in light_capabilities(channel):
            targets.append((device_id, channel_index, channel))
    if not targets:
        raise ControlError("Lichtfunktion nicht für diesen Kanal konfiguriert", 409)
    level = number(data.get("dimLevel", 100), 0, 100, "dimLevel") / 100
    extra = {"dimLevel": level}
    if action == "rgb":
        extra.update(hue=integer(data.get("hue"), "hue", 359),
                     saturationLevel=number(data.get("saturation", 100), 0, 100, "saturation") / 100)
    elif action == "temperature":
        kelvin = integer(data.get("colorTemperature"), "colorTemperature", 10000)
        number(kelvin, 1000, 10000, "colorTemperature")
        for _, _, channel in targets:
            minimum = channel.get("minimalColorTemperature") or 1000
            maximum = channel.get("maximumColorTemperature") or 10000
            number(kelvin, minimum, maximum, "colorTemperature")
        extra["colorTemperature"] = kelvin
    elif action == "scene":
        extra["id"] = integer(data.get("sceneId"), "sceneId")
    command = {"dim": "setDimLevel", "rgb": "setHueSaturationDimLevel",
               "temperature": "setColorTemperatureDimLevel", "scene": "startLightScene"}[action]
    if group_id and action in ("dim", "scene"):
        return [("/hmip/group/switching/" + command, {"groupId": group_id, **extra})]
    # The supplied group-RGB schema contradicts its group path (deviceId body).
    # Use documented device requests for configured members, with per-ACK results.
    return [("/hmip/device/control/" + command, {"deviceId": dev, "channelIndex": index, **extra})
            for dev, index, _ in dict(((dev, index), (dev, index, channel)) for dev, index, channel in targets).values()]
