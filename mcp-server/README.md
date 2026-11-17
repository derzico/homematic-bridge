# homematic-bridge-mcp

MCP-Server (stdio) für die Homematic Bridge. Damit kann ein Assistent – z.B.
neben einem Loxone-Config-MCP – die Homematic-IP-Konfiguration lesen, mit Loxone
abgleichen und auf Wunsch auch steuern. Nur Python-Standardbibliothek.

## Installation / Update

```
pipx install "git+https://github.com/derzico/homematic-bridge.git#subdirectory=mcp-server"
pipx install --force "git+https://github.com/derzico/homematic-bridge.git#subdirectory=mcp-server"
```

Startbefehl: `homematic-bridge-mcp` (alternativ `python -m homematic_bridge_mcp`).

## Konfiguration (Claude Desktop / Claude Code)

```json
"homematic-bridge": {
  "command": "homematic-bridge-mcp",
  "env": {
    "BRIDGE_URL": "http://<bridge-ip>:8080",
    "BRIDGE_API_KEY_FILE": "C:\\Users\\<du>\\.homematic-bridge-mcp\\api_key.txt",
    "BRIDGE_CONTROL_API_KEY_FILE": "C:\\Users\\<du>\\.homematic-bridge-mcp\\control_key.txt"
  }
}
```

| Variable | Inhalt | Wirkung |
|---|---|---|
| `BRIDGE_URL` | Adresse der Bridge | Standard `http://127.0.0.1:8080` |
| `BRIDGE_API_KEY` / `_FILE` | **Nur-Lese-Key** (`data/api_key_readonly.txt`) | Lese-Tools |
| `BRIDGE_CONTROL_API_KEY` / `_FILE` | **Haupt-Key** (`data/api_key.txt`) | schaltet die Steuer-Tools frei |

Ohne Steuer-Key bietet der Server nur Lese-Tools an. Ist der Steuer-Key gesetzt,
kommen die Steuer-Tools hinzu; Lesen läuft weiterhin mit dem Nur-Lese-Key.
Nach Änderungen Claude neu starten.

## Tools

Lesen: `hmip_summary`, `hmip_inventory`, `hmip_loxone_mapping`,
`hmip_loxone_template`, `hmip_diagnostics`.

Steuern (nur mit Steuer-Key):

| Tool | Bridge-Endpunkt | Beispiel |
|---|---|---|
| `hmip_heating` | `POST /hmipHeating` | `{"room":"Bad 2OG","action":"temperature","temperature":21.5}`, `{"room":"Bad 2OG","action":"mode","mode":"MANUAL"}` |
| `hmip_switch` | `POST /hmipSwitch` | `{"label":"Hauptlicht","room":"Bad 2OG","on":true}` |
| `hmip_dimmer` | `POST /hmipDimmer` | `{"label":"Spots","dimLevel":40}` |
| `hmip_shutter` | `POST /hmipShutter` | `{"label":"Rollladen","room":"Bad 2OG","action":"position","level":30}` |
| `hmip_light` | `POST /hmipLight` | `{"groupLabel":"Bad 2OG","action":"dim","dimLevel":20}` |
| `hmip_loxone_resync` | `POST /api/loxone/resync` | alle Werte sofort an Loxone senden |

Die Befehle laufen über die normalen Endpunkte der Bridge: Snapshot-Prüfung,
Namensauflösung (mehrdeutig → 409 mit Kandidaten), Funk-Schutz (Duty Cycle →
429), `control_allowed_ips` (→ 403, dann die IP des Rechners freigeben) und
Rückmeldung `lastCmdOk` an Loxone. Die Sirene (`/hmipAlarm`) ist bewusst nicht
als Tool verfügbar. Gruppen ändern oder Ventilstellungen setzen kann die
HCU-Connect-API nicht – Heizung wird über Solltemperatur/Modus der Heizgruppe
gesteuert.
