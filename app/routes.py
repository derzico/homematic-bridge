# SPDX-License-Identifier: Apache-2.0
# routes.py – Flask-Blueprint mit allen HTTP-Routen

import colorsys
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash

import app.state as state
from app.auth import generate_csrf_token, require_api_key, require_csrf, require_web_auth
from app.view_helpers import (prepare_dashboard, prepare_device_detail,
                               prepare_device_overview, prepare_device_status,
                               prepare_heating, prepare_shelly)
from app.adapters.hmip_messages import (send_hmip_set_dim_level, send_hmip_set_hue_saturation_dim_level,
                                        send_hmip_set_switch)
from app.adapters.hmip_websocket import _register_pending
from app.utils import _find_device_in_list, _locate_devices_container

bp = Blueprint("bridge", __name__)
log = logging.getLogger("bridge-ws")


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



# ── Auth-Routen ───────────────────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
@require_csrf
def login():
    if not state.REQUIRE_API_KEY:
        return redirect("/")
    next_url = request.args.get("next") or "/"
    if request.method == "POST":
        password = request.form.get("password", "")
        pw_hash = state.config_internal.get("web_password_hash")
        if pw_hash:
            ok = check_password_hash(pw_hash, password)
        else:
            # Fallback: Klartext-Passwort oder API-Key
            expected = state.config_internal.get("web_password") or state.API_KEY
            ok = bool(expected and password == expected)
        if ok:
            session.permanent = True
            session["authenticated"] = True
            return redirect(next_url)
        return render_template("login.html", error=True, next_url=next_url, csrf_token=generate_csrf_token())
    return render_template("login.html", error=False, next_url=next_url, csrf_token=generate_csrf_token())


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── Web-Oberfläche ────────────────────────────────────────────────────────────

@bp.route("/")
@require_web_auth
def serve_dashboard():
    return render_template("dashboard.html", **prepare_dashboard(state.config_internal["system_state_path"]))


@bp.route("/heating")
@require_web_auth
def serve_heating():
    return render_template("heating.html", **prepare_heating(state.config_internal["system_state_path"]))


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

@bp.post("/hmipSwitch")
@require_api_key
def hmip_switch_post():
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503
    data = request.get_json(silent=True, force=True) or {}
    device_id     = data.get("device")
    on            = data.get("on")
    channel_index = data.get("channelIndex", 0)
    if not device_id or not isinstance(on, bool):
        return jsonify({"error": "Ungültige Parameter: device (str), on (bool), optional channelIndex (int)"}), 400
    with state.send_lock:
        rid = send_hmip_set_switch(state.conn, device_id, on, channel_index)
    _register_pending(rid, "/hmip/device/control/setSwitchState")
    return jsonify({"status": f"{device_id}: {'ON' if on else 'OFF'}", "request_id": rid}), 200


