# homematic-bridge

> Eine schlanke Bridge, die die **Homematic IP Home Control Unit (HCU)** via WebSocket verbindet und eine kleine **HTTP-API** für andere Smarthome-Systeme bereitstellt.

- **Live-Import** des Systemzustands (Voll-Snapshot) + **inkrementelle Events** (Merge in `system_state.json`).
- **Steuerbefehle** (z. B. `setSwitchState`) als einfache HTTP-API.
- **Weboberfläche** mit Dashboard, Heizung, Geräteübersicht, Gerätestatus und Detailansicht.
- **UDP-Push** an Loxone Miniserver bei Gerätezustandsänderungen.
- **Healthcheck** für Monitoring.
- **Robust**: WebSocket-Keepalive, exponentielles Reconnect, atomare Writes, Request–Response-Korrelation.
- **Docker-Support**: Einfaches Deployment mit `docker compose up -d`.

> ⚠️ Nicht offiziell von eQ-3/Homematic. Marken gehören ihren jeweiligen Inhabern.

---

## Inhalt

- [Features](#features)
- [Architektur](#architektur)
- [Voraussetzungen](#voraussetzungen)
- [Quickstart mit Docker](#quickstart-mit-docker)
- [Manuelle Installation](#manuelle-installation)
- [Konfiguration](#konfiguration)
- [Sicherheit](#sicherheit)
  - [Webinterface-Login](#webinterface-login)
  - [Webinterface-Passwort setzen](#webinterface-passwort-setzen)
  - [SSL/TLS – HCU-Zertifikat einrichten](#ssltls--hcu-zertifikat-einrichten)
- [HTTP-Endpoints](#http-endpoints)
- [Weboberfläche](#weboberfläche)
- [Loxone-Integration](#loxone-integration)
- [Update](#update)
- [Troubleshooting](#troubleshooting)
- [Entwicklung & Logs](#entwicklung--logs)
- [Lizenz](#lizenz)

---

## Features

- ✅ Voll-Snapshot + inkrementeller Event-Merge (Devices **und** Groups)
- ✅ WebSocket-Keepalive (Ping bei Inaktivität, automatischer Reconnect mit exponentiellem Backoff)
- ✅ Steuerung via HTTP (`/hmipSwitch`, `/hmipDimmer`, `/hmipRGB`) – GET und POST
- ✅ Status-Abfrage via HTTP (`/hmipState`) – Gerätezustand aus Snapshot
- ✅ Weboberfläche: Dashboard, Heizung, Geräteübersicht, Gerätestatus, Detailseite
- ✅ Session-basierter Login (kein Basic Auth-Popup)
- ✅ UDP-Push an Loxone Miniserver (Echtzeit-Statusübertragung)
- ✅ Healthcheck (`/healthz`)
- ✅ Atomare Writes (keine halb geschriebenen JSONs)
- ✅ API-Key Auth (automatisch generiert, konfigurierbar)
- ✅ SSL/TLS mit HCU-Zertifikat (Hostname-unabhängige Verifikation)
- ✅ Docker-Support (Waitress als Prod-WSGI)

## Architektur

```
main.py                    # Einstiegspunkt: Config, Logging, Flask-App, WS-Thread
app/
  state.py                 # Geteilter Laufzeitzustand (conn, locks, config)
  auth.py                  # API-Key-Verwaltung, Login-Decorators
  websocket_handler.py     # WS-Loop, Pending-Registry, Log-Level-Update
  routes.py                # Flask-Blueprint mit allen HTTP-Routen
  messages.py              # HmIP-Nachrichten bauen (setSwitchState, etc.)
  utils.py                 # Snapshot speichern + Delta-Merge (Devices & Groups)
  generate_html.py         # HTML-Seiten generieren (Dashboard, Heizung, etc.)
  loxone_udp.py            # UDP-Push an Loxone Miniserver
  request_token.py         # Einmaliges Token-Generierungs-Script
  set_password.py          # Webinterface-Passwort setzen (bcrypt-Hash)
config/
  loader.py                # config.yaml / internal_config.yaml laden
  config.yaml              # HCU-Verbindung, Token, Plugin-ID (nicht im Git)
  config_sample.yaml       # Vorlage für config.yaml
  internal_config.yaml     # Laufzeit-Konfiguration (Logging, Pfade, Passwort-Hash)
data/                      # Laufzeitdaten (api_key.txt, system_state.json) – nicht im Git
logs/                      # Log-Dateien – nicht im Git
```

## Voraussetzungen

- Netzwerkzugriff zur **HCU** (Port `9001` für WebSocket, Port `6969` für Token-Generierung)
- **Docker** (empfohlen) **oder** Python 3.11+

## Quickstart mit Docker

### 1. Token generieren

Vor dem ersten Start muss einmalig ein **Homematic-Token** generiert werden:

**a) In der HCU-Weboberfläche:**
1. HCU-Weboberfläche öffnen → Einstellungen → Developer Mode aktivieren
2. Einen **Activation Key** (kurzer Code, z. B. `697CC4`) generieren

**b) Token anfordern:**
```bash
# Repository klonen
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge

# Config anlegen
cp config/config_sample.yaml config/config.yaml
# → homematic_hcu und plugin_id in config.yaml eintragen

# Token generieren (interaktiv, fragt nach dem Activation Key)
docker compose run --rm homematic-bridge python app/request_token.py
```

Der generierte Token wird automatisch in `config/config.yaml` gespeichert.

### 2. Starten

```bash
docker compose up -d
docker compose logs -f
```

Der API-Key für die HTTP-API wird beim ersten Start automatisch generiert und in `data/api_key.txt` gespeichert.

### 3. Weboberfläche aufrufen

```
http://<bridge-ip>:8080
```

Login mit dem API-Key aus `data/api_key.txt` als Passwort (oder einem eigenen Passwort, siehe [Webinterface-Passwort setzen](#webinterface-passwort-setzen)).

## Manuelle Installation

```bash
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge

python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp config/config_sample.yaml config/config.yaml
# → config.yaml anpassen

python app/request_token.py  # einmalig

python main.py
```

## Konfiguration

### `config/config.yaml`

```yaml
homematic_hcu: hcu1-XXXX.local   # mDNS-Name oder IP der HCU
homematic_token: "<DEIN_TOKEN>"  # einmalig über request_token.py generieren
ssl_verify: false                # true = Zertifikat prüfen (siehe SSL-Abschnitt)
ssl_cert_path: config/hcu.crt   # Pfad zum HCU-Zertifikat (bei ssl_verify: true)
plugin_id: de.example.bridge     # eindeutige Plugin-ID (Reverse-Domain-Notation)
friendly_name:
  en: "Homematic HTTP Bridge"
  de: "Homematic HTTP Bridge"

# Loxone UDP-Push (optional)
loxone:
  miniserver_ip: 192.168.1.100
  udp_port: 7777

# HTTP-API Auth
require_api_key: true
api_key_file: data/api_key.txt
```

### `config/internal_config.yaml`

```yaml
system_state_path: data/system_state.json
log_file: logs/bridge.log
log_level: info          # debug / info / warning / error
log_rotate: true
health_stale_seconds: 60
pending_ttl_seconds: 60

# Passwort-Hash für das Webinterface (setzen mit: python app/set_password.py)
# web_password_hash: pbkdf2:sha256:...
```

---

## Sicherheit

### Webinterface-Login

Das Webinterface ist durch einen **Session-basierten Login** geschützt. Nach dem Einloggen wird ein signiertes Session-Cookie gesetzt — kein HTTP-Basic-Auth-Popup.

- **Standard-Passwort:** API-Key aus `data/api_key.txt`
- **Eigenes Passwort:** Über `app/set_password.py` setzen (wird als bcrypt-Hash gespeichert)
- **API-Endpoints** (`/hmipSwitch`, `/hmipState`, etc.) verwenden weiterhin den `X-API-Key`-Header

### Webinterface-Passwort setzen

Das Passwort wird als **bcrypt-Hash** in `internal_config.yaml` gespeichert — niemals im Klartext.

```bash
# Docker
docker compose exec homematic-bridge python app/set_password.py

# Manuell
python app/set_password.py
```

Das Script fragt interaktiv nach dem neuen Passwort und schreibt den Hash in `internal_config.yaml`:

```yaml
web_password_hash: pbkdf2:sha256:260000$abc123...
```

Danach Bridge neu starten:
```bash
docker compose restart
```

> **Hinweis:** Da `internal_config.yaml` als Volume gemountet ist, ist kein `docker compose build` nötig.

### SSL/TLS – HCU-Zertifikat einrichten

Die HCU verwendet ein **selbstsigniertes Zertifikat**. Standardmäßig ist die Zertifikatsprüfung deaktiviert (`ssl_verify: false`), was für ein lokales Heimnetz ausreichend ist.

Für eine verifizierte Verbindung (stellt sicher, dass es wirklich die eigene HCU ist):

**Schritt 1: Zertifikat von der HCU herunterladen**

```bash
# Auf dem Host/Server (nicht im Container)
openssl s_client -connect <HCU-IP>:9001 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > config/hcu.crt

# Prüfen
openssl x509 -in config/hcu.crt -noout -subject -dates
```

**Schritt 2: `config/config.yaml` anpassen**

```yaml
ssl_verify: true
ssl_cert_path: config/hcu.crt
```

**Schritt 3: Bridge neu starten**

```bash
docker compose restart
```

> **Hinweis:** Das HCU-Zertifikat ist auf den internen CN ausgestellt (z. B. `HCU1-3014F711A00045E26991E461`), nicht auf den mDNS-Hostname. Die Bridge deaktiviert daher die Hostname-Prüfung (`check_hostname: False`), prüft aber weiterhin ob das Zertifikat von dieser HCU stammt (`CERT_REQUIRED`).

> `*.crt`-Dateien sind in `.gitignore` eingetragen und werden nicht ins Repository eingecheckt.

---

## HTTP-Endpoints

Alle schreibenden Endpoints und externe GET-Aufrufe erfordern den Header `X-API-Key: <key>`.

### `POST /hmipSwitch`

```json
{ "device": "<DEVICE_ID>", "on": true, "channelIndex": 0 }
```

### `POST /hmipDimmer`

```json
{ "device": "<DEVICE_ID>", "dimLevel": 75, "channelIndex": 1 }
```

- `dimLevel`: Helligkeit in Prozent (`0`–`100`). `0` schaltet aus.

### `POST /hmipRGB`

```json
{ "device": "<DEVICE_ID>", "rgb": "R=50%,G=30%,B=100%", "channelIndex": 1 }
```

- `rgb`: Loxone-Format `R=X%,G=Y%,B=Z%` (je 0–100%)
- `R=0%,G=0%,B=0%` schaltet aus
- Die Bridge konvertiert RGB → HSV und sendet `setHueSaturationDimLevel` + `setSwitchState`

### `GET /hmipSwitch`

```
GET /hmipSwitch?device=<ID>&on=true&channelIndex=0
```

- Von **localhost**: kein API-Key nötig
- Von **extern**: `X-API-Key`-Header erforderlich

### `GET /hmipState`

Liefert den aktuellen Channel-Zustand aus dem Snapshot.

```
GET /hmipState?device=<ID>&channelIndex=1
Header: X-API-Key: <key>
```

Beispiel-Antwort (Switch):
```json
{ "on": true, "functionalChannelType": "SWITCH_CHANNEL" }
```

### `GET /healthz`

```json
{
  "ws_connected": true,
  "snapshot_age_ms": 1234,
  "devices_count": 42,
  "pending_requests": 0,
  "status": "ok"
}
```

`status`: `ok` | `degraded` | `unhealthy`

---

## Weboberfläche

Alle Seiten sind durch den Session-Login geschützt.

| URL | Beschreibung |
|-----|-------------|
| `/` | **Dashboard** – Wetter, Alarm, Systemstatus, Duty Cycle, Heizungsübersicht, Gerätewarnungen |
| `/heating` | **Heizung** – Alle Heizgruppen mit Ist/Soll-Temperatur, Ventilposition, Modus |
| `/devices/html` | **Geräteübersicht** – Alle Geräte mit Raum, Typ, Links zur Detailseite |
| `/devices/status` | **Gerätestatus** – LowBat, Erreichbarkeit, RSSI, DutyCycle aller Geräte |
| `/devices/<ID>` | **Gerätedetail** – Basisdaten, alle `functionalChannels`, Rohdaten |
| `/healthz` | **Health** – Verbindungsstatus, Snapshot-Alter, offene Requests |

---

## Loxone-Integration

### UDP-Push (HmIP → Loxone)

Bei jeder Gerätezustandsänderung sendet die Bridge den neuen Zustand **automatisch per UDP** an den Loxone Miniserver.

#### 1. Bridge konfigurieren

```yaml
# config/config.yaml
loxone:
  miniserver_ip: 192.168.1.100
  udp_port: 7777
```

#### 2. Variablenformat

```
hmip_<DEVICE_ID>_ch<N>_<feld>@<wert>
```

Beispiel (Schalt-Aktor mit Energiemessung):
```
hmip_3014F711A000085F299F3C0D_ch1_on@1
hmip_3014F711A000085F299F3C0D_ch1_currentPowerConsumption@0.0
hmip_3014F711A000085F299F3C0D_ch1_energyCounter@2.5251
```

Verfügbare Felder:

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `_on` | 0 / 1 | Ein-/Ausschaltzustand |
| `_dimLevel` | 0.0 – 1.0 | Helligkeit (Dimmer) |
| `_hue` | 0 – 360 | Farbton (RGBW) |
| `_saturationLevel` | 0.0 – 1.0 | Farbsättigung (RGBW) |
| `_actualTemperature` | °C | Gemessene Temperatur |
| `_humidity` | % | Luftfeuchtigkeit |
| `_illumination` | lx | Helligkeit (Sensor) |
| `_windSpeed` | km/h | Windgeschwindigkeit |
| `_shutterLevel` | 0.0 – 1.0 | Rollladenposition |
| `_currentPowerConsumption` | W | Aktuelle Leistung |
| `_energyCounter` | kWh | Energiezähler |

Device-ID und Channel-Index findet man in der Weboberfläche unter `/devices/html`.

#### 3. Loxone Config – Virtuellen Eingang anlegen

**Virtuellen Eingang** (Peripherie → Virtuell → Virtuellen Eingang):

| Feld | Wert |
|------|------|
| UDP-Port | `7777` |

**Virtuellen Eingangsbefehl** pro Variable:

| Feld | Wert |
|------|------|
| Befehlskennung | `hmip_3014F711A000085F299F3C0D_ch1_on@\v` |

> `\v` ist der Loxone-Platzhalter für den Zahlenwert. Alles vor `@` muss exakt übereinstimmen.

---

### HTTP-Steuerung (Loxone → HmIP)

**Virtuellen HTTP-Ausgang** anlegen (Peripherie → Virtuell):

| Feld | Wert |
|------|------|
| Adresse | `http://<bridge-ip>:8080` |

#### Switch

| Feld | Wert |
|------|------|
| Befehl bei EIN | `/hmipSwitch` |
| Body bei EIN | `{"device":"<ID>","on":true,"channelIndex":1}` |
| Header | `X-API-Key: <key>`  `Content-Type: application/json` |
| Befehl bei AUS | `/hmipSwitch` |
| Body bei AUS | `{"device":"<ID>","on":false,"channelIndex":1}` |

#### Dimmer (Lichtsteuerungsbaustein)

| Feld | Wert |
|------|------|
| Befehl bei EIN | `/hmipDimmer` |
| Body bei EIN | `{"device":"<ID>","dimLevel":<v>,"channelIndex":1}` |
| Body bei AUS | `{"device":"<ID>","dimLevel":0,"channelIndex":1}` |

`<v>` = Ausgabewert des Lichtsteuerungsbausteins (0–100%).

#### RGB-Controller (Lichtsteuerungsbaustein im RGB-Modus)

| Feld | Wert |
|------|------|
| Befehl bei EIN | `/hmipRGB` |
| Body bei EIN | `{"device":"<ID>","rgb":"R=<vR>%,G=<vG>%,B=<vB>%","channelIndex":1}` |
| Body bei AUS | `{"device":"<ID>","rgb":"R=0%,G=0%,B=0%","channelIndex":1}` |

`<vR>`, `<vG>`, `<vB>` = RGB-Ausgänge des Lichtsteuerungsbausteins.

#### channelIndex ermitteln

In der Gerätedetailseite (`/devices/<ID>`):
- Channel `0` = Basiskanal (Gerätestatus)
- Channel `1` = erster Funktionskanal (z. B. `SWITCH_CHANNEL`, `DIMMER_CHANNEL`)

---

## Update

### Mit Docker

```bash
git pull
docker compose up -d --build
docker compose logs -f
```

`config/`, `data/` und `logs/` bleiben durch die Volumes erhalten.

> Der Homematic-Token und der API-Key müssen **nicht** neu generiert werden.

### Manuell

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Troubleshooting

| Problem | Lösung |
|---------|--------|
| `Kein homematic_token` | `python app/request_token.py` ausführen |
| `devices_count: 0` in `/healthz` | Bridge neu starten triggert `getSystemState` |
| WebSocket verbindet nicht | Hostname/IP der HCU prüfen; bei SSL-Fehlern `ssl_verify: false` |
| `401 Unauthorized` | `X-API-Key`-Header prüfen; Key aus `data/api_key.txt` |
| Login schlägt fehl | Passwort = API-Key (oder gesetztes Passwort via `set_password.py`) |
| `service "bridge" is not running` | Servicename prüfen mit `docker compose ps` |
| SSL: Hostname mismatch | Erwartet – wird intern ignoriert, Zertifikat wird trotzdem geprüft |
| Docker logs leer | `ENV PYTHONUNBUFFERED=1` im Dockerfile sicherstellen |

## Entwicklung & Logs

- Log-Datei: `logs/bridge.log` (Rotation: täglich, 7 Backups)
- Log-Level in `internal_config.yaml` anpassen: `debug` / `info` / `warning` / `error`
- Log-Level auch zur Laufzeit über das HCU-Plugin-Zahnrad-Menü änderbar

## Lizenz

Dieser Code steht unter der **Apache License 2.0**. Siehe [`LICENSE`](./LICENSE) und [`NOTICE`](./NOTICE).

`THIRD-PARTY_NOTICES.md` enthält Informationen zu den verwendeten Open-Source-Bibliotheken.

---

> Nicht offiziell mit eQ-3/Homematic verbunden. Wenn dir die Bridge hilft, freuen wir uns über einen ⭐ auf GitHub!
