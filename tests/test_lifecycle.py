# SPDX-License-Identifier: Apache-2.0
# tests/test_lifecycle.py - Tests für kontrollierten Prozess-Shutdown

import signal
from unittest.mock import MagicMock, patch

import pytest

import app.state as state
from app.lifecycle import install_shutdown_handlers


def test_shutdown_handler_stops_registry_and_interrupts_server():
    registry = MagicMock()
    with patch("app.lifecycle.signal.signal") as register_signal:
        handler = install_shutdown_handlers(registry)

    assert register_signal.call_count == 2
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGTERM, None)

    assert state.stop_event.is_set()
    registry.stop_all.assert_called_once_with()
