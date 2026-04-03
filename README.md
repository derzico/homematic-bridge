# homematic-bridge

Python-Bridge zwischen **Homematic IP (HCU)** und externen Smarthome-Systemen (Shelly, Loxone).

- Echtzeit-Gerätestatus via WebSocket + Delta-Merge
- HTTP-API zur Steuerung (Schalten, Dimmen, RGB, Rollläden, Thermostat, Alarm, Bewässerung)
- Shelly-Integration: Auto-Scan (Gen1 + Gen2), Steuerung, Web-UI-Proxy
- Loxone UDP-Push bei jedem HmIP-Event
- Web-Interface: Dashboard, Heizung, Geräteübersicht, Konfigurationseditor
- Docker-Deployment

> Nicht offiziell von eQ-3/Homematic. Marken gehören ihren jeweiligen Inhabern.

## Schnellstart

```bash
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge
cp config/config_sample.yaml config/config.yaml
# config/config.yaml anpassen (HCU-Hostname, Token)
docker compose up -d --build
```

Web-Interface: `http://<host>:8080`

## Dokumentation

Die vollständige Dokumentation befindet sich im **[GitHub Wiki](https://github.com/derzico/homematic-bridge/wiki)**:

| Seite | Inhalt |
|---|---|
| [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-und-Docker) | Setup, Token, Volumes, Update, Troubleshooting |
| [Konfiguration](https://github.com/derzico/homematic-bridge/wiki/Konfiguration) | config.yaml, internal_config.yaml, SSL/TLS |
| [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP) | WebSocket, Gerätetypen, Alarm, Thermostat |
| [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly) | Scan, Steuerung, Web-UI-Proxy |
| [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP) | UDP-Push-Format, Loxone-Konfiguration |
| [API-Referenz](https://github.com/derzico/homematic-bridge/wiki/API-Referenz) | Alle Endpunkte mit Beispielen |
| [Web-Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface) | Dashboard, Seiten, Features |

## Lizenz

Apache-2.0 – siehe [LICENSE](./LICENSE)
