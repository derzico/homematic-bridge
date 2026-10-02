"""Regression tests for the October audit; all device operations are mocked."""
import errno
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

import app.state as state
import app.routes as routes
import app.adapters.shelly_adapter as shelly
import app.adapters.hmip_websocket as transport
from app.adapters.hmip_adapter import HmIPAdapter
from app.view_helpers import (prepare_dashboard, prepare_heating,
                              prepare_device_overview, prepare_device_status)


@pytest.fixture
def control_client(flask_client, tmp_path):
    state.REQUIRE_API_KEY = True
    state.API_KEY = "test-key"
    state.config_internal = {"system_state_path": str(tmp_path / "snapshot.json")}
    state.conn = MagicMock()
    return flask_client


HEADERS = {"X-API-Key": "test-key"}
CONTROL_ROUTES = ("/hmipSwitch", "/hmipDimmer", "/hmipRGB", "/hmipAlarm",
                  "/hmipThermostat", "/hmipIrrigation")


@pytest.mark.parametrize("path", CONTROL_ROUTES)
@pytest.mark.parametrize("payload", [["invalid"], "invalid", 7, None])
def test_control_requires_object(control_client, path, payload):
    response = control_client.post(path, data=json.dumps(payload), content_type="application/json", headers=HEADERS)
    assert response.status_code == 400
    state.conn.send.assert_not_called()


@pytest.mark.parametrize("value", [1.9, 1.0, True, -1, "1.9", None, "9" * 5000])
def test_channel_must_be_nonnegative_integer(control_client, value):
    response = control_client.post("/hmipSwitch", json={"device": "d", "on": True, "channelIndex": value}, headers=HEADERS)
    assert response.status_code == 400
    state.conn.send.assert_not_called()


@pytest.mark.parametrize("field,value,path", [
    ("device", ["d"], "/hmipSwitch"), ("device", " ", "/hmipSwitch"),
    ("on", 1, "/hmipSwitch"), ("dimLevel", True, "/hmipDimmer"),
    ("dimLevel", "inf", "/hmipDimmer"), ("temperature", "nan", "/hmipThermostat"),
    ("temperature", False, "/hmipThermostat"), ("group", [], "/hmipAlarm"),
    ("rgb", "R=nan%,G=0%,B=0%", "/hmipRGB"),
    ("rgb", "R=101%,G=0%,B=0%", "/hmipRGB"),
    ("rgb", "R=0%,G=0%", "/hmipRGB"),
])
def test_invalid_control_fields_never_send(control_client, field, value, path):
    payload = {"device": "d", "on": True, "group": "g", "dimLevel": 50, "temperature": 20, "rgb": "R=50%,G=0%,B=0%"}
    payload[field] = value
    assert control_client.post(path, json=payload, headers=HEADERS).status_code == 400
    state.conn.send.assert_not_called()


def test_watering_channel_uses_documented_payload(control_client):
    snapshot = {"body": {"body": {"devices": {"d": {"functionalChannels": {"1": {"wateringActive": False}}}}}}}
    with patch.object(routes, "_load_snapshot", return_value=snapshot), patch.object(routes, "_wait_for_ack", return_value={"body": {"code": 200}}):
        response = control_client.post("/hmipIrrigation", json={"device": "d", "on": True}, headers=HEADERS)
    assert response.status_code == 200
    payload = json.loads(state.conn.send.call_args.args[0])["body"]
    assert payload["path"] == "/hmip/device/control/setWateringSwitchState"
    assert payload["body"] == {"deviceId": "d", "channelIndex": 1, "wateringActive": True}


def test_live_idle_snapshot_stays_healthy(control_client):
    path = state.config_internal["system_state_path"]
    with open(path, "w") as stream:
        json.dump({"body": {"body": {"devices": {}}}}, stream)
    os.utime(path, (time.time() - 10000,) * 2)
    state.WS_READY = True
    state.adapter_registry = None
    assert control_client.get("/healthz").status_code == 200


@pytest.mark.parametrize("contents", ["invalid-json", "[]", '{"body":[]}', '{"body":{"code":200}}'])
def test_invalid_snapshot_is_unhealthy(control_client, contents):
    with open(state.config_internal["system_state_path"], "w") as stream:
        stream.write(contents)
    state.WS_READY = True
    assert control_client.get("/healthz").status_code == 503


def test_old_snapshot_is_not_ready_on_new_connection(control_client):
    state.WS_READY = False
    with patch.object(routes, "_load_snapshot", return_value={"body": {"body": {"devices": {}}}}):
        assert control_client.get("/healthz").status_code == 503


