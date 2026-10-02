# SPDX-License-Identifier: Apache-2.0
# routes.py – Flask-Blueprint mit allen HTTP-Routen

import colorsys
import ipaddress
import json
import logging
import math
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import yaml
from flask import Blueprint, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash

import app.adapters.shelly_adapter as shelly_mod
import app.shelly_proxy as shelly_proxy
import app.state as state
from app.adapters.hmip_messages import (send_hmip_set_dim_level,
                                        send_hmip_set_group_state,
                                        send_hmip_set_hue_saturation_dim_level,
                                        send_hmip_set_point_temperature,
                                        send_hmip_set_switch)
from app.adapters.hmip_messages import send_hmip_set_watering_switch
from app.adapters.hmip_messages import send_hmip_request
from app.controls import ControlError, plan_control
from app.adapters.hmip_websocket import _send_tracked, _wait_for_ack
from app.auth import _ensure_api_key, generate_csrf_token, require_api_key, require_csrf, require_web_auth
from app.utils import _find_device_in_list, _locate_devices_container
from app.view_helpers import (prepare_dashboard, prepare_device_detail,
                               prepare_device_overview, prepare_device_status,
                               prepare_heating, prepare_shelly)
from config.loader import resolve_api_key_file, validate_config

bp = Blueprint("bridge", __name__)
log = logging.getLogger("bridge-ws")


