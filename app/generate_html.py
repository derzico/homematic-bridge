# SPDX-License-Identifier: Apache-2.0

# app/generate_html.py
import json
from pathlib import Path
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
        + lnk("/devices/html", "Geräte", "devices")
        + lnk("/devices/status", "Status", "status")
        + lnk("/healthz", "Health", "health")
        + '</div>'
        '<div class="spacer"></div>'
        + badge
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
def generate_device_overview(system_state_path: str, output_path: str = "static/device_overview.html") -> str:
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

    result = _page(
        "HmIP Geräteübersicht",
        _nav("devices", count),
        body,
        _JS_SEARCH,
    )

    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(result, encoding="utf-8")
    return str(outp)


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
