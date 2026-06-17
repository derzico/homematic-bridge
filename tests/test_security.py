# SPDX-License-Identifier: Apache-2.0
# tests/test_security.py – Tests für dateibasierte Sicherheitshelfer

import os
import stat

import pytest

from app.security import load_or_create_secret_key


def test_secret_key_is_created_with_private_permissions(tmp_path):
    path = tmp_path / "secret_key.bin"

    key = load_or_create_secret_key(str(path))

    assert len(key) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_secret_key_permissions_are_hardened(tmp_path):
    path = tmp_path / "secret_key.bin"
    path.write_bytes(os.urandom(32))
    path.chmod(0o644)

    load_or_create_secret_key(str(path))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_short_existing_secret_key_is_rejected(tmp_path):
    path = tmp_path / "secret_key.bin"
    path.write_bytes(b"short")

    with pytest.raises(ValueError, match="zu kurz"):
        load_or_create_secret_key(str(path))
