# homematic-bridge

[Deutsch](./README.md) | [English](./README.en.md)

> Python-Bridge zwischen **Homematic IP (HCU)** und externen Smarthome-Systemen – gebaut weil kein System von Haus aus mit dem anderen redet.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](./docker-compose.yml)
[![CI](https://github.com/derzico/homematic-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/derzico/homematic-bridge/actions/workflows/ci.yml)

---

## Was ist das?

Die `homematic-bridge` ist ein kleiner Python-Server, der als Vermittler zwischen Homematic IP, Loxone und Shelly fungiert. Er verbindet sich per WebSocket direkt mit der **Homematic IP Control Unit (HCU)** und empfängt in Echtzeit alle Gerätezustände.

```
HCU (WebSocket) ──► homematic-bridge ──► Loxone Miniserver (UDP)
                          │
                          ├──► HTTP-API (Steuerung)
                          ├──► Shelly-Integration
                          └──► Web-Interface :8080
```

---

## Features

| Feature | Beschreibung |
|---|---|
| **Echtzeit-Events** | WebSocket-Verbindung zur HCU mit Delta-Merge |
| **Loxone UDP-Push** | Geräte-, Home- und Gruppenereignisse; Vollzustand beim Verbinden, periodisch und auf Anfrage |
| **HTTP-API** | Schalten, Dimmen, RGB, Rollladenposition/Stop, Heizgruppen-Sollwert/Modus/Boost, Sirenengruppe, Bewässerung |
| **Shelly-Integration** | Auto-Scan (Gen1 + Gen2), Steuerung, sicherer Link zur Geräte-WebUI |
| **Web-Interface** | Dashboard, echte Heiz-/Licht-/Rollladensteuerung, Sicherheitsstatus, Diagnose, Konfiguration |
| **Lokale Energiehistorie** | Optional: vorhandene erreichbare Shelly-Zähler, Leistung und Bezugs-/Einspeisesummen; ohne Cloud |
| **API-Key-Auth** | Optionale Absicherung aller Endpunkte |
| **Docker-Deployment** | Ein Befehl, sofort einsatzbereit |

Steuerbefehle gelten erst nach einem erfolgreichen HCU-ACK als angenommen. Bei
Verbindungsfehlern, Ablehnung oder Timeout liefert die API `503`, `502` bzw. `504`.
`/healthz` antwortet bei getrennter Verbindung, fehlendem/ungültigem Vollsnapshot
oder einem ausgefallenen Adapter mit `503`. Ein alter Änderungszeitpunkt allein
ist kein Ausfall. Nach 30 Sekunden ohne Nachricht wird eine rein lesende HCU-Abfrage
mit Antwortfrist gesendet; bleibt die Antwort aus, erfolgt ein Reconnect.

Externe Shelly-Steuerung: `POST /api/shelly/<ip>/relay/<channel>` mit
`X-API-Key` und JSON `{"on": true}`. IP und Kanal müssen im Geräte-Cache stehen.
Der Browser-Endpunkt `/shelly/<ip>/relay/<channel>` benötigt weiterhin Session
und CSRF-Token. Gen2+ verwendet Digest-Authentifizierung und ermittelt die
tatsächlichen Switch-Komponenten bei jedem Statusabruf.

Änderungen an HCU-Host, Token, Plugin-ID oder TLS-Einstellungen bauen die
Verbindung kontrolliert neu auf. Docker mountet das Konfigurationsverzeichnis,
damit Konfigurationsdateien atomar ersetzt werden können; das Zertifikat bleibt
separat schreibgeschützt. Beim Update vorhandene Konfiguration und Daten behalten.

In der Shelly-Übersicht (`/shelly`) können einzelne Geräte nach Bestätigung aus
dem Cache gelöscht werden. **Geräte vollständig neu einlesen** ersetzt die
Liste nach einem erfolgreichen Scan durch die aktuell gefundenen Geräte;
auch vorübergehend offline Geräte werden dabei entfernt. **Netzwerk scannen**
behält bekannte Offline-Geräte weiterhin bei. Ein noch erreichbares, gelöschtes
Gerät wird bei einem späteren Scan wieder erkannt.

Neu in **1.19.0**: `/controls`, `/security`, `/energy` und `/diagnostics`.
Die Energieaufzeichnung ist standardmäßig aus und kann auf `/energy` aktiviert
werden. Lokale Auswahl: `config/energy.yaml`; Historie: `data/energy.sqlite3`.
Erste Messungen sind nur Ausgangswerte, keine nachträglich rekonstruierte Historie.
Lichtfunktionen folgen den vom HCU-Kanal gemeldeten Fähigkeiten, nicht dem
Gerätenamen oder `channelActive`. Scharfschaltung folgt den Sicherheitszonen,
nicht dem Aktivierungsflag der Sicherheitslösung.
Payloads, Einheiten, Grenzen und Updatehinweise: [Version 1.19.0](docs/releases/1.19.0.md).

Geschützte, geprüfte Code-/Config-/Datensicherungen ohne Dienstneustart:
[Backup-Werkzeug und Grenzen](docs/operations/backups.md). Eine Sicherung im
gleichen Container ersetzt kein unabhängiges Proxmox-/Offsite-Backup.

---

## Schnellstart

```bash
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge
cp config/config_sample.yaml config/config.yaml
# config/config.yaml anpassen (HCU-Hostname, Token)
docker compose up -d --build
```

Web-Interface: `http://<host>:8080`

---

## Konfiguration

Alle Einstellungen in `config/config.yaml` (Vorlage: `config/config_sample.yaml`):

```yaml
homematic_hcu: hcu1-E461.local   # Hostname oder IP der HCU
homematic_token:                  # API-Token (über /api/token abrufen)
plugin_id: de.schnellniclas.homematic-bridge

# Loxone UDP-Push (optional)
loxone:
  miniserver_ip:   # z.B. 192.168.1.100 (leer = deaktiviert)
  udp_port: 7777
  resync_interval_seconds: 300 # 0 = keine periodische Vollsynchronisation

energy:
  enabled: false
  interval_seconds: 60
  retention_days: 90
  timezone: Europe/Berlin

# Shelly-Scanner (optional)
shelly:
  enabled: false
  subnet: "192.168.1.0/24"

# API-Absicherung
api_key:
require_api_key: true
```

### Sirene per HTTP/Loxone schalten

```http
POST /hmipAlarm
X-API-Key: <api-key>
Content-Type: application/json

{"on": true}
```

`{"on": false}` schaltet die Sirene wieder aus. Die Bridge ermittelt die
`ALARM_SWITCHING`-Gruppe automatisch; alternativ kann sie mit
`{"group": "<gruppen-id>", "on": true}` explizit angegeben werden. Signalart
und Laufzeit werden in der HCU-Gruppenkonfiguration festgelegt.

---

## Dokumentation

Vollständige Dokumentation im **[GitHub Wiki](https://github.com/derzico/homematic-bridge/wiki)**. Die wichtigsten Seiten gibt es auf Deutsch und Englisch:

| Deutsch | English | Inhalt |
|---|---|---|
| [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-und-Docker) | [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-and-Docker) | Setup, Token, Volumes, Update, Troubleshooting |
| [Konfiguration](https://github.com/derzico/homematic-bridge/wiki/Konfiguration) | [Configuration](https://github.com/derzico/homematic-bridge/wiki/Configuration) | config.yaml, internal_config.yaml, SSL/TLS |
| [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP) | [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP-en) | WebSocket, Gerätetypen, Alarm, Thermostat |
| [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly) | [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly-en) | Scan, Steuerung, Geräte-WebUI |
| [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP) | [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP-en) | UDP-Push-Format, Loxone-Konfiguration |
| [API-Referenz](https://github.com/derzico/homematic-bridge/wiki/API-Referenz) | [API Reference](https://github.com/derzico/homematic-bridge/wiki/API-Reference) | Alle Endpunkte mit Beispielen |
| [Web-Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface) | [Web Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface-en) | Dashboard, Seiten, Features |

Weitere Infos: **[schnellniclas.de/homematic-bridge](https://schnellniclas.de/homematic-bridge)**

---

## Lizenz

Apache-2.0 – siehe [LICENSE](./LICENSE)

> Nicht offiziell von eQ-3/Homematic IP. Alle Markennamen gehören ihren jeweiligen Inhabern.
