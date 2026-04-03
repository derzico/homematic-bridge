# homematic-bridge

> Python-Bridge zwischen **Homematic IP (HCU)** und externen Smarthome-Systemen – gebaut weil kein System von Haus aus mit dem anderen redet.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](./docker-compose.yml)

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
| **Loxone UDP-Push** | Jede HmIP-Zustandsänderung als UDP-Paket an den Miniserver |
| **HTTP-API** | Schalten, Dimmen, RGB, Rollläden, Thermostat, Alarm, Bewässerung |
| **Shelly-Integration** | Auto-Scan (Gen1 + Gen2), Steuerung, Web-UI-Proxy |
| **Web-Interface** | Dashboard, Heizung, Geräteübersicht, Konfigurationseditor |
| **API-Key-Auth** | Optionale Absicherung aller Endpunkte |
| **Docker-Deployment** | Ein Befehl, sofort einsatzbereit |

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

# Loxone UDP-Push (optional)
loxone:
  miniserver_ip:   # z.B. 192.168.1.100 (leer = deaktiviert)
  udp_port: 7777

# Shelly-Scanner (optional)
shelly:
  enabled: false
  subnet: "192.168.1.0/24"

# API-Absicherung
api_key:
require_api_key: true
```

---

## Dokumentation

Vollständige Dokumentation im **[GitHub Wiki](https://github.com/derzico/homematic-bridge/wiki)**:

| Seite | Inhalt |
|---|---|
| [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-und-Docker) | Setup, Token, Volumes, Update, Troubleshooting |
| [Konfiguration](https://github.com/derzico/homematic-bridge/wiki/Konfiguration) | config.yaml, internal_config.yaml, SSL/TLS |
| [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP) | WebSocket, Gerätetypen, Alarm, Thermostat |
| [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly) | Scan, Steuerung, Web-UI-Proxy |
| [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP) | UDP-Push-Format, Loxone-Konfiguration |
| [API-Referenz](https://github.com/derzico/homematic-bridge/wiki/API-Referenz) | Alle Endpunkte mit Beispielen |
| [Web-Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface) | Dashboard, Seiten, Features |

Weitere Infos: **[schnellniclas.de/homematic-bridge](https://schnellniclas.de/homematic-bridge)**

---

## Lizenz

Apache-2.0 – siehe [LICENSE](./LICENSE)

> Nicht offiziell von eQ-3/Homematic IP. Alle Markennamen gehören ihren jeweiligen Inhabern.
