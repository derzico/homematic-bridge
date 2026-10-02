# SPDX-License-Identifier: Apache-2.0
"""Tests für Sensor-Integration: Alarm-Endpoints, Thermostat, Bewässerung, Loxone UDP."""

import json
from unittest.mock import ANY, MagicMock, patch

import pytest
from flask import Flask

import app.state as state
from app.routes import bp

_ACK_OK = {"body": {"code": 200}}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = Flask(__name__, template_folder="../templates")
    app.secret_key = "test"
    app.register_blueprint(bp)
    state.REQUIRE_API_KEY = True
    state.API_KEY = "test-key"
    with app.test_client() as c:
        with app.app_context():
            yield c


def _headers():
    return {"X-API-Key": "test-key", "Content-Type": "application/json"}


# ── Alarm-Endpoint ─────────────────────────────────────────────────────────────

# ── Thermostat-Endpoint ────────────────────────────────────────────────────────

class TestHmipAlarm:
    def test_explicit_group_uses_documented_group_endpoint(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_group_state", return_value="rid-a") as sender, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            response = client.post(
                "/hmipAlarm", json={"group": "SIREN1", "on": True}, headers=_headers()
            )
        assert response.status_code == 200
        assert response.get_json()["status"] == "on"
        sender.assert_called_once_with(state.conn, "SIREN1", True, request_id=ANY)

    def test_legacy_off_mode_stops_siren(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_group_state", return_value="rid-a") as sender, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            response = client.post(
                "/hmipAlarm", json={"group": "SIREN1", "mode": "off"}, headers=_headers()
            )
        assert response.status_code == 200
        sender.assert_called_once_with(state.conn, "SIREN1", False, request_id=ANY)

    def test_finds_siren_group_in_hcu_snapshot(self, client):
        state.conn = MagicMock()
        snapshot = {"body": {"body": {
            "groups": {"SIREN1": {"id": "SIREN1", "type": "ALARM_SWITCHING"}},
            "home": {},
        }}}
        with patch("app.routes._load_snapshot", return_value=snapshot), \
             patch("app.routes.send_hmip_set_group_state", return_value="rid-a") as sender, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            response = client.post("/hmipAlarm", json={"on": True}, headers=_headers())
        assert response.status_code == 200
        sender.assert_called_once_with(state.conn, "SIREN1", True, request_id=ANY)

    def test_message_uses_documented_group_state_shape(self):
        from app.adapters.hmip_messages import send_hmip_set_group_state

        connection = MagicMock()
        send_hmip_set_group_state(connection, "SIREN1", True, request_id="request-1")
        payload = json.loads(connection.send.call_args.args[0])
        assert payload["body"] == {
            "path": "/hmip/group/switching/setState",
            "body": {"on": True, "groupId": "SIREN1"},
        }


class TestHmipThermostat:
    def test_missing_group_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipThermostat",
                        data=json.dumps({"temperature": 21.0}),
                        headers=_headers())
        assert r.status_code == 400

    def test_non_finite_temperature_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipThermostat",
                        data=json.dumps({"group": "HEATING1", "temperature": float("nan")}),
                        headers=_headers())
        assert r.status_code == 400

    def test_valid_request_sends_command(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_point_temperature", return_value="rid-t") as mock_t, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            r = client.post("/hmipThermostat",
                            data=json.dumps({"group": "HEATING1", "temperature": 22.5}),
                            headers=_headers())
        assert r.status_code == 200
        mock_t.assert_called_once_with(
            state.conn, "HEATING1", 22.5, request_id=ANY
        )

    def test_uses_documented_heating_group_shape(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_point_temperature", return_value="rid") as mock_t, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            client.post("/hmipThermostat",
                        data=json.dumps({"group": "HEATING1", "temperature": 20.0}),
                        headers=_headers())
        mock_t.assert_called_once_with(
            state.conn, "HEATING1", 20.0, request_id=ANY
        )

    def test_message_uses_documented_group_path_and_body(self):
        from app.adapters.hmip_messages import send_hmip_set_point_temperature

        connection = MagicMock()
        send_hmip_set_point_temperature(
            connection, "HEATING1", 20.5, request_id="request-1"
        )
        payload = json.loads(connection.send.call_args.args[0])
        assert payload["body"] == {
            "path": "/hmip/group/heating/setSetPointTemperature",
            "body": {"setPointTemperature": 20.5, "groupId": "HEATING1"},
        }