@bp.before_request
def validate_control_json():
    """Reject malformed control requests before any network/device operation."""
    if request.method != "POST" or not (
        request.path in {"/hmipSwitch", "/hmipDimmer", "/hmipRGB", "/hmipAlarm",
                         "/hmipThermostat", "/hmipIrrigation"}
        or (request.path.startswith("/shelly/") and "/relay/" in request.path)
    ):
        return None
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Body muss ein JSON-Objekt sein"}), 400
    for field in ("device", "group", "groupId", "rgb", "mode"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            return jsonify({"error": f"{field} muss ein nicht-leerer String sein"}), 400
    if "on" in data and not isinstance(data["on"], bool):
        return jsonify({"error": "on muss ein Boolean sein"}), 400
    if "channelIndex" in data:
        value = data["channelIndex"]
        if (isinstance(value, str) and len(value) <= 10
                and value.isascii() and value.isdecimal()):
            value = int(value)
        if type(value) is not int or value < 0:
            return jsonify({"error": "channelIndex muss eine nicht-negative ganze Zahl sein"}), 400
        data["channelIndex"] = value
    for field in ("dimLevel", "temperature"):
        if field not in data:
            continue
        try:
            if isinstance(data[field], bool) or not math.isfinite(float(data[field])):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            return jsonify({"error": f"{field} muss eine endliche Zahl sein"}), 400
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_snapshot() -> Optional[Dict[str, Any]]:
    try:
        with open(state.config_internal["system_state_path"], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _devices_count_from_snapshot(snap: Optional[Dict[str, Any]]) -> int:
    if not isinstance(snap, dict):
        return 0
    devs, _ = _locate_devices_container(snap)
    if isinstance(devs, dict):
        return len(devs)
    if isinstance(devs, list):
        return sum(1 for x in devs if isinstance(x, dict))
    return 0


def _snapshot_age_ms(path: str) -> Optional[int]:
    try:
        return int((time.time() - os.path.getmtime(path)) * 1000)
    except Exception:
        return None


def _snapshot_is_valid(snapshot) -> bool:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("body"), dict):
        return False
    body = snapshot["body"]
    system = body.get("body", body)
    return isinstance(system, dict) and isinstance(system.get("devices"), (dict, list))


def _safe_redirect_target(target: Optional[str]) -> str:
    """Normalisiert Redirect-Ziele auf interne Pfade."""
    if not target:
        return "/"
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc != request.host or parsed.scheme not in {"http", "https"}:
            return "/"
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _apply_runtime_config(parsed: Dict[str, Any]) -> None:
    """Übernimmt runtime-relevante Config-Werte nach dem Speichern."""
    with state.config_lock:
        connection_fields = ("homematic_hcu", "homematic_token", "plugin_id", "ssl_verify", "ssl_cert_path")
        reconnect = any(state.config.get(key) != parsed.get(key) for key in connection_fields)
        state.config = parsed
        _lox = parsed.get("loxone") or {}
        state.LOXONE_HOST = _lox.get("miniserver_ip") or ""
        state.LOXONE_UDP_PORT = int(_lox.get("udp_port") or 7777)
        state.REQUIRE_API_KEY = bool(
            state.config_internal.get("require_api_key", parsed.get("require_api_key", True))
        )
        state.API_KEY_FILE = resolve_api_key_file(parsed, state.config_internal)
        state.API_KEY = os.getenv("BRIDGE_API_KEY") or state.config_internal.get("api_key") or parsed.get("api_key")

    if reconnect:
        with state.send_lock:
            connection = state.conn
            state.conn = None
            state.WS_READY = False
            if connection is not None:
                connection.close()

    _ensure_api_key()

    shelly_cfg = dict(parsed.get("shelly") or {})
    if state.runtime_services is not None:
        state.runtime_services.update_config()
    shelly_mod.set_credentials(shelly_cfg.get("username"), shelly_cfg.get("password"))
    registry = state.adapter_registry
    if registry is None:
        return
    adapter = registry.get("shelly")
    if adapter is not None and hasattr(adapter, "update_config"):
        adapter.update_config(shelly_cfg)
    elif shelly_cfg.get("enabled"):
        from app.adapters.shelly_adapter import ShellyAdapter
        adapter = ShellyAdapter(shelly_cfg)
        registry.register(adapter)
    if shelly_cfg.get("enabled") and adapter is not None:
        adapter.start()



# ── Auth-Routen ───────────────────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
@require_csrf
def login():
    if not state.REQUIRE_API_KEY:
        return redirect("/")
    next_url = _safe_redirect_target(request.args.get("next"))
    from app import __version__ as app_version
    if request.method == "POST":
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "1"
        pw_hash = state.config_internal.get("web_password_hash")
        if pw_hash:
            ok = check_password_hash(pw_hash, password)
        else:
            # Fallback: API-Key (kein Klartext-Passwort mehr unterstützt)
            ok = bool(state.API_KEY and password == state.API_KEY)
        if ok:
            # remember=False ⇒ Session-Cookie (verfällt bei Browser-Schluss)
            session.permanent = remember
            session["authenticated"] = True
            return redirect(next_url)
        return render_template("login.html", error=True, next_url=next_url,
                               csrf_token=generate_csrf_token(), app_version=app_version)
    return render_template("login.html", error=False, next_url=next_url,
                           csrf_token=generate_csrf_token(), app_version=app_version)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@bp.get("/lang/<code>")
def set_language(code: str):
    """Setzt das Sprach-Cookie und leitet zurück zur Quellseite."""
    from app.i18n import COOKIE_NAME, LANGUAGES
    next_url = _safe_redirect_target(request.args.get("next") or request.referrer)
    if code not in LANGUAGES:
        return redirect(next_url)
    resp = redirect(next_url)
    resp.set_cookie(COOKIE_NAME, code, max_age=365 * 24 * 3600,
                    httponly=False, samesite="Lax")
    return resp


# ── Web-Oberfläche ────────────────────────────────────────────────────────────

@bp.route("/")
@require_web_auth
def serve_dashboard():
    return render_template(
        "dashboard.html",
        **prepare_dashboard(state.config_internal["system_state_path"]),
        csrf_token=generate_csrf_token(),
    )


@bp.route("/heating")
@require_web_auth
def serve_heating():
    return render_template("heating.html", **prepare_heating(state.config_internal["system_state_path"]),
                           csrf_token=generate_csrf_token())


@bp.route("/devices/html")
@require_web_auth
def serve_html_overview():
    return render_template("devices.html", **prepare_device_overview(state.config_internal["system_state_path"]))


@bp.route("/devices/status")
@require_web_auth
def serve_device_status():
    return render_template("status.html", **prepare_device_status(state.config_internal["system_state_path"]))


@bp.route("/devices/<device_id>")
@require_web_auth
def serve_device_detail(device_id):
    return render_template("device_detail.html", **prepare_device_detail(state.config_internal["system_state_path"], device_id))


# ── API: Switch ───────────────────────────────────────────────────────────────

def _send_hmip_command(send_func, path: str, *args):
    """Sendet einen HmIP-Befehl und wartet auf die korrelierte HCU-Antwort."""
    try:
        rid = _send_tracked(send_func, path, *args, wait_for_ack=True)
    except Exception:
        log.exception("HmIP-Befehl konnte nicht gesendet werden: %s", path)
        return None, (jsonify({"error": "WebSocket-Senden fehlgeschlagen"}), 503)

    response = _wait_for_ack(rid)
    if response is None:
        return rid, (jsonify({
            "error": "HCU-Antwort nicht rechtzeitig eingetroffen",
            "request_id": rid,
        }), 504)
    code = (response.get("body") or {}).get("code")
    if code == 503:
        return rid, (jsonify({
            "error": "WebSocket-Verbindung während des Befehls unterbrochen",
            "request_id": rid,
        }), 503)
    if code != 200:
        return rid, (jsonify({
            "error": "HCU hat den Befehl abgelehnt",
            "hcu_code": code,
            "request_id": rid,
        }), 502)
    return rid, None


def _execute_control(kind):
    if state.conn is None or not state.WS_READY:
        return jsonify({"error": "HCU noch nicht bereit"}), 503
    try:
        commands = plan_control(kind, request.get_json(silent=True), _load_snapshot())
    except ControlError as error:
        return jsonify({"error": str(error)}), error.status
    results = []
    for path, payload in commands:
        rid, error = _send_hmip_command(send_hmip_request, path, path, payload)
        if error:
            response, status = error
            return jsonify({"error": response.get_json()["error"], "completed": results,
                            "failed_target": payload.get("deviceId") or payload.get("groupId"),
                            "failure": response.get_json()}), status
        results.append({"request_id": rid, "target": payload.get("deviceId") or payload.get("groupId")})
    return jsonify({"status": "acknowledged", "results": results}), 200


@bp.post("/hmipShutter")
@require_api_key
def hmip_shutter():
    return _execute_control("shutter")


@bp.post("/hmipHeating")
@require_api_key
def hmip_heating():
    return _execute_control("heating")


@bp.post("/hmipLight")
@require_api_key
def hmip_light():
    return _execute_control("light")


@bp.post("/control/<kind>")
@require_web_auth
@require_csrf
def web_control(kind):
    return _execute_control(kind)


@bp.get("/controls")
@require_web_auth
def serve_controls():
    from app.feature_views import prepare_controls
    return render_template("controls.html", **prepare_controls(), csrf_token=generate_csrf_token())


@bp.get("/security")
@require_web_auth
def serve_security():
    from app.feature_views import prepare_security
    return render_template("security.html", **prepare_security())


@bp.get("/energy")
@require_web_auth
def serve_energy():
    from app.view_helpers import _common_context
    from app.energy import EnergyCollector
    collector = state.runtime_services.energy if state.runtime_services is not None else EnergyCollector()
    return render_template("energy.html", energy_status=collector.status(), energy_summary=collector.summary(),
                           csrf_token=generate_csrf_token(), active_nav="energy",
                           **_common_context(state.config_internal.get("system_state_path", "")))


@bp.post("/energy/settings")
@require_web_auth
@require_csrf
def energy_settings():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return jsonify({"error": "enabled muss ein Boolean sein"}), 400
    if state.runtime_services is None:
        return jsonify({"error": "Hintergrunddienste nicht gestartet"}), 503
    try:
        state.runtime_services.set_energy_enabled(data["enabled"])
    except (ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(state.runtime_services.energy.status())


@bp.get("/api/energy")
@require_api_key
def energy_api():
    if state.runtime_services is None:
        return jsonify({"error": "Hintergrunddienste nicht gestartet"}), 503
    return jsonify({"status": state.runtime_services.energy.status(), "summary": state.runtime_services.energy.summary()})


def _resync_response():
    if state.runtime_services is None:
        return jsonify({"error": "Hintergrunddienste nicht gestartet"}), 503
    try:
        at = state.runtime_services.sync_snapshot()
    except (ValueError, OSError) as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"status": "sent", "sent_at": at, "delivery": "UDP is not acknowledged"})


