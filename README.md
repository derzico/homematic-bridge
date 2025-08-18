# homematic-bridge

> Eine schlanke Bridge, die die **Homematic IP Home Control Unit (HCU)** via WebSocket verbindet und eine kleine **HTTP-API** für andere Smarthome-Systeme bereitstellt.

- **Live-Import** des Systemzustands (Voll-Snapshot) + **inkrementelle Events** (Merge in `system_state.json`).
- **Steuerbefehle** (z. B. `setSwitchState`) als einfache HTTP-API.
- **HTML-Übersicht** aller Geräte + **Detailansicht** pro Device.
- **Healthcheck** für Monitoring.
- **Robust**: Exponentielles Reconnect, atomare Writes, Request–Response-Korrelation (ID-Mapping).

> ⚠️ Nicht offiziell von eQ-3/Homematic. Marken gehören ihren jeweiligen Inhabern.

---

## Inhalt

- [Überblick](#überblick)
- [Features](#features)
- [Architektur](#architektur)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Konfiguration](#konfiguration)

  - [config.yaml](#configyaml)
  - [internal.yaml](#internalyaml)

- [Starten](#starten)
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
- Eine kleine **HTTP-API** bietet Bedienfunktionen (z. B. Schalter an/aus) und **HTML-Seiten** zur Übersicht/Inspektion.

## Features

- ✅ Voll-Snapshot + Event-Merge
- ✅ Steuerung via HTTP (`/hmipSwitch`)
- ✅ HTML-Übersicht (`/devices/html`) & Gerätedetail (`/devices/<id>`)
- ✅ Healthcheck (`/healthz`)
- ✅ Exponentielles Backoff + Jitter
- ✅ Atomare Writes (keine halb geschriebenen JSONs)
- ✅ Pending-Registry (Request–Response-Korrelation über `id`)

## Architektur

- **`main.py`** – Verbindet WebSocket, startet HTTP-Server, Healthcheck, Pending-Registry, Routen.
- **`app/messages.py`** – Baut **HMIP_SYSTEM_REQUEST**-Messages (u. a. `getSystemState`, `setSwitchState`) und liefert **Request-IDs** zurück.
- **`app/utils.py`**

  - `save_system_state(msg)`: Speichert **Vollzustand** oder merged **Events** in `system_state.json`.
  - Atomare Writes, Event-Log optional.

- **`app/generate_html.py`** – Baut **Übersichts-HTML** und **Geräte-Detail-HTML** (zeigt auch `functionalChannels`).
- **`config/loader.py`** – Lädt `config.yaml`/`internal.yaml`.

## Voraussetzungen

- **Python 3.10+** empfohlen
- Netzwerkzugriff zur **HCU** (Port `9001`)
- Optional: **Waitress** (Windows) als stabiler HTTP-Server

## Installation

```bash
# 1) Repository klonen
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge

# 2) Virtuelle Umgebung (optional, empfohlen)
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 3) Abhängigkeiten
pip install -r requirements.txt
```

## Konfiguration

Lege eine **`config.yaml`** und **`internal.yaml`** auf Basis der `*_sample.yaml`-Dateien an (liegen im Repo). `config.yaml` gehört **nicht** in die Versionskontrolle.

### `config.yaml`

```yaml
homematic_hcu: hcu1-E461.local
homematic_token: "<DEIN_TOKEN>"
ssl_cert_path: # optional, z. B. /etc/ssl/certs/ca-bundle.crt
ssl_verify: false # true = Zertifikate prüfen (empfohlen in Prod)
plugin_id: de.zico.homematic.bridge
friendly_name:
  en: "Homematic HTTP Bridge"
  de: "Homematic HTTP Bridge"

# HTTP-API Auth (optional, aber empfohlen)
api_key: # leer lassen, wenn per ENV oder Auto-Generation
require_api_key: true
api_key_file: data/api_key.txt # falls kein Key vorhanden, wird automatisch generiert
```

> **ENV**: `BRIDGE_API_KEY` kann den Key vorgeben (hat Vorrang). Ist kein Key gesetzt und `require_api_key: true`, generiert die App beim Start automatisch einen und speichert ihn in `data/api_key.txt`.

### `internal.yaml`

```yaml
system_state_path: data/system_state.json
device_html_path: static/device_overview.html
log_file: logs/bridge.log
log_level: info
log_rotate: true
health_stale_seconds: 60
pending_ttl_seconds: 60
# events_log_path: data/events.log.jsonl   # optionales Event-Log
```

## Starten

**Windows (empfohlen mit Waitress):**

```powershell
# Dev-Start (Fallback auf Flask-Server):
python main.py

```

**Linux/macOS:**

```bash
python main.py
# oder via gunicorn (optional): gunicorn -w 2 -b 0.0.0.0:8080 'main:app'
```

Bei Start verbindet `main.py` den WebSocket, sendet `getSystemState` und fährt den HTTP-Server hoch.

## HTTP-Endpoints

### `POST /hmipSwitch`

**Steuert** einen Switch-Kanal.

- **Header:** `X-API-Key: <key>` (falls `require_api_key: true`)
- **Body (JSON):**

  ```json
  { "device": "<DEVICE_ID>", "on": true, "channelIndex": 0 }
  ```

- **Response:**

  ```json
  { "status": "<DEVICE_ID>: ON", "request_id": "<uuid>" }
  ```

### `GET /hmipSwitch` (nur lokal erlaubt)

Komfort-Endpunkt für Tests von `127.0.0.1`/`::1`.

- **Query:** `?device=<ID>&on=true|false&channelIndex=0`

### `GET /devices/html`

Generiert und liefert eine einfache **Geräte-Übersicht** als HTML.

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

Die Übersicht verlinkt direkt auf die Detailseiten. Der Zielordner (`static/`) wird bei Bedarf erstellt.

## Sicherheit

- **POST + API-Key** (Header `X-API-Key`) für schreibende Endpunkte.
- **GET /hmipSwitch** ist **nur lokal** erlaubt – in Prod am besten deaktivieren.
- **TLS zum HCU-WebSocket** aktivieren (`ssl_verify: true` + `ssl_cert_path` **oder** `certifi`).
- Hinter **Reverse Proxy** betreiben (TLS/Rate-Limits/Firewall).

## Troubleshooting

- **`devices_count: 0` in `/healthz`** → Prüfe, ob `system_state.json` einen **Vollzustand** enthält (Pfad: `body.body.(home.)devices` wird unterstützt). Evtl. einmal `/hmip/home/getSystemState` erneut triggern (Bridge neustarten).
- **Snapshot wird überschrieben nach Switch-ACK** → Kein Problem: ACK-Responses (`code: 200`) werden **nicht** als Vollsnapshot gespeichert. Nur `getSystemState` schreibt den Vollzustand.
- **WebSocket verbindet nicht** → Hostname/Port zur HCU prüfen; Firewall; bei Zertifikatfehlern `ssl_verify: false` (nur Test!) oder korrekte CA angeben.
- **HTML zeigt „Keine Geräte“** → Prüfe `system_state.json`; die Bridge unterstützt Dict- und Listen-Layouts unter `body`/`body.body`.

## Entwicklung & Logs

- Log-Datei: `logs/bridge.log` (Rotation: täglich, 7 Backups)
- `log_level`: `debug`/`info`/`warning` … (Standard `info`).
- Sensible Payloads werden nur auf **DEBUG** geloggt.

## Lizenz

Dieser Code steht unter der **Apache License 2.0**. Siehe [`LICENSE`](./LICENSE) und [`NOTICE`](./NOTICE).

Optional: `THIRD_PARTY_NOTICES.md` enthält Kurz-Infos zu Laufzeit-Abhängigkeiten.

## Hinweise / Danksagung

- Nicht offiziell mit eQ-3/Homematic verbunden.
- Danke an die Maintainer der verwendeten Open-Source-Bibliotheken.

---

Viel Spaß beim Bridgen! Wenn dir die Bridge hilft, freuen wir uns über ⭐ auf GitHub und Issues/PRs. 🙌
