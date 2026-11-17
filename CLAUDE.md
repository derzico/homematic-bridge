# CLAUDE.md – Projektkontext für Claude Code

## Schnellstart

```bash
# Abhängigkeiten installieren
pip install -r requirements.txt

# Tests ausführen
python -m pytest tests/ -v

# Server starten (benötigt config/config.yaml)
python main.py

# Docker (Produktiv-Deployment)
docker compose up -d --build
```

## Projektübersicht

Python-Bridge zwischen Homematic IP (HmIP) und externen Systemen (Shelly, Loxone).
Flask-HTTP-Server (waitress) auf Port 8080, HmIP-WebSocket auf Port 9001.
Deployment via Docker (`docker-compose.yml`).

## Projektstruktur

```
app/
  adapters/              # Adapter-Pattern für Drittsysteme
    base.py              # BaseAdapter ABC, Device/DeviceChannel/DeviceCapability
    registry.py          # AdapterRegistry – zentrales Adapter-Management
    shelly_adapter.py    # ShellyAdapter + Scan/Cache/Steuerung
    hmip_adapter.py      # HmIPAdapter – wrappt WebSocket + Messages
    hmip_messages.py     # HmIP WebSocket-Nachrichten (Request/Response Builder)
    hmip_websocket.py    # HmIP WebSocket-Loop, Pending-Registry, Reconnect
  auth.py                # API-Key, CSRF, Session-Auth Decorators
  routes.py              # Flask Blueprint mit allen HTTP-Routen
  view_helpers.py        # Datenvorbereitung für Jinja2-Templates
  state.py               # Shared Runtime State (Locks, Config, Registry)
  utils.py               # Snapshot-Merge, Device-Container-Suche
  loxone_udp.py          # Loxone UDP-Push (eigenständig, kein Adapter)
templates/               # Jinja2-Templates (HTML)
  base.html              # Base-Layout (CSS, Navigation)
  macros.html            # Wiederverwendbare Makros (val_html, bool_pill, rssi_html)
  dashboard.html         # Dashboard (Alarm, Wetter, Heizungsübersicht)
  devices.html           # Geräteübersicht nach Räumen
  device_detail.html     # Gerätedetail mit Channels + Raw JSON
  status.html            # Gerätestatus (Batterie, Erreichbarkeit, RSSI)
  heating.html           # Heizungsseite mit Gruppen
  shelly.html            # Shelly-Geräte mit Steuerung + isolierten WebUI-Links
  config.html            # Konfigurationseditor
  login.html             # Login-Seite
config/
  loader.py              # YAML laden + Validierung
  config_sample.yaml     # Vorlage für config.yaml
  internal_config.yaml   # Deployment-Konfiguration
tests/                   # pytest-Tests
main.py                  # Einstiegspunkt
docker-compose.yml       # Docker-Deployment
```

## Architektur

### Adapter-Pattern
Neue Drittsysteme werden als Adapter unter `app/adapters/` integriert:
1. Klasse von `BaseAdapter` ableiten (start/stop/get_devices/control)
2. In `main.py` bei `AdapterRegistry` registrieren

### Threading-Modell
- Kein asyncio – alles über `threading.Thread(daemon=True)`
- Shared State in `app/state.py`, geschützt durch Locks:
  - `config_lock` – config/config_internal
  - `pending_lock` – pending Requests
  - `send_lock` – WebSocket-Sends

### Konfiguration
- `config/config.yaml` (git-ignored) – Benutzer-Konfiguration
- `config/internal_config.yaml` – Deployment-Einstellungen
- Validierung beim Start über `validate_config()` / `validate_internal_config()`
- Laufzeit-Zugriff über `state.config` / `state.config_internal` (unter Lock!)

### HTTP-API Endpunkte (Übersicht)
- `POST /hmipSwitch` – Schalten
- `POST /hmipDimmer` – Dimmen
- `POST /hmipRGB` – RGB-Licht (Format: `R=100,G=50,B=0`, Prozentwerte)
- `POST /hmipThermostat` – Solltemperatur einer Heizgruppe setzen (`group`, `temperature`)
- `POST /hmipAlarm` – ALARM_SWITCHING-Gruppe schalten (`on`, optional `group`)
- `POST /hmipIrrigation` – Bewässerungsventil
- `GET /hmipState` – Gerätezustand aus Snapshot
- `POST /shelly/<ip>/relay/<ch>` – Shelly schalten
- `GET /shelly/<ip>/webui/` – Weiterleitung zur isolierten Shelly WebUI
- `GET /healthz` – Healthcheck

