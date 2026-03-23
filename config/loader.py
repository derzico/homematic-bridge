# SPDX-License-Identifier: Apache-2.0

import logging
import os
from typing import Any, Dict, List

import yaml

log = logging.getLogger("bridge-ws")


def load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> dict:
    return load_yaml("config/config.yaml")


def load_internal_config() -> dict:
    return load_yaml("config/internal_config.yaml")


# ── Config-Validierung ───────────────────────────────────────────────────────

def _check_type(errors: List[str], cfg: dict, key: str, expected, *, required: bool = False, label: str = "") -> None:
    """Helper: prüft ob cfg[key] den erwarteten Typ hat."""
    path = label or key
    val = cfg.get(key)
    if val is None:
        if required:
            errors.append(f"'{path}' fehlt (Pflichtfeld)")
        return
    if not isinstance(val, expected):
        tname = expected.__name__ if isinstance(expected, type) else str(expected)
        errors.append(f"'{path}' muss vom Typ {tname} sein, ist aber {type(val).__name__}")


def validate_config(config: dict) -> List[str]:
    """Validiert die Struktur von config.yaml. Gibt Liste von Fehlern zurück (leer = OK)."""
    errors: List[str] = []

    if not isinstance(config, dict):
        return ["Config muss ein YAML-Mapping (dict) sein"]

    # Pflichtfelder
    _check_type(errors, config, "homematic_hcu", str, required=True)
    _check_type(errors, config, "homematic_token", str, required=True)
    _check_type(errors, config, "plugin_id", str, required=True)

    fn = config.get("friendly_name")
    if fn is not None and not isinstance(fn, dict):
        errors.append("'friendly_name' muss ein Dict sein (z.B. {en: ..., de: ...})")

    # Optionale Felder
    _check_type(errors, config, "ssl_verify", bool)
    _check_type(errors, config, "ssl_cert_path", str)
    _check_type(errors, config, "require_api_key", bool)
    _check_type(errors, config, "api_key", str)
    _check_type(errors, config, "api_key_file", str)

    # Loxone-Sektion
    lox = config.get("loxone")
    if lox is not None:
        if not isinstance(lox, dict):
            errors.append("'loxone' muss ein Dict sein")
        else:
            _check_type(errors, lox, "miniserver_ip", str, label="loxone.miniserver_ip")
            _check_type(errors, lox, "udp_port", int, label="loxone.udp_port")

    # Shelly-Sektion
    shelly = config.get("shelly")
    if shelly is not None:
        if not isinstance(shelly, dict):
            errors.append("'shelly' muss ein Dict sein")
        else:
            _check_type(errors, shelly, "enabled", bool, label="shelly.enabled")
            _check_type(errors, shelly, "subnet", str, label="shelly.subnet")
            _check_type(errors, shelly, "timeout_sec", (int, float), label="shelly.timeout_sec")
            _check_type(errors, shelly, "username", str, label="shelly.username")
            _check_type(errors, shelly, "password", str, label="shelly.password")
            _check_type(errors, shelly, "scan_on_startup", bool, label="shelly.scan_on_startup")
            _check_type(errors, shelly, "scan_interval_hours", (int, float), label="shelly.scan_interval_hours")

    return errors


def validate_internal_config(config: dict) -> List[str]:
    """Validiert die Struktur von internal_config.yaml."""
    errors: List[str] = []

    if not isinstance(config, dict):
        return ["Internal config muss ein YAML-Mapping (dict) sein"]

    _check_type(errors, config, "system_state_path", str, required=True)
    _check_type(errors, config, "log_file", str)
    _check_type(errors, config, "health_stale_seconds", (int, float))

    log_level = config.get("log_level")
    if log_level is not None and log_level not in ("debug", "info", "warning", "error"):
        errors.append(f"'log_level' muss debug/info/warning/error sein, ist aber '{log_level}'")

    return errors