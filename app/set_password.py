# SPDX-License-Identifier: Apache-2.0
# set_password.py – Webinterface-Passwort setzen (bcrypt-Hash in internal_config.yaml)
#
# Verwendung:
#   python app/set_password.py
#   docker compose exec bridge python app/set_password.py

import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from werkzeug.security import generate_password_hash

CONFIG_FILE = "config/internal_config.yaml"

def main():
    print("=== Homematic Bridge – Passwort setzen ===")
    password = getpass.getpass("Neues Passwort: ")
    if not password:
        print("Abgebrochen – kein Passwort eingegeben.")
        sys.exit(1)
    confirm = getpass.getpass("Passwort bestätigen: ")
    if password != confirm:
        print("Fehler: Passwörter stimmen nicht überein.")
        sys.exit(1)

    pw_hash = generate_password_hash(password)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    config.pop("web_password", None)        # Klartext-Eintrag entfernen falls vorhanden
    config["web_password_hash"] = pw_hash

    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)
    os.replace(tmp, CONFIG_FILE)

    print(f"Passwort gesetzt und als Hash in {CONFIG_FILE} gespeichert.")
    print("Container-Neustart: docker compose restart")

if __name__ == "__main__":
    main()
