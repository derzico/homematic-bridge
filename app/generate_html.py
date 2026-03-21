# SPDX-License-Identifier: Apache-2.0

# app/generate_html.py
import json
from typing import Any, Dict, Iterable, Tuple, Optional
import html

# ── Shared CSS ──────────────────────────────────────────────────────────────
_CSS = """
:root {
  --bg:          #0d1117;
  --surface:     #161b22;
  --surface2:    #21262d;
  --border:      #30363d;
  --text:        #e6edf3;
  --muted:       #8b949e;
  --accent:      #58a6ff;
  --accent-dim:  #1f3d6e;
  --green:       #3fb950;
  --red:         #f85149;
  --yellow:      #e3b341;
  --mono: 'JetBrains Mono','Fira Code','Consolas',monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui,-apple-system,sans-serif; font-size: 14px; min-height: 100vh; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Nav */
nav {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0 24px; height: 52px; display: flex; align-items: center; gap: 20px;
  position: sticky; top: 0; z-index: 100;
}
.brand { font-weight: 600; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.brand em { color: var(--accent); font-style: normal; }
.nav-links { display: flex; gap: 2px; }
.nav-links a { color: var(--muted); padding: 6px 12px; border-radius: 6px; font-size: 13px; transition: color .15s, background .15s; }
.nav-links a:hover, .nav-links a.active { color: var(--text); background: var(--surface2); text-decoration: none; }
.spacer { flex: 1; }
.nav-badge { font-size: 11px; background: var(--surface2); border: 1px solid var(--border); color: var(--muted); padding: 2px 8px; border-radius: 20px; }

/* Layout */
.container { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }
.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 20px; font-weight: 600; }
.page-header .sub { color: var(--muted); margin-top: 6px; font-size: 13px; }

/* Search */
.search-wrap { margin-bottom: 16px; }
.search-wrap input {
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 8px 14px; border-radius: 6px; width: 320px; font-size: 13px; outline: none;
}
.search-wrap input:focus { border-color: var(--accent); }
.search-wrap input::placeholder { color: var(--muted); }

/* Table */
.data-table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.data-table th { background: var(--surface2); color: var(--muted); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; padding: 10px 16px; text-align: left; border-bottom: 1px solid var(--border); }
.data-table td { padding: 10px 16px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--surface2); }
.mono { font-family: var(--mono); font-size: 12px; color: var(--muted); }
.label-cell { font-weight: 500; }
.type-pill { background: var(--accent-dim); color: var(--accent); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-family: var(--mono); white-space: nowrap; }

/* Breadcrumb */
.breadcrumb { font-size: 13px; color: var(--muted); margin-bottom: 24px; }
.breadcrumb a { color: var(--muted); }
.breadcrumb a:hover { color: var(--accent); text-decoration: none; }
.breadcrumb .sep { margin: 0 6px; opacity: .5; }

/* Meta grid */
.meta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; margin-bottom: 24px; }
.meta-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; }
.meta-card .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }
.meta-card .v { font-family: var(--mono); font-size: 13px; font-weight: 500; word-break: break-all; }

/* Collapsible cards */
details { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
details + details { }
summary {
  padding: 12px 20px; cursor: pointer; list-style: none; display: flex; align-items: center; gap: 12px;
  user-select: none; font-size: 13px; font-weight: 600;
}
summary::-webkit-details-marker { display: none; }
summary:hover { background: var(--surface2); }
.summary-title { flex: 1; }
.summary-sub { font-family: var(--mono); font-size: 11px; color: var(--muted); font-weight: 400; }
.chevron { color: var(--muted); font-size: 11px; transition: transform .2s; }
details[open] .chevron { transform: rotate(180deg); }

/* KV table inside details */
.kv-table { width: 100%; border-collapse: collapse; border-top: 1px solid var(--border); }
.kv-table tr:not(:last-child) td, .kv-table tr:not(:last-child) th { border-bottom: 1px solid var(--border); }
.kv-table th { padding: 7px 20px; color: var(--muted); font-weight: 400; font-size: 12px; font-family: var(--mono); width: 240px; white-space: nowrap; background: transparent; text-align: left; }
.kv-table td { padding: 7px 20px; font-size: 12px; font-family: var(--mono); word-break: break-all; }
.v-true  { color: var(--green); }
.v-false { color: var(--red); }
.v-num   { color: var(--yellow); }
.v-null  { color: var(--muted); font-style: italic; }

/* Raw JSON */
pre.json-raw { padding: 20px; background: var(--bg); font-family: var(--mono); font-size: 12px; overflow-x: auto; line-height: 1.6; border-top: 1px solid var(--border); }

/* Status page */
.status-pill { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }
.pill-ok      { background: #1a3a1a; color: var(--green); }
.pill-warn    { background: #3a2a00; color: var(--yellow); }
.pill-error   { background: #3a1010; color: var(--red); }
.pill-muted   { background: var(--surface2); color: var(--muted); }
.rssi-bar { display: inline-flex; gap: 2px; align-items: flex-end; height: 14px; }
.rssi-bar span { width: 4px; border-radius: 1px; background: var(--border); }
.rssi-bar span.lit { background: var(--green); }
.rssi-bar.warn span.lit { background: var(--yellow); }
.rssi-bar.bad  span.lit { background: var(--red); }
.row-warn td { background: #1a1500 !important; }
.row-error td { background: #1a0a0a !important; }

/* Dashboard */
.dash-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
.dash-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px 24px; }
.dash-card.wide { grid-column: span 2; }
.dash-card h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); font-weight: 600; margin-bottom: 14px; }
.dash-card .big-val { font-size: 36px; font-weight: 300; line-height: 1; margin-bottom: 4px; }
.dash-card .big-val em { font-size: 18px; font-style: normal; color: var(--muted); }
.dash-card .sub-val { font-size: 12px; color: var(--muted); margin-top: 6px; }
.weather-icon { font-size: 48px; line-height: 1; margin-bottom: 8px; }
.dash-kv { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.dash-kv:last-child { border-bottom: none; }
.dash-kv .dk { color: var(--muted); font-size: 12px; }
.dash-kv .dv { font-family: var(--mono); font-size: 12px; }
.progress-bar { background: var(--surface2); border-radius: 4px; height: 6px; margin-top: 8px; overflow: hidden; }
.progress-bar .fill { height: 100%; border-radius: 4px; background: var(--green); transition: width .3s; }
.progress-bar .fill.warn { background: var(--yellow); }
.progress-bar .fill.bad  { background: var(--red); }
.heating-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.heat-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
.heat-card .room { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.heat-card .temps { display: flex; align-items: baseline; gap: 6px; }
.heat-card .actual { font-size: 22px; font-weight: 300; }
.heat-card .setpoint { font-size: 12px; color: var(--muted); }
.heat-card .valve { font-size: 11px; color: var(--muted); margin-top: 4px; }
.heat-card .mode-pill { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--surface); color: var(--muted); border: 1px solid var(--border); margin-top: 6px; display: inline-block; }
.alarm-active { border-color: var(--red) !important; }
.alarm-ok     { border-color: var(--green) !important; }

/* Login page */
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 40px 48px; width: 100%; max-width: 380px; }
.login-card .logo { text-align: center; font-size: 22px; font-weight: 600; margin-bottom: 32px; }
.login-card .logo em { color: var(--accent); font-style: normal; }
.login-field { margin-bottom: 16px; }
.login-field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .5px; }
.login-field input { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 6px; font-size: 14px; outline: none; }
.login-field input:focus { border-color: var(--accent); }
.login-btn { width: 100%; background: var(--accent); color: #0d1117; border: none; padding: 11px; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; margin-top: 8px; }
.login-btn:hover { opacity: .9; }
.login-error { background: #3a1010; border: 1px solid var(--red); color: var(--red); padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }
"""

