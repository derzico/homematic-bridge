# SPDX-License-Identifier: Apache-2.0
"""Tests für Sensor-Integration: Alarm-Endpoints, Thermostat, Bewässerung, Loxone UDP."""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import app.state as state
from app.routes import bp


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


def _snap_with_smoke_detector():
    """Minimaler Snapshot mit einem Rauchmelder der ALARM_SIREN_CHANNEL hat."""
    return {
        "body": {
            "devices": {
                "SD001": {
                    "id": "SD001",
                    "label": "Rauchmelder Wohnzimmer",
                    "type": "SMOKE_DETECTOR",
                    "functionalChannels": {
                        "0": {"functionalChannelType": "DEVICE_BASE"},
                        "1": {"functionalChannelType": "SMOKE_DETECTOR_CHANNEL", "smokeDetectorAlarmType": "IDLE_OFF"},
                        "2": {"functionalChannelType": "ALARM_SIREN_CHANNEL", "opticalAlarmSignal": "NO_ALARM"},
                    },
                },
                "SWITCH001": {
                    "id": "SWITCH001",
                    "label": "Schalter",
                    "type": "PLUGGABLE_SWITCH",
                    "functionalChannels": {
                        "0": {"functionalChannelType": "DEVICE_BASE"},
                        "1": {"functionalChannelType": "SWITCH_CHANNEL", "on": False},
                    },
                },
            }
        }
    }


# ── Alarm-Endpoint ─────────────────────────────────────────────────────────────

class TestHmipAlarm:
    def test_no_websocket_returns_503(self, client):
        state.conn = None
        r = client.post("/hmipAlarm", data=json.dumps({"mode": "optical"}), headers=_headers())
        assert r.status_code == 503

    def test_invalid_mode_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipAlarm", data=json.dumps({"mode": "blinking"}), headers=_headers())
        assert r.status_code == 400
        assert "mode" in r.get_json()["error"]

    def test_invalid_signal_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipAlarm",
                        data=json.dumps({"mode": "optical", "signal": "LOUD_NOISE"}),
                        headers=_headers())
        assert r.status_code == 400
        assert "signal" in r.get_json()["error"]

    def test_no_devices_in_snapshot_returns_404(self, client):
        state.conn = MagicMock()
        empty_snap = {"body": {"devices": {}}}
        with patch("app.routes._load_snapshot", return_value=empty_snap):
            r = client.post("/hmipAlarm", data=json.dumps({"mode": "optical"}), headers=_headers())
        assert r.status_code == 404

    def test_optical_alarm_all_devices(self, client):
        state.conn = MagicMock()
        snap = _snap_with_smoke_detector()
        with patch("app.routes._load_snapshot", return_value=snap), \
             patch("app.routes.send_hmip_set_alarm_signal_optical", return_value="rid-1") as mock_optical, \
             patch("app.routes._register_pending"):
            r = client.post("/hmipAlarm",
                            data=json.dumps({"mode": "optical", "signal": "FULL_ALARM"}),
                            headers=_headers())
        assert r.status_code == 200
        body = r.get_json()
        assert body["mode"] == "optical"
        assert body["signal"] == "FULL_ALARM"
        # Nur SD001 hat ALARM_SIREN_CHANNEL
        assert len(body["sent"]) == 1
        assert body["sent"][0]["device"] == "SD001"
        mock_optical.assert_called_once_with(state.conn, "SD001", "FULL_ALARM", 2)

    def test_both_sends_optical_and_acoustic(self, client):
        state.conn = MagicMock()
        snap = _snap_with_smoke_detector()
        with patch("app.routes._load_snapshot", return_value=snap), \
             patch("app.routes.send_hmip_set_alarm_signal_optical", return_value="rid-o") as mock_o, \
             patch("app.routes.send_hmip_set_alarm_signal_acoustic", return_value="rid-a") as mock_a, \
             patch("app.routes._register_pending"):
            r = client.post("/hmipAlarm",
                            data=json.dumps({"mode": "both"}),
                            headers=_headers())
        assert r.status_code == 200
        assert len(r.get_json()["sent"]) == 2
        mock_o.assert_called_once()
        mock_a.assert_called_once()

    def test_off_sends_no_alarm_signal(self, client):
        state.conn = MagicMock()
        snap = _snap_with_smoke_detector()
        with patch("app.routes._load_snapshot", return_value=snap), \
             patch("app.routes.send_hmip_set_alarm_signal_optical", return_value="rid-o") as mock_o, \
             patch("app.routes.send_hmip_set_alarm_signal_acoustic", return_value="rid-a") as mock_a, \
             patch("app.routes._register_pending"):
            r = client.post("/hmipAlarm",
                            data=json.dumps({"mode": "off"}),
                            headers=_headers())
        assert r.status_code == 200
        mock_o.assert_called_once_with(state.conn, "SD001", "NO_ALARM", 2)
        mock_a.assert_called_once_with(state.conn, "SD001", "NO_ALARM", 2)

    def test_single_device_with_channel_override(self, client):
        state.conn = MagicMock()
        with patch("app.routes._load_snapshot", return_value=_snap_with_smoke_detector()), \
             patch("app.routes.send_hmip_set_alarm_signal_optical", return_value="rid") as mock_o, \
             patch("app.routes._register_pending"):
            r = client.post("/hmipAlarm",
                            data=json.dumps({"mode": "optical", "device": "SD001", "channelIndex": 3}),
                            headers=_headers())
        assert r.status_code == 200
        mock_o.assert_called_once_with(state.conn, "SD001", "FULL_ALARM", 3)


# ── Thermostat-Endpoint ────────────────────────────────────────────────────────

class TestHmipThermostat:
    def test_missing_device_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipThermostat",
                        data=json.dumps({"temperature": 21.0}),
                        headers=_headers())
        assert r.status_code == 400

    def test_temperature_out_of_range_returns_400(self, client):
        state.conn = MagicMock()
        r = client.post("/hmipThermostat",
                        data=json.dumps({"device": "DEV1", "temperature": 40.0}),
                        headers=_headers())
        assert r.status_code == 400

    def test_valid_request_sends_command(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_point_temperature", return_value="rid-t") as mock_t, \
             patch("app.routes._register_pending"):
            r = client.post("/hmipThermostat",
                            data=json.dumps({"device": "TRV001", "temperature": 22.5}),
                            headers=_headers())
        assert r.status_code == 200
        mock_t.assert_called_once_with(state.conn, "TRV001", 22.5, 1)

    def test_custom_channel_index(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_point_temperature", return_value="rid") as mock_t, \
             patch("app.routes._register_pending"):
            client.post("/hmipThermostat",
                        data=json.dumps({"device": "TRV001", "temperature": 20.0, "channelIndex": 2}),
                        headers=_headers())
        mock_t.assert_called_once_with(state.conn, "TRV001", 20.0, 2)


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
             patch("app.routes._register_pending"):
            r = client.post("/hmipIrrigation",
                            data=json.dumps({"device": "WHS001", "on": True}),
                            headers=_headers())
        assert r.status_code == 200
        mock_sw.assert_called_once_with(state.conn, "WHS001", True, 1)
        assert "geöffnet" in r.get_json()["status"]

    def test_close_valve(self, client):
        state.conn = MagicMock()
        with patch("app.routes.send_hmip_set_switch", return_value="rid-w") as mock_sw, \
             patch("app.routes._register_pending"):
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
