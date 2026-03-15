# SPDX-License-Identifier: Apache-2.0
# state.py – Gemeinsamer Laufzeitzustand (wird von main.py initialisiert)

from threading import Lock
from typing import Dict, Any, Optional

# WebSocket-Verbindung
conn = None
send_lock = Lock()

# Pending-Requests: id -> {"path": str, "ts": float}
pending: Dict[str, Dict[str, Any]] = {}
pending_lock = Lock()

# Auth
API_KEY: Optional[str] = None
REQUIRE_API_KEY: bool = True
API_KEY_FILE: str = "data/api_key.txt"

# Loxone UDP-Push
LOXONE_HOST: str = ""
LOXONE_UDP_PORT: int = 7777

# Health / Timeouts
STALE_SEC: float = 60.0
PENDING_TTL: float = 60.0

# Konfiguration (wird von main.py gesetzt)
config: Dict[str, Any] = {}
config_internal: Dict[str, Any] = {}