_JS_SEARCH = """
document.getElementById('search').addEventListener('input', function() {
  const q = this.value.toLowerCase();
  document.querySelectorAll('tbody tr').forEach(r => {
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
});
"""

# ── Helpers: JSON-Navigation ─────────────────────────────────────────────────
def _build_room_map(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Gibt {device_id: room_label} zurück, basierend auf META-Gruppen."""
    room_map: Dict[str, str] = {}
    candidates = [
        ("body", "groups"),
        ("body", "body", "groups"),
    ]
    groups = None
    for path in candidates:
        groups = _get_nested(snapshot, path)
        if isinstance(groups, dict):
            break
    if not isinstance(groups, dict):
        return room_map
    for g in groups.values():
        if not isinstance(g, dict) or g.get("type") != "META":
            continue
        room_label = str(g.get("label") or "–")
        for ch in (g.get("channels") or []):
            if isinstance(ch, dict) and ch.get("deviceId"):
                room_map[ch["deviceId"]] = room_label
    return room_map

def _get_nested(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def _iter_devices(snapshot: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    candidates = [
        ("body", "devices"),
        ("body", "home", "devices"),
        ("body", "body", "devices"),
        ("body", "body", "home", "devices"),
    ]
    devices = None
    for path in candidates:
        devices = _get_nested(snapshot, path)
        if isinstance(devices, (dict, list)):
            break

    if isinstance(devices, dict):
        for dev_id, dev in devices.items():
            if isinstance(dev, dict):
                yield str(dev_id), dev
        return

    if isinstance(devices, list):
        for dev in devices:
            if isinstance(dev, dict):
                dev_id = dev.get("id", "")
                yield str(dev_id), dev
        return

def _find_device(snapshot: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
    for dev_id, dev in _iter_devices(snapshot):
        if dev_id == device_id:
            return dev
    return None

# ── Shared page wrapper ───────────────────────────────────────────────────────
def _nav(active: str = "", device_count: int = 0) -> str:
    badge = f'<span class="nav-badge">{device_count} Geräte</span>' if device_count else ""
    def lnk(href, label, key):
        cls = ' class="active"' if active == key else ''
        return f'<a href="{href}"{cls}>{label}</a>'
    return (
        '<nav>'
        '<div class="brand">⚡ Homematic <em>Bridge</em></div>'
        '<div class="nav-links">'
        + lnk("/", "Dashboard", "dashboard")
        + lnk("/devices/html", "Geräte", "devices")
        + lnk("/devices/status", "Status", "status")
        + lnk("/heating", "Heizung", "heating")
        + lnk("/shelly", "Shelly", "shelly")
        + lnk("/config", "Konfig", "config")
        + lnk("/healthz", "Health", "health")
        + '</div>'
        '<div class="spacer"></div>'
        + badge
        + '<a href="/logout" style="font-size:12px;color:var(--muted);padding:6px 10px;border-radius:6px;border:1px solid var(--border)">Abmelden</a>'
        + '</nav>'
    )

def _page(title: str, nav_html: str, body: str, extra_js: str = "") -> str:
    js_tag = f"<script>{extra_js}</script>" if extra_js else ""
    return (
        "<!DOCTYPE html>"
        '<html lang="de">'
        "<head>"
        '<meta charset="UTF-8">'
        f"<title>{html.escape(title)}</title>"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{_CSS}</style>"
        "</head>"
        f"<body>{nav_html}"
        f'<div class="container">{body}</div>'
        f"{js_tag}"
        "</body></html>"
    )

# ── Value renderer ────────────────────────────────────────────────────────────
def _val_html(v: Any) -> str:
    if v is None:
        return '<span class="v-null">null</span>'
    if isinstance(v, bool):
        cls = "v-true" if v else "v-false"
        return f'<span class="{cls}">{"true" if v else "false"}</span>'
    if isinstance(v, (int, float)):
        return f'<span class="v-num">{html.escape(str(v))}</span>'
    if isinstance(v, (dict, list)):
        return f'<span class="v-null">{html.escape(json.dumps(v, ensure_ascii=False))}</span>'
    return html.escape(str(v))

# ── Overview page ─────────────────────────────────────────────────────────────
def generate_device_overview(system_state_path: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    room_map = _build_room_map(data)
    rows = []
    count = 0
    for dev_id, dev in _iter_devices(data):
        count += 1
        label  = html.escape(str(dev.get("label", "–")))
        dtype  = html.escape(str(dev.get("type", "–")))
        model  = html.escape(str(dev.get("modelType", "–")))
        room   = html.escape(room_map.get(dev_id, "–"))
        eid    = html.escape(dev_id)
        rows.append(
            f'<tr>'
            f'<td class="mono"><a href="/devices/{eid}">{eid}</a></td>'
            f'<td class="label-cell">{label}</td>'
            f'<td>{room}</td>'
            f'<td><span class="type-pill">{dtype}</span></td>'
            f'<td class="mono">{model}</td>'
            f'</tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:24px">Keine Geräte gefunden.</td></tr>')

    tbody = "".join(rows)
    body = (
        '<div class="page-header">'
        '<h1>Geräteübersicht</h1>'
        f'<div class="sub">{count} Geräte im aktuellen Snapshot</div>'
        '</div>'
        '<div class="search-wrap"><input id="search" type="text" placeholder="Suchen nach ID, Label, Raum, Typ …" autocomplete="off"></div>'
        '<table class="data-table">'
        '<thead><tr><th>Device ID</th><th>Label</th><th>Raum</th><th>Typ</th><th>Modell</th></tr></thead>'
        f'<tbody>{tbody}</tbody>'
        '</table>'
    )

    return _page("HmIP Geräteübersicht", _nav("devices", count), body, _JS_SEARCH)


# ── Detail page ───────────────────────────────────────────────────────────────
def _kv_rows(d: Dict[str, Any], skip: Optional[set] = None) -> str:
    skip = skip or set()
    rows = []
    for k in sorted(d.keys()):
        if k in skip:
            continue
        rows.append(
            f'<tr>'
            f'<th>{html.escape(str(k))}</th>'
            f'<td>{_val_html(d[k])}</td>'
            f'</tr>'
        )
    return "".join(rows) if rows else '<tr><td colspan="2" style="color:var(--muted)">Keine Daten</td></tr>'

def _channel_card(ch_idx: str, ch: Dict[str, Any], open_default: bool = False) -> str:
    ch_type = html.escape(str(ch.get("functionalChannelType", "")))
    title   = f"Kanal {html.escape(str(ch_idx))}"
    open_attr = " open" if open_default else ""
    kv = _kv_rows(ch)
    return (
        f'<details{open_attr}>'
        f'<summary>'
        f'<span class="summary-title">{title}</span>'
        f'<span class="summary-sub">{ch_type}</span>'
        f'<span class="chevron">▼</span>'
        f'</summary>'
        f'<table class="kv-table"><tbody>{kv}</tbody></table>'
        f'</details>'
    )

def generate_device_detail_html(system_state_path: str, device_id: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dev = _find_device(data, device_id)
    if not isinstance(dev, dict):
        body = (
            '<div class="page-header"><h1>Gerät nicht gefunden</h1></div>'
            f'<p style="color:var(--muted)">Device <code>{html.escape(device_id)}</code> wurde im aktuellen Snapshot nicht gefunden.</p>'
            '<p style="margin-top:16px"><a href="/devices/html">« Zurück zur Übersicht</a></p>'
        )
        return _page("Gerät nicht gefunden", _nav(), body)

    room_map = _build_room_map(data)
    label = html.escape(str(dev.get("label", "–")))
    dtype = html.escape(str(dev.get("type", "–")))
    model = html.escape(str(dev.get("modelType", "–")))
    room  = html.escape(room_map.get(device_id, "–"))
    eid   = html.escape(device_id)

    # Breadcrumb
    breadcrumb = (
        '<div class="breadcrumb">'
        '<a href="/devices/html">Geräte</a>'
        '<span class="sep">›</span>'
        f'{room} <span style="opacity:.4">›</span> '
        f'{label}'
        '</div>'
    )

    # Meta-Grid
    meta_keys = ["id", "label", "type", "modelType", "homeId", "permanentlyReachable", "firmwareVersion"]
    meta_items = (
        f'<div class="meta-card">'
        f'<div class="k">Raum</div>'
        f'<div class="v">{room}</div>'
        f'</div>'
    )
    for k in meta_keys:
        if k in dev:
            meta_items += (
                f'<div class="meta-card">'
                f'<div class="k">{html.escape(k)}</div>'
                f'<div class="v">{_val_html(dev[k])}</div>'
                f'</div>'
            )
    meta_grid = f'<div class="meta-grid">{meta_items}</div>'

    # Channels
    fch = dev.get("functionalChannels", {})
    ch_parts = []
    if isinstance(fch, dict):
        for i, ch_idx in enumerate(sorted(fch.keys(), key=str)):
            ch = fch.get(ch_idx, {})
            if isinstance(ch, dict):
                ch_parts.append(_channel_card(ch_idx, ch, open_default=(i == 0)))
    channels_html = "".join(ch_parts) if ch_parts else '<p style="color:var(--muted)">Keine Channels</p>'

    # Raw JSON (collapsed by default)
    full_json = html.escape(json.dumps(dev, ensure_ascii=False, indent=2))
    raw_section = (
        '<details>'
        '<summary><span class="summary-title">Rohdaten (JSON)</span><span class="chevron">▼</span></summary>'
        f'<pre class="json-raw">{full_json}</pre>'
        '</details>'
    )

    body = (
        breadcrumb
        + '<div class="page-header">'
        + f'<h1>{label}</h1>'
        + f'<div class="sub"><span class="type-pill">{dtype}</span>&nbsp; {model} &nbsp;·&nbsp; <span class="mono">{eid}</span></div>'
        + '</div>'
        + meta_grid
        + f'<h2 style="font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Functional Channels</h2>'
        + channels_html
        + '<div style="margin-top:16px">'
        + raw_section
        + '</div>'
    )

    return _page(f"Device · {label}", _nav("devices"), body)


# ── Status page ───────────────────────────────────────────────────────────────
def _rssi_html(rssi: Any) -> str:
    """Rendert einen RSSI-Wert als Mini-Balken + Zahl."""
    if rssi is None or not isinstance(rssi, (int, float)):
        return '<span class="v-null">–</span>'
    v = int(rssi)
    # 128 = no signal / unbekannt
    if v == 128:
        return '<span class="v-null">n/a</span>'
    # Stärke: ≥ -70 gut, -70..-85 mittel, < -85 schlecht
    if v >= -70:
        bars, cls = 4, ""
    elif v >= -80:
        bars, cls = 3, ""
    elif v >= -90:
        bars, cls = 2, "warn"
    else:
        bars, cls = 1, "bad"
    bar_html = "".join(
        f'<span style="height:{(i+1)*3+2}px" class="{"lit" if i < bars else ""}"></span>'
        for i in range(4)
    )
    return f'<span class="rssi-bar {cls}">{bar_html}</span> <span class="mono">{v} dBm</span>'

def _bool_pill(val: Any, label_true: str, label_false: str,
               cls_true: str = "pill-error", cls_false: str = "pill-ok") -> str:
    if val is True:
        return f'<span class="status-pill {cls_true}">{label_true}</span>'
    if val is False:
        return f'<span class="status-pill {cls_false}">{label_false}</span>'
    return '<span class="status-pill pill-muted">–</span>'

def generate_device_status_html(system_state_path: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    room_map = _build_room_map(data)

    # Collect status per device
    entries = []
    for dev_id, dev in _iter_devices(data):
        ch0 = dev.get("functionalChannels", {}).get("0", {})
        low_bat    = ch0.get("lowBat")
        unreach    = ch0.get("unreach")
        duty       = ch0.get("dutyCycle")
        sabotage   = ch0.get("sabotage")
        rssi       = ch0.get("rssiDeviceValue")
        label      = dev.get("label", "–")
        dtype      = dev.get("type", "–")
        room       = room_map.get(dev_id, "–")

        # Severity: 2=error, 1=warn, 0=ok
        sev = 0
        if low_bat or unreach or duty or sabotage:
            sev = 2
        elif isinstance(rssi, (int, float)) and rssi != 128 and rssi < -85:
            sev = 1

        entries.append((sev, label, dev_id, dtype, room, low_bat, unreach, duty, sabotage, rssi))

    # Sort: errors first, then warn, then ok; within group alphabetically
    entries.sort(key=lambda e: (-e[0], str(e[1]).lower()))

    warn_count  = sum(1 for e in entries if e[0] >= 2)
    total       = len(entries)

    rows = []
    for sev, label, dev_id, dtype, room, low_bat, unreach, duty, sabotage, rssi in entries:
        row_cls = 'row-error' if sev == 2 else ('row-warn' if sev == 1 else '')
        eid = html.escape(dev_id)
        rows.append(
            f'<tr class="{row_cls}">'
            f'<td class="label-cell"><a href="/devices/{eid}">{html.escape(str(label))}</a></td>'
            f'<td>{html.escape(str(room))}</td>'
            f'<td><span class="type-pill">{html.escape(str(dtype))}</span></td>'
            f'<td>{_bool_pill(low_bat, "Low Bat", "OK")}</td>'
            f'<td>{_bool_pill(unreach, "Nicht erreichbar", "Erreichbar")}</td>'
            f'<td>{_bool_pill(duty, "Duty Cycle", "OK")}</td>'
            f'<td>{_bool_pill(sabotage, "Sabotage", "OK")}</td>'
            f'<td>{_rssi_html(rssi)}</td>'
            f'<td class="mono" style="font-size:11px;color:var(--muted)">'
            f'<a href="/devices/{eid}">{eid}</a></td>'
            f'</tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="9" style="color:var(--muted);text-align:center;padding:24px">Keine Geräte.</td></tr>')

    summary_pill = (
        f'<span class="status-pill pill-error">{warn_count} Warnung{"en" if warn_count != 1 else ""}</span>'
        if warn_count else
        f'<span class="status-pill pill-ok">Alle {total} Geräte OK</span>'
    )

    body = (
        '<div class="page-header">'
        f'<h1>Gerätestatus &nbsp;{summary_pill}</h1>'
        f'<div class="sub">{total} Geräte · Geräte mit Warnungen werden oben angezeigt</div>'
        '</div>'
        '<table class="data-table">'
        '<thead><tr>'
        '<th>Label</th><th>Raum</th><th>Typ</th>'
        '<th>Batterie</th><th>Erreichbarkeit</th><th>Duty Cycle</th><th>Sabotage</th>'
        '<th>RSSI</th><th>Device ID</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )

    return _page("HmIP Gerätestatus", _nav("status", total), body)


# ── Shared data extractors ─────────────────────────────────────────────────────
_WEATHER_ICON = {
    "CLEAR": "☀️", "PARTLY_CLOUDY": "🌤️", "CLOUDY": "☁️", "HEAVILY_CLOUDY": "☁️",
    "FOGGY": "🌫️", "STRONG_WIND": "💨", "RAINY": "🌧️", "HEAVY_RAIN": "⛈️",
    "LIGHT_RAIN": "🌦️", "SNOWY": "❄️", "SNOWY_RAINY": "🌨️", "THUNDERSTORM": "⛈️",
}

def _wind_dir(deg: Any) -> str:
    if not isinstance(deg, (int, float)):
        return "–"
    dirs = ["N","NO","O","SO","S","SW","W","NW"]
    return dirs[round(int(deg) / 45) % 8]

def _get_home_and_groups(data: Dict[str, Any]):
    """Gibt (home_dict, groups_dict) zurück."""
    body = data.get("body", {}).get("body", {}) or data.get("body", {})
    home = body.get("home", {})
    groups = body.get("groups", {})
    return home, groups

def _get_security_functional_home(home: Dict[str, Any], groups: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for fh in (home.get("functionalHomes") or {}).values():
        if isinstance(fh, dict) and fh.get("solution") == "SECURITY_AND_ALARM":
            return fh
    return None

def _get_heating_groups(groups: Dict[str, Any]) -> list:
    result = []
    for g in groups.values():
        if isinstance(g, dict) and g.get("type") == "HEATING":
            result.append(g)
    return sorted(result, key=lambda g: str(g.get("label", "")))


# ── Dashboard ──────────────────────────────────────────────────────────────────
def generate_dashboard_html(system_state_path: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    home, groups = _get_home_and_groups(data)
    weather = home.get("weather") or {}
    sec_fh  = _get_security_functional_home(home, groups)

    # ── Weather card ──
    condition  = weather.get("weatherCondition", "–")
    icon       = _WEATHER_ICON.get(condition, "🌡️")
    temp       = weather.get("temperature")
    hum        = weather.get("humidity")
    wind       = weather.get("windSpeed")
    wind_dir   = _wind_dir(weather.get("windDirection"))
    temp_min   = weather.get("minTemperature")
    temp_max   = weather.get("maxTemperature")
    temp_str   = f"{temp:.1f}" if isinstance(temp, (int, float)) else "–"
    wind_str   = f"{wind:.1f} km/h {wind_dir}" if isinstance(wind, (int, float)) else "–"
    minmax_str = f"{temp_min:.1f}° / {temp_max:.1f}°" if isinstance(temp_min, (int, float)) else ""

    weather_card = (
        '<div class="dash-card">'
        '<h3>Wetter</h3>'
        f'<div class="weather-icon">{icon}</div>'
        f'<div class="big-val">{temp_str}<em> °C</em></div>'
        f'<div class="sub-val">{html.escape(condition.replace("_"," ").title())}'
        + (f' · {minmax_str}' if minmax_str else '') + '</div>'
        f'<div style="margin-top:12px">'
        f'<div class="dash-kv"><span class="dk">Luftfeuchtigkeit</span><span class="dv">{hum} %</span></div>'
        f'<div class="dash-kv"><span class="dk">Wind</span><span class="dv">{html.escape(wind_str)}</span></div>'
        f'</div>'
        '</div>'
    )

    # ── Alarm card ──
    alarm_active     = sec_fh.get("alarmActive", False) if sec_fh else None
    safety_active    = sec_fh.get("safetyAlarmActive", False) if sec_fh else None
    intrusion_active = sec_fh.get("intrusionAlarmActive", False) if sec_fh else None
    any_alarm        = alarm_active or safety_active or intrusion_active

    last_event_ts    = sec_fh.get("alarmEventTimestamp") if sec_fh else None
    last_event_type  = sec_fh.get("alarmSecurityJournalEntryType", "") if sec_fh else ""

    import datetime
    def _ts(ms):
        if not ms: return "–"
        try:
            return datetime.datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return "–"

    alarm_border = "alarm-active" if any_alarm else "alarm-ok"
    alarm_status = (
        '<span class="status-pill pill-error">⚠ ALARM AKTIV</span>'
        if any_alarm else
        '<span class="status-pill pill-ok">Kein Alarm</span>'
    )
    alarm_card = (
        f'<div class="dash-card {alarm_border}">'
        '<h3>Alarm / Sicherheit</h3>'
        f'<div style="margin-bottom:12px">{alarm_status}</div>'
        '<div class="dash-kv"><span class="dk">Einbruch</span>'
        f'<span class="dv">{_bool_pill(intrusion_active,"AKTIV","OK")}</span></div>'
        '<div class="dash-kv"><span class="dk">Sicherheitsalarm</span>'
        f'<span class="dv">{_bool_pill(safety_active,"AKTIV","OK")}</span></div>'
        '<div class="dash-kv"><span class="dk">Letztes Ereignis</span>'
        f'<span class="dv">{html.escape(last_event_type.replace("_"," "))}</span></div>'
        '<div class="dash-kv"><span class="dk">Zeitpunkt</span>'
        f'<span class="dv">{_ts(last_event_ts)}</span></div>'
        '</div>'
    )

    # ── System card ──
    duty      = home.get("dutyCycle") or 0
    connected = home.get("connected")
    upd_state = home.get("updateState", "–")
    duty_cls  = "bad" if duty > 80 else ("warn" if duty > 50 else "")
    sys_card = (
        '<div class="dash-card">'
        '<h3>System</h3>'
        f'<div class="dash-kv"><span class="dk">Verbindung</span>'
        f'<span class="dv">{_bool_pill(connected,"Verbunden","Getrennt","pill-ok","pill-error")}</span></div>'
        f'<div class="dash-kv"><span class="dk">Firmware</span>'
        f'<span class="dv">{html.escape(str(upd_state))}</span></div>'
        f'<div class="dash-kv"><span class="dk">Duty Cycle</span>'
        f'<span class="dv mono">{duty:.1f} %</span></div>'
        f'<div class="progress-bar"><div class="fill {duty_cls}" style="width:{min(duty,100):.0f}%"></div></div>'
        '</div>'
    )

    # ── Heating summary card ──
    heating_groups = _get_heating_groups(groups)
    heat_items = ""
    for hg in heating_groups[:6]:  # max 6 auf Dashboard
        lbl   = html.escape(str(hg.get("label", "–")))
        actual = hg.get("actualTemperature")
        setp  = hg.get("setPointTemperature")
        valve = hg.get("valvePosition")
        mode  = hg.get("controlMode", "")
        actual_str = f"{actual:.1f}°" if isinstance(actual, (int, float)) else "–"
        setp_str   = f"Soll: {setp:.1f}°" if isinstance(setp, (int, float)) else ""
        valve_str  = f"Ventil: {valve*100:.0f}%" if isinstance(valve, (int, float)) else ""
        mode_str   = mode.replace("_", " ").title() if mode else ""
        heat_items += (
            f'<div class="heat-card">'
            f'<div class="room">{lbl}</div>'
            f'<div class="temps"><span class="actual">{actual_str}</span>'
            f'<span class="setpoint">{setp_str}</span></div>'
            + (f'<div class="valve">{valve_str}</div>' if valve_str else '')
            + (f'<div class="mode-pill">{mode_str}</div>' if mode_str else '')
            + '</div>'
        )
    more = len(heating_groups) - 6
    if more > 0:
        heat_items += f'<div class="heat-card" style="display:flex;align-items:center;justify-content:center;color:var(--muted)">+{more} weitere → <a href="/heating" style="margin-left:6px">Alle</a></div>'

    heat_card = (
        '<div class="dash-card wide">'
        '<h3>Heizung <a href="/heating" style="font-size:11px;float:right;color:var(--accent)">Alle →</a></h3>'
        f'<div class="heating-grid">{heat_items}</div>'
        '</div>'
    ) if heating_groups else ""

    # ── Device warning summary ──
    warn_devs = []
    for dev_id, dev in _iter_devices(data):
        ch0 = dev.get("functionalChannels", {}).get("0", {})
        if ch0.get("lowBat") or ch0.get("unreach") or ch0.get("dutyCycle") or ch0.get("sabotage"):
            warn_devs.append(dev.get("label", dev_id))
    if warn_devs:
        warn_list = "".join(f'<div class="dash-kv"><span class="dk">⚠</span><span class="dv">{html.escape(str(l))}</span></div>' for l in warn_devs[:6])
        if len(warn_devs) > 6:
            warn_list += f'<div class="dash-kv" style="color:var(--muted)">... +{len(warn_devs)-6} weitere</div>'
        warn_card = (
            '<div class="dash-card alarm-active">'
            f'<h3>Gerätewarnungen <a href="/devices/status" style="font-size:11px;float:right;color:var(--accent)">Alle →</a></h3>'
            + warn_list + '</div>'
        )
    else:
        warn_card = (
            '<div class="dash-card alarm-ok">'
            '<h3>Gerätewarnungen</h3>'
            '<div style="color:var(--green);font-size:13px">Alle Geräte OK</div>'
            '</div>'
        )

    body = (
        '<div class="page-header"><h1>Dashboard</h1></div>'
        f'<div class="dash-grid">'
        + weather_card + alarm_card + sys_card + warn_card
        + '</div>'
        + heat_card
    )

    return _page("HmIP Dashboard", _nav("dashboard"), body)


# ── Heating page ───────────────────────────────────────────────────────────────
def generate_heating_html(system_state_path: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    home, groups = _get_home_and_groups(data)
    heating_groups = _get_heating_groups(groups)
    absence = "–"
    for fh in (home.get("functionalHomes") or {}).values():
        if isinstance(fh, dict) and fh.get("solution") == "INDOOR_CLIMATE":
            absence = str(fh.get("absenceType", "–")).replace("_", " ").title()

    rows = []
    for hg in heating_groups:
        lbl    = html.escape(str(hg.get("label", "–")))
        actual = hg.get("actualTemperature")
        setp   = hg.get("setPointTemperature")
        hum    = hg.get("humidity")
        valve  = hg.get("valvePosition")
        mode   = hg.get("controlMode", "–")
        boost  = hg.get("boostMode", False)
        party  = hg.get("partyMode", False)

        actual_str = f"{actual:.1f} °C" if isinstance(actual, (int, float)) else "–"
        setp_str   = f"{setp:.1f} °C"   if isinstance(setp,   (int, float)) else "–"
        hum_str    = f"{hum} %"          if isinstance(hum,    (int, float)) else "–"
        valve_str  = f"{valve*100:.0f} %" if isinstance(valve, (int, float)) else "–"
        mode_str   = html.escape(str(mode).replace("_", " ").title())

        flags = ""
        if boost:  flags += ' <span class="status-pill pill-warn">Boost</span>'
        if party:  flags += ' <span class="status-pill pill-warn">Party</span>'

        rows.append(
            f'<tr>'
            f'<td class="label-cell">{lbl}</td>'
            f'<td class="mono">{actual_str}</td>'
            f'<td class="mono">{setp_str}</td>'
            f'<td class="mono">{hum_str}</td>'
            f'<td class="mono">{valve_str}</td>'
            f'<td>{mode_str}{flags}</td>'
            f'</tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="6" style="color:var(--muted);text-align:center;padding:24px">Keine Heizgruppen gefunden.</td></tr>')

    body = (
        '<div class="page-header">'
        '<h1>Heizung</h1>'
        f'<div class="sub">Abwesenheitsmodus: <strong>{html.escape(absence)}</strong></div>'
        '</div>'
        '<table class="data-table">'
        '<thead><tr>'
        '<th>Raum</th><th>Ist-Temp</th><th>Soll-Temp</th><th>Luftfeuchte</th><th>Ventil</th><th>Modus</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )

    return _page("HmIP Heizung", _nav("heating"), body)


# ── Shelly-Seite ──────────────────────────────────────────────────────────────

_JS_SHELLY = """
async function shellyRelay(ip, gen, channel, on) {
  const btn = document.getElementById('btn-' + ip.replace(/\\./g,'-') + '-' + channel);
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await fetch('/shelly/' + ip + '/relay/' + channel, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({on: on})
    });
    const d = await r.json();
    if (d.success) { setTimeout(() => location.reload(), 800); }
    else { alert('Fehler beim Schalten'); if (btn) btn.disabled = false; }
  } catch(e) { alert('Netzwerkfehler'); if (btn) btn.disabled = false; }
}