@bp.get("/hmipSwitch")
def hmip_switch_get():
    local = request.remote_addr in {"127.0.0.1", "::1"}
    if not local:
        if not state.REQUIRE_API_KEY or not state.API_KEY:
            return jsonify({"error": "Nur lokal erlaubt oder X-API-Key erforderlich"}), 403
        if request.headers.get("X-API-Key") != state.API_KEY:
            return jsonify({"error": "unauthorized"}), 401
    if state.conn is None:
        return jsonify({"error": "WebSocket nicht verbunden"}), 503
    device_id = request.args.get("device")
    on_param  = request.args.get("on")
    try:
        channel_index = int(request.args.get("channelIndex", "0"))
    except ValueError:
        return jsonify({"error": "channelIndex muss eine Zahl sein"}), 400
    if not device_id or on_param not in {"true", "false"}:
        return jsonify({"error": "Ungültige Parameter"}), 400
    on = on_param == "true"
    with state.send_lock:
        rid = send_hmip_set_switch(state.conn, device_id, on, channel_index)
    _register_pending(rid, "/hmip/device/control/setSwitchState")
    return jsonify({"status": f"{device_id}: {'ON' if on else 'OFF'}", "request_id": rid}), 200


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
    with state.send_lock:
        rid = send_hmip_set_dim_level(state.conn, device_id, round(dim_level / 100.0, 2), channel_index)
    _register_pending(rid, "/hmip/device/control/setDimLevel")
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
        parts = {}
        for part in rgb_str.replace(" ", "").split(","):
            key, val = part.split("=")
            parts[key.upper()] = float(val.rstrip("%")) / 100.0
        r = max(0.0, min(1.0, parts.get("R", 0.0)))
        g = max(0.0, min(1.0, parts.get("G", 0.0)))
        b = max(0.0, min(1.0, parts.get("B", 0.0)))
    except Exception:
        return jsonify({"error": f"Ungültiges RGB-Format. Erwartet: 'R=50%,G=30%,B=100%', erhalten: '{rgb_str}'"}), 400
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue, saturation, dim_level = round(h * 360), round(s, 3), round(v, 3)
    with state.send_lock:
        if r == 0.0 and g == 0.0 and b == 0.0:
            rid = send_hmip_set_switch(state.conn, device_id, False, channel_index)
            _register_pending(rid, "/hmip/device/control/setSwitchState")
            return jsonify({"status": f"{device_id}: off", "request_id": rid}), 200
        rid = send_hmip_set_hue_saturation_dim_level(state.conn, device_id, hue, saturation, dim_level, channel_index)
    _register_pending(rid, "/hmip/device/control/setHueSaturationDimLevel")
    return jsonify({"status": f"{device_id}: hue={hue}° sat={saturation} dim={dim_level}", "request_id": rid}), 200


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


# ── Shelly ───────────────────────────────────────────────────────────────────

import yaml
import app.adapters.shelly_adapter as shelly_mod
from config.loader import validate_config

_CONFIG_PATH = "config/config.yaml"


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
            # State neu laden (unter Lock, da andere Threads config lesen)
            with state.config_lock:
                state.config = parsed
                _lox = parsed.get("loxone") or {}
                state.LOXONE_HOST = _lox.get("miniserver_ip") or ""
                state.LOXONE_UDP_PORT = int(_lox.get("udp_port") or 7777)
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

@bp.route("/shelly")
@require_web_auth
def serve_shelly():
    return render_template("shelly.html", **prepare_shelly())


@bp.post("/shelly/scan")
@require_web_auth
def shelly_scan():
    cfg = state.config.get("shelly", {})
    if not cfg.get("enabled", False):
        return jsonify({"error": "Shelly-Scanner ist in der config.yaml nicht aktiviert"}), 400
    subnet = cfg.get("subnet", "")
    if not subnet:
        return jsonify({"error": "Kein subnet in config.yaml konfiguriert"}), 400
    timeout = float(cfg.get("timeout_sec", 1.5))
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    started = shelly_mod.start_scan(subnet, timeout_sec=timeout)
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


@bp.post("/shelly/refresh-status")
@require_web_auth
def shelly_refresh_status():
    cfg = state.config.get("shelly", {})
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    count = shelly_mod.refresh_all_devices()
    return jsonify({"updated": count}), 200


@bp.post("/shelly/check-updates")
@require_web_auth
def shelly_check_updates():
    shelly_mod.check_updates_all()
    return jsonify({"status": "triggered"}), 200


@bp.post("/shelly/<ip>/update")
@require_web_auth
def shelly_update(ip: str):
    cached = {d["ip"]: d for d in shelly_mod.load_cached()}
    device = cached.get(ip)
    if not device:
        return jsonify({"error": f"Gerät {ip} nicht im Cache"}), 404
    ok = shelly_mod.trigger_update(ip, device.get("gen", 1))
    return jsonify({"success": ok, "ip": ip}), 200 if ok else 502


@bp.post("/shelly/<ip>/relay/<int:channel>")
@require_web_auth
def shelly_relay(ip: str, channel: int):
    data = request.get_json(silent=True, force=True) or {}
    on = data.get("on")
    if not isinstance(on, bool):
        return jsonify({"error": "Parameter 'on' (bool) fehlt"}), 400
    # Gen aus Cache ermitteln
    cached = {d["ip"]: d for d in shelly_mod.load_cached()}
    device = cached.get(ip)
    if not device:
        return jsonify({"error": f"Gerät {ip} nicht im Cache – zuerst scannen"}), 404
    gen = device.get("gen", 1)
    cfg = state.config.get("shelly", {})
    shelly_mod.set_credentials(cfg.get("username"), cfg.get("password"))
    ok = shelly_mod.set_relay(ip, gen, channel, on)
    if ok:
        shelly_mod.refresh_device(ip, gen)
    return jsonify({"success": ok, "ip": ip, "channel": channel, "on": on}), 200 if ok else 502


