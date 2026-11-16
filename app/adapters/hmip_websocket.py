# SPDX-License-Identifier: Apache-2.0
# app/adapters/hmip_websocket.py – WebSocket-Loop, Pending-Registry, Log-Level-Update

import json
import errno
import logging
import os
import random
import ssl
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

import certifi
import websocket
import yaml

import app.state as state
from app.adapters.hmip_messages import (send_config_template_response, send_config_update_response,
                                       send_get_system_state, send_plugin_state)
from app.utils import _read_snapshot, save_system_state
from app.loxone_udp import push_aggregates_if_changed, push_event_devices, update_topology

log = logging.getLogger("bridge-ws")
SNAPSHOT_TIMEOUT = 30.0
SNAPSHOT_ATTEMPTS = 3
CONNECTION_FIELDS = ("homematic_hcu", "homematic_token", "plugin_id", "ssl_verify", "ssl_cert_path")


# ── Pending-Registry ──────────────────────────────────────────────────────────

def _register_pending(req_id: str, path: str, *, wait_for_ack: bool = False) -> Optional[Dict[str, Any]]:
    if not req_id:
        return None
    meta = {
        "path": path,
        "ts": time.time(),
        "wait_for_ack": wait_for_ack,
        "event": threading.Event(),
        "response": None,
    }
    with state.pending_lock:
        state.pending[req_id] = meta
    return meta


