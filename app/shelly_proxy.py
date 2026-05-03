# SPDX-License-Identifier: Apache-2.0
# shelly_proxy.py – HTTP-Proxy für die Web-UI eines Shelly-Geräts.
#
# Leitet Anfragen mit Auto-Login (Basic Auth) an das Shelly-Gerät weiter und
# schreibt Links/Redirects so um, dass die Navigation innerhalb des Proxies bleibt.
# SSRF-Schutz: nur IPs aus dem Shelly-Cache werden akzeptiert.

import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from flask import jsonify, redirect, request

import app.adapters.shelly_adapter as shelly_mod

log = logging.getLogger("bridge-ws")

_PROXY_TIMEOUT_SEC = 10


def _build_interceptor(proxy_base: str, device_origin: str) -> str:
    """Liefert ein <script>-Snippet, das XHR/fetch/location/history-Pfade umschreibt."""
    return (
        "<script>"
        "(function(){"
        f"var B='{proxy_base}';"
        f"var O='{device_origin}';"
        "function rw(u){"
        "if(typeof u!=='string')return u;"
        "if(u.startsWith(O))u=u.slice(O.length)||'/';"
        "if(u.charAt(0)==='/'&&u.charAt(1)!=='/')u=B+u;"
        "return u;}"
        "var oX=XMLHttpRequest.prototype.open;"
        "XMLHttpRequest.prototype.open=function(m,u){return oX.apply(this,[m,rw(u)].concat([].slice.call(arguments,2)));};"
        "if(window.fetch){var oF=window.fetch;window.fetch=function(u,o){return oF.call(window,rw(u),o);};}"
        "try{var lD=Object.getOwnPropertyDescriptor(Location.prototype,'href');"
        "if(lD&&lD.set){Object.defineProperty(Location.prototype,'href',{"
        "get:lD.get,set:function(v){lD.set.call(this,rw(v));}});}}catch(e){}"
        "['pushState','replaceState'].forEach(function(fn){var o=history[fn];"
        "history[fn]=function(s,t,u){return o.call(history,s,t,u?rw(u):u);};});"
        "})();"
        "</script>"
    )


def _rewrite_html(html_text: str, proxy_base: str, device_origin: str) -> str:
    """Schreibt Links, meta-refresh und Inline-URLs einer Shelly-HTML-Seite um."""
    interceptor = _build_interceptor(proxy_base, device_origin)

    def _rewrite_attr(m: re.Match) -> str:
        attr, path = m.group(1), m.group(2)
        if path.startswith(device_origin):
            path = path[len(device_origin):] or "/"
        if path.startswith("/") and not path.startswith("//"):
            return f'{attr}="{proxy_base}{path}"'
        return m.group(0)

    html_text = re.sub(
        r'(src|href|action|data)="((?:' + re.escape(device_origin) + r')?/[^"]*)"',
        _rewrite_attr,
        html_text,
    )

    def _rewrite_meta(m: re.Match) -> str:
        content = m.group(1)

        def _sub_url(mu: re.Match) -> str:
            url = mu.group(1)
            if url.startswith(device_origin):
                url = url[len(device_origin):] or "/"
            if url.startswith("/") and not url.startswith("//"):
                url = proxy_base + url
            return f"url={url}"

        return 'content="' + re.sub(r"url=([^\s;\"']+)", _sub_url, content, flags=re.IGNORECASE) + '"'

    html_text = re.sub(r'content="([^"]*url=[^"]*)"', _rewrite_meta, html_text, flags=re.IGNORECASE)

    base_tag = f'<base href="{proxy_base}/">'
    if "<head>" in html_text:
        html_text = html_text.replace("<head>", f"<head>{base_tag}{interceptor}", 1)
    elif "<HEAD>" in html_text:
        html_text = html_text.replace("<HEAD>", f"<HEAD>{base_tag}{interceptor}", 1)
    else:
        html_text = base_tag + interceptor + html_text
    return html_text


def _resolve_redirect(location: str, ip: str, device_origin: str) -> str:
    """Wandelt eine Location-Header-URL des Geräts in einen Proxy-Pfad um."""
    parsed_loc = urlparse(location)
    # Absolute URL vom Gerät → Path+Query extrahieren
    if parsed_loc.scheme in ("http", "https") and parsed_loc.netloc:
        location = parsed_loc.path or "/"
        if parsed_loc.query:
            location += "?" + parsed_loc.query
    # Relativen Pfad ohne führenden Slash normalisieren
    if location and not location.startswith("/"):
        location = "/" + location
    if location.startswith("/") and not location.startswith("//"):
        location = f"/shelly/{ip}/webui{location}"
    return location


def _check_ip_allowed(ip: str) -> Optional[Tuple[dict, int]]:
    """Prüft, ob die IP im Shelly-Cache steht (SSRF-Schutz).

    Gibt None zurück bei Erfolg, sonst (error_response_dict, status_code).
    """
    cached = {d["ip"]: d for d in shelly_mod.load_cached()}
    if ip not in cached:
        log.warning("Shelly-Proxy abgelehnt: IP %s nicht im Cache (SSRF-Schutz)", ip)
        return ({"error": f"IP {ip} nicht im Shelly-Cache – zuerst scannen"}, 404)
    return None


def proxy_request(ip: str, subpath: str):
    """Hauptfunktion: führt den HTTP-Proxy-Aufruf aus.

    SSRF-Schutz: nur IPs aus dem Shelly-Cache werden akzeptiert.
    CSRF-Schutz: über SameSite=Lax-Session-Cookie (Cross-Site-POST sendet keinen Cookie).
    """
    err = _check_ip_allowed(ip)
    if err is not None:
        body, status = err
        return jsonify(body), status

    target = f"http://{ip}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", errors="replace")

    creds = shelly_mod.get_credentials()
    auth = creds if creds and creds[0] else None

    try:
        if request.method == "POST":
            r = requests.post(
                target, auth=auth,
                data=request.get_data(),
                headers={"Content-Type": request.content_type or "application/x-www-form-urlencoded"},
                timeout=_PROXY_TIMEOUT_SEC, allow_redirects=False,
            )
        else:
            r = requests.get(target, auth=auth, timeout=_PROXY_TIMEOUT_SEC, allow_redirects=False)
    except Exception as exc:
        log.warning("Shelly-Proxy: %s nicht erreichbar (%s)", ip, exc)
        return f"<h3 style='font-family:sans-serif;padding:24px'>Shelly nicht erreichbar: {exc}</h3>", 502

    device_origin = f"http://{ip}"
    proxy_base = f"/shelly/{ip}/webui"

    # Redirect → durch den Proxy umleiten
    if r.status_code in (301, 302, 303, 307, 308):
        loc = _resolve_redirect(r.headers.get("Location", "/"), ip, device_origin)
        log.debug("Shelly-Proxy redirect → %s", loc)
        return redirect(loc, r.status_code)

    content_type = r.headers.get("Content-Type", "application/octet-stream")
    if "text/html" in content_type:
        html_text = _rewrite_html(r.text, proxy_base, device_origin)
        return html_text, r.status_code, {"Content-Type": "text/html; charset=utf-8"}

    # Alle anderen Inhalte (JS, CSS, Bilder, JSON) – durchreichen
    return r.content, r.status_code, {"Content-Type": content_type}
