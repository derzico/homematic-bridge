# SPDX-License-Identifier: Apache-2.0
"""Shelly-Relaiszustände an Loxone melden (auch bei Schalten am Wandschalter).

Fragt die Relais aller Shellys aus dem Cache zyklisch ab (shelly.state_poll_seconds,
Standard 10 s, 0 = aus) und sendet per UDP:
  shelly_<ID>_relay<N>_on   0/1  – bei jeder Änderung sofort
  shelly_<ID>_available     0/1  – Erreichbarkeit
Alle resync_interval_seconds wird der komplette Stand erneut gesendet. Nur lesende
HTTP-Abfragen an die Shellys; nie Schaltbefehle.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import app.adapters.shelly_adapter as shelly_mod
import app.loxone_udp as loxone_udp
import app.state as state

log = logging.getLogger("bridge-ws")

DEFAULT_POLL_SECONDS = 10


def relay_variable(device_id: str, channel: Any) -> str:
    return f"shelly_{device_id}_relay{channel}_on"


def state_lines(device_id: str, relays: Optional[Dict[str, bool]]) -> Iterator[str]:
    """UDP-Zeilen für ein Gerät; relays=None bedeutet nicht erreichbar."""
    if not loxone_udp._valid_id(device_id):
        return
    yield f"shelly_{device_id}_available@{0 if relays is None else 1}\r\n"
    for channel, on in sorted((relays or {}).items(), key=lambda kv: int(kv[0]) if kv[0].isdecimal() else 0):
        if loxone_udp._valid_channel(channel):
            yield f"{relay_variable(device_id, channel)}@{1 if on else 0}\r\n"


class ShellyStatePoller:
    """Hintergrund-Thread; Zeit/Abfrage/Senden für Tests austauschbar."""

    def __init__(self, fetch: Callable[[str, int], Optional[Dict[str, bool]]] = shelly_mod.get_relay_states,
                 send: Optional[Callable[[str, int, Iterable[str]], None]] = None,
                 devices: Callable[[], List[Dict[str, Any]]] = shelly_mod.load_cached,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._send = send or loxone_udp._send_lines
        self._devices = devices
        self._clock = clock
        self._lock = threading.Lock()
        self._known: Dict[str, Optional[Dict[str, bool]]] = {}   # device_id -> relays | None
        self._last_full = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Konfiguration ────────────────────────────────────────────────────────
    @staticmethod
    def _settings() -> Tuple[str, int, float, float]:
        with state.config_lock:
            loxone = dict(state.config.get("loxone") or {})
            shelly = dict(state.config.get("shelly") or {})
        poll = shelly.get("state_poll_seconds", DEFAULT_POLL_SECONDS)
        if not shelly.get("enabled"):
            poll = 0
        return (loxone.get("miniserver_ip") or "", int(loxone.get("udp_port") or 7777),
                float(poll or 0), float(loxone.get("resync_interval_seconds", 300) or 0))

    # ── Abfrage ──────────────────────────────────────────────────────────────
    def known_states(self) -> Dict[str, Optional[Dict[str, bool]]]:
        with self._lock:
            return {k: (dict(v) if v is not None else None) for k, v in self._known.items()}

    def _poll_one(self, device: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, bool]]]:
        try:
            return device["id"], self._fetch(device["ip"], int(device.get("gen", 1)))
        except Exception:
            log.debug("Shelly-Statusabfrage fehlgeschlagen: %s", device.get("ip"), exc_info=True)
            return device["id"], None

    def poll(self, force_full: bool = False, only_ip: Optional[str] = None) -> int:
        """Einmal abfragen; Änderungen (oder bei force_full alles) senden. Gibt Zeilenzahl zurück."""
        host, port, _, _ = self._settings()
        devices = [d for d in self._devices()
                   if isinstance(d, dict) and loxone_udp._valid_id(d.get("id")) and d.get("ip")
                   and (only_ip is None or d.get("ip") == only_ip)]
        if not devices:
            return 0
        with ThreadPoolExecutor(max_workers=min(16, len(devices))) as pool:
            results = list(pool.map(self._poll_one, devices))
        lines: List[str] = []
        with self._lock:
            for device_id, relays in results:
                previous = self._known.get(device_id, "unknown")
                self._known[device_id] = relays
                if force_full or previous == "unknown":
                    lines.extend(state_lines(device_id, relays))
                    continue
                if (previous is None) != (relays is None):
                    lines.append(f"shelly_{device_id}_available@{0 if relays is None else 1}\r\n")
                for channel, on in (relays or {}).items():
                    if previous is None or previous.get(channel) != on:
                        lines.append(f"{relay_variable(device_id, channel)}@{1 if on else 0}\r\n")
        if lines and host:
            self._send(host, port, lines)
        return len(lines)

    # ── Thread ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="shelly-state", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not state.stop_event.is_set():
            host, _, poll, resync = self._settings()
            if not host or not poll:
                if self._stop.wait(5):
                    return
                continue
            now = self._clock()
            full = self._last_full is None or (resync and now - self._last_full >= resync)
            try:
                self.poll(force_full=bool(full))
                if full:
                    self._last_full = now
            except Exception:
                log.warning("Shelly-Statusabfrage fehlgeschlagen", exc_info=True)
            if self._stop.wait(max(2.0, poll)):
                return