@bp.post("/api/loxone/resync")
@require_api_key
def loxone_resync_api():
    return _resync_response()


@bp.post("/loxone/resync")
@require_web_auth
@require_csrf
def loxone_resync_web():
    return _resync_response()


@bp.get("/api/security")
@require_api_key
def security_api():
    from app.feature_views import security_data
    return jsonify(security_data(_load_snapshot()))


def _diagnostic_data():
    from app import __version__
    from app.controls import indexed, system_body
    system = system_body(_load_snapshot())
    services = state.runtime_services
    return {"version": __version__, "build_commit": os.getenv("BRIDGE_BUILD_SHA", "unknown"),
            "hcu": {"connected": state.conn is not None, "snapshot_ready": state.WS_READY,
                    "reconnects": state.WS_RECONNECTS, "last_message_at": state.WS_LAST_MESSAGE_AT},
            "counts": {"hmip_devices": len(indexed(system.get("devices"))),
                       "hmip_groups": len(indexed(system.get("groups"))),
                       "shelly_cached": len(shelly_mod.load_cached())},
            "loxone": {"configured": bool(state.LOXONE_HOST),
                       "last_snapshot_sent_at": services.last_sync_at if services else None,
                       "delivery": "UDP, no acknowledgement"},
            "energy": services.energy.status() if services else {"enabled": False},
            "note": "No configuration, tokens, passwords or raw snapshots included."}


