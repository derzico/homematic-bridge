# SPDX-License-Identifier: Apache-2.0
# tests/test_hmip_websocket.py - WebSocket-Verbindungs- und Reconnect-Tests

import json
from unittest.mock import MagicMock, patch

import app.state as state
from app.adapters.hmip_websocket import ws_loop


class _SnapshotConnection:
    def __init__(self) -> None:
        self.timeout = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self) -> str:
        state.stop_event.set()
        return json.dumps({
            "id": "request-state",
            "type": "HMIP_SYSTEM_RESPONSE",
            "body": {"code": 200, "body": {"devices": {}}},
        })


class _TimeoutConnection:
    def __init__(self) -> None:
        self.ping_calls = 0

    def settimeout(self, _timeout: float) -> None:
        pass

    def recv(self) -> str:
        from websocket import WebSocketTimeoutException
        raise WebSocketTimeoutException("idle")

    def ping(self) -> None:
        self.ping_calls += 1
        state.stop_event.set()


def _configure_websocket() -> None:
    state.config = {
        "homematic_hcu": "hcu.test.local",
        "homematic_token": "secret-token",
        "plugin_id": "test-plugin",
        "ssl_verify": False,
    }
    state.config_internal = {"log_level": "info"}


def test_ws_loop_correlates_and_saves_initial_snapshot():
    _configure_websocket()
    connection = _SnapshotConnection()

    with patch("app.adapters.hmip_websocket.websocket.create_connection", return_value=connection) as create, \
         patch("app.adapters.hmip_websocket.send_plugin_state") as send_plugin_state, \
         patch("app.adapters.hmip_websocket.send_get_system_state", return_value="request-state"), \
         patch("app.adapters.hmip_websocket.save_system_state") as save_system_state:
        ws_loop()

    create.assert_called_once()
    assert create.call_args.args[0] == "wss://hcu.test.local:9001"
    assert create.call_args.kwargs["header"]["authtoken"] == "secret-token"
    assert connection.timeout == 30
    send_plugin_state.assert_called_once_with(connection)
    save_system_state.assert_called_once()
    assert "request-state" not in state.pending


def test_ws_loop_sends_keepalive_after_read_timeout():
    _configure_websocket()
    connection = _TimeoutConnection()

    with patch("app.adapters.hmip_websocket.websocket.create_connection", return_value=connection), \
         patch("app.adapters.hmip_websocket.send_plugin_state"), \
         patch("app.adapters.hmip_websocket.send_get_system_state", return_value="request-state"):
        ws_loop()

    assert connection.ping_calls == 1


def test_ws_loop_clears_connection_and_pending_before_reconnect_wait():
    _configure_websocket()
    old_connection = MagicMock()
    state.conn = old_connection
    state.pending["orphan"] = {"path": "/test", "ts": 0}

    with patch("app.adapters.hmip_websocket.websocket.create_connection", side_effect=OSError("offline")), \
         patch("app.adapters.hmip_websocket.random.uniform", return_value=0), \
         patch.object(state.stop_event, "wait", return_value=True) as wait:
        ws_loop()

    old_connection.close.assert_called_once_with()
    assert state.conn is None
    assert state.pending == {}
    wait.assert_called_once_with(timeout=1.0)
