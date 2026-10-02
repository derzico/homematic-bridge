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
SNAPSHOT_PATH = config_internal.get("system_state_path", "data/system_state.json")
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
def _get_nested(d: Any, keys: tuple) -> Any:
    """Navigiert verschachtelte Dicts entlang einer Key-Sequenz."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


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


def _event_sort_key(item: tuple) -> tuple:
    """Sortiert HCU-Eventindizes numerisch; unbekannte Schlüssel folgen stabil danach."""
    key = item[0]
    try:
        return 0, int(key)
    except (TypeError, ValueError):
        return 1, str(key)


def _replace_indexed(container: Any, item: Dict[str, Any]) -> None:
    """Fügt ein vollständiges HCU-Objekt hinzu oder ersetzt es anhand seiner ID."""
    item_id = item.get("id")
    if not item_id:
        return
    if isinstance(container, dict):
        container[item_id] = item
    elif isinstance(container, list):
        current, index = _find_device_in_list(container, item_id)
        if current is None:
            container.append(item)
        else:
            container[index] = item


def _remove_indexed(container: Any, item_id: Any) -> None:
    """Entfernt ein HCU-Objekt aus einem ID-Mapping oder einer Objektliste."""
    if not item_id:
        return
    if isinstance(container, dict):
        container.pop(item_id, None)
    elif isinstance(container, list):
        container[:] = [item for item in container
                        if not isinstance(item, dict) or item.get("id") != item_id]

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
    - erfolgreicher HMIP_SYSTEM_RESPONSE -> gesamten Snapshot atomar speichern
    - HMIP_SYSTEM_EVENT -> vollständige geänderte Objekte ersetzen und Entfernungen anwenden
    """
    try:
        if not isinstance(msg, dict):
            raise ValueError("save_system_state: msg muss ein Dict sein.")

        msg_type = msg.get("type")
        if msg_type == "HMIP_SYSTEM_RESPONSE":
            response = msg.get("body") or {}
            if response.get("code") != 200 or not isinstance(response.get("body"), dict):
                log.warning("Ungültiger/fehlgeschlagener Vollsnapshot wird nicht gespeichert.")
                return
            _write_snapshot(msg)
            log.info("Systemzustand (Vollsnapshot) gespeichert → %s", SNAPSHOT_PATH)
            return True

        if msg_type == "HMIP_SYSTEM_EVENT":
            with _snapshot_lock:
                snapshot = _read_snapshot()
                if snapshot is None:
                    log.warning("Kein vorhandener Snapshot – HMIP_SYSTEM_EVENT wird ignoriert (warte auf Vollsnapshot).")
                    return

                body_inner = (snapshot.get("body") or {}).get("body")
                if not isinstance(body_inner, dict):
                    log.warning("Systemzustand enthält keinen gültigen Response-Body.")
                    return

                devices_container, _ = _locate_devices_container(snapshot)
                groups_container = body_inner.get("groups")
                clients_container = body_inner.get("clients")

                tx = (msg.get("body") or {}).get("eventTransaction") or {}
                events = tx.get("events") or {}

                for _, ev in sorted(events.items(), key=_event_sort_key):
                    if not isinstance(ev, dict):
                        continue

                    event_type = ev.get("pushEventType")
                    if event_type == "HOME_CHANGED" and isinstance(ev.get("home"), dict):
                        body_inner["home"] = ev["home"]
                    elif event_type in {"DEVICE_ADDED", "DEVICE_CHANGED"}:
                        if isinstance(ev.get("device"), dict):
                            _replace_indexed(devices_container, ev["device"])
                    elif event_type == "DEVICE_REMOVED":
                        _remove_indexed(devices_container, ev.get("id"))
                    elif event_type in {"GROUP_ADDED", "GROUP_CHANGED"}:
                        if isinstance(ev.get("group"), dict):
                            _replace_indexed(groups_container, ev["group"])
                    elif event_type == "GROUP_REMOVED":
                        _remove_indexed(groups_container, ev.get("id"))
                    elif event_type in {"CLIENT_ADDED", "CLIENT_CHANGED"}:
                        if isinstance(ev.get("client"), dict):
                            _replace_indexed(clients_container, ev["client"])
                    elif event_type == "CLIENT_REMOVED":
                        _remove_indexed(clients_container, ev.get("id"))

                _write_snapshot(snapshot)
                log.debug("Systemzustand mit HCU-Events aktualisiert.")
            return

        # Andere Typen ignorieren wir still (oder debug-loggen)
        log.debug("save_system_state: Nachrichtentyp %r ignoriert.", msg_type)

    except Exception as e:
        log.exception("Fehler beim Aktualisieren des Systemzustands")
        return False
