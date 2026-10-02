# SPDX-License-Identifier: Apache-2.0
# tests/test_routes_reliability.py - ACK- und Health-Verhalten der HTTP-Routen

import json
from unittest.mock import MagicMock, patch

import app.state as state
from app.adapters.hmip_websocket import _resolve_pending


def _api_headers():
    return {"X-API-Key": "test-key", "Content-Type": "application/json"}


def _prepare_api() -> None:
    state.REQUIRE_API_KEY = True
    state.API_KEY = "test-key"
    state.conn = MagicMock()


def test_switch_returns_success_only_after_hcu_ack(flask_client):
    _prepare_api()
    def acknowledge_during_send(_conn, _device, _on, _channel, request_id=None):
        _resolve_pending(request_id, {"body": {"code": 200}})
        return request_id

    with patch(
        "app.routes.send_hmip_set_switch", side_effect=acknowledge_during_send
    ):
        response = flask_client.post(
            "/hmipSwitch",
            data=json.dumps({"device": "switch-1", "on": True}),
            headers=_api_headers(),
        )

    assert response.status_code == 200
    assert response.get_json()["request_id"]


def test_switch_returns_service_unavailable_when_send_fails(flask_client):
    _prepare_api()
    state.conn.send.side_effect = OSError("connection lost")

    response = flask_client.post(
        "/hmipSwitch",
        data=json.dumps({"device": "switch-1", "on": True}),
        headers=_api_headers(),
    )

    assert response.status_code == 503


def test_switch_returns_gateway_timeout_without_ack(flask_client):
    _prepare_api()
    with patch("app.routes._wait_for_ack", return_value=None):
        response = flask_client.post(
            "/hmipSwitch",
            data=json.dumps({"device": "switch-1", "on": True}),
            headers=_api_headers(),
        )

    assert response.status_code == 504
    assert "request_id" in response.get_json()


def test_switch_returns_bad_gateway_when_hcu_rejects_command(flask_client):
    _prepare_api()
    with patch("app.routes._wait_for_ack", return_value={"body": {"code": 400}}):
        response = flask_client.post(
            "/hmipSwitch",
            data=json.dumps({"device": "switch-1", "on": True}),
            headers=_api_headers(),
        )

    assert response.status_code == 502
    assert response.get_json()["hcu_code"] == 400


def test_health_returns_503_when_websocket_is_disconnected(flask_client, tmp_path):
    snapshot = tmp_path / "state.json"
    snapshot.write_text('{"body":{"devices":{}}}', encoding="utf-8")
    state.config_internal = {"system_state_path": str(snapshot)}
    state.conn = None
    state.adapter_registry = MagicMock()
    state.adapter_registry.health.return_value = {"hmip": False}

    response = flask_client.get("/healthz")

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_health_exposes_websocket_telemetry_when_healthy(flask_client, tmp_path):
    snapshot = tmp_path / "state.json"
    snapshot.write_text('{"body":{"devices":{"d1":{}}}}', encoding="utf-8")
    state.config_internal = {"system_state_path": str(snapshot)}
    state.conn = MagicMock()
    state.WS_CONNECTED_AT = 100.0
    state.WS_READY = True
    state.WS_LAST_MESSAGE_AT = 101.0
    state.WS_LAST_ERROR = None
    state.WS_RECONNECTS = 2
    state.adapter_registry = MagicMock()
    state.adapter_registry.health.return_value = {"hmip": True}

    response = flask_client.get("/healthz")

    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["devices_count"] == 1
    assert body["ws_reconnects"] == 2
    assert body["adapters"] == {"hmip": True}
