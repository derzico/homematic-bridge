# request_token.py

import requests
import yaml
import sys
import os
import urllib3
import logging
from logging.handlers import TimedRotatingFileHandler
from config.loader import load_config, load_internal_config

# Konfiguration laden (inkl. Token sicherstellen)
config = load_config()
config_internal = load_internal_config()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PLUGIN_ID = config.get("plugin_id", "de.doe.jane.plugin.example")
FRIENDLY_NAME = config.get("friendly_name")
CONFIG_FILE = "config.yaml"

HEADERS = {
    "VERSION": "12"
}

# Logging einrichten
def setup_logger(config_internal):
    logger = logging.getLogger("request_token")
    log_level = getattr(logging, config_internal.get("log_level", "INFO").upper(), logging.INFO)
    logger.setLevel(log_level)
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

    # Konsole
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Datei
    log_file = config_internal.get("log_file")
    if log_file:
        rotate = config_internal.get("log_rotate", False)
        if rotate:
            fh = TimedRotatingFileHandler(log_file, when="midnight", backupCount=7, encoding="utf-8")
        else:
            fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

def save_token_to_config(config, token, log):
    config['homematic_token'] = token
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f)
    log.info("Token gespeichert in config.yaml")

def get_ssl_verify_options(config, log):
    if config.get("ssl_verify", False):
        cert_path = config.get("ssl_cert_path")
        if cert_path:
            log.info(f"[SSL] Verwende Zertifikatspfad: {cert_path}")
            return cert_path
        else:
            log.info("[SSL] Systemvertrauenswürdige Zertifikate werden verwendet.")
            return True
    else:
        log.warning("[SSL] Verbindung ohne Zertifikatsprüfung (unsicher)")
        return False

def request_token(hcu_host, code, log, verify):
    url = f"https://{hcu_host}:6969/hmip/auth/requestConnectApiAuthToken"
    payload = {
        "activationKey": code,
        "pluginId": PLUGIN_ID,
        "friendlyName": FRIENDLY_NAME
    }
    try:
        response = requests.post(url, headers=HEADERS, json=payload, verify=verify, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if 'authToken' in data:
                log.info("Token erhalten.")
                return data['authToken']
            else:
                log.error("Antwort enthält keinen authToken.")
        else:
            log.error(f"HTTP {response.status_code}: {response.text}")
    except Exception as e:
        log.error(f"Anfrage fehlgeschlagen: {e}")
    return None

def confirm_token(hcu_host, code, token, log, verify):
    url = f"https://{hcu_host}:6969/hmip/auth/confirmConnectApiAuthToken"
    payload = {
        "activationKey": code,
        "authToken": token
    }
    try:
        response = requests.post(url, headers=HEADERS, json=payload, verify=verify, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if 'clientId' in data:
                log.info(f"Token bestätigt. Client-ID: {data['clientId']}")
                return True
            else:
                log.error("Antwort enthält keine clientId.")
        else:
            log.error(f"Bestätigung fehlgeschlagen HTTP {response.status_code}: {response.text}")
    except Exception as e:
        log.error(f"Token-Bestätigung fehlgeschlagen: {e}")
    return False

def ensure_token():
    log = setup_logger(config_internal)
    verify = get_ssl_verify_options(config, log)

    if config.get("homematic_token"):
        log.info("Token bereits vorhanden, keine Aktion erforderlich.")
        return config

    hcu_host = config.get("homematic_hcu")
    if not hcu_host:
        log.error("homematic_hcu fehlt in config.yaml")
        sys.exit(1)

    log.info("Kein Token gefunden. Aktivierungsschlüssel erforderlich.")
    code = input("Aktivierungsschlüssel eingeben (z. B. 697CC4): ").strip()
    if not code or len(code) < 6:
        log.error("Ungültiger Aktivierungsschlüssel.")
        sys.exit(1)

    token = request_token(hcu_host, code, log, verify)
    if token:
        if confirm_token(hcu_host, code, token, log, verify):
            save_token_to_config(config, token, log)
            config['homematic_token'] = token
            return config
        else:
            log.error("Token konnte nicht bestätigt werden.")
    else:
        log.error("Token konnte nicht angefordert werden.")
    sys.exit(1)

def main():
    ensure_token()

if __name__ == "__main__":
    main()