HmIP-Steuerbefehle warten bis zu `ack_timeout_seconds` auf die korrelierte
HCU-Antwort. `/healthz` liefert bei getrennter Verbindung, fehlendem gültigem
Vollsnapshot oder ausgefallenem Adapter HTTP 503, nicht allein bei alter Datei-mtime.

Version 1.19: snapshotvalidierte `/hmipShutter`, `/hmipHeating`, `/hmipLight`;
session-/CSRF-geschützte Browsersteuerung `/control/<kind>`, Security-Anzeige,
Loxone-Vollsync und optionale lokale Shelly-Energiehistorie. Details unter
`docs/releases/1.19.0.md`. Keine echten Gerätebefehle in Tests verwenden.

Version 1.20: read-only `GET /api/inventory` und `GET /api/loxone/mapping`
(`app/inventory.py`, Allowlist-Felder, Variablennamen aus `loxone_udp._system_lines`)
sowie stdio-MCP-Server `ops/mcp_server.py` für den Abgleich mit einem
Loxone-Config-MCP. Details unter `docs/releases/1.20.0.md`.

Version 1.21: Loxone-Zusatzwerte und `loxone.filter` (`app/loxone_udp.py`,
Modulzustand über `configure()`/`update_topology()`), Funk-Schutz
(`app/command_guard.py`, greift in `routes._send_hmip_command`), Namensauflösung
und Loxone-RGB (`app/targets.py`), Nur-Lese-Key `@require_read_key` und
`control_allowed_ips` (`app/auth.py`), MCP-Paket unter `mcp-server/`,
Deploy mit `ops/deploy.sh`. Tests in `tests/test_loxone_sync.py` sichern das alte
UDP-Format (Zusatzwerte dort aus), neue Werte in `tests/test_bridge_extras.py`.
Details unter `docs/releases/1.21.0.md`.

MCP 1.22: Steuer-Tools (`hmip_heating`, `hmip_switch`, `hmip_dimmer`, `hmip_shutter`,
`hmip_light`, `hmip_loxone_resync`) nur mit `BRIDGE_CONTROL_API_KEY`; Lese-Tools
weiter mit Nur-Lese-Key. Keine Sirene. Einrichtung: `mcp-server/README.md`.

## Konventionen

- **Sprache**: Kommentare und Docstrings auf Deutsch
- **Logging**: Immer `log = logging.getLogger("bridge-ws")`, nie print()
- **Fehler loggen**: `log.exception(...)` statt `log.error(f"...{e}")` (Stacktrace erhalten)
- **Type Hints**: Alle Funktionssignaturen typisiert
- **SPDX-Header**: Jede Quelldatei beginnt mit `# SPDX-License-Identifier: Apache-2.0`
- **Thread Safety**: Shared State immer unter Lock lesen/schreiben
- **Auth-Decorators**: `@require_api_key` für steuernde API-Routen, `@require_read_key` für lesende API-Routen, `@require_web_auth` für Web-UI, `@require_csrf` für POST-Formulare
- **RGB**: Kein separater `setSwitchState` vor `setHueSaturationDimLevel` – Gerät schaltet implizit ein

## Wichtige Befehle

```bash
# Alle Tests
python -m pytest tests/ -v

# Tests mit Coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

# Passwort setzen (für Web-Login)
docker compose exec homematic-bridge python app/set_password.py

# HmIP-Token anfordern (einmalig, vor erstem Start)
docker compose run --rm homematic-bridge python app/request_token.py

# Docker neu bauen und starten
docker compose up -d --build

# Logs verfolgen
docker compose logs -f
```

## Laufzeitdaten (git-ignored)

- `data/system_state.json` – HmIP Vollsnapshot + Delta-Merge
- `data/shelly_devices.json` – Shelly-Geräte-Cache
- `data/api_key.txt` – Auto-generierter API-Key
- `data/secret_key.bin` – Flask Session Secret

## Dokumentation

Vollständige Dokumentation im [GitHub Wiki](https://github.com/derzico/homematic-bridge/wiki).