@pytest.mark.parametrize("prepare", [prepare_dashboard, prepare_heating, prepare_device_overview, prepare_device_status])
@pytest.mark.parametrize("contents", [None, "invalid-json", "[]", '{"body":[]}'])
def test_views_handle_unavailable_snapshots(tmp_path, prepare, contents):
    path = tmp_path / "state.json"
    if contents is not None:
        path.write_text(contents)
    assert isinstance(prepare(str(path)), dict)


@pytest.mark.parametrize("unreach,expected", [(False, True), (True, False), (None, True)])
def test_battery_device_uses_unreach_not_reachability(unreach, expected):
    device = HmIPAdapter._to_device("contact", {"permanentlyReachable": False,
        "functionalChannels": {"0": {"unreach": unreach}}})
    assert device.online is expected


def test_generation_specific_auth(monkeypatch):
    monkeypatch.setattr(shelly, "get_credentials", lambda: ("gen1-user", "password"))
    basic = shelly._http_auth("/status")
    digest = shelly._http_auth("/rpc/Shelly.GetStatus")
    assert isinstance(basic, HTTPBasicAuth) and basic.username == "gen1-user"
    assert isinstance(digest, HTTPDigestAuth) and digest.username == "admin"
    digest.init_per_thread_state()
    digest._thread_local.chal = {"realm": "test", "nonce": "nonce", "qop": "auth", "algorithm": "SHA-256"}
    header = digest.build_digest_header("GET", "http://device/rpc/Shelly.GetStatus")
    assert 'algorithm="SHA-256"' in header and 'response="' in header


def test_rpc_unauthorized_not_retried_without_auth(monkeypatch):
    monkeypatch.setattr(shelly, "get_credentials", lambda: ("admin", "password"))
    with patch.object(shelly.requests, "get", return_value=MagicMock(status_code=401)) as get:
        assert shelly._get("192.168.1.2", "/rpc/Shelly.GetStatus", 1) is None
    get.assert_called_once()
    assert isinstance(get.call_args.kwargs["auth"], HTTPDigestAuth)


def test_gen2_discovers_sparse_switch_ids_and_metadata():
    status = {"switch:0": {"output": False}, "switch:3": {"output": True, "apower": 50, "aenergy": {"total": 2500}},
              "wifi": {"rssi": -60}, "sys": {"available_updates": {"stable": {"version": "2.0"}}}, "input:1": {"state": True}}
    with patch.object(shelly, "_get", return_value=status) as get:
        result = shelly._get_status_gen2("192.168.1.2", 1, 1)
    assert set(result["channels"]) == {"0", "3"}
    assert result["channels"]["3"]["total_kwh"] == 2.5
    assert result["rssi"] == -60 and result["new_fw"] == "2.0"
    get.assert_called_once_with("192.168.1.2", "/rpc/Shelly.GetStatus", 1)


def test_shelly_external_control_with_key_no_session(control_client):
    device = {"ip": "192.168.1.2", "gen": 2, "channels": {"3": {}}}
    with patch.object(shelly, "load_cached", return_value=[device]), patch.object(shelly, "set_relay", return_value=True) as relay, patch.object(shelly, "refresh_device"):
        response = control_client.post("/api/shelly/192.168.1.2/relay/3", json={"on": True}, headers=HEADERS)
    assert response.status_code == 200
    relay.assert_called_once_with("192.168.1.2", 2, 3, True)


def test_shelly_external_control_rejects_unknown_channel(control_client):
    with patch.object(shelly, "load_cached", return_value=[{"ip": "192.168.1.2", "channels": {"0": {}}}]), patch.object(shelly, "set_relay") as relay:
        assert control_client.post("/api/shelly/192.168.1.2/relay/3", json={"on": True}, headers=HEADERS).status_code == 404
    relay.assert_not_called()


def test_shelly_external_control_requires_key(control_client):
    with patch.object(shelly, "set_relay") as relay:
        assert control_client.post("/api/shelly/192.168.1.2/relay/0", json={"on": True}).status_code == 401
    relay.assert_not_called()


@pytest.mark.parametrize("bind_mount", [False, True])
def test_log_level_persists_and_preserves_other_settings(tmp_path, monkeypatch, bind_mount):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    path = tmp_path / "config/internal_config.yaml"
    path.write_text("log_level: info\nother: keep\n")
    if bind_mount:
        monkeypatch.setattr(transport.os, "replace", MagicMock(side_effect=OSError(errno.EBUSY, "mountpoint")))
    original_level = transport.log.level
    try:
        assert transport._apply_log_level("warning") is True
        assert "other: keep" in path.read_text() and "log_level: warning" in path.read_text()
        assert state.config_internal["log_level"] == "warning"
    finally:
        transport.log.setLevel(original_level)


def test_log_level_persistence_failure_not_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.config_internal = {"log_level": "info"}
    assert transport._apply_log_level("debug") is False
    assert state.config_internal["log_level"] == "info"
