# SPDX-License-Identifier: Apache-2.0

import ipaddress
import logging
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Dict, List

import yaml

log = logging.getLogger("bridge-ws")
MAX_SHELLY_SCAN_ADDRESSES = 4096


def load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> dict:
    return load_yaml("config/config.yaml")


def load_internal_config() -> dict:
    return load_yaml("config/internal_config.yaml")


def resolve_api_key_file(config: dict, config_internal: dict) -> str:
    """Ermittelt den API-Key-Pfad mit derselben Priorität bei Start und Reload."""
    return config_internal.get("api_key_file", config.get("api_key_file", "data/api_key.txt"))


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


def validate_shelly_subnet(subnet: str) -> str:
    """Liefert eine Fehlermeldung für unsichere Scan-Netze, sonst einen Leerstring."""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        return f"ungültiges IPv4-Netz: {exc}"
    if network.version != 4:
        return "nur IPv4-Netze werden unterstützt"
    if network.num_addresses > MAX_SHELLY_SCAN_ADDRESSES:
        return f"maximal {MAX_SHELLY_SCAN_ADDRESSES} Adressen sind erlaubt"
    return ""


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
    elif isinstance(fn, dict):
        if not isinstance(fn.get("de"), str) or not fn["de"].strip():
            errors.append("'friendly_name.de' fehlt oder ist kein nicht-leerer String")
        for language, name in fn.items():
            if (not isinstance(language, str) or len(language) != 2
                    or not language.isalpha() or language != language.lower()
                    or not isinstance(name, str)):
                errors.append("'friendly_name' muss ISO-639-1-Codes auf Strings abbilden")
                break

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
            interval = lox.get("resync_interval_seconds", 300)
            if type(interval) is not int or not (interval == 0 or 30 <= interval <= 86400):
                errors.append("'loxone.resync_interval_seconds' muss 0 oder 30..86400 sein")
            heartbeat = lox.get("heartbeat_interval_seconds", 60)
            if type(heartbeat) is not int or not (heartbeat == 0 or 5 <= heartbeat <= 3600):
                errors.append("'loxone.heartbeat_interval_seconds' muss 0 oder 5..3600 sein")
            for key in ("numeric_states", "percent_values", "aggregates", "command_feedback"):
                _check_type(errors, lox, key, bool, label=f"loxone.{key}")
            filt = lox.get("filter")
            if filt is not None:
                if not isinstance(filt, dict):
                    errors.append("'loxone.filter' muss ein Dict sein")
                else:
                    for key in ("groups", "diagnostics"):
                        _check_type(errors, filt, key, bool, label=f"loxone.filter.{key}")
                    for key in ("include_fields", "exclude_fields", "include_rooms",
                                "exclude_rooms", "include_devices", "exclude_devices"):
                        values = filt.get(key)
                        if values is not None and (not isinstance(values, list)
                                                   or not all(isinstance(v, str) for v in values)):
                            errors.append(f"'loxone.filter.{key}' muss eine Liste von Strings sein")

    # Sicherheit: Nur-Lese-Key und IP-Freigabe für Steuerbefehle
    _check_type(errors, config, "api_key_readonly", str)
    _check_type(errors, config, "api_key_readonly_file", str)
    allowed = config.get("control_allowed_ips")
    if allowed is not None:
        if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed):
            errors.append("'control_allowed_ips' muss eine Liste von IP-Adressen/Netzen sein")
        else:
            for value in allowed:
                try:
                    ipaddress.ip_network(value, strict=False)
                except ValueError:
                    errors.append(f"'control_allowed_ips' enthält ungültige Adresse '{value}'")

    # Schutz vor Funküberlastung
    guard = config.get("command_guard")
    if guard is not None:
        if not isinstance(guard, dict):
            errors.append("'command_guard' muss ein Dict sein")
        else:
            limits = {"dedupe_window_ms": (0, 10000), "min_interval_ms": (0, 5000),
                      "duty_cycle_limit_percent": (0, 100)}
            for key, (low, high) in limits.items():
                value = guard.get(key)
                if value is not None and (type(value) is not int or not low <= value <= high):
                    errors.append(f"'command_guard.{key}' muss eine ganze Zahl {low}..{high} sein")

    energy = config.get("energy")
    if energy is not None:
        if not isinstance(energy, dict):
            errors.append("'energy' muss ein Dict sein")
        else:
            if not isinstance(energy.get("enabled", False), bool):
                errors.append("'energy.enabled' muss ein Boolean sein")
            interval, retention = energy.get("interval_seconds", 60), energy.get("retention_days", 90)
            if type(interval) is not int or not 30 <= interval <= 86400:
                errors.append("'energy.interval_seconds' muss 30..86400 sein")
            if type(retention) is not int or not 1 <= retention <= 730:
                errors.append("'energy.retention_days' muss 1..730 sein")
            try:
                zone = energy.get("timezone", "Europe/Berlin")
                if not isinstance(zone, str):
                    raise ValueError
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                errors.append("'energy.timezone' muss eine gültige IANA-Zeitzone sein")

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
            poll = shelly.get("state_poll_seconds", 10)
            if type(poll) is not int or not (poll == 0 or 2 <= poll <= 3600):
                errors.append("'shelly.state_poll_seconds' muss 0 oder 2..3600 sein")
            subnet = shelly.get("subnet")
            if isinstance(subnet, str) and subnet:
                subnet_error = validate_shelly_subnet(subnet)
                if subnet_error:
                    errors.append(f"'shelly.subnet' {subnet_error}")

    return errors


def validate_internal_config(config: dict) -> List[str]:
    """Validiert die Struktur von internal_config.yaml."""
    errors: List[str] = []

    if not isinstance(config, dict):
        return ["Internal config muss ein YAML-Mapping (dict) sein"]

    _check_type(errors, config, "system_state_path", str, required=True)
    _check_type(errors, config, "log_file", str)
    _check_type(errors, config, "health_stale_seconds", (int, float))
    _check_type(errors, config, "ack_timeout_seconds", (int, float))

    ack_timeout = config.get("ack_timeout_seconds")
    if isinstance(ack_timeout, (int, float)) and not 0.1 <= ack_timeout <= 30:
        errors.append("'ack_timeout_seconds' muss zwischen 0.1 und 30 Sekunden liegen")

    log_level = config.get("log_level")
    if log_level is not None and log_level not in ("debug", "info", "warning", "error"):
        errors.append(f"'log_level' muss debug/info/warning/error sein, ist aber '{log_level}'")

    return errors
