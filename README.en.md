# homematic-bridge

[Deutsch](./README.md) | [English](./README.en.md)

> Python bridge between **Homematic IP (HCU)** and external smart-home systems, built because these systems do not talk to each other out of the box.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](./docker-compose.yml)

---

## What is this?

`homematic-bridge` is a small Python server that acts as a bridge between Homematic IP, Loxone, and Shelly. It connects to the **Homematic IP Control Unit (HCU)** via WebSocket and receives device state changes in real time.

```text
HCU (WebSocket) --> homematic-bridge --> Loxone Miniserver (UDP)
                          |
                          +--> HTTP API (control)
                          +--> Shelly integration
                          +--> Web interface :8080
```

---

## Features

| Feature | Description |
|---|---|
| **Real-time events** | WebSocket connection to the HCU with snapshot and delta merge |
| **Loxone UDP push** | Sends every HmIP state change as a UDP packet to the Miniserver |
| **HTTP API** | Switches, dimmers, RGB lights, shutters, thermostats, alarms, irrigation |
| **Shelly integration** | Auto scan for Gen1/Gen2/Gen3 devices, control, and safe device Web UI links |
| **Web interface** | Dashboard, heating view, device overview, configuration editor |
| **API key auth** | Optional protection for API endpoints |
| **Docker deployment** | Production-oriented setup with Docker Compose |

---

## Quick Start

```bash
git clone https://github.com/derzico/homematic-bridge.git
cd homematic-bridge
cp config/config_sample.yaml config/config.yaml
# edit config/config.yaml (HCU host, token, optional integrations)
docker compose up -d --build
```

Web interface: `http://<host>:8080`

---

## Configuration

All user settings live in `config/config.yaml` using `config/config_sample.yaml` as a template:

```yaml
homematic_hcu: hcu1-E461.local
homematic_token:
ssl_cert_path:
ssl_verify: false
plugin_id: de.schnellniclas.homematic-bridge

# Optional Loxone UDP push
loxone:
  miniserver_ip: # e.g. 192.168.1.100, empty disables UDP push
  udp_port: 7777

# Optional Shelly network scanner
shelly:
  enabled: false
  subnet: "192.168.1.0/24"
  timeout_sec: 1.5
  username: admin
  password:
  scan_on_startup: false
  scan_interval_hours: 0

# HTTP API authentication
api_key:
require_api_key: true
api_key_file: data/api_key.txt
```

---

## Documentation

Full documentation is available in the **[GitHub Wiki](https://github.com/derzico/homematic-bridge/wiki)** in German and English:

| Deutsch | English | Contents |
|---|---|---|
| [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-und-Docker) | [Installation & Docker](https://github.com/derzico/homematic-bridge/wiki/Installation-and-Docker) | Setup, token, volumes, updates, troubleshooting |
| [Konfiguration](https://github.com/derzico/homematic-bridge/wiki/Konfiguration) | [Configuration](https://github.com/derzico/homematic-bridge/wiki/Configuration) | config.yaml, internal_config.yaml, SSL/TLS |
| [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP) | [Homematic IP](https://github.com/derzico/homematic-bridge/wiki/Homematic-IP-en) | WebSocket, device types, alarm, thermostat |
| [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly) | [Shelly](https://github.com/derzico/homematic-bridge/wiki/Shelly-en) | Scan, control, device Web UI |
| [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP) | [Loxone UDP](https://github.com/derzico/homematic-bridge/wiki/Loxone-UDP-en) | UDP push format, Loxone configuration |
| [API-Referenz](https://github.com/derzico/homematic-bridge/wiki/API-Referenz) | [API Reference](https://github.com/derzico/homematic-bridge/wiki/API-Reference) | All endpoints with examples |
| [Web-Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface) | [Web Interface](https://github.com/derzico/homematic-bridge/wiki/Web-Interface-en) | Dashboard, pages, features |

More information: **[schnellniclas.de/homematic-bridge](https://schnellniclas.de/homematic-bridge)**

---

## License

Apache-2.0, see [LICENSE](./LICENSE).

> This project is not officially affiliated with eQ-3 or Homematic IP. All trademarks belong to their respective owners.
