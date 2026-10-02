# homematic-bridge

[Deutsch](./README.md) | [English](./README.en.md)

> Python bridge between **Homematic IP (HCU)** and external smart-home systems, built because these systems do not talk to each other out of the box.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](./docker-compose.yml)
[![CI](https://github.com/derzico/homematic-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/derzico/homematic-bridge/actions/workflows/ci.yml)

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
| **Loxone UDP push** | Device, home and group events; initial, periodic and manually requested full state |
| **HTTP API** | Switching, dimming, RGB, shutter position/stop, heating-group setpoint/mode/boost, siren group and irrigation |
| **Shelly integration** | Auto scan for Gen1/Gen2/Gen3 devices, control, and safe device Web UI links |
| **Web interface** | Dashboard, working heating/light/shutter controls, security status, diagnostics and configuration |
| **Local energy history** | Optional: existing online Shelly meters, power and import/export totals, no cloud |
| **API key auth** | Optional protection for API endpoints |
| **Docker deployment** | Production-oriented setup with Docker Compose |

Control commands are accepted only after a successful HCU acknowledgement. The
API returns `503`, `502`, or `504` for connection failures, rejected commands,
or timeouts. `/healthz` returns `503` for disconnected connections, missing/invalid
full snapshots or unhealthy adapters. An old state-change timestamp alone is not
a failure. After 30 seconds without messages, a read-only HCU request checks
end-to-end liveness with a response deadline; a missing response causes reconnect.

External Shelly control: `POST /api/shelly/<ip>/relay/<channel>` with `X-API-Key`
and JSON `{"on": true}`. The device and channel must be present in the cache.
The browser endpoint still requires a session and CSRF token. Gen2+ uses Digest
authentication and discovers actual switch component IDs on every refresh.

Changes to HCU host, token, plugin ID or TLS settings rebuild the connection.
Docker mounts the configuration directory for atomic file replacement, while
the certificate remains separately read-only. Preserve existing config/data on update.

The Shelly page (`/shelly`) allows individual devices to be deleted from the
cache after confirmation. **Rebuild device list** replaces the list with the
devices found by a successful scan; temporarily offline devices are removed
as well. **Scan network** continues to retain known offline devices. A deleted
device that is still reachable will be discovered again by a future scan.

New in **1.19.0**: `/controls`, `/security`, `/energy` and `/diagnostics`.
Energy collection is off by default; enable it explicitly on `/energy`.
The local override lives in `config/energy.yaml`, history in `data/energy.sqlite3`.
The first reading establishes a baseline, not reconstructed past consumption.
Light controls follow declared channel capabilities, not model names or
`channelActive`. Arming follows security zones, not the security solution flag.
Payloads, units and update notes: [1.19.0 release documentation (German)](docs/releases/1.19.0.md).

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

### Triggering the siren via HTTP/Loxone

```http
POST /hmipAlarm
X-API-Key: <api-key>
Content-Type: application/json

{"on": true}
```

Use `{"on": false}` to stop the siren. The bridge automatically resolves the
HCU's `ALARM_SWITCHING` group; alternatively provide it explicitly with
`{"group": "<group-id>", "on": true}`. Signal pattern and duration are defined
by the HCU group configuration.

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