def _resolve_pending(
    req_id: Optional[str], response: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    if not req_id:
        return None
    with state.pending_lock:
        meta = state.pending.get(req_id)
        if meta is None:
            return None
        if response is not None and meta.get("wait_for_ack"):
            meta["response"] = response
            meta["event"].set()
            return meta
        return state.pending.pop(req_id, None)


def _wait_for_ack(req_id: str, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Wartet auf eine HCU-Antwort und entfernt den Request anschließend."""
    with state.pending_lock:
        meta = state.pending.get(req_id)
    if meta is None:
        return None

    acknowledged = meta["event"].wait(
        timeout=state.ACK_TIMEOUT_SEC if timeout is None else timeout
    )
    with state.pending_lock:
        state.pending.pop(req_id, None)
    return meta.get("response") if acknowledged else None


def _send_tracked(
    send_func: Callable,
    path: str,
    *args,
    wait_for_ack: bool = False,
    **kwargs,
) -> str:
    """Registriert eine Request-ID vor dem Versand und verhindert so ACK-Races."""
    rid = str(uuid.uuid4())
    _register_pending(rid, path, wait_for_ack=wait_for_ack)
    try:
        with state.send_lock:
            if state.conn is None:
                raise ConnectionError("WebSocket nicht verbunden")
            send_func(state.conn, *args, request_id=rid, **kwargs)
    except Exception:
        with state.pending_lock:
            state.pending.pop(rid, None)
        raise
    return rid


_TOPOLOGY_EVENTS = {"DEVICE_ADDED", "DEVICE_REMOVED", "GROUP_ADDED", "GROUP_CHANGED", "GROUP_REMOVED"}


def _push_event_aggregates(msg_data: Dict[str, Any]) -> None:
    """Nach einem Event Sammelwerte (unreach/lowBat/sabotage) bei Änderung an Loxone senden."""
    try:
        snapshot = _read_snapshot()
        if snapshot is None:
            return
        events = (((msg_data.get("body") or {}).get("eventTransaction") or {}).get("events") or {})
        if isinstance(events, dict) and any(isinstance(ev, dict) and ev.get("pushEventType") in _TOPOLOGY_EVENTS
                                            for ev in events.values()):
            update_topology(snapshot)
        push_aggregates_if_changed(state.LOXONE_HOST, state.LOXONE_UDP_PORT, snapshot)
    except Exception:
        log.warning("Loxone-Sammelwerte konnten nicht aktualisiert werden", exc_info=True)


def _cleanup_pending() -> None:
    now = time.time()
    with state.pending_lock:
        to_del = [rid for rid, meta in state.pending.items()
                  if now - meta.get("ts", 0) > state.PENDING_TTL]
        for rid in to_del:
            meta = state.pending.pop(rid, None)
            if meta:
                log.warning("Pending-Request abgelaufen: %s (path=%s, age=%.0fs)",
                            rid, meta.get("path"), now - meta.get("ts", 0))
    if to_del:
        log.info("Pending-Requests bereinigt: %d orphans entfernt", len(to_del))


def _fail_all_pending(message: str) -> None:
    """Weckt ACK-Waiter bei Verbindungsabbruch und verwirft asynchrone Requests."""
    with state.pending_lock:
        for rid, meta in list(state.pending.items()):
            if meta.get("wait_for_ack"):
                meta["response"] = {"body": {"code": 503, "message": message}}
                meta["event"].set()
            else:
                state.pending.pop(rid, None)


# ── Log-Level zur Laufzeit ändern ─────────────────────────────────────────────

def _apply_log_level(new_level: str) -> bool:
    level = getattr(logging, new_level.upper(), None)
    if level is None:
        return False
    cfg_path = "config/internal_config.yaml"
    tmp = cfg_path + ".tmp"
    try:
        with state.config_lock:
            with open(cfg_path, "r", encoding="utf-8") as f:
                ic = yaml.safe_load(f) or {}
            ic["log_level"] = new_level.lower()
            content = yaml.safe_dump(ic)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.replace(tmp, cfg_path)
            except OSError as error:
                if error.errno != errno.EBUSY:
                    raise
                # Single-file Docker bind mounts cannot be atomically replaced.
                # Preserve the mount inode; report any write failure to the HCU.
                with open(cfg_path, "r+", encoding="utf-8") as f:
                    f.write(content)
                    f.truncate()
                    f.flush()
                    os.fsync(f.fileno())
            state.config_internal["log_level"] = new_level.lower()
    except Exception as e:
        log.error("log_level konnte nicht in internal_config.yaml gespeichert werden: %s", e)
        return False
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("Temporäre Log-Level-Datei konnte nicht entfernt werden.")
    log.setLevel(level)
    return True


# ── WebSocket-Loop ────────────────────────────────────────────────────────────

def _connection_options():
    with state.config_lock:
        config = dict(state.config)
    headers = {
        "authtoken": config["homematic_token"],
        "plugin-id": config.get("plugin_id"),
        "hmip-system-events": "true",
    }
    url = f"wss://{config['homematic_hcu']}:9001"

    ssl_verify = config.get("ssl_verify", False)
    cert_path  = config.get("ssl_cert_path")

    if ssl_verify and cert_path:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": cert_path, "check_hostname": False}
        log.info("[SSL] Zertifikatspfad verwendet: %s", cert_path)
    elif ssl_verify:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()}
        log.info("[SSL] Certifi CA-Bundle wird verwendet.")
    else:
        sslopt = {"cert_reqs": ssl.CERT_NONE}
        log.warning("[SSL] Verbindung ohne Zertifikatsprüfung (unsicher)")
    return config, url, headers, sslopt


def ws_loop() -> None:
    backoff = 1.0
    while not state.stop_event.is_set():
        try:
            config, url, headers, sslopt = _connection_options()
            log.info("Verbinde zu WebSocket (Port 9001)...")
            new_conn = websocket.create_connection(url, header=headers, sslopt=sslopt, timeout=10)
            new_conn.settimeout(30)
            with state.config_lock:
                if any(config.get(key) != state.config.get(key) for key in CONNECTION_FIELDS):
                    new_conn.close()
                    raise ConnectionError("HCU-Konfiguration während des Verbindungsaufbaus geändert")
                with state.send_lock:
                    state.conn = new_conn
                    state.WS_PLUGIN_ID = config.get("plugin_id")
                    state.WS_READY = False
            state.WS_CONNECTED_AT = time.time()
            state.WS_LAST_ERROR = None
            log.info("WebSocket-Verbindung hergestellt.")
            backoff = 1.0

            with state.send_lock:
                send_plugin_state(state.conn)
            probe = _send_tracked(send_get_system_state, "/hmip/home/getSystemState")
            deadline = time.monotonic() + SNAPSHOT_TIMEOUT
            attempts = 1
            initial_forwarded = False

            while not state.stop_event.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    with state.pending_lock:
                        state.pending.pop(probe, None)
                    if state.WS_READY or attempts >= SNAPSHOT_ATTEMPTS:
                        raise ConnectionError("HCU-Vollsnapshot/Liveness-Antwort ausgeblieben")
                    attempts += 1
                    probe = _send_tracked(send_get_system_state, "/hmip/home/getSystemState")
                    deadline = time.monotonic() + SNAPSHOT_TIMEOUT
                try:
                    msg = new_conn.recv()
                except websocket.WebSocketTimeoutException:
                    try:
                        with state.send_lock:
                            new_conn.ping()
                        log.debug("Keepalive-Ping gesendet.")
                    except Exception as ping_err:
                        log.warning("Keepalive-Ping fehlgeschlagen: %s", ping_err)
                        raise
                    _cleanup_pending()
                    if probe is None and not state.stop_event.is_set():
                        # recv() hides Pong frames. A correlated, read-only HCU
                        # response proves end-to-end liveness with a deadline.
                        probe = _send_tracked(send_get_system_state, "/hmip/home/getSystemState")
                        deadline = time.monotonic() + SNAPSHOT_TIMEOUT
                    continue

                if not msg:
                    raise ConnectionError("HCU-WebSocket geschlossen")

                log.debug("Nachricht empfangen: %s", msg)
                state.WS_LAST_MESSAGE_AT = time.time()
                try:
                    msg_data = json.loads(msg)
                    msg_type = msg_data.get("type")

                    if msg_type == "PLUGIN_STATE_REQUEST":
                        with state.send_lock:
                            send_plugin_state(state.conn, msg_id=msg_data.get("id"))

                    elif msg_type == "HMIP_SYSTEM_RESPONSE":
                        rid  = msg_data.get("id")
                        meta = _resolve_pending(rid, msg_data)
                        code = (msg_data.get("body") or {}).get("code")
                        if meta:
                            path = meta.get("path")
                            if path == "/hmip/home/getSystemState":
                                response_body = (msg_data.get("body") or {}).get("body")
                                if (code == 200 and isinstance(response_body, dict)
                                        and isinstance(response_body.get("devices"), (dict, list))):
                                    if save_system_state(msg_data) is False:
                                        raise OSError("HCU-Snapshot konnte nicht gespeichert werden")
                                    update_topology(msg_data)
                                    state.WS_READY = True
                                    if not initial_forwarded:
                                        if state.runtime_services is not None and state.LOXONE_HOST:
                                            try:
                                                state.runtime_services.sync_snapshot(msg_data)
                                            except Exception:
                                                log.warning("Initiale Loxone-Synchronisierung fehlgeschlagen", exc_info=True)
                                        initial_forwarded = True
                                    if rid == probe:
                                        probe = None
                                        deadline = None
                                    attempts = 0
                                    log.info("getSystemState → Snapshot gespeichert (code=200)")
                                else:
                                    log.warning(
                                        "getSystemState fehlgeschlagen; vorhandener Snapshot bleibt erhalten "
                                        "(code=%s, id=%s)", code, rid,
                                    )
                                    if rid == probe:
                                        deadline = 0.0
                            elif code == 200:
                                log.info("ACK %s OK (id=%s)", path, rid)
                            else:
                                log.warning("ACK %s Fehler (code=%s, id=%s)", path, code, rid)
                        else:
                            log.debug("Unkorrelierte HMIP_SYSTEM_RESPONSE (id=%s, code=%s) ignoriert.", rid, code)

                    elif msg_type == "CONFIG_TEMPLATE_REQUEST":
                        with state.send_lock:
                            send_config_template_response(
                                state.conn, msg_data.get("id"),
                                state.config_internal.get("log_level", "info"),
                            )

                    elif msg_type == "CONFIG_UPDATE_REQUEST":
                        props     = (msg_data.get("body") or {}).get("properties") or {}
                        new_level = props.get("log_level")
                        if new_level and new_level in ("debug", "info", "warning", "error"):
                            applied = _apply_log_level(new_level)
                            feedback = (f"Log-Level auf '{new_level}' gesetzt." if applied
                                        else "Log-Level konnte nicht dauerhaft gespeichert werden.")
                            status = "APPLIED" if applied else "FAILED"
                            log.info(feedback)
                        else:
                            feedback = "Ungültiger oder fehlender Wert für log_level."
                            status = "FAILED"
                        with state.send_lock:
                            send_config_update_response(state.conn, msg_data.get("id"), status, feedback)

                    elif msg_type == "HMIP_SYSTEM_EVENT":
                        save_system_state(msg_data)
                        push_event_devices(state.LOXONE_HOST, state.LOXONE_UDP_PORT, msg_data)
                        if state.LOXONE_HOST:
                            _push_event_aggregates(msg_data)

                    else:
                        log.debug("Unbehandelter Nachrichtentyp: %r", msg_type)

                except Exception:
                    log.exception("Fehler beim Verarbeiten der Nachricht")

                _cleanup_pending()

        except Exception as exc:
            if state.stop_event.is_set():
                log.info("WebSocket-Loop beendet (stop_event).")
                break
            log.exception("WebSocket Fehler")
            state.WS_LAST_ERROR = str(exc)
            state.WS_RECONNECTS += 1
            state.WS_CONNECTED_AT = None
            state.WS_READY = False
            state.WS_PLUGIN_ID = None
            try:
                with state.send_lock:
                    if state.conn:
                        state.conn.close()
                    state.conn = None
            except Exception:
                with state.send_lock:
                    state.conn = None
            _fail_all_pending("WebSocket-Verbindung unterbrochen")
            sleep_for = backoff + random.uniform(0, 0.3 * backoff)
            log.info("Reconnect in %.1fs (Backoff: %.1fs)", sleep_for, backoff)
            # Mit stop_event warten – ermöglicht sofortige Beendigung
            if state.stop_event.wait(timeout=sleep_for):
                log.info("WebSocket-Loop beendet (stop_event).")
                break
            backoff = min(backoff * 2.0, 60.0)
