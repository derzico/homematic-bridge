# SPDX-License-Identifier: Apache-2.0
"""Read-only MCP-Server (stdio) für die Homematic Bridge.

Stellt Inventar, Loxone-Mapping, Loxone-Vorlage und Diagnose der Bridge als
MCP-Tools bereit, damit ein Assistent sie neben einem Loxone-Config-MCP abfragen
und beide Konfigurationen abgleichen kann. Nur GET-Abfragen, keine Steuerbefehle.
Nur Python-Standardbibliothek.

Umgebung:
  BRIDGE_URL           z.B. http://192.168.1.10:8080 (Standard: http://127.0.0.1:8080)
  BRIDGE_API_KEY       Nur-Lese-Key (empfohlen) oder Haupt-Key der Bridge
  BRIDGE_API_KEY_FILE  Pfad zu einer Datei mit dem Key (z.B. data/api_key_readonly.txt)
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


def _api_key() -> Optional[str]:
    key = os.getenv("BRIDGE_API_KEY")
    if key:
        return key.strip()
    path = os.getenv("BRIDGE_API_KEY_FILE")
    if path:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read().strip()
    return None


def _request(path: str, params: Optional[Dict[str, Any]] = None) -> bytes:
    base = os.getenv("BRIDGE_URL", "http://127.0.0.1:8080").rstrip("/")
    query = {k: v for k, v in (params or {}).items() if v not in (None, "", False)}
    url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(url, method="GET")
    key = _api_key()
    if key:
        request.add_header("X-API-Key", key)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return response.read()


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


_FILTER_PROPS = {
    "room": {"type": "string", "description": "Raum (Label, exakt, ohne Groß/Klein)"},
    "entity": {"type": "string", "enum": ["device", "group", "home"]},
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
HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "hmip_summary": tool_summary,
    "hmip_inventory": tool_inventory,
    "hmip_loxone_mapping": tool_loxone_mapping,
    "hmip_loxone_template": tool_loxone_template,
    "hmip_diagnostics": tool_diagnostics,
}


def _call_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    handler = HANDLERS.get(params.get("name"))
    if handler is None:
        raise KeyError(f"Unbekanntes Tool: {params.get('name')}")
    args = params.get("arguments") or {}
    try:
        result = handler(args if isinstance(args, dict) else {})
    except urllib.error.HTTPError as error:
        return {"isError": True, "content": [{"type": "text",
                "text": f"Bridge antwortet HTTP {error.code} (API-Key/URL prüfen)."}]}
    except (urllib.error.URLError, OSError, ValueError) as error:
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
            result = {"tools": TOOLS}
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
