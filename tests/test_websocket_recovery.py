"""Deterministic connection failure tests, without sleeping or real HCU access."""
import json
from unittest.mock import MagicMock, patch

import pytest

import app.state as state
import app.routes as routes
import app.adapters.hmip_websocket as transport


@pytest.fixture
def ws_setup(monkeypatch):
    state.config = {"homematic_hcu": "old.test", "homematic_token": "old-token", "plugin_id": "old.plugin", "ssl_verify": False}
    state.config_internal = {"log_level": "info"}
    state.conn = None
    state.pending.clear()
    state.WS_READY = False
    clock = [0.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(state.stop_event, "wait", lambda timeout: False)
    return clock


class FakeConnection:
    def __init__(self, receive):
        self.receive = receive
        self.requests = []
        self.closed = False
        self.pings = 0

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, message):
        payload = json.loads(message)
        if payload["type"] == "HMIP_SYSTEM_REQUEST":
            self.requests.append(payload)

    def recv(self):
        return self.receive(self)

    def ping(self):
        self.pings += 1

    def close(self):
        self.closed = True

    def response(self, code=200):
        return json.dumps({"id": self.requests[-1]["id"], "type": "HMIP_SYSTEM_RESPONSE",
                           "body": {"code": code, "body": {"devices": {}, "groups": {}, "home": {}}}})


def stop_with_snapshot(connection):
    state.stop_event.set()
    return connection.response()


def test_initial_failure_retries_in_same_connection(ws_setup):
    def receive(connection):
        if len(connection.requests) == 1:
            return connection.response(500)
        state.stop_event.set()
        return connection.response()
    connection = FakeConnection(receive)
    with patch.object(transport.websocket, "create_connection", return_value=connection) as create, patch.object(transport, "save_system_state", return_value=True) as save:
        transport.ws_loop()
    assert len(connection.requests) == 2
    create.assert_called_once()
    save.assert_called_once()
    assert state.WS_READY is True
    assert state.pending == {}


def test_initial_snapshot_timeout_retries_bounded_then_reconnects(ws_setup):
    def receive(_connection):
        ws_setup[0] += 31
        raise transport.websocket.WebSocketTimeoutException("timeout")
    old = FakeConnection(receive)
    new = FakeConnection(stop_with_snapshot)
    with patch.object(transport.websocket, "create_connection", side_effect=[old, new]), patch.object(transport, "save_system_state", return_value=True):
        transport.ws_loop()
    assert len(old.requests) == transport.SNAPSHOT_ATTEMPTS
    assert old.closed
    assert state.WS_RECONNECTS >= 1
    assert state.pending == {}


def test_invalid_success_snapshot_is_retried_not_marked_ready(ws_setup):
    def receive(connection):
        if len(connection.requests) == 1:
            response = json.loads(connection.response())
            response["body"]["body"] = {"unexpected": "not a snapshot"}
            return json.dumps(response)
        assert state.WS_READY is False
        state.stop_event.set()
        return connection.response()
    connection = FakeConnection(receive)
    with patch.object(transport.websocket, "create_connection", return_value=connection), patch.object(transport, "save_system_state", return_value=True) as save:
        transport.ws_loop()
    assert len(connection.requests) == 2
    save.assert_called_once()


def test_ping_success_without_hcu_response_reconnects(ws_setup):
    calls = [0]
    def receive(connection):
        calls[0] += 1
        if calls[0] == 1:
            return connection.response()
        ws_setup[0] += 31
        raise transport.websocket.WebSocketTimeoutException("idle")
    old = FakeConnection(receive)
    new = FakeConnection(stop_with_snapshot)
    with patch.object(transport.websocket, "create_connection", side_effect=[old, new]), patch.object(transport, "save_system_state", return_value=True):
        transport.ws_loop()
    assert old.pings == 2
    assert len(old.requests) == 2  # Initial snapshot plus read-only liveness probe.
    assert old.closed
    assert len(new.requests) == 1


def test_unrelated_messages_do_not_hide_initial_timeout(ws_setup):
    def receive(_connection):
        ws_setup[0] += 16
        return json.dumps({"type": "UNRELATED"})
    old = FakeConnection(receive)
    new = FakeConnection(stop_with_snapshot)
    with patch.object(transport.websocket, "create_connection", side_effect=[old, new]), patch.object(transport, "save_system_state", return_value=True):
        transport.ws_loop()
    assert len(old.requests) == transport.SNAPSHOT_ATTEMPTS
    assert old.closed


def test_close_frame_immediately_reconnects(ws_setup):
    old = FakeConnection(lambda _connection: "")
    new = FakeConnection(stop_with_snapshot)
    with patch.object(transport.websocket, "create_connection", side_effect=[old, new]), patch.object(transport, "save_system_state", return_value=True):
        transport.ws_loop()
    assert old.closed
    assert old.pings == 0
    assert state.pending == {}


def test_config_reload_uses_new_handshake_and_plugin_identity(ws_setup):
    def receive(_connection):
        routes._apply_runtime_config({**state.config, "homematic_hcu": "new.test", "homematic_token": "new-token", "plugin_id": "new.plugin"})
        raise ConnectionError("config changed")
    old = FakeConnection(receive)
    new = FakeConnection(stop_with_snapshot)
    with patch.object(transport.websocket, "create_connection", side_effect=[old, new]) as create, patch.object(transport, "save_system_state", return_value=True), patch.object(routes, "_ensure_api_key"):
        transport.ws_loop()
    assert create.call_args_list[1].args[0] == "wss://new.test:9001"
    assert create.call_args_list[1].kwargs["header"]["authtoken"] == "new-token"
    assert create.call_args_list[1].kwargs["header"]["plugin-id"] == "new.plugin"
    assert new.requests[0]["pluginId"] == "new.plugin"
    assert old.closed


def test_config_change_during_connect_discards_stale_connection(ws_setup):
    old = FakeConnection(lambda _connection: pytest.fail("stale connection must not receive"))
    new = FakeConnection(stop_with_snapshot)
    calls = [0]
    def create(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            state.config = {**state.config, "homematic_hcu": "new.test", "plugin_id": "new.plugin"}
            return old
        return new
    with patch.object(transport.websocket, "create_connection", side_effect=create), patch.object(transport, "save_system_state", return_value=True):
        transport.ws_loop()
    assert old.closed and old.requests == []
    assert new.requests[0]["pluginId"] == "new.plugin"


def test_failed_config_persistence_reports_failed(ws_setup):
    def receive(_connection):
        state.stop_event.set()
        return json.dumps({"id": "update-id", "type": "CONFIG_UPDATE_REQUEST", "body": {"properties": {"log_level": "debug"}}})
    connection = FakeConnection(receive)
    with patch.object(transport.websocket, "create_connection", return_value=connection), patch.object(transport, "_apply_log_level", return_value=False), patch.object(transport, "send_config_update_response") as response:
        transport.ws_loop()
    assert response.call_args.args[2] == "FAILED"


def test_failed_snapshot_write_not_ready(ws_setup):
    calls = [0]
    def receive(connection):
        calls[0] += 1
        if calls[0] == 2:
            state.stop_event.set()
            raise ConnectionError("end test")
        return connection.response()
    connection = FakeConnection(receive)
    with patch.object(transport.websocket, "create_connection", return_value=connection), patch.object(transport, "save_system_state", return_value=False):
        transport.ws_loop()
    assert state.WS_READY is False