@bp.get("/api/diagnostics")
@require_api_key
def diagnostics_api():
    return jsonify(_diagnostic_data())


@bp.get("/diagnostics")
@require_web_auth
def diagnostics_web():
    return jsonify(_diagnostic_data())


def _do_switch(device_id: Optional[str], on: Optional[bool], channel_index: Any):
    """Validiert Parameter und sendet einen Schaltbefehl."""
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503
    if not device_id or not isinstance(on, bool):
        return jsonify({"error": "Ungültige Parameter: device (str), on (bool), optional channelIndex (int)"}), 400
    try:
        channel_index = int(channel_index)
    except (TypeError, ValueError):
        return jsonify({"error": "channelIndex muss eine ganze Zahl sein"}), 400
    rid, error = _send_hmip_command(
        send_hmip_set_switch, "/hmip/device/control/setSwitchState",
        device_id, on, channel_index,
    )
    if error:
        return error
    return jsonify({"status": f"{device_id}: {'ON' if on else 'OFF'}", "request_id": rid}), 200


@bp.post("/hmipSwitch")
@require_api_key
def hmip_switch_post():
    data = request.get_json(silent=True, force=True) or {}
    return _do_switch(data.get("device"), data.get("on"), data.get("channelIndex", 0))


# ── API: Dimmer ───────────────────────────────────────────────────────────────

@bp.post("/hmipDimmer")
@require_api_key
def hmip_dimmer_post():
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503
    data = request.get_json(silent=True, force=True) or {}
    device_id     = data.get("device")
    dim_level     = data.get("dimLevel")
    channel_index = data.get("channelIndex", 1)
    if not device_id or dim_level is None:
        return jsonify({"error": "Ungültige Parameter: device (str), dimLevel (0-100), optional channelIndex (int, default 1)"}), 400
    try:
        dim_level = float(dim_level)
    except (TypeError, ValueError):
        return jsonify({"error": "dimLevel muss eine Zahl sein"}), 400
    if not 0 <= dim_level <= 100:
        return jsonify({"error": "dimLevel muss zwischen 0 und 100 liegen"}), 400
    try:
        channel_index = int(channel_index)
    except (TypeError, ValueError):
        return jsonify({"error": "channelIndex muss eine ganze Zahl sein"}), 400
    rid, error = _send_hmip_command(
        send_hmip_set_dim_level, "/hmip/device/control/setDimLevel",
        device_id, round(dim_level / 100.0, 2), channel_index,
    )
    if error:
        return error
    return jsonify({"status": f"{device_id}: dimLevel={dim_level}%", "request_id": rid}), 200


# ── API: RGB ──────────────────────────────────────────────────────────────────