class TestHmipRgb:
    def test_hue_is_wrapped_to_documented_range(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_hue_saturation_dim_level", return_value="rid") as sender, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            response = client.post(
                "/hmipRGB",
                json={"device": "RGB1", "rgb": "R=100%,G=0%,B=0.1%"},
                headers=_headers(),
            )
        assert response.status_code == 200
        hue = sender.call_args.args[2]
        assert 0 <= hue <= 359


# ── Bewässerungs-Endpoint ─────────────────────────────────────────────────────

class TestHmipIrrigation:
    def test_missing_on_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipIrrigation",
                        data=json.dumps({"device": "WHS001"}),
                        headers=_headers())
        assert r.status_code == 400

    def test_open_valve(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_switch", return_value="rid-w") as mock_sw, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            r = client.post("/hmipIrrigation",
                            data=json.dumps({"device": "WHS001", "on": True}),
                            headers=_headers())
        assert r.status_code == 200
        mock_sw.assert_called_once_with(
            state.conn, "WHS001", True, 1, request_id=ANY
        )
        assert "geöffnet" in r.get_json()["status"]

    def test_close_valve(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_switch", return_value="rid-w") as mock_sw, \
             patch("app.routes._wait_for_ack", return_value=_ACK_OK):
            r = client.post("/hmipIrrigation",
                            data=json.dumps({"device": "WHS001", "on": False}),
                            headers=_headers())
        assert r.status_code == 200
        assert "geschlossen" in r.get_json()["status"]


# ── Loxone UDP Push ───────────────────────────────────────────────────────────

class TestLoxoneUdpPush:
    def test_bool_fields_encoded_as_0_1(self):
        from app.loxone_udp import push_channel_state
        import socket
        sent = []
        with patch.object(socket.socket, "sendto", lambda self, data, addr: sent.append(data)):
            push_channel_state("127.0.0.1", 7777, "DEV1", 1, {
                "motionDetected": True,
                "presenceDetected": False,
                "lowBat": True,
            })
        payload = b"".join(sent).decode()
        assert "hmip_DEV1_ch1_motionDetected@1" in payload
        assert "hmip_DEV1_ch1_presenceDetected@0" in payload
        assert "hmip_DEV1_ch1_lowBat@1" in payload

    def test_numeric_sensor_fields(self):
        from app.loxone_udp import push_channel_state
        import socket
        sent = []
        with patch.object(socket.socket, "sendto", lambda self, data, addr: sent.append(data)):
            push_channel_state("127.0.0.1", 7777, "DEV2", 1, {
                "valvePosition": 0.35,
                "setPointTemperature": 21.5,
                "actualTemperature": 19.8,
            })
        payload = b"".join(sent).decode()
        assert "hmip_DEV2_ch1_valvePosition@0.35" in payload
        assert "hmip_DEV2_ch1_setPointTemperature@21.5" in payload
        assert "hmip_DEV2_ch1_actualTemperature@19.8" in payload

    def test_string_fields_pushed_as_text(self):
        from app.loxone_udp import push_channel_state
        import socket
        sent = []
        with patch.object(socket.socket, "sendto", lambda self, data, addr: sent.append(data)):
            push_channel_state("127.0.0.1", 7777, "SD1", 1, {
                "smokeDetectorAlarmType": "PRIMARY_ALARM",
            })
        payload = b"".join(sent).decode()
        assert "hmip_SD1_ch1_smokeDetectorAlarmType@PRIMARY_ALARM" in payload

    def test_unknown_fields_not_pushed(self):
        from app.loxone_udp import push_channel_state
        import socket
        sent = []
        with patch.object(socket.socket, "sendto", lambda self, data, addr: sent.append(data)):
            push_channel_state("127.0.0.1", 7777, "DEV3", 0, {
                "functionalChannelType": "DEVICE_BASE",
                "groupIndex": 0,
            })
        assert not sent  # keine relevanten Felder → kein UDP-Paket
