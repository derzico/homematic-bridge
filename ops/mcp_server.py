# SPDX-License-Identifier: Apache-2.0
"""Read-only MCP-Server (stdio) für die Homematic Bridge.

Stellt die Bridge-Endpunkte /api/inventory, /api/loxone/mapping und
/api/diagnostics als MCP-Tools bereit, damit ein Assistent sie neben einem
Loxone-Config-MCP abfragen und beide Konfigurationen abgleichen kann.

Nur GET-Abfragen, keine Steuerbefehle. Nur Python-Standardbibliothek.

Umgebung:
  BRIDGE_URL           z.B. http://192.168.1.10:8080 (Standard: http://127.0.0.1:8080)
  BRIDGE_API_KEY       API-Key der Bridge (alternativ BRIDGE_API_KEY_FILE)
  BRIDGE_API_KEY_FILE  Pfad zu data/api_key.txt
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("bridge-ws")

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "homematic-bridge", "version": "1.20.0"}
TIMEOUT_SEC = 15


def _api_key() -> Optional[str]:
    key = os.getenv("BRIDGE_API_KEY")
    if key:
        return key.strip()
    path = os.getenv("BRIDGE_API_KEY_FILE")
    if path:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read().strip()
    return None


def _get(path: str) -> Any:
    base = os.getenv("BRIDGE_URL", "http://127.0.0.1:8080").rstrip("/")
    request = urllib.request.Request(base + path, method="GET")
    key = _api_key()
    if key:
        request.add_header("X-API-Key", key)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


def _matches(entry: Dict[str, Any], needle: str) -> bool:
    return needle in json.dumps(entry, ensure_ascii=False).lower()


def tool_inventory(args: Dict[str, Any]) -> Any:
    data = _get("/api/inventory")
    room = str(args.get("room") or "").strip().lower()
    if room:
        data["devices"] = [d for d in data.get("devices", [])
                           if str(d.get("room") or "").lower() == room]
        data["rooms"] = [r for r in data.get("rooms", []) if str(r.get("label", "")).lower() == room]
    if args.get("include_values") is False:
        for device in data.get("devices", []):
            for channel in device.get("channels", []):
                channel.pop("values", None)
    return data


def tool_loxone_mapping(args: Dict[str, Any]) -> Any:
    data = _get("/api/loxone/mapping")
    needle = str(args.get("filter") or "").strip().lower()
    if needle:
        data["variables"] = [v for v in data.get("variables", []) if _matches(v, needle)]
        data["count"] = len(data["variables"])
    return data


def tool_diagnostics(_args: Dict[str, Any]) -> Any:
    return _get("/api/diagnostics")


TOOLS: List[Dict[str, Any]] = [
    {"name": "hmip_inventory",
     "description": ("Homematic-IP-Gesamtinventar: Räume, Geräte (Typ, Modell, Raum), Kanäle mit "
                     "aktuellen Werten, Gruppen mit Mitgliedern, Shelly-Geräte und je Kanal die "
                     "Loxone-UDP-Variablennamen. Read-only."),
     "inputSchema": {"type": "object", "properties": {
         "room": {"type": "string", "description": "Optional: nur Geräte dieses Raums (Label)"},
         "include_values": {"type": "boolean", "description": "false = Kanalwerte weglassen"},
     }}},
    {"name": "hmip_loxone_mapping",
     "description": ("Alle Variablen, die die Bridge per UDP an den Loxone Miniserver sendet "
                     "(hmip_<ID>_ch<N>_<feld>), mit Gerät, Raum, Kanaltyp, aktuellem Wert, Typ "
                     "(bool/number/text) und passender Befehlserkennung für Virtual UDP Inputs. "
                     "Zum Abgleich mit der Loxone Config. Read-only."),
     "inputSchema": {"type": "object", "properties": {
         "filter": {"type": "string",
                    "description": "Optional: Volltextfilter (Raum, Label, Feld, Geräte-ID)"},
     }}},
    {"name": "hmip_diagnostics",
     "description": "Bridge-Status: Version, HCU-Verbindung, Anzahl Geräte/Gruppen, Loxone-Sync. Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
]
HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "hmip_inventory": tool_inventory,
    "hmip_loxone_mapping": tool_loxone_mapping,
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


def main() -> None:
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
