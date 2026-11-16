# SPDX-License-Identifier: Apache-2.0
# state.py – Gemeinsamer Laufzeitzustand (wird von main.py initialisiert)

from threading import Event, Lock
from typing import Dict, Any, Optional

# WebSocket-Verbindung (gelesen/geschrieben unter send_lock)
conn = None
send_lock = Lock()

# Globales Stop-Flag für daemon-Threads (ws_loop, Shelly-Periodic-Scan)
stop_event = Event()

# Pending-Requests: id -> {"path": str, "ts": float}
pending: Dict[str, Dict[str, Any]] = {}
pending_lock = Lock()

# Auth
API_KEY: Optional[str] = None
REQUIRE_API_KEY: bool = True
API_KEY_FILE: str = "data/api_key.txt"
# Nur-Lese-Key (Inventar, Mapping, Diagnose, Zustände) – kann nichts schalten
API_KEY_READONLY: Optional[str] = None
API_KEY_READONLY_FILE: str = "data/api_key_readonly.txt"
# Erlaubte Quellnetze für Steuerbefehle (leer = alle); Liste von ipaddress-Netzen
CONTROL_ALLOWED_NETS: list = []

# Loxone UDP-Push
LOXONE_HOST: str = ""
LOXONE_UDP_PORT: int = 7777

# Health / Timeouts
STALE_SEC: float = 60.0
PENDING_TTL: float = 60.0
ACK_TIMEOUT_SEC: float = 3.0

# WebSocket-Telemetrie
WS_CONNECTED_AT: Optional[float] = None
WS_LAST_MESSAGE_AT: Optional[float] = None
WS_LAST_ERROR: Optional[str] = None
WS_RECONNECTS: int = 0
WS_READY: bool = False
WS_PLUGIN_ID: Optional[str] = None

# Konfiguration (wird von main.py gesetzt)
config: Dict[str, Any] = {}
config_internal: Dict[str, Any] = {}
config_lock = Lock()  # schützt config / config_internal vor gleichzeitigem Lesen/Schreiben

# Adapter-Registry (wird von main.py gesetzt)
adapter_registry: Optional[Any] = None  # Type: AdapterRegistry (vermeidet zirkulären Import)
runtime_services: Optional[Any] = None