@bp.post("/hmipRGB")
@require_api_key
def hmip_rgb_post():
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503
    data = request.get_json(silent=True, force=True) or {}
    device_id     = data.get("device")
    rgb_str       = data.get("rgb")
    channel_index = data.get("channelIndex", 1)
    if not device_id or not rgb_str:
        return jsonify({"error": "Ungültige Parameter: device (str), rgb (str, z.B. 'R=50%,G=30%,B=100%'), optional channelIndex (int, default 1)"}), 400
    try:
        channel_index = int(channel_index)
    except (TypeError, ValueError):
        return jsonify({"error": "channelIndex muss eine ganze Zahl sein"}), 400
    try:
        parts = {}
        for part in rgb_str.replace(" ", "").split(","):
            key, val = part.split("=")
            parts[key.upper()] = float(val.rstrip("%")) / 100.0
        if set(parts) != {"R", "G", "B"} or any(
            not math.isfinite(value) or not 0 <= value <= 1 for value in parts.values()
        ):
            raise ValueError("RGB components must be finite percentages in 0..100")
        r = max(0.0, min(1.0, parts.get("R", 0.0)))
        g = max(0.0, min(1.0, parts.get("G", 0.0)))
        b = max(0.0, min(1.0, parts.get("B", 0.0)))
    except Exception:
        return jsonify({"error": f"Ungültiges RGB-Format. Erwartet: 'R=50%,G=30%,B=100%', erhalten: '{rgb_str}'"}), 400
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue, saturation, dim_level = round(h * 360) % 360, round(s, 3), round(v, 3)
    if r == 0.0 and g == 0.0 and b == 0.0:
        rid, error = _send_hmip_command(
            send_hmip_set_switch, "/hmip/device/control/setSwitchState",
            device_id, False, channel_index,
        )
        if error:
            return error
        return jsonify({"status": f"{device_id}: off", "request_id": rid}), 200
    rid, error = _send_hmip_command(
        send_hmip_set_hue_saturation_dim_level,
        "/hmip/device/control/setHueSaturationDimLevel",
        device_id, hue, saturation, dim_level, channel_index,
    )
    if error:
        return error
    return jsonify({"status": f"{device_id}: hue={hue}° sat={saturation} dim={dim_level}", "request_id": rid}), 200


# ── API: Alarm (Rauchmelder als Sirene) ───────────────────────────────────────

def _find_alarm_switching_group(snap: Dict[str, Any]) -> Optional[str]:
    """Ermittelt die von der HCU angelegte ALARM_SWITCHING-Gruppe."""
    outer_body = snap.get("body") or {}
    nested_body = outer_body.get("body") if isinstance(outer_body, dict) else None
    system = nested_body if isinstance(nested_body, dict) else outer_body
    if not isinstance(system, dict):
        return None

    home = system.get("home") or {}
    functional_homes = home.get("functionalHomes") or {}
    if isinstance(functional_homes, dict):
        for functional_home in functional_homes.values():
            if not isinstance(functional_home, dict):
                continue
            security_groups = functional_home.get("securitySwitchingGroups") or {}
            siren_group = security_groups.get("SIREN") if isinstance(security_groups, dict) else None
            if isinstance(siren_group, str) and siren_group:
                return siren_group

    groups = system.get("groups") or {}
    if isinstance(groups, dict):
        candidates = groups.values()
    elif isinstance(groups, list):
        candidates = groups
    else:
        candidates = []
    for group in candidates:
        if isinstance(group, dict) and group.get("type") == "ALARM_SWITCHING":
            group_id = group.get("id")
            if isinstance(group_id, str) and group_id:
                return group_id
    return None


@bp.post("/hmipAlarm")
@require_api_key
def hmip_alarm_post():
    """Schaltet die dokumentierte ALARM_SWITCHING-Gruppe der HCU."""
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    data = request.get_json(silent=True, force=True) or {}
    on = data.get("on")
    if not isinstance(on, bool):
        mode = str(data.get("mode", "")).lower()
        if mode in {"on", "optical", "acoustic", "both", "test"}:
            on = True
        elif mode in {"off", "clear"}:
            on = False
        else:
            return jsonify({"error": "Pflichtfeld: on (bool) oder mode (on/off)"}), 400

    group_id = data.get("group") or data.get("groupId")
    if group_id is not None and (not isinstance(group_id, str) or not group_id.strip()):
        return jsonify({"error": "group muss eine nicht-leere String-ID sein"}), 400
    if not group_id:
        snapshot = _load_snapshot()
        group_id = _find_alarm_switching_group(snapshot) if snapshot else None
    if not group_id:
        return jsonify({"error": "Keine ALARM_SWITCHING-Gruppe im Snapshot gefunden"}), 404

    rid, error = _send_hmip_command(
        send_hmip_set_group_state,
        "/hmip/group/switching/setState",
        group_id, on,
    )
    if error:
        return error
    return jsonify({
        "status": "on" if on else "off",
        "group": group_id,
        "request_id": rid,
        "info": "Signalart und Laufzeit entsprechen der HCU-Gruppenkonfiguration",
    }), 200


