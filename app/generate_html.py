# SPDX-License-Identifier: Apache-2.0

# app/generate_html.py
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple, Optional
import html

# ---- Helpers: JSON-Navigation ----
def _get_nested(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def _iter_devices(snapshot: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """
    Liefert (device_id, device_dict) aus möglichen Pfaden:
      - body.devices
      - body.home.devices
      - body.body.devices
      - body.body.home.devices
    """
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

    # nichts gefunden
    return []

def _find_device(snapshot: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
    for dev_id, dev in _iter_devices(snapshot):
        if dev_id == device_id:
            return dev
    return None

# ---- Overview-Seite (bestehend) ----
def generate_device_overview(system_state_path: str, output_path: str = "static/device_overview.html") -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows_html = []
    count = 0
    for dev_id, dev in _iter_devices(data):
        count += 1
        label = html.escape(str(dev.get("label", "")))
        dtype = html.escape(str(dev.get("type", "")))
        rows_html.append(
            f"<tr>"
            f"<td><a href=\"/devices/{html.escape(dev_id)}\">{html.escape(dev_id)}</a></td>"
            f"<td>{label}</td>"
            f"<td>{dtype}</td>"
            f"</tr>"
        )

    if not rows_html:
        rows_html.append('<tr><td colspan="3"><em>Keine Geräte gefunden.</em></td></tr>')

    html_template = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>HMIP Geräteliste</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background-color: #f2f2f2; }}
    .muted {{ color: #666; font-size: 0.9em; }}
    a {{ text-decoration: none; }}
  </style>
</head>
<body>
  <h1>Homematic IP – Geräteübersicht</h1>
  <p class="muted">Quelle: {html.escape(system_state_path)} · Geräte: {count}</p>
  <table>
    <thead>
      <tr><th>Device ID</th><th>Label</th><th>Typ</th></tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>"""

    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(html_template, encoding="utf-8")
    return str(outp)

# ---- Detail-Seite pro Device ----
def _render_simple_table(d: Dict[str, Any], *, title: str = "", skip_keys: Optional[set] = None) -> str:
    if skip_keys is None:
        skip_keys = set()
    rows = []
    for k in sorted(d.keys()):
        if k in skip_keys:
            continue
        v = d[k]
        # Für verschachtelte Strukturen JSON-kompakt darstellen
        if isinstance(v, (dict, list)):
            v_str = html.escape(json.dumps(v, ensure_ascii=False))
        else:
            v_str = html.escape(str(v))
        rows.append(f"<tr><th>{html.escape(str(k))}</th><td>{v_str}</td></tr>")
    caption = f"<caption style='text-align:left;font-weight:bold;padding:6px 0;'>{html.escape(title)}</caption>" if title else ""
    return (
        f"<table>"
        f"{caption}"
        f"<tbody>"
        f"{''.join(rows) if rows else '<tr><td><em>Keine Daten</em></td></tr>'}"
        f"</tbody>"
        f"</table>"
    )

def generate_device_detail_html(system_state_path: str, device_id: str) -> str:
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dev = _find_device(data, device_id)
    if not isinstance(dev, dict):
        # 404-Seite als HTML
        return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><title>Gerät nicht gefunden</title></head>
<body style="font-family:Arial, sans-serif; margin:40px;">
  <h1>Gerät nicht gefunden</h1>
  <p>Device <code>{html.escape(device_id)}</code> wurde im aktuellen Snapshot nicht gefunden.</p>
  <p><a href="/devices/html">Zurück zur Übersicht</a></p>
</body>
</html>"""

    label = html.escape(str(dev.get("label", "")))
    dtype = html.escape(str(dev.get("type", "")))
    model = html.escape(str(dev.get("modelType", "")))

    # Oberste Meta-Tabelle (einige häufige Felder)
    meta_keys = ["id", "label", "type", "modelType", "homeId", "permanentlyReachable"]
    meta = {k: dev.get(k) for k in meta_keys if k in dev}
    meta_html = _render_simple_table(meta, title="Basisdaten")

    # functionalChannels hübsch darstellen
    ch_html_parts = []
    fch = dev.get("functionalChannels", {})
    if isinstance(fch, dict):
        for ch_idx in sorted(fch.keys(), key=lambda x: (str(x))):
            ch = fch.get(ch_idx, {})
            if isinstance(ch, dict):
                title = f"Kanal {html.escape(str(ch_idx))} – {html.escape(str(ch.get('functionalChannelType','')))}"
                ch_html_parts.append(_render_simple_table(ch, title=title))
    channels_html = "".join(ch_html_parts) if ch_html_parts else "<p><em>Keine Channels</em></p>"

    # Komplettes Device-JSON als <pre> (für Deep-Dive)
    full_json = html.escape(json.dumps(dev, ensure_ascii=False, indent=2))

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Device {html.escape(device_id)}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; }}
    h1, h2 {{ margin: 0 0 10px 0; }}
    .muted {{ color: #666; font-size: 0.9em; margin-bottom: 20px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background-color: #f9f9f9; width: 240px; }}
    pre {{ background:#f7f7f7; padding:16px; overflow:auto; }}
    a {{ text-decoration: none; }}
  </style>
</head>
<body>
  <h1>Device: {html.escape(device_id)}</h1>
  <div class="muted">Label: {label} · Typ: {dtype} · Modell: {model}</div>

  {meta_html}

  <h2>Functional Channels</h2>
  {channels_html}

  <h2>Rohdaten</h2>
  <pre>{full_json}</pre>

  <p><a href="/devices/html">« Zur Übersicht</a></p>
</body>
</html>"""
