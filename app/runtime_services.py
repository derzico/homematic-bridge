# SPDX-License-Identifier: Apache-2.0
"""Optionale, nur lesende Hintergrunddienste und Loxone-Zustandssynchronisation."""
import json
import logging
import threading
import time
from pathlib import Path

import yaml

import app.state as state
from app.controls import system_body
from app.energy import EnergyCollector
from app.loxone_udp import push_shelly_energy, push_system_state
from app.utils import _atomic_write

log = logging.getLogger("bridge-ws")
ENERGY_SETTINGS_PATH = "config/energy.yaml"


class RuntimeServices:
    def __init__(self):
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.last_sync_at = None
        self.last_sync_error = None
        self.energy = EnergyCollector(config=self.effective_config, on_sample=self.forward_energy)

    def effective_config(self):
        with state.config_lock:
            config = dict(state.config)
        try:
            overrides = yaml.safe_load(Path(ENERGY_SETTINGS_PATH).read_text())
            if isinstance(overrides, dict):
                from config.loader import validate_config
                if validate_config({**config, "energy": overrides}):
                    log.warning("Ungültige lokale Energie-Einstellungen; Hauptkonfiguration wird verwendet")
                else:
                    config["energy"] = overrides
        except FileNotFoundError:
            pass
        except (OSError, yaml.YAMLError):
            log.warning("Lokale Energie-Einstellungen nicht lesbar; Hauptkonfiguration wird verwendet")
        return config

    def forward_energy(self, sample):
        with state.config_lock:
            loxone = dict(state.config.get("loxone") or {})
        push_shelly_energy(loxone.get("miniserver_ip") or "", loxone.get("udp_port", 7777), sample)

    def start(self):
        self._stop.clear()
        try:
            self.energy.start()
        except Exception:
            log.warning("Optionaler Energiedienst konnte nicht gestartet werden", exc_info=True)
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="bridge-resync", daemon=True)
                self._thread.start()

    def update_config(self):
        self.energy.update_config(self.effective_config())

    def set_energy_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled muss ein Boolean sein")
        with self._lock:
            config = self.effective_config()
            energy = dict(config.get("energy") or {})
            energy["enabled"] = enabled
            # Validate before persistence; collector validates full settings too.
            from config.loader import validate_config
            errors = validate_config({**config, "energy": energy})
            if errors:
                raise ValueError("; ".join(errors))
            _atomic_write(ENERGY_SETTINGS_PATH, yaml.safe_dump(energy))
            self.energy.update_config({**config, "energy": energy})

    def sync_snapshot(self, snapshot=None):
        if not state.WS_READY:
            raise ValueError("HCU-Snapshot noch nicht initialisiert")
        if not self._sync_lock.acquire(blocking=False):
            raise ValueError("Synchronisierung läuft bereits")
        try:
            with state.config_lock:
                loxone = dict(state.config.get("loxone") or {})
            host = loxone.get("miniserver_ip") or ""
            if not host:
                raise ValueError("Loxone ist nicht konfiguriert")
            if snapshot is None:
                path = state.config_internal.get("system_state_path", "data/system_state.json")
                snapshot = json.loads(Path(path).read_text())
            if not isinstance(system_body(snapshot).get("devices"), (dict, list)):
                raise ValueError("Kein gültiger Vollsnapshot")
            push_system_state(host, loxone.get("udp_port", 7777), snapshot)
            self.last_sync_at = time.time()
            self.last_sync_error = None
            return self.last_sync_at
        except Exception as error:
            self.last_sync_error = str(error)
            raise
        finally:
            self._sync_lock.release()

    def _loop(self):
        last_attempt = time.monotonic()
        while not state.stop_event.is_set() and not self._stop.wait(1):
            with state.config_lock:
                config = dict(state.config.get("loxone") or {})
            interval = config.get("resync_interval_seconds", 300)
            if not config.get("miniserver_ip") or not interval or not state.WS_READY:
                continue
            if time.monotonic() - last_attempt >= interval:
                last_attempt = time.monotonic()
                try:
                    self.sync_snapshot()
                except Exception:
                    log.warning("Periodische Loxone-Synchronisierung fehlgeschlagen", exc_info=True)

    def stop(self):
        self._stop.set()
        self.energy.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