async function startScan() {
  const btn = document.getElementById('scan-btn');
  btn.disabled = true; btn.textContent = 'Scan läuft…';
  document.getElementById('scan-info').textContent = 'Scan gestartet – bitte warten…';
  try {
    const r = await fetch('/shelly/scan', {method: 'POST'});
    const d = await r.json();
    if (d.status === 'started' || d.status === 'already_running') {
      pollScan();
    } else {
      document.getElementById('scan-info').textContent = d.error || 'Fehler';
      btn.disabled = false; btn.textContent = 'Netzwerk scannen';
    }
  } catch(e) {
    document.getElementById('scan-info').textContent = 'Netzwerkfehler';
    btn.disabled = false; btn.textContent = 'Netzwerk scannen';
  }
}

function pollScan() {
  fetch('/shelly/status')
    .then(r => r.json())
    .then(d => {
      if (d.running) {
        document.getElementById('scan-info').textContent = 'Scan läuft…';
        setTimeout(pollScan, 2000);
      } else {
        location.reload();
      }
    });
}

async function shellyUpdate(ip) {
  if (!confirm('Firmware-Update für ' + ip + ' starten?\nDas Gerät ist während des Updates nicht erreichbar.')) return;
  const btn = document.getElementById('upd-' + ip.replace(/\\./g,'-'));
  if (btn) { btn.disabled = true; btn.textContent = 'Update läuft…'; }
  try {
    const r = await fetch('/shelly/' + ip + '/update', {method: 'POST'});
    const d = await r.json();
    if (d.success) {
      if (btn) btn.textContent = 'Neustart…';
      setTimeout(() => location.reload(), 15000);
    } else {
      alert('Update fehlgeschlagen');
      if (btn) { btn.disabled = false; btn.textContent = 'Update'; }
    }
  } catch(e) { alert('Netzwerkfehler'); if (btn) { btn.disabled = false; btn.textContent = 'Update'; } }
}

