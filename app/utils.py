import json
import logging
from config.loader import load_internal_config

config_internal = load_internal_config()

def save_system_state(data, path=config_internal["system_state_path"]):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.getLogger("bridge-ws").info("Systemzustand gespeichert → system_state.json")
    except Exception as e:
        logging.getLogger("bridge-ws").error(f"Fehler beim Speichern des Systemzustands: {e}")