# ── API: Thermostat ────────────────────────────────────────────────────────────

@bp.post("/hmipThermostat")
@require_api_key
def hmip_thermostat_post():
    """Solltemperatur einer Homematic-IP-Heizgruppe setzen.

    Body:
      group         (str)   – ID einer HEATING-Gruppe
      temperature   (float) – Zieltemperatur in °C
    """
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    data = request.get_json(silent=True, force=True) or {}
    group_id = data.get("group")
    temperature = data.get("temperature")

    if not isinstance(group_id, str) or not group_id.strip() or temperature is None:
        return jsonify({"error": "Pflichtfelder: group (str), temperature (float)"}), 400
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        return jsonify({"error": "temperature muss eine Zahl sein"}), 400
    if not math.isfinite(temperature):
        return jsonify({"error": "temperature muss endlich sein"}), 400

    rid, error = _send_hmip_command(
        send_hmip_set_point_temperature,
        "/hmip/group/heating/setSetPointTemperature",
        group_id, temperature,
    )
    if error:
        return error
    return jsonify({"status": f"{group_id}: setpoint={temperature:.1f}°C", "request_id": rid}), 200


# ── API: Bewässerung ───────────────────────────────────────────────────────────

@bp.post("/hmipIrrigation")
@require_api_key
def hmip_irrigation_post():
    """Bewässerungsaktor schalten (HMIP-WHS2 o.ä.).

    Body:
      device        (str)  – Device-ID
      on            (bool) – true = Ventil öffnen, false = schließen
      channelIndex  (int)  – optional, Standard: 1
    """
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503

    data = request.get_json(silent=True, force=True) or {}
    device_id     = data.get("device")
    on            = data.get("on")
    channel_index = data.get("channelIndex", 1)

    if not device_id or not isinstance(on, bool):
        return jsonify({"error": "Pflichtfelder: device (str), on (bool)"}), 400
    try:
        ch_idx = int(channel_index)
    except (TypeError, ValueError):
        return jsonify({"error": "channelIndex muss eine ganze Zahl sein"}), 400

    snapshot = _load_snapshot()
    device = None
    if isinstance(snapshot, dict):
        devices, _ = _locate_devices_container(snapshot)
        if isinstance(devices, dict):
            device = devices.get(device_id)
        elif isinstance(devices, list):
            device, _ = _find_device_in_list(devices, device_id)
    channel = (device.get("functionalChannels") or {}).get(str(ch_idx), {}) if isinstance(device, dict) else {}
    watering = isinstance(channel, dict) and (
        "wateringActive" in channel or "WATERING" in str(channel.get("functionalChannelType", ""))
    )
    send_func = send_hmip_set_watering_switch if watering else send_hmip_set_switch
    command_path = "/hmip/device/control/setWateringSwitchState" if watering else "/hmip/device/control/setSwitchState"
    rid, error = _send_hmip_command(
        send_func, command_path,
        device_id, on, ch_idx,
    )
    if error:
        return error
    return jsonify({
        "status": f"{device_id}: {'geöffnet' if on else 'geschlossen'}",
        "request_id": rid,
    }), 200


# ── API: State ────────────────────────────────────────────────────────────────

@bp.get("/hmipState")
@require_api_key
def hmip_state_get():
    device_id = request.args.get("device")
    if not device_id:
        return jsonify({"error": "Parameter 'device' fehlt"}), 400
    try:
        channel_index = str(int(request.args.get("channelIndex", "0")))
    except ValueError:
        return jsonify({"error": "channelIndex muss eine Zahl sein"}), 400
    snap = _load_snapshot()
    if snap is None:
        return jsonify({"error": "Kein Snapshot vorhanden"}), 503
    devices_container, _ = _locate_devices_container(snap)
    if devices_container is None:
        return jsonify({"error": "Devices nicht im Snapshot gefunden"}), 503
    if isinstance(devices_container, dict):
        device = devices_container.get(device_id)
    else:
        device, _ = _find_device_in_list(devices_container, device_id)
    if device is None:
        return jsonify({"error": f"Device {device_id} nicht gefunden"}), 404
    channel = device.get("functionalChannels", {}).get(channel_index)
    if channel is None:
        return jsonify({"error": f"Channel {channel_index} nicht gefunden"}), 404
    return jsonify(channel), 200


