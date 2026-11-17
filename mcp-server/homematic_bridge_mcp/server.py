# SPDX-License-Identifier: Apache-2.0
"""Read-only MCP-Server (stdio) für die Homematic Bridge.

MCP-Server (stdio) für die Homematic Bridge.

Stellt Inventar, Loxone-Mapping, Loxone-Vorlage und Diagnose der Bridge als
MCP-Tools bereit, damit ein Assistent sie neben einem Loxone-Config-MCP abfragen
und beide Konfigurationen abgleichen kann. Optional (nur mit Steuer-Key) auch
Steuer-Tools für Heizung, Schalten, Dimmen, Rollläden, Licht und Loxone-Resync.
Steuerbefehle laufen über die normalen Bridge-Endpunkte, also mit Snapshot-
Prüfung, Funk-Schutz, IP-Freigabe und Loxone-Rückmeldung. Keine Sirene.
Nur Python-Standardbibliothek.

Umgebung:
  BRIDGE_URL                   z.B. http://192.168.1.10:8080 (Standard: http://127.0.0.1:8080)
  BRIDGE_API_KEY               Nur-Lese-Key (empfohlen) oder Haupt-Key – für Lese-Tools
  BRIDGE_API_KEY_FILE          Pfad zu einer Datei mit dem Lese-Key
  BRIDGE_CONTROL_API_KEY       Haupt-Key der Bridge – schaltet die Steuer-Tools frei
  BRIDGE_CONTROL_API_KEY_FILE  Pfad zu einer Datei mit dem Haupt-Key (data/api_key.txt)
Ohne Steuer-Key werden die Steuer-Tools weder angeboten noch ausgeführt.
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from homematic_bridge_mcp import __version__

log = logging.getLogger("bridge-ws")

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "homematic-bridge", "version": __version__}
TIMEOUT_SEC = 15
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
COMPACT_FIELDS = ("variable", "room", "label", "channel_label", "field", "kind", "unit", "value",
                  "command", "sent")


def _key_from_env(name: str) -> Optional[str]:
    key = os.getenv(name)
    if key and key.strip():
        return key.strip()
    path = os.getenv(name + "_FILE")
    if path:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read().strip() or None
    return None


def _api_key() -> Optional[str]:
    return _key_from_env("BRIDGE_API_KEY")


def _control_key() -> Optional[str]:
    """Haupt-Key für Steuerbefehle; None = Steuer-Tools gesperrt."""
    try:
        return _key_from_env("BRIDGE_CONTROL_API_KEY")
    except OSError:
        log.warning("Steuer-Key-Datei nicht lesbar – Steuer-Tools gesperrt.")
        return None


def control_enabled() -> bool:
    return bool(_control_key())


def _base_url() -> str:
    return os.getenv("BRIDGE_URL", "http://127.0.0.1:8080").rstrip("/")


def _request(path: str, params: Optional[Dict[str, Any]] = None) -> bytes:
    query = {k: v for k, v in (params or {}).items() if v not in (None, "", False)}
    url = _base_url() + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(url, method="GET")
    key = _api_key()
    if key:
        request.add_header("X-API-Key", key)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return response.read()


class ControlDisabled(RuntimeError):
    """Steuer-Tool ohne BRIDGE_CONTROL_API_KEY aufgerufen."""


def _post(path: str, body: Dict[str, Any]) -> Any:
    """POST mit Haupt-Key an einen Steuer-Endpunkt der Bridge (nur feste Pfade)."""
    key = _control_key()
    if not key:
        raise ControlDisabled("Steuerung nicht freigeschaltet (BRIDGE_CONTROL_API_KEY fehlt).")
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(_base_url() + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "X-API-Key": key})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return json.loads(_request(path, params).decode("utf-8"))


def _text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _limit(args: Dict[str, Any], default: int = DEFAULT_LIMIT) -> int:
    try:
        value = int(args.get("limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_LIMIT))


def _offset(args: Dict[str, Any]) -> int:
    try:
        return max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def tool_summary(_args: Dict[str, Any]) -> Any:
    """Kompakte Übersicht: Räume mit Geräteanzahl, Gerätetypen, Variablen nach Typ/Status."""
    inventory = _get("/api/inventory")
    mapping = _get("/api/loxone/mapping")
    variables = mapping.get("variables", [])
    devices = inventory.get("devices", [])
    return {
        "counts": inventory.get("counts"),
        "rooms": {r.get("label"): len(r.get("devices", [])) for r in inventory.get("rooms", [])},
        "devices_without_room": sum(1 for d in devices if not d.get("room")),
        "device_types": dict(Counter(d.get("type") or "?" for d in devices).most_common()),
        "group_types": dict(Counter(g.get("type") or "?" for g in inventory.get("groups", [])).most_common()),
        "loxone": {
            "miniserver_configured": mapping.get("miniserver_configured"),
            "udp_port": mapping.get("udp_port"),
            "variables_total": len(variables),
            "variables_sent": sum(1 for v in variables if v.get("sent", True)),
            "by_entity": dict(Counter(v.get("entity") for v in variables)),
            "by_kind": dict(Counter(v.get("kind") for v in variables)),
            "top_fields": dict(Counter(v.get("field") for v in variables).most_common(25)),
        },
        "hint": "Details mit hmip_inventory/hmip_loxone_mapping und Filtern (room, field, target …) abrufen.",
    }


def tool_inventory(args: Dict[str, Any]) -> Any:
    data = _get("/api/inventory")
    room, dtype, target = _text(args.get("room")), _text(args.get("type")), _text(args.get("device"))
    devices = data.get("devices", [])
    if room:
        devices = [d for d in devices if _text(d.get("room")) == room]
        data["rooms"] = [r for r in data.get("rooms", []) if _text(r.get("label")) == room]
    if dtype:
        devices = [d for d in devices if dtype in _text(d.get("type")) or dtype in _text(d.get("modelType"))]
    if target:
        devices = [d for d in devices if target in (_text(d.get("id")), _text(d.get("label")))
                   or target in _text(d.get("label"))]
    include_values = args.get("include_values", True) is not False
    include_variables = args.get("include_variables", False) is True
    for device in devices:
        if not include_variables:
            device.pop("loxone_variables", None)
        for channel in device.get("channels", []):
            if not include_values:
                channel.pop("values", None)
            if not include_variables:
                channel.pop("loxone_variables", None)
    offset, limit = _offset(args), _limit(args)
    data["devices_total"] = len(devices)
    data["devices"] = devices[offset:offset + limit]
    data["offset"] = offset
    if args.get("include_groups", True) is False or room or dtype or target:
        data.pop("groups", None)
    elif not include_variables:
        for group in data.get("groups", []):
            group.pop("loxone_variables", None)
    if room or dtype or target:
        data.pop("shelly", None)
    return data


def tool_loxone_mapping(args: Dict[str, Any]) -> Any:
    params = {key: args.get(key) for key in ("room", "entity", "field", "target", "kind")}
    params["sent"] = "1" if args.get("sent_only") else None
    params["offset"], params["limit"] = _offset(args), _limit(args)
    data = _get("/api/loxone/mapping", params)
    needle = _text(args.get("filter"))
    if needle:
        data["variables"] = [v for v in data.get("variables", [])
                             if needle in json.dumps(v, ensure_ascii=False).casefold()]
        data["count"] = len(data["variables"])
    if args.get("compact", True) is not False:
        data["variables"] = [{k: v[k] for k in COMPACT_FIELDS if k in v} for v in data.get("variables", [])]
    return data


def tool_loxone_template(args: Dict[str, Any]) -> Any:
    params = {key: args.get(key) for key in ("room", "entity", "field", "target", "kind", "title", "address")}
    xml = _request("/api/loxone/template.xml", params).decode("utf-8")
    return {"filename_hint": "VIU_" + str(args.get("title") or "Homematic_Bridge").replace(" ", "_") + ".xml",
            "commands": xml.count("<VirtualInUdpCmd"), "xml": xml}


def tool_diagnostics(_args: Dict[str, Any]) -> Any:
    return _get("/api/diagnostics")


# ── Steuer-Tools ─────────────────────────────────────────────────────────────

_DEVICE_TARGET = ("device", "label", "room", "channelIndex")
_GROUP_TARGET = ("group", "groupLabel", "room")


def _body(args: Dict[str, Any], allowed: tuple, **fixed: Any) -> Dict[str, Any]:
    """Nur erlaubte Felder durchreichen; leere Werte weglassen."""
    body = {k: args[k] for k in allowed if k in args and args[k] not in (None, "")}
    body.update({k: v for k, v in fixed.items() if v is not None})
    return body


def _require_target(body: Dict[str, Any], keys: tuple) -> None:
    if not any(body.get(k) for k in keys):
        raise ValueError("Ziel fehlt: " + " oder ".join(keys) + " angeben")


def tool_heating(args: Dict[str, Any]) -> Any:
    action = args.get("action")
    fields = {"temperature": ("temperature",), "mode": ("mode",), "boost": ("boost",)}.get(action)
    if fields is None:
        raise ValueError("action: temperature, mode oder boost")
    body = _body(args, _GROUP_TARGET + fields, action=action)
    _require_target(body, _GROUP_TARGET)
    return _post("/hmipHeating", body)


def tool_switch(args: Dict[str, Any]) -> Any:
    body = _body(args, _DEVICE_TARGET + ("on",))
    _require_target(body, ("device", "label"))
    if not isinstance(body.get("on"), bool):
        raise ValueError("on (true/false) fehlt")
    return _post("/hmipSwitch", body)


def tool_dimmer(args: Dict[str, Any]) -> Any:
    body = _body(args, _DEVICE_TARGET + ("dimLevel",))
    _require_target(body, ("device", "label"))
    return _post("/hmipDimmer", body)


def tool_shutter(args: Dict[str, Any]) -> Any:
    if args.get("action") not in ("position", "stop"):
        raise ValueError("action: position oder stop")
    body = _body(args, _DEVICE_TARGET + ("level",), action=args["action"])
    _require_target(body, ("device", "label"))
    return _post("/hmipShutter", body)


def tool_light(args: Dict[str, Any]) -> Any:
    if args.get("action") not in ("dim", "rgb", "temperature", "scene"):
        raise ValueError("action: dim, rgb, temperature oder scene")
    body = _body(args, _DEVICE_TARGET + ("group", "groupLabel", "dimLevel", "hue", "saturation",
                                         "colorTemperature", "sceneId"), action=args["action"])
    _require_target(body, ("device", "label", "group", "groupLabel"))
    return _post("/hmipLight", body)


def tool_loxone_resync(_args: Dict[str, Any]) -> Any:
    return _post("/api/loxone/resync", {})


_DEVICE_PROPS = {
    "device": {"type": "string", "description": "Geräte-ID (Vorrang vor label)"},
    "label": {"type": "string", "description": "Gerätename wie in der HmIP-App"},
    "room": {"type": "string", "description": "Raum, um label eindeutig zu machen"},
    "channelIndex": {"type": "integer", "minimum": 0},
}
_CONTROL_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

CONTROL_TOOLS: List[Dict[str, Any]] = [
    {"name": "hmip_heating",
     "description": ("Heizgruppe steuern: Solltemperatur, Modus (MANUAL/AUTOMATIC) oder Boost. "
                     "Ziel per room (Heizgruppe des Raums), groupLabel oder group-ID. "
                     "Ventilstellung ist über die HCU nicht direkt setzbar."),
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "enum": ["temperature", "mode", "boost"]},
         "room": {"type": "string"}, "groupLabel": {"type": "string"}, "group": {"type": "string"},
         "temperature": {"type": "number", "description": "°C, innerhalb min/max der Gruppe"},
         "mode": {"type": "string", "enum": ["MANUAL", "AUTOMATIC"]},
         "boost": {"type": "boolean"},
     }}},
    {"name": "hmip_switch",
     "description": "Schaltaktor ein/aus (z.B. Licht, Steckdose).",
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "required": ["on"], "properties": {
         **_DEVICE_PROPS, "on": {"type": "boolean"}}}},
    {"name": "hmip_dimmer",
     "description": "Dimmer auf 0..100 % setzen.",
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "required": ["dimLevel"], "properties": {
         **_DEVICE_PROPS, "dimLevel": {"type": "number", "minimum": 0, "maximum": 100}}}},
    {"name": "hmip_shutter",
     "description": "Rollladen auf Position 0..100 (wie HCU shutterLevel) fahren oder stoppen.",
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         **_DEVICE_PROPS, "action": {"type": "string", "enum": ["position", "stop"]},
         "level": {"type": "number", "minimum": 0, "maximum": 100}}}},
    {"name": "hmip_light",
     "description": ("Licht (Gerät oder Schaltgruppe): dimmen, Farbe (hue/saturation), "
                     "Farbtemperatur (Kelvin) oder Lichtszene."),
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         **_DEVICE_PROPS, "group": {"type": "string"}, "groupLabel": {"type": "string"},
         "action": {"type": "string", "enum": ["dim", "rgb", "temperature", "scene"]},
         "dimLevel": {"type": "number", "minimum": 0, "maximum": 100},
         "hue": {"type": "integer", "minimum": 0, "maximum": 359},
         "saturation": {"type": "number", "minimum": 0, "maximum": 100},
         "colorTemperature": {"type": "integer", "minimum": 1000, "maximum": 10000},
         "sceneId": {"type": "integer", "minimum": 0}}}},
    {"name": "hmip_loxone_resync",
     "description": "Alle aktuellen HmIP-Werte sofort per UDP an den Loxone Miniserver senden.",
     "annotations": {**_CONTROL_ANNOTATIONS, "idempotentHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
]
CONTROL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "hmip_heating": tool_heating,
    "hmip_switch": tool_switch,
    "hmip_dimmer": tool_dimmer,
    "hmip_shutter": tool_shutter,
    "hmip_light": tool_light,
    "hmip_loxone_resync": tool_loxone_resync,
}


_FILTER_PROPS = {
    "room": {"type": "string", "description": "Raum (Label, exakt, ohne Groß/Klein)"},
    "entity": {"type": "string", "enum": ["device", "group", "home", "shelly"]},
    "field": {"type": "string", "description": "Feld(er), kommagetrennt, z.B. windowOpen,smokeAlarm"},
    "target": {"type": "string", "description": "Gerät/Gruppe: ID oder Label (exakt)"},
    "kind": {"type": "string", "enum": ["bool", "number", "text"]},
}
_PAGE_PROPS = {
    "offset": {"type": "integer", "minimum": 0},
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
              "description": f"Standard {DEFAULT_LIMIT}"},
}

TOOLS: List[Dict[str, Any]] = [
    {"name": "hmip_summary",
     "description": ("Kurzüberblick zuerst nutzen: Räume mit Geräteanzahl, Geräte-/Gruppentypen und "
                     "Loxone-Variablen nach Entität/Typ. Klein, ohne Einzelwerte. Read-only."),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hmip_inventory",
     "description": ("Homematic-IP-Inventar: Räume, Geräte (Typ, Modell, Raum), Kanäle mit Werten, "
                     "Gruppen, Shellys. Filterbar und seitenweise. Read-only."),
     "inputSchema": {"type": "object", "properties": {
         "room": _FILTER_PROPS["room"],
         "type": {"type": "string", "description": "Gerätetyp/Modell enthält, z.B. SHUTTER_CONTACT"},
         "device": {"type": "string", "description": "Geräte-ID oder Teil des Labels"},
         "include_values": {"type": "boolean", "description": "Kanalwerte (Standard true)"},
         "include_variables": {"type": "boolean", "description": "Loxone-Variablen je Kanal (Standard false)"},
         "include_groups": {"type": "boolean", "description": "Gruppenliste (Standard true ohne Filter)"},
         **_PAGE_PROPS,
     }}},
    {"name": "hmip_loxone_mapping",
     "description": ("Variablen, die die Bridge per UDP an den Loxone Miniserver sendet, mit Raum, Gerät, "
                     "Wert, Typ, Einheit, Befehlserkennung (…@\\v) und sent-Flag (Filter). "
                     "Zum Abgleich mit der Loxone Config. Seitenweise. Read-only."),
     "inputSchema": {"type": "object", "properties": {
         **_FILTER_PROPS,
         "sent_only": {"type": "boolean", "description": "nur tatsächlich gesendete Variablen"},
         "filter": {"type": "string", "description": "Volltext innerhalb der Seite"},
         "compact": {"type": "boolean", "description": "nur wichtige Spalten (Standard true)"},
         **_PAGE_PROPS,
     }}},
    {"name": "hmip_loxone_template",
     "description": ("Loxone-Config-Vorlage (XML, Virtueller UDP-Eingang) mit Befehlserkennung, "
                     "Digital/Analog und Einheit für die gefilterten Variablen. Read-only."),
     "inputSchema": {"type": "object", "properties": {
         **_FILTER_PROPS,
         "title": {"type": "string", "description": "Name des Virtuellen UDP-Eingangs"},
         "address": {"type": "string", "description": "optional: Absender-IP der Bridge"},
     }}},
    {"name": "hmip_diagnostics",
     "description": "Bridge-Status: Version, Build, HCU-Verbindung, Anzahl Geräte/Gruppen, Loxone-Sync. Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
]
for _tool in TOOLS:
    _tool["annotations"] = {"readOnlyHint": True, "openWorldHint": True}

HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "hmip_summary": tool_summary,
    "hmip_inventory": tool_inventory,
    "hmip_loxone_mapping": tool_loxone_mapping,
    "hmip_loxone_template": tool_loxone_template,
    "hmip_diagnostics": tool_diagnostics,
}


def available_tools() -> List[Dict[str, Any]]:
    return TOOLS + (CONTROL_TOOLS if control_enabled() else [])


def _http_error_text(error: urllib.error.HTTPError) -> str:
    """HTTP-Fehler mit der JSON-Antwort der Bridge (z.B. Kandidaten bei 409)."""
    detail = ""
    try:
        raw = error.read()
        detail = json.dumps(json.loads(raw.decode("utf-8")), ensure_ascii=False) if raw else ""
    except (ValueError, OSError, AttributeError):
        detail = ""
    hints = {401: "API-Key prüfen (Steuern braucht den Haupt-Key)",
             403: "Quell-IP nicht in control_allowed_ips",
             429: "Duty Cycle zu hoch, später erneut versuchen"}
    text = f"Bridge antwortet HTTP {error.code}"
    if error.code in hints:
        text += f" – {hints[error.code]}"
    return text + (f": {detail}" if detail else ".")


def _call_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    handler = HANDLERS.get(name) or CONTROL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"Unbekanntes Tool: {name}")
    args = params.get("arguments") or {}
    try:
        result = handler(args if isinstance(args, dict) else {})
    except ControlDisabled as error:
        return {"isError": True, "content": [{"type": "text", "text": str(error)}]}
    except urllib.error.HTTPError as error:
        return {"isError": True, "content": [{"type": "text", "text": _http_error_text(error)}]}
    except ValueError as error:
        return {"isError": True, "content": [{"type": "text", "text": f"Ungültige Eingabe: {error}"}]}
    except (urllib.error.URLError, OSError) as error:
        return {"isError": True, "content": [{"type": "text",
                "text": f"Bridge nicht erreichbar: {error}"}]}
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]}


def handle(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Verarbeitet eine JSON-RPC-Nachricht; Notifications liefern None."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None
    try:
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            result: Any = {"protocolVersion": requested or PROTOCOL_VERSION,
                           "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": available_tools()}
        elif method == "tools/call":
            result = _call_tool(message.get("params") or {})
        else:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"Methode nicht unterstützt: {method}"}}
    except KeyError as error:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": str(error)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _force_utf8() -> None:
    """Windows-Konsolen nutzen sonst cp1252 → Umlaute brechen das JSON-RPC (ohne -X utf8)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main() -> None:
    _force_utf8()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            log.warning("Ungültiges JSON von MCP-Client verworfen.")
            continue
        response = handle(message) if isinstance(message, dict) else None
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
