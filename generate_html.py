import json
from pathlib import Path

def generate_device_overview(system_state_path: str, output_path: str = "static/device_overview.html"):
    with open(system_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    devices = data["body"]["body"].get("devices", {})

    html_rows = ""
    for dev_id, dev in devices.items():
        html_rows += f"<tr><td>{dev_id}</td><td>{dev.get('label', '')}</td><td>{dev.get('type', '')}</td></tr>"

    html_template = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <title>HMIP Geräteliste</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h1>Homematic IP Geräteübersicht</h1>
        <table>
            <thead>
                <tr><th>Device ID</th><th>Label</th><th>Typ</th></tr>
            </thead>
            <tbody>
                {html_rows}
            </tbody>
        </table>
    </body>
    </html>
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)  # → Ordner anlegen

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_template)

    return str(output_path)
