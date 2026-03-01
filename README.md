# homematic-bridge

> Eine schlanke Bridge, die die **Homematic IP Home Control Unit (HCU)** via WebSocket verbindet und eine kleine **HTTP-API** für andere Smarthome-Systeme bereitstellt.

- **Live-Import** des Systemzustands (Voll-Snapshot) + **inkrementelle Events** (Merge in `system_state.json`).
- **Steuerbefehle** (z. B. `setSwitchState`) als einfache HTTP-API.
- **HTML-Übersicht** aller Geräte + **Detailansicht** pro Device.
- **Healthcheck** für Monitoring.
- **Robust**: WebSocket-Keepalive, exponentielles Reconnect, atomare Writes, Request–Response-Korrelation (ID-Mapping).
- **Docker-Support**: Einfaches Deployment mit `docker compose up -d`.

> ⚠️ Nicht offiziell von eQ-3/Homematic. Marken gehören ihren jeweiligen Inhabern.

---

## Inhalt

- [Überblick](#überblick)
- [Features](#features)
- [Architektur](#architektur)
- [Voraussetzungen](#voraussetzungen)
- [Quickstart mit Docker](#quickstart-mit-docker)
- [Manuelle Installation](#manuelle-installation)
- [Konfiguration](#konfiguration)
  - [config.yaml](#configyaml)
  - [internal_config.yaml](#internal_configyaml)
- [HTTP-Endpoints](#http-endpoints)
- [HTML-Ansichten](#html-ansichten)
- [Sicherheit](#sicherheit)
- [Troubleshooting](#troubleshooting)
- [Entwicklung & Logs](#entwicklung--logs)
- [Lizenz](#lizenz)
- [Hinweise / Danksagung](#hinweise--danksagung)

---

## Überblick

Diese Bridge verbindet sich per **WebSocket** auf die HCU (Port `9001`), sendet initial `getSystemState` (Vollzustand) und verarbeitet danach **Systemevents** (`HMIP_SYSTEM_EVENT`).

- Vollzustand wird in `data/system_state.json` gespeichert.
- Events werden **in diese Datei gemerged** (geräteweise, inklusive `functionalChannels`).
- Eine kleine **HTTP-API** bietet Bedienfunktionen (z. B. Schalter an/aus) und **HTML-Seiten** zur Übersicht/Inspektion.

## Features

- ✅ Voll-Snapshot + Event-Merge
- ✅ WebSocket-Keepalive (Ping bei Inaktivität, automatischer Reconnect mit exponentiellem Backoff)
- ✅ Steuerung via HTTP (`/hmipSwitch`) – GET und POST
- ✅ HTML-Übersicht (`/devices/html`) & Gerätedetail (`/devices/<id>`)
- ✅ Healthcheck (`/healthz`)
- ✅ Atomare Writes (keine halb geschriebenen JSONs)
- ✅ Pending-Registry (Request–Response-Korrelation über `id`)
- ✅ API-Key Auth (automatisch generiert, konfigurierbar)
- ✅ Docker-Support

## Architektur

- **`main.py`** – Verbindet WebSocket, startet HTTP-Server, Healthcheck, Pending-Registry, Routen.
- **`app/messages.py`** – Baut **HMIP_SYSTEM_REQUEST**-Messages (u. a. `getSystemState`, `setSwitchState`) und liefert **Request-IDs** zurück.
- **`app/utils.py`** – `save_system_state(msg)`: Speichert **Vollzustand** oder merged **Events** in `system_state.json`. Atomare Writes.
- **`app/generate_html.py`** – Baut **Übersichts-HTML** und **Geräte-Detail-HTML** (zeigt auch `functionalChannels`).
- **`config/loader.py`** – Lädt `config.yaml` / `internal_config.yaml`.

## Voraussetzungen

- Netzwerkzugriff zur **HCU** (Port `9001`)
- **Docker** (empfohlen) **oder** Python 3.11+

## Quickstart mit Docker

```bash
# 1. Repository klonen
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge

# 2. Config anlegen und befüllen
cp config/config_sample.yaml config/config.yaml
# → homematic_hcu, homematic_token, plugin_id eintragen

# 3. Starten
docker compose up -d

# Logs verfolgen
docker compose logs -f

# Stoppen
docker compose down
```

Der API-Key wird beim ersten Start automatisch generiert und in `data/api_key.txt` gespeichert.

## Manuelle Installation

```bash
# 1. Repository klonen
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge

# 2. Virtuelle Umgebung anlegen
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Config anlegen und befüllen
cp config/config_sample.yaml config/config.yaml

# 5. Starten
python main.py
```

## Konfiguration

### `config.yaml`

```yaml
homematic_hcu: hcu1-XXXX.local   # mDNS-Name oder IP der HCU
homematic_token: "<DEIN_TOKEN>"  # einmalig über request_token.py generieren
ssl_cert_path:                   # optional: Pfad zum CA-Zertifikat
ssl_verify: false                # true = Zertifikate prüfen (empfohlen in Prod)
plugin_id: de.example.bridge     # eindeutige Plugin-ID (Reverse-Domain-Notation)
friendly_name:
  en: "Homematic HTTP Bridge"
  de: "Homematic HTTP Bridge"

# HTTP-API Auth
api_key:          # optional: Key hier eintragen, sonst wird automatisch generiert
require_api_key: true
api_key_file: data/api_key.txt
```

> **Token generieren:** HCU-Weboberfläche → Developer Mode → Activation Key generieren, dann `python app/request_token.py` ausführen.

> **API-Key:** Wird beim ersten Start automatisch generiert und in `data/api_key.txt` gespeichert. Alternativ über die Umgebungsvariable `BRIDGE_API_KEY` vorgeben.

### `internal_config.yaml`

```yaml
system_state_path: data/system_state.json
device_html_path: static/device_overview.html
log_file: logs/bridge.log
log_level: info       # debug / info / warning / error
log_rotate: true
health_stale_seconds: 60
pending_ttl_seconds: 60
```

## HTTP-Endpoints

### `POST /hmipSwitch`

Steuert einen Switch-Kanal von extern (z. B. Home Assistant, Loxone).

- **Header:** `X-API-Key: <key>`
- **Body (JSON):**
  ```json
  { "device": "<DEVICE_ID>", "on": true, "channelIndex": 0 }
  ```
- **Response:**
  ```json
  { "status": "<DEVICE_ID>: ON", "request_id": "<uuid>" }
  ```

### `GET /hmipSwitch`

Komfort-Endpunkt für schnelle Tests und Integrationen wie Loxone.

- Von **localhost** (`127.0.0.1` / `::1`): kein API-Key erforderlich
- Von **extern** (z. B. Loxone): `X-API-Key`-Header erforderlich
- **Query:** `?device=<ID>&on=true|false&channelIndex=0`

**Beispiel für Loxone** (Virtueller HTTP-Ausgang):
```
http://<bridge-ip>:8080/hmipSwitch?device=<DEVICE_ID>&on=true&channelIndex=0
Header: X-API-Key: <key aus data/api_key.txt>
```

### `GET /devices/html`

Generiert und liefert eine **Geräte-Übersicht** als HTML.

### `GET /devices/<DEVICE_ID>`

Detailseite mit **Basisdaten**, allen **`functionalChannels`** und **Rohdaten** (pretty-printed JSON).

### `GET /healthz`

Zustand der Bridge:

```json
{
  "ws_connected": true,
  "snapshot_age_ms": 1234,
  "devices_count": 42,
  "pending_requests": 0,
  "status": "ok|degraded|unhealthy"
}
```

## HTML-Ansichten

- Übersicht: `http://localhost:8080/devices/html`
- Detail: `http://localhost:8080/devices/<DEVICE_ID>`

Die Übersicht verlinkt direkt auf die Detailseiten.

## Sicherheit

- **API-Key** (Header `X-API-Key`) schützt alle schreibenden Endpunkte sowie externe GET-Aufrufe.
- **GET /hmipSwitch** ist von localhost ohne Key nutzbar – für externe Zugriffe wird der Key geprüft.
- **TLS zum HCU-WebSocket** aktivieren: `ssl_verify: true` + `ssl_cert_path` oder `certifi`-Bundle.
- Empfehlung: Bridge hinter einem **Reverse Proxy** (z. B. Caddy, nginx) mit TLS betreiben.

## Troubleshooting

- **`devices_count: 0` in `/healthz`** → Prüfe ob `system_state.json` einen Vollzustand enthält. Bridge neustarten triggert erneut `getSystemState`.
- **WebSocket verbindet nicht** → Hostname/Port zur HCU prüfen; Firewall; bei Zertifikatfehlern `ssl_verify: false` (nur Test!) oder korrekte CA angeben.
- **HTML zeigt „Keine Geräte"** → Prüfe `system_state.json`; die Bridge unterstützt Dict- und Listen-Layouts unter `body` / `body.body`.
- **401 bei GET-Aufruf von extern** → `X-API-Key`-Header fehlt oder falsch; Key aus `data/api_key.txt` verwenden.

## Entwicklung & Logs

- Log-Datei: `logs/bridge.log` (Rotation: täglich, 7 Backups)
- Log-Level in `internal_config.yaml` anpassen: `debug` / `info` / `warning` / `error`
- Sensible Payloads werden nur auf **DEBUG** geloggt

## Lizenz

Dieser Code steht unter der **Apache License 2.0**. Siehe [`LICENSE`](./LICENSE) und [`NOTICE`](./NOTICE).

`THIRD-PARTY_NOTICES.md` enthält Informationen zu den verwendeten Open-Source-Bibliotheken.

## Hinweise / Danksagung

- Nicht offiziell mit eQ-3/Homematic verbunden.
- Danke an die Maintainer der verwendeten Open-Source-Bibliotheken.

---

Wenn dir die Bridge hilft, freuen wir uns über einen ⭐ auf GitHub und über Issues/PRs!
