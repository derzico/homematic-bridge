# SPDX-License-Identifier: Apache-2.0

# utils.py
import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Tuple, Optional
from config.loader import load_internal_config

config_internal = load_internal_config()
SNAPSHOT_PATH = config_internal["system_state_path"]
log = logging.getLogger("bridge-ws")
_snapshot_lock = threading.Lock()

# --------- Helpers: IO ---------
def _atomic_write(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)  # atomar
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def _read_snapshot() -> Optional[Dict[str, Any]]:
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.exception("Snapshot lesen fehlgeschlagen")
        return None

def _write_snapshot(obj: Dict[str, Any]) -> None:
    _atomic_write(SNAPSHOT_PATH, json.dumps(obj, ensure_ascii=False, indent=2))

# --------- Helpers: Struktur finden ---------
def _locate_devices_container(snap: Dict[str, Any]) -> Tuple[Optional[Any], str]:
    """
    Liefert (devices_container, path_hint):
      - devices_container: Liste von Devices ODER Dict {id: device}
    Prüft mehrere Pfade (inkl. body.body.*).
    """
    candidates = [
        ("body", "devices"),
        ("body", "home", "devices"),
        ("body", "body", "devices"),
        ("body", "body", "home", "devices"),
    ]
    for path in candidates:
        cur = snap
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and isinstance(cur, (list, dict)):
            return cur, ".".join(path)
    return None, "not found"

def _find_device_in_list(devs_list: list, dev_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    for idx, d in enumerate(devs_list):
        if isinstance(d, dict) and d.get("id") == dev_id:
            return d, idx
    return None, None

# --------- Helpers: Merge-Logik ---------
def _merge_functional_channels(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Merget functionalChannels: pro Index flach überlagern."""
    cur = current or {}
    inc = incoming or {}
    out = dict(cur)  # shallow copy
    for ch_idx, ch_state in inc.items():
        # Keys bleiben als Strings ("0","1",...)
        base = out.get(ch_idx, {})
        if not isinstance(base, dict):
            base = {}
        if not isinstance(ch_state, dict):
            ch_state = {}
        out[ch_idx] = {**base, **ch_state}
    return out

def _merge_device(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Flacher Merge der Device-Felder, functionalChannels tiefer zusammenführen."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(incoming, dict):
        incoming = {}

    merged = {**current, **{k: v for k, v in incoming.items() if k != "functionalChannels"}}

    if "functionalChannels" in incoming:
        merged["functionalChannels"] = _merge_functional_channels(
            current.get("functionalChannels", {}),
            incoming.get("functionalChannels", {})
        )

    return merged

def _merge_group(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Flacher Merge der Group-Felder."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(incoming, dict):
        incoming = {}
    return {**current, **incoming}

# --------- Public: Save + Merge ---------
def save_system_state(msg: Dict[str, Any]) -> None:
    """
    - HMIP_SYSTEM_RESPONSE  -> gesamten Snapshot atomar speichern
    - HMIP_SYSTEM_EVENT     -> bestehende Datei laden, Devices daraus mit Event-Devices mergen, zurückschreiben
    """
    try:
        if not isinstance(msg, dict):
            raise ValueError("save_system_state: msg muss ein Dict sein.")

        msg_type = msg.get("type")
        if msg_type == "HMIP_SYSTEM_RESPONSE":
            _write_snapshot(msg)
            log.info("Systemzustand (Vollsnapshot) gespeichert → %s", SNAPSHOT_PATH)
            return

        if msg_type == "HMIP_SYSTEM_EVENT":
            with _snapshot_lock:
                snapshot = _read_snapshot()
                if snapshot is None:
                    log.warning("Kein vorhandener Snapshot – HMIP_SYSTEM_EVENT wird ignoriert (warte auf Vollsnapshot).")
                    return

                body_inner = (snapshot.get("body") or {}).get("body") or {}
                devices_container, hint = _locate_devices_container(snapshot)
                groups_container = body_inner.get("groups")

                if devices_container is None:
                    log.warning("Devices-Container im Snapshot nicht gefunden (%s) – Event kann nicht gemerged werden.", hint)
                    return

                tx = (msg.get("body") or {}).get("eventTransaction") or {}
                events = tx.get("events") or {}

                for _, ev in sorted(events.items(), key=lambda kv: kv[0]):
                    if not isinstance(ev, dict):
                        continue

                    # ── Device-Event ──
                    dev = ev.get("device")
                    if isinstance(dev, dict):
                        dev_id = dev.get("id")
                        if dev_id:
                            if isinstance(devices_container, list):
                                cur, idx = _find_device_in_list(devices_container, dev_id)
                                if cur is None:
                                    devices_container.append(dev)
                                    log.debug("Neues Device angelegt (Liste): %s", dev_id)
                                else:
                                    devices_container[idx] = _merge_device(cur, dev)
                                    log.debug("Device gemerged: %s", dev_id)
                            elif isinstance(devices_container, dict):
                                cur = devices_container.get(dev_id, {})
                                devices_container[dev_id] = _merge_device(cur, dev)
                                log.debug("Device gemerged: %s", dev_id)

                    # ── Group-Event ──
                    grp = ev.get("group")
                    if isinstance(grp, dict) and isinstance(groups_container, dict):
                        grp_id = grp.get("id")
                        if grp_id:
                            cur = groups_container.get(grp_id, {})
                            groups_container[grp_id] = _merge_group(cur, grp)
                            log.debug("Group gemerged: %s (%s)", grp_id, grp.get("label", "–"))

                _write_snapshot(snapshot)
                log.debug("Systemzustand (Delta-Event) in Snapshot gemerged.")
            return

        # Andere Typen ignorieren wir still (oder debug-loggen)
        log.debug("save_system_state: Nachrichtentyp %r ignoriert.", msg_type)

    except Exception as e:
        log.exception("Fehler beim Aktualisieren des Systemzustands")