# ── Web-UI: Alarm Test/Clear ──────────────────────────────────────────────────

# ── Shelly / Config-Editor ───────────────────────────────────────────────────

_CONFIG_PATH = "config/config.yaml"
_CONFIG_SAMPLE_PATH = "config/config_sample.yaml"


@bp.route("/config", methods=["GET", "POST"])
@require_web_auth
@require_csrf
def serve_config():
    error = None
    success = False
    if request.method == "POST":
        raw = request.form.get("content", "")
        try:
            parsed = yaml.safe_load(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Ungültiges YAML – muss ein Mapping sein")
            cfg_errors = validate_config(parsed)
            if cfg_errors:
                raise ValueError("Validierung fehlgeschlagen:\n• " + "\n• ".join(cfg_errors))
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(raw)
            _apply_runtime_config(parsed)
            success = True
        except Exception as e:
            error = str(e)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    return render_template("config.html", content=content, error=error, success=success,
                           csrf_token=generate_csrf_token(), active_nav="config")


@bp.post("/config/validate")
@require_web_auth
@require_csrf
def validate_config_endpoint():
    """Validiert YAML ohne zu speichern. Liefert {ok, errors, lines, bytes}."""
    raw = request.get_json(silent=True, force=True) or {}
    content = raw.get("content", "")
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            return jsonify({"ok": False, "errors": ["Ungültiges YAML – muss ein Mapping sein"]}), 200
        errors = validate_config(parsed)
        return jsonify({
            "ok": not errors,
            "errors": errors,
            "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
            "bytes": len(content.encode("utf-8")),
        }), 200
    except yaml.YAMLError as e:
        return jsonify({"ok": False, "errors": [f"YAML-Syntaxfehler: {e}"]}), 200


@bp.get("/config/sample")
@require_web_auth
def serve_config_sample():
    """Gibt den Inhalt der Beispiel-Konfiguration zurück."""
    try:
        with open(_CONFIG_SAMPLE_PATH, "r", encoding="utf-8") as f:
            return jsonify({"content": f.read()}), 200
    except FileNotFoundError:
        return jsonify({"error": "config_sample.yaml nicht gefunden"}), 404


@bp.route("/shelly")
@require_web_auth
def serve_shelly():
    return render_template("shelly.html", **prepare_shelly(), csrf_token=generate_csrf_token())


@bp.post("/shelly/scan")
@require_web_auth
@require_csrf
def shelly_scan():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("replace_cache", False), bool):
        return jsonify({"error": "replace_cache muss ein Boolean in einem JSON-Objekt sein"}), 400
    cfg = state.config.get("shelly", {})
    if not cfg.get("enabled", False):
        return jsonify({"error": "Shelly-Scanner ist in der config.yaml nicht aktiviert"}), 400
    subnet = cfg.get("subnet", "")
    if not subnet:
        return jsonify({"error": "Kein subnet in config.yaml konfiguriert"}), 400
    timeout = float(cfg.get("timeout_sec", 1.5))
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    started = shelly_mod.start_scan(
        subnet, timeout_sec=timeout, replace_cache=data.get("replace_cache", False),
    )
    if not started:
        return jsonify({"status": "already_running"}), 202
    return jsonify({"status": "started", "subnet": subnet}), 202


@bp.get("/shelly/status")
@require_web_auth
def shelly_scan_status():
    return jsonify(shelly_mod.scan_status()), 200


@bp.get("/shelly/devices")
@require_web_auth
def shelly_devices():
    return jsonify(shelly_mod.load_cached()), 200


@bp.post("/shelly/<ip>/delete")
@require_web_auth
@require_csrf
def shelly_delete(ip: str):
    try:
        address = ipaddress.ip_address(ip)
        if address.version != 4:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Ungültige Shelly-IPv4-Adresse"}), 400
    try:
        removed = shelly_mod.delete_cached_device(str(address))
    except shelly_mod.ScanInProgressError:
        return jsonify({"error": "Bitte den laufenden Scan abwarten"}), 409
    if not removed:
        return jsonify({"error": "Gerät nicht im Cache gefunden"}), 404
    return jsonify({"success": True, "ip": str(address)}), 200


