import yaml
import os
import sys

CONFIG_PATH = "config.yaml"

_required_fields = [
    "homematic_hcu",
    "homematic_token",
    "bridge_user",
    "bridge_pass",
    "plugin_id",
    "ssl_verify",
    "friendly_name"
]

def load_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        print(f"[FATAL] Konfigurationsdatei '{path}' nicht gefunden.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"[FATAL] Fehler beim Laden von YAML: {e}")
            sys.exit(1)

    if not isinstance(config, dict):
        print("[FATAL] config.yaml hat ein ungültiges Format.")
        sys.exit(1)

    missing = [key for key in _required_fields if key not in config]
    if missing:
        print(f"[FATAL] Fehlende Konfigurationswerte: {', '.join(missing)}")
        sys.exit(1)

    return config
