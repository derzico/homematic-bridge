# SPDX-License-Identifier: Apache-2.0
# websocket_handler.py – WebSocket-Loop, Pending-Registry, Log-Level-Update

import json
import logging
import os
import random
import ssl
import time
from typing import Any, Dict, Optional

import certifi
import websocket
import yaml

import app.state as state
from app.messages import (send_config_template_response, send_config_update_response,
                          send_get_system_state, send_plugin_state)
from app.utils import save_system_state
from app.loxone_udp import push_event_devices

log = logging.getLogger("bridge-ws")


# ── Pending-Registry ──────────────────────────────────────────────────────────

def _register_pending(req_id: str, path: str) -> None:
    if not req_id:
        return
    with state.pending_lock:
        state.pending[req_id] = {"path": path, "ts": time.time()}


def _resolve_pending(req_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not req_id:
        return None
    with state.pending_lock:
        return state.pending.pop(req_id, None)


def _cleanup_pending() -> None:
    now = time.time()
    with state.pending_lock:
        to_del = [rid for rid, meta in state.pending.items()
                  if now - meta.get("ts", 0) > state.PENDING_TTL]
        for rid in to_del:
            state.pending.pop(rid, None)
    if to_del:
        log.debug("Pending-Requests bereinigt: %d", len(to_del))


# ── Log-Level zur Laufzeit ändern ─────────────────────────────────────────────

def _apply_log_level(new_level: str) -> bool:
    level = getattr(logging, new_level.upper(), None)
    if level is None:
        return False
    log.setLevel(level)
    state.config_internal["log_level"] = new_level.lower()
    cfg_path = "config/internal_config.yaml"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            ic = yaml.safe_load(f) or {}
        ic["log_level"] = new_level.lower()
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(ic, f)
        os.replace(tmp, cfg_path)
    except Exception as e:
        log.error("log_level konnte nicht in internal_config.yaml gespeichert werden: %s", e)
    return True


# ── WebSocket-Loop ────────────────────────────────────────────────────────────

def ws_loop() -> None:
    headers = {
        "authtoken": state.config["homematic_token"],
        "plugin-id": state.config.get("plugin_id"),
        "hmip-system-events": "true",
    }
    url = f"wss://{state.config['homematic_hcu']}:9001"

    ssl_verify = state.config.get("ssl_verify", False)
    cert_path  = state.config.get("ssl_cert_path")

    if ssl_verify and cert_path:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": cert_path}
        log.info("[SSL] Zertifikatspfad verwendet: %s", cert_path)
    elif ssl_verify:
        sslopt = {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()}
        log.info("[SSL] Certifi CA-Bundle wird verwendet.")
    else:
        sslopt = {"cert_reqs": ssl.CERT_NONE}
        log.warning("[SSL] Verbindung ohne Zertifikatsprüfung (unsicher)")

    backoff = 1.0
    while True:
        try:
            log.info("Verbinde zu WebSocket (Port 9001)...")
            state.conn = websocket.create_connection(url, header=headers, sslopt=sslopt, timeout=10)
            state.conn.settimeout(30)
            log.info("WebSocket-Verbindung hergestellt.")
            backoff = 1.0

            with state.send_lock:
                send_plugin_state(state.conn)
                rid = send_get_system_state(state.conn)
            _register_pending(rid, "/hmip/home/getSystemState")

            while True:
                try:
                    msg = state.conn.recv()
                except websocket.WebSocketTimeoutException:
                    try:
                        with state.send_lock:
                            state.conn.ping()
                        log.debug("Keepalive-Ping gesendet.")
                    except Exception as ping_err:
                        log.warning("Keepalive-Ping fehlgeschlagen: %s", ping_err)
                        raise
                    continue

                log.debug("Nachricht empfangen: %s", msg)
                try:
                    msg_data = json.loads(msg)
                    msg_type = msg_data.get("type")

                    if msg_type == "PLUGIN_STATE_REQUEST":
                        with state.send_lock:
                            send_plugin_state(state.conn, msg_id=msg_data.get("id"))

                    elif msg_type == "HMIP_SYSTEM_RESPONSE":
                        rid  = msg_data.get("id")
                        meta = _resolve_pending(rid)
                        code = (msg_data.get("body") or {}).get("code")
                        if meta:
                            path = meta.get("path")
                            if path == "/hmip/home/getSystemState":
                                save_system_state(msg_data)
                                log.info("getSystemState → Snapshot gespeichert (code=%s)", code)
                            elif code == 200:
                                log.info("ACK %s OK (id=%s)", path, rid)
                            else:
                                log.warning("ACK %s Fehler (code=%s, id=%s)", path, code, rid)
                        else:
                            log.debug("Unkorrelierte HMIP_SYSTEM_RESPONSE (id=%s, code=%s) ignoriert.", rid, code)
                        _cleanup_pending()

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
                            _apply_log_level(new_level)
                            feedback = f"Log-Level auf '{new_level}' gesetzt."
                            log.info(feedback)
                        else:
                            feedback = None
                        with state.send_lock:
                            send_config_update_response(state.conn, msg_data.get("id"), "APPLIED", feedback)

                    elif msg_type == "HMIP_SYSTEM_EVENT":
                        save_system_state(msg_data)
                        push_event_devices(state.LOXONE_HOST, state.LOXONE_UDP_PORT, msg_data)

                    else:
                        log.debug("Unbehandelter Nachrichtentyp: %r", msg_type)

                except Exception as e:
                    log.error("Fehler beim Verarbeiten der Nachricht: %s", e)

        except Exception as e:
            log.error("WebSocket Fehler: %s", e)
            try:
                if state.conn:
                    state.conn.close()
            except Exception:
                pass
            finally:
                state.conn = None
            with state.pending_lock:
                state.pending.clear()
            sleep_for = backoff + random.uniform(0, 0.3 * backoff)
            log.info("Reconnect in %.1fs (Backoff: %.1fs)", sleep_for, backoff)
            time.sleep(sleep_for)
            backoff = min(backoff * 2.0, 60.0)