@bp.post("/shelly/refresh-status")
@require_web_auth
@require_csrf
def shelly_refresh_status():
    cfg = state.config.get("shelly", {})
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    count = shelly_mod.refresh_all_devices()
    return jsonify({"updated": count}), 200


@bp.post("/shelly/check-updates")
@require_web_auth
@require_csrf
def shelly_check_updates():
    shelly_mod.check_updates_all()
    return jsonify({"status": "triggered"}), 200


@bp.post("/shelly/<ip>/update")
@require_web_auth
@require_csrf
def shelly_update(ip: str):
    cached = {d["ip"]: d for d in shelly_mod.load_cached()}
    device = cached.get(ip)
    if not device:
        return jsonify({"error": f"Gerät {ip} nicht im Cache"}), 404
    ok = shelly_mod.trigger_update(ip, device.get("gen", 1))
    return jsonify({"success": ok, "ip": ip}), 200 if ok else 502


@bp.post("/shelly/<ip>/relay/<int:channel>")
@require_web_auth
@require_csrf
def shelly_relay(ip: str, channel: int):
    return _shelly_relay(ip, channel)


@bp.post("/api/shelly/<ip>/relay/<int:channel>")
@require_api_key
def shelly_relay_api(ip: str, channel: int):
    """External HTTP control; browser endpoint remains session/CSRF protected."""
    return _shelly_relay(ip, channel)


def _shelly_relay(ip: str, channel: int):
    data = request.get_json(silent=True, force=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Body muss ein JSON-Objekt sein"}), 400
    on = data.get("on")
    if not isinstance(on, bool):
        return jsonify({"error": "Parameter 'on' (bool) fehlt"}), 400
    # Gen aus Cache ermitteln
    cached = {d["ip"]: d for d in shelly_mod.load_cached()}
    device = cached.get(ip)
    if not device:
        return jsonify({"error": f"Gerät {ip} nicht im Cache – zuerst scannen"}), 404
    if str(channel) not in device.get("channels", {}):
        return jsonify({"error": "Kanal nicht im Geräte-Cache gefunden"}), 404
    gen = device.get("gen", 1)
    cfg = state.config.get("shelly", {})
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    ok = shelly_mod.set_relay(ip, gen, channel, on)
    if ok:
        shelly_mod.refresh_device(ip, gen)
    return jsonify({"success": ok, "ip": ip, "channel": channel, "on": on}), 200 if ok else 502


@bp.get("/shelly/<ip>/webui", defaults={"subpath": ""}, strict_slashes=False)
@bp.get("/shelly/<ip>/webui/<path:subpath>")
@require_web_auth
def shelly_webui_proxy(ip: str, subpath: str):
    """Leitet zur separaten Geräte-Origin weiter, damit Gerätecode isoliert bleibt."""
    return shelly_proxy.redirect_to_device(ip, subpath)


# ── Health ────────────────────────────────────────────────────────────────────

@bp.route("/healthz")
def healthz():
    ws_connected = state.conn is not None
    path         = state.config_internal["system_state_path"]
    age_ms       = _snapshot_age_ms(path)
    snap         = _load_snapshot()
    devices_count = _devices_count_from_snapshot(snap)
    with state.pending_lock:
        pending_count = sum(
            1 for meta in state.pending.values() if meta.get("response") is None
        )

    adapter_health = (
        state.adapter_registry.health() if state.adapter_registry is not None else {}
    )

    if not ws_connected:
        status = "degraded"
    elif not state.WS_READY or not _snapshot_is_valid(snap):
        status = "degraded"
    elif any(not healthy for healthy in adapter_health.values()):
        status = "degraded"
    else:
        status = "ok"

    return jsonify({
        "ws_connected":    ws_connected,
        "snapshot_ready": state.WS_READY,
        "snapshot_age_ms": age_ms,
        "devices_count":   devices_count,
        "pending_requests": pending_count,
        "adapters":         adapter_health,
        "ws_connected_at":  state.WS_CONNECTED_AT,
        "ws_last_message_at": state.WS_LAST_MESSAGE_AT,
        "ws_last_error":    state.WS_LAST_ERROR,
        "ws_reconnects":    state.WS_RECONNECTS,
        "status":          status,
    }), 200 if status == "ok" else 503