@bp.route("/shelly/<ip>/webui", defaults={"subpath": ""}, methods=["GET", "POST"])
@bp.route("/shelly/<ip>/webui/<path:subpath>", methods=["GET", "POST"])
@require_web_auth
def shelly_webui_proxy(ip: str, subpath: str):
    """Proxied Shelly web-UI – forwards requests with credentials so the user
    is automatically logged in without having to enter the password manually."""
    import re
    import requests as _req

    target = f"http://{ip}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", errors="replace")

    creds = shelly_mod._credentials
    auth = creds if (creds and creds[0]) else None

    try:
        if request.method == "POST":
            r = _req.post(
                target, auth=auth,
                data=request.get_data(),
                headers={"Content-Type": request.content_type or "application/x-www-form-urlencoded"},
                timeout=10, allow_redirects=False,
            )
        else:
            r = _req.get(target, auth=auth, timeout=10, allow_redirects=False)
    except Exception as exc:
        return f"<h3 style='font-family:sans-serif;padding:24px'>Shelly nicht erreichbar: {exc}</h3>", 502

    # Redirect → rewrite Location so it stays within the proxy
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "/")
        if loc.startswith("/") and not loc.startswith("//"):
            loc = f"/shelly/{ip}/webui{loc}"
        return redirect(loc, r.status_code)

    content_type = r.headers.get("Content-Type", "application/octet-stream")
    proxy_base = f"/shelly/{ip}/webui"

    if "text/html" in content_type:
        # JS interceptor: rewrites absolute-path XHR / fetch calls through our proxy
        interceptor = (
            "<script>"
            "(function(){"
            f"var B='{proxy_base}';"
            "var oX=XMLHttpRequest.prototype.open;"
            "XMLHttpRequest.prototype.open=function(m,u){"
            "if(typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/')u=B+u;"
            "return oX.apply(this,arguments);};"
            "if(window.fetch){var oF=window.fetch;window.fetch=function(u,o){"
            "if(typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/')u=B+u;"
            "return oF.call(window,u,o);};"
            "}"
            "})();"
            "</script>"
        )
        html_text = r.text

        # Rewrite absolute paths in src / href / action attributes
        def _rewrite(m: re.Match) -> str:
            attr, path = m.group(1), m.group(2)
            if path.startswith("/") and not path.startswith("//"):
                return f'{attr}="{proxy_base}{path}"'
            return m.group(0)

        html_text = re.sub(r'(src|href|action)="(/[^"]*)"', _rewrite, html_text)

        # Inject base href (helps with truly relative URLs like "js/app.js")
        base_tag = f'<base href="{proxy_base}/">'
        if "<head>" in html_text:
            html_text = html_text.replace("<head>", f"<head>{base_tag}{interceptor}", 1)
        else:
            html_text = base_tag + interceptor + html_text

        return html_text, r.status_code, {"Content-Type": "text/html; charset=utf-8"}

    # All other content (JS, CSS, images, JSON) – pass through as-is
    return r.content, r.status_code, {"Content-Type": content_type}


# ── Health ────────────────────────────────────────────────────────────────────

@bp.route("/healthz")
def healthz():
    ws_connected = state.conn is not None
    path         = state.config_internal["system_state_path"]
    age_ms       = _snapshot_age_ms(path)
    snap         = _load_snapshot()
    devices_count = _devices_count_from_snapshot(snap)
    with state.pending_lock:
        pending_count = len(state.pending)

    if not ws_connected:
        status = "degraded"
    elif age_ms is None or age_ms > state.STALE_SEC * 1000:
        status = "degraded"
    else:
        status = "ok"

    return jsonify({
        "ws_connected":    ws_connected,
        "snapshot_age_ms": age_ms,
        "devices_count":   devices_count,
        "pending_requests": pending_count,
        "status":          status,
    }), 200