async function refreshStatus() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true; btn.textContent = 'Aktualisiere…';
  try {
    await fetch('/shelly/refresh-status', {method: 'POST'});
    location.reload();
  } catch(e) {
    btn.disabled = false; btn.textContent = 'Status aktualisieren';
  }
}

// Auto-refresh alle 60s
setTimeout(() => { fetch('/shelly/refresh-status', {method:'POST'}).then(() => location.reload()); }, 60000);
"""


def generate_shelly_html() -> str:
    import app.shelly as shelly_mod
    import app.state as state

    devices = shelly_mod.load_cached()
    status = shelly_mod.scan_status()
    cfg = state.config.get("shelly", {})
    enabled = cfg.get("enabled", False)
    subnet = cfg.get("subnet", "–")

    # Config-Hinweis wenn nicht aktiviert
    if not enabled:
        body = (
            '<div class="page-header"><h1>Shelly Scanner</h1></div>'
            '<div class="dash-card" style="max-width:520px">'
            '<h3>Nicht aktiviert</h3>'
            '<p style="color:var(--muted);font-size:13px;line-height:1.6">Füge in <code>config/config.yaml</code> folgendes hinzu:</p>'
            '<pre class="json-raw" style="margin-top:12px">shelly:\n  enabled: true\n  subnet: "192.168.1.0/24"\n  timeout_sec: 1.5</pre>'
            '</div>'
        )
        return _page("Shelly", _nav("shelly"), body)

    # Header-Bereich
    scan_info = ""
    if status["running"]:
        scan_info = "Scan läuft…"
    elif status["error"]:
        scan_info = f"Fehler: {html.escape(status['error'])}"

    _BTN_STYLE = (
        "border:1px solid var(--border);padding:3px 10px;border-radius:4px;"
        "font-size:11px;cursor:pointer;margin-right:4px;color:var(--text)"
    )

    def _rssi_html(rssi):
        if not isinstance(rssi, (int, float)):
            return '<span class="mono" style="color:var(--muted)">–</span>'
        if rssi >= -60:   cls, bars = "", 4
        elif rssi >= -75: cls, bars = "warn", 2
        else:             cls, bars = "bad", 1
        bar_spans = "".join(
            f'<span style="height:{4+i*3}px" class="{"lit" if i < bars else ""}"></span>'
            for i in range(4)
        )
        return f'<span class="rssi-bar {cls}">{bar_spans}</span> <span class="mono">{rssi} dBm</span>'

    def _age_str(last_seen):
        if not last_seen:
            return "–"
        import time as _t
        age = int(_t.time() - last_seen)
        if age < 60:    return f"{age}s"
        if age < 3600:  return f"{age//60}m"
        return f"{age//3600}h"

    def _em_cell(emeters: dict) -> str:
        """Energie-Monitor Darstellung (SHEM, SHEM-3)."""
        if not emeters:
            return '<span style="color:var(--muted)">–</span>'
        phase_labels = {0: "L1", 1: "L2", 2: "L3"}
        parts = []
        total_power = 0.0
        for idx, em in sorted(emeters.items(), key=lambda x: x[0]):
            i = int(idx)
            lbl = phase_labels.get(i, f"CH{i}")
            pw  = em.get("power_w")
            v   = em.get("voltage")
            a   = em.get("current")
            pf  = em.get("pf")
            kwh = em.get("total_kwh")
            ret = em.get("returned_kwh")
            if isinstance(pw, (int, float)):
                total_power += pw
            pw_str  = f'{pw:.0f} W'   if isinstance(pw, (int, float)) else "–"
            v_str   = f'{v:.1f} V'    if isinstance(v,  (int, float)) else ""
            a_str   = f'{a:.2f} A'    if isinstance(a,  (int, float)) else ""
            pf_str  = f'PF {pf:.2f}'  if isinstance(pf, (int, float)) else ""
            kwh_str = f'{kwh:.1f} kWh' if isinstance(kwh, (int, float)) else ""
            ret_str = f'↩ {ret:.1f} kWh' if isinstance(ret, (int, float)) and ret > 0 else ""
            details = " · ".join(filter(None, [v_str, a_str, pf_str]))
            energy  = " · ".join(filter(None, [kwh_str, ret_str]))
            parts.append(
                f'<div style="margin-bottom:4px">'
                f'<span style="color:var(--muted);font-size:10px;font-family:var(--mono)">{lbl}</span> '
                f'<strong class="mono">{pw_str}</strong>'
                + (f' <span style="color:var(--muted);font-size:11px;font-family:var(--mono)">({details})</span>' if details else '')
                + (f'<br><span style="color:var(--muted);font-size:11px;font-family:var(--mono)">{energy}</span>' if energy else '')
                + '</div>'
            )
        total_str = f'<div style="border-top:1px solid var(--border);margin-top:4px;padding-top:4px;font-family:var(--mono);font-size:11px;color:var(--muted)">Gesamt: <strong style="color:var(--text)">{total_power:.0f} W</strong></div>' if len(emeters) > 1 else ""
        return "".join(parts) + total_str

    def _switch_cell(ip: str, gen: int, channels: dict) -> str:
        """Schalter-Darstellung mit Toggle-Button."""
        if not channels:
            return '<span style="color:var(--muted)">–</span>'
        parts = []
        for ch_idx, ch in sorted(channels.items(), key=lambda x: x[0]):
            is_on = ch.get("on", False)
            power = ch.get("power_w")
            kwh   = ch.get("total_kwh")
            btn_id    = f"btn-{ip.replace('.', '-')}-{ch_idx}"
            btn_color = "var(--green)" if is_on else "var(--surface2)"
            new_state = "false" if is_on else "true"
            btn_text  = "EIN" if is_on else "AUS"
            pw_str  = f' · {power:.0f} W'    if isinstance(power, (int, float)) else ""
            kwh_str = f' · {kwh:.2f} kWh'    if isinstance(kwh,  (int, float)) else ""
            parts.append(
                f'<button id="{btn_id}" '
                f'onclick="shellyRelay(\'{ip}\',{gen},{ch_idx},{new_state})" '
                f'style="background:{btn_color};{_BTN_STYLE}">'
                f'CH{ch_idx} {btn_text}{pw_str}</button>'
                + (f'<span style="font-size:11px;color:var(--muted);font-family:var(--mono)">{kwh_str}</span>' if kwh_str else '')
            )
        return "".join(parts)

    header = (
        '<div class="page-header">'
        '<h1>Shelly</h1>'
        f'<div class="sub">Subnet: <strong>{html.escape(subnet)}</strong>'
        f' &nbsp;·&nbsp; {len(devices)} Geräte &nbsp;·&nbsp; '
        'Auto-Refresh: 60s</div>'
        '</div>'
        '<div style="margin-bottom:20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        '<button id="scan-btn" onclick="startScan()" style="'
        'background:var(--accent);color:#0d1117;border:none;padding:8px 18px;'
        'border-radius:6px;font-size:13px;font-weight:600;cursor:pointer">'
        'Netzwerk scannen</button>'
        '<button id="refresh-btn" onclick="refreshStatus()" style="'
        'background:var(--surface2);color:var(--text);border:1px solid var(--border);padding:8px 14px;'
        'border-radius:6px;font-size:13px;cursor:pointer">'
        'Status aktualisieren</button>'
        f'<span id="scan-info" style="color:var(--muted);font-size:13px">{html.escape(scan_info)}</span>'
        '</div>'
    )

    if not devices:
        table = '<div style="color:var(--muted);font-size:13px">Noch keine Geräte gefunden. Starte einen Scan.</div>'
    else:
        rows = []
        for dev in devices:
            ip         = html.escape(dev.get("ip", ""))
            name       = html.escape(dev.get("name") or dev.get("id") or "–")
            model      = html.escape(dev.get("model", "–"))
            gen        = dev.get("gen", 1)
            mac        = html.escape(dev.get("mac", "–"))
            fw         = html.escape(dev.get("fw", "–"))
            new_fw     = html.escape(dev.get("new_fw", ""))
            upd_avail  = dev.get("update_available", False)
            channels   = dev.get("channels", {})
            emeters    = dev.get("emeters", {})

            # Energie-Monitor wenn emeters vorhanden, sonst Schalter
            if emeters:
                control_cell = _em_cell(emeters)
                # Relay-Button zusätzlich wenn vorhanden (SHEM hat Relay)
                if channels:
                    control_cell += '<div style="margin-top:6px">' + _switch_cell(ip, gen, channels) + '</div>'
            else:
                control_cell = _switch_cell(ip, gen, channels)

            rows.append(
                f'<tr>'
                f'<td class="label-cell">{name}</td>'
                f'<td><span class="type-pill">{model}</span></td>'
                f'<td class="mono">Gen{gen}</td>'
                f'<td><a href="http://{ip}" target="_blank" class="mono">{ip}</a></td>'
                f'<td class="mono" style="font-size:11px">{mac}</td>'
                f'<td class="mono" style="font-size:11px;color:var(--muted)">{fw}'
                + (
                    f'<br><button id="upd-{ip.replace(".", "-")}" onclick="shellyUpdate(\'{ip}\')" '
                    f'style="background:#3a2a00;color:var(--yellow);border:1px solid var(--yellow);'
                    f'padding:2px 8px;border-radius:4px;font-size:10px;cursor:pointer;margin-top:3px">'
                    f'▲ {new_fw}</button>'
                    if upd_avail else ''
                )
                + '</td>'
                f'<td>{_rssi_html(dev.get("rssi"))}</td>'
                f'<td>{control_cell}</td>'
                f'<td class="mono" style="color:var(--muted)">{_age_str(dev.get("last_seen"))}</td>'
                f'</tr>'
            )

        table = (
            '<div class="search-wrap"><input id="search" placeholder="Suchen…"></div>'
            '<table class="data-table">'
            '<thead><tr>'
            '<th>Name</th><th>Modell</th><th>Gen</th><th>IP</th><th>MAC</th>'
            '<th>Firmware</th><th>WLAN</th><th>Status / Steuerung</th><th>Gesehen</th>'
            '</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            '</table>'
        )

    body = header + table
    return _page("Shelly", _nav("shelly"), body, extra_js=_JS_SEARCH + _JS_SHELLY)


# ── Config-Editor ─────────────────────────────────────────────────────────────

def generate_config_html(content: str, error: Optional[str] = None, success: bool = False) -> str:
    alert = ""
    if error:
        alert = f'<div class="login-error" style="margin-bottom:16px">Fehler: {html.escape(error)}</div>'
    elif success:
        alert = '<div style="background:#1a3a1a;border:1px solid var(--green);color:var(--green);padding:10px 14px;border-radius:6px;font-size:13px;margin-bottom:16px">Gespeichert. Verbindungsänderungen (HCU, Token) erfordern einen Neustart.</div>'

    body = (
        '<div class="page-header">'
        '<h1>Konfiguration</h1>'
        '<div class="sub">config/config.yaml – nach dem Speichern werden Loxone- und Shelly-Einstellungen sofort übernommen. HCU-Verbindungsänderungen erfordern einen Neustart.</div>'
        '</div>'
        + alert +
        '<form method="POST" action="/config">'
        '<textarea name="content" spellcheck="false" style="'
        'width:100%;height:520px;background:var(--bg);color:var(--text);'
        'border:1px solid var(--border);border-radius:8px;padding:16px;'
        'font-family:var(--mono);font-size:13px;line-height:1.6;resize:vertical;outline:none">'
        + html.escape(content) +
        '</textarea>'
        '<div style="margin-top:12px;display:flex;gap:10px;align-items:center">'
        '<button type="submit" style="background:var(--accent);color:#0d1117;border:none;'
        'padding:9px 22px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer">'
        'Speichern</button>'
        '<span style="color:var(--muted);font-size:12px">YAML wird vor dem Speichern validiert.</span>'
        '</div>'
        '</form>'
    )
    return _page("Konfiguration", _nav("config"), body)


# ── Login-Seite ────────────────────────────────────────────────────────────────
def generate_login_html(error: bool = False, next_url: str = "/") -> str:
    error_block = '<div class="login-error">Falsches Passwort. Bitte erneut versuchen.</div>' if error else ""
    next_escaped = html.escape(next_url)
    body = (
        '<div class="login-wrap">'
        '<div class="login-card">'
        '<div class="logo">⚡ Homematic <em>Bridge</em></div>'
        + error_block +
        f'<form method="POST" action="/login?next={next_escaped}">'
        '<div class="login-field">'
        '<label>Passwort (API-Key)</label>'
        '<input type="password" name="password" autofocus autocomplete="current-password" placeholder="API-Key eingeben">'
        '</div>'
        '<button class="login-btn" type="submit">Anmelden</button>'
        '</form>'
        '</div>'
        '</div>'
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="de">'
        "<head>"
        '<meta charset="UTF-8">'
        "<title>Login – Homematic Bridge</title>"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{_CSS}</style>"
        "</head>"
        f"<body>{body}</body>"
        "</html>"
    )
