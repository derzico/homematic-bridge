# SPDX-License-Identifier: Apache-2.0
# shelly_proxy.py – Sicherer Redirect zur Web-UI eines Shelly-Geräts

import ipaddress
import logging
from typing import Optional, Tuple
from urllib.parse import quote

from flask import jsonify, redirect, request

import app.adapters.shelly_adapter as shelly_mod

log = logging.getLogger("bridge-ws")


def _check_ip_allowed(ip: str) -> Optional[Tuple[dict, int]]:
    """Akzeptiert ausschließlich gecachte IPv4-Adressen."""
    try:
        parsed_ip = ipaddress.ip_address(ip)
        if parsed_ip.version != 4:
            raise ValueError
    except ValueError:
        return ({"error": "Ungültige Shelly-IPv4-Adresse"}, 400)

    cached_ips = {str(d.get("ip")) for d in shelly_mod.load_cached() if d.get("ip")}
    if str(parsed_ip) not in cached_ips:
        log.warning("Shelly-WebUI abgelehnt: IP %s nicht im Cache", ip)
        return ({"error": f"IP {ip} nicht im Shelly-Cache – zuerst scannen"}, 404)
    return None


def redirect_to_device(ip: str, subpath: str):
    """Leitet zur Geräte-Origin um, statt fremden Code unter der Bridge-Origin auszuführen."""
    err = _check_ip_allowed(ip)
    if err is not None:
        body, status = err
        return jsonify(body), status

    parsed_ip = ipaddress.ip_address(ip)
    target = f"http://{parsed_ip}/{quote(subpath, safe='/')}"
    if request.query_string:
        target += "?" + request.query_string.decode("ascii", errors="ignore")
    return redirect(target, 302)
