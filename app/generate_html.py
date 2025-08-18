# app/generate_html.py
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
import html

def _get_nested(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def _iter_devices(snapshot: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """
    Liefert (device_id, device_dict) aus verschiedenen möglichen Pfaden:
      - body.devices
      - body.home.devices
      - body.body.devices
      - body.body.home.devices
    und verarbeitet devices als Dict {id: dev} oder als List[dev].
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

    # Fallback: nichts gefunden
    return []

def generate_device_overview(system_state_path: str, output_path: str = "static/device_overview.html") -> str:
    # Snapshot laden
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Zeilen bauen
    rows_html = []
    count = 0
    for dev_id, dev in _iter_devices(data):
        count += 1
        label = html.escape(str(dev.get("label", "")))
        dtype = html.escape(str(dev.get("type", "")))
        rows_html.append(f"<tr><td>{html.escape(dev_id)}</td><td>{label}</td><td>{dtype}</td></tr>")

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

    # Zielordner sicherstellen und schreiben
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(html_template, encoding="utf-8")
    return str(outp)
