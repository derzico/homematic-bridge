# SPDX-License-Identifier: Apache-2.0
# app/shelly.py – Shelly Netzwerk-Scanner und Steuerung

import ipaddress
import json
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("bridge-ws")

_SHELLY_CACHE = "data/shelly_devices.json"
_WORKERS = 64

# ── Scan-Status (thread-safe) ─────────────────────────────────────────────────

_scan_lock = threading.Lock()
_scan_running = False
_scan_started: Optional[float] = None
_scan_error: Optional[str] = None


def scan_status() -> Dict[str, Any]:
    with _scan_lock:
        return {
            "running": _scan_running,
            "started": _scan_started,
            "error": _scan_error,
        }


# ── HTTP-Helpers ──────────────────────────────────────────────────────────────

def _get(ip: str, path: str, timeout: float) -> Optional[Dict]:
    try:
        r = requests.get(f"http://{ip}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ── Geräteerkennung ───────────────────────────────────────────────────────────

def _detect(ip: str, timeout: float) -> Optional[Dict[str, Any]]:
    """Erkennt ob eine IP ein Shelly ist. Gibt {'gen': 1|2, 'info': {...}} zurück."""
    # Gen2/Gen3 zuerst
    info = _get(ip, "/rpc/Shelly.GetDeviceInfo", timeout)
    if isinstance(info, dict) and "app" in info:
        return {"gen": 2, "info": info}
    # Gen1
    info = _get(ip, "/shelly", timeout)
    if isinstance(info, dict) and ("type" in info or "model" in info):
        return {"gen": 1, "info": info}
    return None


def _get_status_gen1(ip: str, timeout: float) -> Dict:
    data = _get(ip, "/status", timeout) or {}
    relays = data.get("relays", [])
    meters = data.get("meters", [])
    channels = {}
    for i, rel in enumerate(relays):
        channels[str(i)] = {
            "on": rel.get("ison", False),
            "power_w": meters[i].get("power") if i < len(meters) else None,
        }
    return {"channels": channels, "rssi": data.get("wifi_sta", {}).get("rssi")}


def _get_status_gen2(ip: str, num_channels: int, timeout: float) -> Dict:
    channels = {}
    for i in range(num_channels):
        sw = _get(ip, f"/rpc/Switch.GetStatus?id={i}", timeout) or {}
        if sw:
            channels[str(i)] = {
                "on": sw.get("output", False),
                "power_w": sw.get("apower"),
                "voltage": sw.get("voltage"),
                "current": sw.get("current"),
            }
    wifi = _get(ip, "/rpc/Wifi.GetStatus", timeout) or {}
    return {"channels": channels, "rssi": wifi.get("rssi")}


def _build_device(ip: str, gen: int, info: Dict, timeout: float) -> Dict[str, Any]:
    if gen == 2:
        num_ch = info.get("num_outputs") or 1
        status = _get_status_gen2(ip, num_ch, timeout)
        # Bei Gen2: Label aus GetConfig holen
        cfg = _get(ip, "/rpc/Sys.GetConfig", timeout) or {}
        name = cfg.get("device", {}).get("name") or info.get("app") or info.get("id", "Shelly")
        return {
            "ip": ip, "gen": gen,
            "id": info.get("id", ""),
            "name": name,
            "model": info.get("app", info.get("model", "")),
            "mac": info.get("mac", ""),
            "fw": info.get("ver", ""),
            "rssi": status["rssi"],
            "channels": status["channels"],
            "last_seen": int(time.time()),
        }
    else:
        settings = _get(ip, "/settings", timeout) or {}
        name = settings.get("name") or info.get("type", "Shelly")
        status = _get_status_gen1(ip, timeout)
        mac = info.get("mac", "")
        model = info.get("type", info.get("model", ""))
        return {
            "ip": ip, "gen": gen,
            "id": f"{model}-{mac[-6:].lower()}" if mac else "",
            "name": name,
            "model": model,
            "mac": mac,
            "fw": info.get("fw", ""),
            "rssi": status["rssi"],
            "channels": status["channels"],
            "last_seen": int(time.time()),
        }


def _probe_ip(ip: str, timeout: float) -> Optional[Dict[str, Any]]:
    detected = _detect(ip, timeout)
    if not detected:
        return None
    try:
        return _build_device(ip, detected["gen"], detected["info"], timeout)
    except Exception as e:
        log.warning(f"Shelly probe fehlgeschlagen für {ip}: {e}")
        return None


# ── Scan ──────────────────────────────────────────────────────────────────────

def _run_scan(subnet: str, timeout_sec: float, include_mdns: bool) -> None:
    global _scan_running, _scan_error
    try:
        found: List[Dict] = []
        seen_ips: set = set()

        # mDNS-Scan (optional)
        if include_mdns:
            mdns_results = _mdns_scan(timeout_sec=3.0)
            for dev in mdns_results:
                found.append(dev)
                seen_ips.add(dev["ip"])

        # Netzwerk-Sweep
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as e:
            with _scan_lock:
                _scan_error = f"Ungültiges Subnet '{subnet}': {e}"
                _scan_running = False
            return

        hosts = [str(h) for h in network.hosts() if str(h) not in seen_ips]
        log.info(f"Shelly-Sweep: {len(hosts)} IPs in {subnet} …")

        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            futures = {ex.submit(_probe_ip, ip, timeout_sec): ip for ip in hosts}
            for fut in as_completed(futures):
                dev = fut.result()
                if dev:
                    found.append(dev)
                    log.info(f"Shelly: {dev['name']} ({dev['ip']}, Gen{dev['gen']})")

        found.sort(key=lambda d: d["ip"])
        save_cache(found)
        log.info(f"Shelly-Scan fertig: {len(found)} Geräte")

    except Exception as e:
        with _scan_lock:
            _scan_error = str(e)
        log.exception("Shelly-Scan Fehler")
    finally:
        with _scan_lock:
            _scan_running = False


def start_scan(subnet: str, timeout_sec: float = 1.5, include_mdns: bool = True) -> bool:
    """Startet Scan im Hintergrund. Gibt False zurück wenn Scan bereits läuft."""
    global _scan_running, _scan_started, _scan_error
    with _scan_lock:
        if _scan_running:
            return False
        _scan_running = True
        _scan_started = time.time()
        _scan_error = None
    t = threading.Thread(
        target=_run_scan, args=(subnet, timeout_sec, include_mdns), daemon=True
    )
    t.start()
    return True


# ── mDNS ─────────────────────────────────────────────────────────────────────

def _mdns_scan(timeout_sec: float = 3.0) -> List[Dict[str, Any]]:
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore
    except ImportError:
        log.debug("zeroconf nicht installiert – mDNS-Scan übersprungen")
        return []

    found_ips: List[str] = []

    class _Handler:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                found_ips.append(ip)

        def remove_service(self, *_): pass
        def update_service(self, *_): pass

    zc = Zeroconf()
    # Shelly registriert sich unter beiden Service-Typen
    ServiceBrowser(zc, "_http._tcp.local.", _Handler())
    ServiceBrowser(zc, "_shelly._tcp.local.", _Handler())
    time.sleep(timeout_sec)
    zc.close()

    results = []
    for ip in set(found_ips):
        dev = _probe_ip(ip, timeout=2.0)
        if dev:
            results.append(dev)
    return results


# ── Cache ─────────────────────────────────────────────────────────────────────

def load_cached() -> List[Dict[str, Any]]:
    try:
        with open(_SHELLY_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_cache(devices: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(_SHELLY_CACHE), exist_ok=True)
    with open(_SHELLY_CACHE, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)


def refresh_device(ip: str, gen: int) -> Optional[Dict]:
    """Aktualisiert Status eines einzelnen Geräts und speichert in Cache."""
    num_ch = 1  # Standard; wird aus Cache übernommen wenn möglich
    cached = {d["ip"]: d for d in load_cached()}
    if ip in cached:
        num_ch = len(cached[ip].get("channels", {1: None}))
    if gen == 2:
        status = _get_status_gen2(ip, num_ch, timeout=3.0)
    else:
        status = _get_status_gen1(ip, timeout=3.0)
    if ip in cached:
        cached[ip]["channels"] = status["channels"]
        cached[ip]["rssi"] = status["rssi"]
        cached[ip]["last_seen"] = int(time.time())
        save_cache(list(cached.values()))
        return cached[ip]
    return None


# ── Steuerung ─────────────────────────────────────────────────────────────────

def set_relay(ip: str, gen: int, channel: int = 0, on: bool = True) -> bool:
    """Schaltet Relay. Gibt True bei Erfolg zurück."""
    try:
        if gen == 2:
            r = requests.post(
                f"http://{ip}/rpc/Switch.Set",
                json={"id": channel, "on": on},
                timeout=5,
            )
            return r.status_code == 200
        else:
            r = requests.post(
                f"http://{ip}/relay/{channel}",
                data={"turn": "on" if on else "off"},
                timeout=5,
            )
            return r.status_code == 200
    except Exception as e:
        log.error(f"Shelly Relay-Steuerung fehlgeschlagen {ip}:{channel}: {e}")
        return False
