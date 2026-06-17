# SPDX-License-Identifier: Apache-2.0
# security.py – Dateibasierte Sicherheitshelfer

import os
import secrets


def load_or_create_secret_key(path: str) -> bytes:
    """Lädt oder erzeugt einen Flask-Signaturschlüssel mit Modus 0600."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = None

    if fd is not None:
        key = secrets.token_bytes(32)
        with os.fdopen(fd, "wb") as f:
            f.write(key)

    os.chmod(path, 0o600)
    with open(path, "rb") as f:
        key = f.read()
    if len(key) < 32:
        raise ValueError(f"Session-Schlüssel in {path} ist zu kurz")
    return key
