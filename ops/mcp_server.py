# SPDX-License-Identifier: Apache-2.0
"""Kompatibilitäts-Start für bestehende MCP-Einträge (ops/mcp_server.py).

Empfohlen ist das installierbare Paket:
  pipx install "git+https://github.com/derzico/homematic-bridge.git#subdirectory=mcp-server"
Startbefehl danach: homematic-bridge-mcp
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))

from homematic_bridge_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
