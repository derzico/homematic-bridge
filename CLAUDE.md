# CLAUDE.md – Projektkontext für Claude Code

## Schnellstart

```bash
# Abhängigkeiten installieren
pip install -r requirements.txt

# Tests ausführen
python -m pytest tests/ -v

# Server starten (benötigt config/config.yaml)
python main.py
```

## Projektübersicht

Python-Bridge zwischen Homematic IP (HmIP) und externen Systemen (Shelly, Loxone).
Flask-HTTP-Server (waitress) auf Port 8080, HmIP-WebSocket auf Port 9001.

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
  dashboard.html         # Dashboard-Seite
  devices.html           # Geräteübersicht
  device_detail.html     # Gerätedetail mit Channels + Raw JSON
  status.html            # Gerätestatus (Batterie, Erreichbarkeit, RSSI)
  heating.html           # Heizungsseite
  shelly.html            # Shelly-Geräte mit Steuerung
  config.html            # Konfigurationseditor
  login.html             # Login-Seite
config/
  loader.py              # YAML laden + Validierung
  config_sample.yaml     # Vorlage für config.yaml
  internal_config.yaml   # Deployment-Konfiguration
tests/                   # pytest-Tests
main.py                  # Einstiegspunkt
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

## Konventionen

- **Sprache**: Kommentare und Docstrings auf Deutsch
- **Logging**: Immer `log = logging.getLogger("bridge-ws")`, nie print()
- **Fehler loggen**: `log.exception(...)` statt `log.error(f"...{e}")` (Stacktrace erhalten)
- **Type Hints**: Alle Funktionssignaturen typisiert
- **SPDX-Header**: Jede Quelldatei beginnt mit `# SPDX-License-Identifier: Apache-2.0`
- **Thread Safety**: Shared State immer unter Lock lesen/schreiben
- **Auth-Decorators**: `@require_api_key` für API-Routen, `@require_web_auth` für Web-UI, `@require_csrf` für POST-Formulare

## Wichtige Befehle

```bash
# Alle Tests
python -m pytest tests/ -v

# Tests mit Coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

# Passwort setzen (für Web-Login)
python app/set_password.py

# HmIP-Token anfordern
python app/request_token.py
```

## Laufzeitdaten (git-ignored)

- `data/system_state.json` – HmIP Vollsnapshot + Delta-Merge
- `data/shelly_devices.json` – Shelly-Geräte-Cache
- `data/api_key.txt` – Auto-generierter API-Key
- `data/secret_key.bin` – Flask Session Secret
