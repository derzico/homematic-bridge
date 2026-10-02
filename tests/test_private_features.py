# SPDX-License-Identifier: Apache-2.0
"""Private-feature regressions using synthetic snapshots, never live hardware."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from flask import Flask
from flask_babel import Babel
from markupsafe import escape

import app.feature_views as views
import app.routes as routes
import app.state as state
from app.controls import ControlError, light_capabilities, plan_control


API_KEY = "private-test-key-not-a-real-secret"
CSRF = "private-test-csrf"
MALICIOUS = '<script>alert("private-test")</script><img src=x onerror=alert(1)>'
DEVICE_PATH = "/hmip/device/control/"
GROUP_PATH = "/hmip/group/switching/"
FEATURES = {
    "dim": "IOptionalFeatureDimmerState",
    "rgb": "IOptionalFeatureHueSaturationValue",
    "temperature": "IOptionalFeatureColorTemperature",
    "scene": "IOptionalFeatureLightScene",
}
ENDPOINTS = (
    ("/hmipShutter", "shutter"),
    ("/hmipHeating", "heating"),
    ("/hmipLight", "light"),
    ("/control/shutter", "shutter"),
    ("/control/heating", "heating"),
    ("/control/light", "light"),
)


@pytest.fixture
def private_snapshot():
    def rgb_channel(*capabilities):
        return {
            "functionalChannelType": "UNIVERSAL_LIGHT_CHANNEL",
            "channelRole": "RGB",
            "channelActive": False,
            "supportedOptionalFeatures": {FEATURES[key]: True for key in capabilities},
            "dimLevel": 0.4,
            "hue": 120,
            "saturationLevel": 0.75,
        }

    devices = {
        "fixture-shutter": {
            "id": "fixture-shutter", "modelType": "HmIP-BROLL", "label": "Synthetic shutter",
            "functionalChannels": {
                "0": {"functionalChannelType": "DEVICE_BASE", "unreach": False},
                "1": {"functionalChannelType": "SHUTTER_CHANNEL", "shutterLevel": 0.25},
                "2": {"functionalChannelType": "KEY_CHANNEL"},
            },
        },
        "fixture-rgb-a": {
            "id": "fixture-rgb-a", "modelType": "HmIP-RGBW", "label": "Synthetic RGB A",
            "functionalChannels": {"1": rgb_channel("dim", "rgb", "scene")},
        },
        "fixture-rgb-b": {
            "id": "fixture-rgb-b", "modelType": "HmIP-RGBW", "label": "Synthetic RGB B",
            "functionalChannels": {"1": rgb_channel("dim", "rgb", "scene")},
        },
        "fixture-cct": {
            "id": "fixture-cct", "modelType": "HmIP-RGBW", "label": "Synthetic CCT",
            "functionalChannels": {"1": {
                **rgb_channel("dim", "temperature"), "channelRole": "TUNABLE_WHITE",
                "minimalColorTemperature": 2700, "maximumColorTemperature": 6500,
                "colorTemperature": 4000,
            }},
        },
        "fixture-unconfigured": {
            "id": "fixture-unconfigured", "modelType": "HmIP-RGBW", "label": "Unconfigured",
            "functionalChannels": {"1": {
                "functionalChannelType": "UNIVERSAL_LIGHT_CHANNEL", "channelRole": None,
                "channelActive": False, "dimLevel": 0.8, "hue": 60,
                "colorTemperature": 4000,
            }},
        },
        "fixture-sensor": {
            "id": "fixture-sensor", "label": "Synthetic contact/presence/smoke",
            "functionalChannels": {
                "0": {"lowBat": True, "unreach": False, "sabotage": False,
                      "privateToken": "must-not-be-exposed"},
                "1": {"windowState": "OPEN", "presenceDetected": True,
                      "motionDetected": False, "smokeDetectorAlarmType": "IDLE",
                      "smokeDetectorActive": False, "arbitraryField": "must-not-be-exposed"},
            },
        },
    }
    members = [{"deviceId": name, "channelIndex": 1}
               for name in ("fixture-rgb-a", "fixture-rgb-b")]
    groups = {
        "fixture-heating": {
            "id": "fixture-heating", "type": "HEATING", "label": "Synthetic heating",
            "controllable": True, "boostAllowed": True, "minTemperature": 8,
            "maxTemperature": 26, "actualTemperature": 20, "setPointTemperature": 21,
            "controlMode": "AUTOMATIC", "boostMode": False,
        },
        "fixture-lights": {"id": "fixture-lights", "type": "SWITCHING",
                           "label": "Synthetic lights", "channels": members},
        "fixture-alarm": {"id": "fixture-alarm", "type": "ALARM_SWITCHING",
                          "label": "Synthetic alarm", "channels": deepcopy(members)},
        "fixture-internal": {"id": "fixture-internal", "type": "SECURITY_ZONE",
                             "label": "Synthetic internal", "active": False,
                             "windowState": "CLOSED", "motionDetected": False},
        "fixture-external": {"id": "fixture-external", "type": "SECURITY_ZONE",
                             "label": "Synthetic external", "active": False,
                             "presenceDetected": False, "unreach": False},
    }
    return {"body": {"body": {
        "devices": devices, "groups": groups,
        "home": {"weather": {}, "functionalHomes": {
            "fixture-security": {
                "solution": "SECURITY_AND_ALARM", "active": True, "alarmActive": False,
                "safetyAlarmActive": False, "intrusionAlarmActive": False,
                "activationInProgress": False,
                "securityZones": {"INTERNAL": "fixture-internal", "EXTERNAL": "fixture-external"},
            },
        }},
    }}}


def system(snapshot):
    return snapshot["body"]["body"]


def channel(snapshot, device="fixture-rgb-a"):
    return system(snapshot)["devices"][device]["functionalChannels"]["1"]


def security(snapshot):
    return system(snapshot)["home"]["functionalHomes"]["fixture-security"]


def body_for(kind):
    return {
        "shutter": {"device": "fixture-shutter", "channelIndex": 1,
                    "action": "position", "level": 25},
        "heating": {"group": "fixture-heating", "action": "temperature", "temperature": 21},
        "light": {"device": "fixture-rgb-a", "channelIndex": 1, "action": "rgb",
                  "hue": 120, "saturation": 75, "dimLevel": 40},
    }[kind]


@pytest.fixture
def private_harness(monkeypatch, tmp_path, private_snapshot):
    """Local Babel instance; mocked snapshot readers, senders, ACKs and cache."""
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
    app.config.update(TESTING=True, SECRET_KEY="private-test-session-key",
                      BABEL_DEFAULT_LOCALE="en", BABEL_TRANSLATION_DIRECTORIES=str(tmp_path))
    Babel(app, locale_selector=lambda: "en")
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(state, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(state, "API_KEY", API_KEY)
    monkeypatch.setattr(state, "config", {})
    monkeypatch.setattr(state, "config_internal", {"system_state_path": str(tmp_path / "synthetic.json")})
    connection = Mock(name="never-live-hardware")
    connection.send.side_effect = AssertionError("No test may send to hardware")
    monkeypatch.setattr(state, "conn", connection)
    monkeypatch.setattr(state, "WS_READY", True)
    load = Mock(return_value=private_snapshot)
    monkeypatch.setattr(routes, "_load_snapshot", load)
    monkeypatch.setattr(views, "_load_view_snapshot", Mock(return_value=private_snapshot))
    monkeypatch.setattr("app.view_helpers._load_view_snapshot", Mock(return_value=private_snapshot))
    monkeypatch.setattr("app.adapters.shelly_adapter.load_cached", Mock(return_value=[]))
    send = Mock(name="mocked-send", side_effect=["fixture-request-1", "fixture-request-2", "fixture-request-3"])
    ack = Mock(name="mocked-ack", return_value={"body": {"code": 200}})
    request_sender = Mock(name="mocked-request-sender")
    monkeypatch.setattr(routes, "_send_tracked", send)
    monkeypatch.setattr(routes, "_wait_for_ack", ack)
    monkeypatch.setattr(routes, "send_hmip_request", request_sender)
    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["csrf_token"] = CSRF
    yield SimpleNamespace(app=app, client=client, snapshot=private_snapshot, load=load,
                          send=send, ack=ack, request_sender=request_sender,
                          headers={"X-API-Key": API_KEY, "X-CSRF-Token": CSRF})
    connection.send.assert_not_called()
    request_sender.assert_not_called()


def assert_rejected(harness, response, status=400):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json and response.get_json()["error"]
    harness.send.assert_not_called()
    harness.ack.assert_not_called()


@pytest.mark.parametrize("level,expected", [(0, 0), (25, 0.25), (37.5, 0.375), (100, 1)])
def test_broll_position_is_direct_percent_to_hcu_level(private_snapshot, level, expected):
    body = {**body_for("shutter"), "level": level}
    assert plan_control("shutter", body, private_snapshot) == [
        (DEVICE_PATH + "setShutterLevel",
         {"deviceId": "fixture-shutter", "channelIndex": 1, "shutterLevel": expected})]


def test_broll_single_channel_default_and_stop(private_snapshot):
    assert plan_control("shutter", {"device": "fixture-shutter", "action": "stop"}, private_snapshot) == [
        (DEVICE_PATH + "stop", {"deviceId": "fixture-shutter", "channelIndex": 1})]


@pytest.mark.parametrize("changes,status", [
    ({"device": "fixture-rgb-a"}, 404), ({"device": "fixture-missing"}, 404),
    ({"channelIndex": 0}, 404), ({"channelIndex": 2}, 404),
    ({"channelIndex": True}, 400), ({"channelIndex": 1.0}, 400),
    ({"channelIndex": "1"}, 400), ({"channelIndex": -1}, 400),
    ({"channelIndex": float("nan")}, 400), ({"action": "open"}, 400),
    ({"level": True}, 400), ({"level": False}, 400), ({"level": -0.1}, 400),
    ({"level": 100.1}, 400), ({"level": float("nan")}, 400),
    ({"level": float("inf")}, 400), ({"level": "50"}, 400), ({"level": None}, 400),
])
def test_shutter_rejects_wrong_devices_channels_and_values(private_harness, changes, status):
    response = private_harness.client.post("/hmipShutter", json={**body_for("shutter"), **changes},
                                           headers=private_harness.headers)
    assert_rejected(private_harness, response, status)


def test_shutter_functional_type_gate_does_not_infer_capability_from_model(private_harness):
    channel(private_harness.snapshot, "fixture-shutter")["functionalChannelType"] = "KEY_CHANNEL"
    response = private_harness.client.post("/hmipShutter", json=body_for("shutter"),
                                           headers=private_harness.headers)
    assert_rejected(private_harness, response, 404)


def test_shutter_multiple_channels_requires_explicit_channel(private_snapshot):
    channels = system(private_snapshot)["devices"]["fixture-shutter"]["functionalChannels"]
    channels["3"] = deepcopy(channels["1"])
    with pytest.raises(ControlError):
        plan_control("shutter", {"device": "fixture-shutter", "action": "stop"}, private_snapshot)
    assert plan_control("shutter", {"device": "fixture-shutter", "channelIndex": 3, "action": "stop"},
                        private_snapshot)[0][1]["channelIndex"] == 3


@pytest.mark.parametrize("temperature", [8, 21.5, 26])
def test_heating_temperature_respects_group_bounds(private_snapshot, temperature):
    assert plan_control("heating", {**body_for("heating"), "temperature": temperature}, private_snapshot) == [
        ("/hmip/group/heating/setSetPointTemperature",
         {"groupId": "fixture-heating", "setPointTemperature": temperature})]


@pytest.mark.parametrize("temperature", [7.9, 26.1, True, False, float("nan"), float("inf"), "21", None])
def test_heating_rejects_invalid_temperature_without_send(private_harness, temperature):
    response = private_harness.client.post("/hmipHeating", headers=private_harness.headers,
                                           json={**body_for("heating"), "temperature": temperature})
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("mode", ["AUTOMATIC", "MANUAL"])
def test_heating_auto_manual_wire_modes(private_snapshot, mode):
    assert plan_control("heating", {"group": "fixture-heating", "action": "mode", "mode": mode},
                        private_snapshot) == [("/hmip/group/heating/setControlMode",
                                              {"groupId": "fixture-heating", "controlMode": mode})]


@pytest.mark.parametrize("mode", ["BOOST", "AUTO", "automatic", True, None, 1])
def test_heating_rejects_non_protocol_modes(private_harness, mode):
    response = private_harness.client.post("/hmipHeating", headers=private_harness.headers,
                                           json={"group": "fixture-heating", "action": "mode", "mode": mode})
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("boost", [True, False])
def test_heating_boost_is_a_boolean_command(private_snapshot, boost):
    assert plan_control("heating", {"group": "fixture-heating", "action": "boost", "boost": boost},
                        private_snapshot) == [("/hmip/group/heating/setBoost",
                                              {"groupId": "fixture-heating", "boost": boost})]


@pytest.mark.parametrize("boost", [0, 1, "true", "false", None, []])
def test_heating_boost_rejects_boolean_coercion(private_harness, boost):
    response = private_harness.client.post("/hmipHeating", headers=private_harness.headers,
                                           json={"group": "fixture-heating", "action": "boost", "boost": boost})
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("mutation,body,status", [
    ({"type": "SWITCHING"}, body_for("heating"), 404),
    ({"controllable": False}, body_for("heating"), 409),
    ({"boostAllowed": False}, {"group": "fixture-heating", "action": "boost", "boost": True}, 409),
    ({"boostAllowed": False}, {"group": "fixture-heating", "action": "boost", "boost": False}, 409),
    ({}, {"group": "fixture-missing", "action": "temperature", "temperature": 21}, 404),
])
def test_heating_group_type_controllability_and_boost_gate(private_harness, mutation, body, status):
    system(private_harness.snapshot)["groups"]["fixture-heating"].update(mutation)
    response = private_harness.client.post("/hmipHeating", json=body, headers=private_harness.headers)
    assert_rejected(private_harness, response, status)


def test_rgbw_hsv_feature_is_configured_even_when_channel_inactive(private_snapshot):
    assert channel(private_snapshot)["channelActive"] is False
    assert light_capabilities(channel(private_snapshot)) == {"dim", "rgb", "scene"}
    assert plan_control("light", body_for("light"), private_snapshot) == [
        (DEVICE_PATH + "setHueSaturationDimLevel", {"deviceId": "fixture-rgb-a", "channelIndex": 1,
                                                  "hue": 120, "saturationLevel": 0.75, "dimLevel": 0.4})]


@pytest.mark.parametrize("features", [None, {}, [], {FEATURES["rgb"]: False},
                                      {FEATURES["rgb"]: 1}, {"IOptionalFeatureHueSaturation": True}])
def test_rgbw_requires_exact_positive_optional_feature(private_harness, features):
    channel(private_harness.snapshot)["supportedOptionalFeatures"] = features
    response = private_harness.client.post("/hmipLight", json=body_for("light"), headers=private_harness.headers)
    assert_rejected(private_harness, response, 409)


@pytest.mark.parametrize("action", ["dim", "rgb", "temperature", "scene"])
def test_rgbw_null_role_missing_features_never_implies_capability(private_harness, action):
    body = {**body_for("light"), "device": "fixture-unconfigured", "action": action,
            "colorTemperature": 4000, "sceneId": 1}
    response = private_harness.client.post("/hmipLight", json=body, headers=private_harness.headers)
    assert_rejected(private_harness, response, 409)


def test_rgb_channel_rejects_cct_even_if_raw_temperature_exists(private_harness):
    channel(private_harness.snapshot)["colorTemperature"] = 4000
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={**body_for("light"), "action": "temperature", "colorTemperature": 4000})
    assert_rejected(private_harness, response, 409)


@pytest.mark.parametrize("kelvin", [2700, 4000, 6500])
def test_supported_cct_respects_snapshot_min_max(private_snapshot, kelvin):
    assert plan_control("light", {"device": "fixture-cct", "action": "temperature",
                                   "colorTemperature": kelvin, "dimLevel": 50}, private_snapshot) == [
        (DEVICE_PATH + "setColorTemperatureDimLevel", {"deviceId": "fixture-cct", "channelIndex": 1,
                                                      "colorTemperature": kelvin, "dimLevel": 0.5})]


@pytest.mark.parametrize("kelvin", [2699, 6501, 4000.0, True, float("nan"), "4000", None])
def test_cct_rejects_outside_device_bounds_and_non_integer(private_harness, kelvin):
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"device": "fixture-cct", "action": "temperature", "colorTemperature": kelvin})
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("scene_id", [0, 7, 2147483647])
def test_scene_uses_documented_integer_id(private_snapshot, scene_id):
    assert plan_control("light", {"device": "fixture-rgb-a", "action": "scene", "sceneId": scene_id},
                        private_snapshot) == [(DEVICE_PATH + "startLightScene",
                                              {"deviceId": "fixture-rgb-a", "channelIndex": 1,
                                               "dimLevel": 1, "id": scene_id})]


@pytest.mark.parametrize("scene_id", [True, False, -1, 1.0, "1", None, 2147483648, float("nan")])
def test_scene_id_does_not_coerce_to_integer(private_harness, scene_id):
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"device": "fixture-rgb-a", "action": "scene", "sceneId": scene_id})
    assert_rejected(private_harness, response)


def test_scene_requires_light_scene_optional_feature(private_harness):
    channel(private_harness.snapshot)["supportedOptionalFeatures"].pop(FEATURES["scene"])
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"device": "fixture-rgb-a", "action": "scene", "sceneId": 1})
    assert_rejected(private_harness, response, 409)


@pytest.mark.parametrize("field,value", [
    ("hue", True), ("hue", 120.0), ("hue", -1), ("hue", 360), ("hue", float("nan")),
    ("saturation", True), ("saturation", -1), ("saturation", 101), ("saturation", float("nan")),
    ("dimLevel", False), ("dimLevel", -1), ("dimLevel", 101), ("dimLevel", float("inf")),
    ("channelIndex", True), ("channelIndex", 1.0), ("channelIndex", "1"),
])
def test_rgb_rejects_invalid_channel_and_numeric_values(private_harness, field, value):
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={**body_for("light"), field: value})
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("action", ["dim", "rgb", "temperature", "scene"])
def test_alarm_switching_is_never_a_normal_light_group(private_harness, action):
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"group": "fixture-alarm", "action": action,
                                                 "hue": 120, "colorTemperature": 4000, "sceneId": 1})
    assert_rejected(private_harness, response, 404)


@pytest.mark.parametrize("group_type", ["SWITCHING", "LINKED_SWITCHING", "EXTENDED_LINKED_SWITCHING"])
def test_group_rgb_uses_unambiguous_device_requests_and_deduplicates(private_snapshot, group_type):
    group = system(private_snapshot)["groups"]["fixture-lights"]
    group["type"] = group_type
    group["channels"].extend([deepcopy(group["channels"][0]),
                              {"deviceId": "fixture-unconfigured", "channelIndex": 1},
                              {"deviceId": "fixture-missing", "channelIndex": 1}])
    commands = plan_control("light", {"group": "fixture-lights", "action": "rgb",
                                       "hue": 359, "saturation": 50, "dimLevel": 25}, private_snapshot)
    # Group RGB documentation has a group path but a deviceId body: do not guess a group request.
    assert commands == [(DEVICE_PATH + "setHueSaturationDimLevel",
                         {"deviceId": device, "channelIndex": 1, "hue": 359,
                          "saturationLevel": 0.5, "dimLevel": 0.25})
                        for device in ("fixture-rgb-a", "fixture-rgb-b")]


@pytest.mark.parametrize("action,extra,command,expected", [
    ("dim", {"dimLevel": 50}, "setDimLevel", {"dimLevel": 0.5}),
    ("scene", {"sceneId": 4}, "startLightScene", {"dimLevel": 1, "id": 4}),
])
def test_supported_group_dim_and_scene_use_group_payload(private_snapshot, action, extra, command, expected):
    assert plan_control("light", {"group": "fixture-lights", "action": action, **extra}, private_snapshot) == [
        (GROUP_PATH + command, {"groupId": "fixture-lights", **expected})]


@pytest.mark.parametrize("body", [
    {"action": "rgb", "hue": 120},
    {**body_for("light"), "group": "fixture-lights"},
    {**body_for("light"), "device": True},
])
def test_light_requires_exactly_one_valid_target(private_harness, body):
    response = private_harness.client.post("/hmipLight", json=body, headers=private_harness.headers)
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("path,kind", ENDPOINTS)
def test_http_controls_acknowledge_exact_planned_commands(private_harness, path, kind):
    body = body_for(kind)
    command, payload = plan_control(kind, body, private_harness.snapshot)[0]
    response = private_harness.client.post(path, json=body, headers=private_harness.headers)
    assert response.status_code == 200
    assert response.get_json() == {"status": "acknowledged", "results": [
        {"request_id": "fixture-request-1", "target": payload.get("deviceId") or payload.get("groupId")}]}
    private_harness.send.assert_called_once_with(private_harness.request_sender, command, command,
                                                payload, wait_for_ack=True)
    private_harness.ack.assert_called_once_with("fixture-request-1")


@pytest.mark.parametrize("kind", ["shutter", "heating", "light"])
def test_web_controls_accept_session_and_csrf_without_api_key(private_harness, kind):
    response = private_harness.client.post("/control/" + kind, json=body_for(kind),
                                           headers={"X-CSRF-Token": CSRF})
    assert response.status_code == 200
    private_harness.send.assert_called_once()
    private_harness.ack.assert_called_once_with("fixture-request-1")


@pytest.mark.parametrize("path,kind", ENDPOINTS[:3])
def test_api_controls_accept_key_without_web_session_or_csrf(private_harness, path, kind):
    with private_harness.client.session_transaction() as session:
        session.clear()
    response = private_harness.client.post(path, json=body_for(kind), headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    private_harness.send.assert_called_once()
    private_harness.ack.assert_called_once_with("fixture-request-1")


def test_shutter_stop_http_sends_no_level(private_harness):
    response = private_harness.client.post("/hmipShutter", headers=private_harness.headers,
                                           json={"device": "fixture-shutter", "action": "stop", "channelIndex": 1})
    assert response.status_code == 200
    path = DEVICE_PATH + "stop"
    private_harness.send.assert_called_once_with(private_harness.request_sender, path, path,
                                                {"deviceId": "fixture-shutter", "channelIndex": 1},
                                                wait_for_ack=True)


@pytest.mark.parametrize("path,kind", ENDPOINTS)
def test_controls_are_post_only(private_harness, path, kind):
    assert private_harness.client.get(path, headers=private_harness.headers).status_code == 405
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


@pytest.mark.parametrize("path,kind", ENDPOINTS)
@pytest.mark.parametrize("raw", ['{"action":', "null", "[]", '"string"', "true", "42"])
def test_controls_require_well_formed_json_object(private_harness, path, kind, raw):
    response = private_harness.client.post(path, data=raw, content_type="application/json",
                                           headers=private_harness.headers)
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("path,kind", ENDPOINTS)
def test_controls_reject_missing_json_body(private_harness, path, kind):
    response = private_harness.client.post(path, headers=private_harness.headers)
    assert_rejected(private_harness, response)


@pytest.mark.parametrize("path,kind", ENDPOINTS)
@pytest.mark.parametrize("connected,ready", [(False, False), (False, True), (True, False)])
def test_controls_require_connection_and_hcu_ready(private_harness, monkeypatch, path, kind, connected, ready):
    if not connected:
        monkeypatch.setattr(state, "conn", None)
    monkeypatch.setattr(state, "WS_READY", ready)
    response = private_harness.client.post(path, json=body_for(kind), headers=private_harness.headers)
    assert_rejected(private_harness, response, 503)


@pytest.mark.parametrize("snapshot", [None, [], {}, {"body": []}, {"body": {"devices": None}}])
def test_controls_reject_unusable_snapshot_before_send(private_harness, snapshot):
    private_harness.load.return_value = snapshot
    response = private_harness.client.post("/hmipShutter", json=body_for("shutter"), headers=private_harness.headers)
    assert_rejected(private_harness, response, 503)


@pytest.mark.parametrize("path,kind", ENDPOINTS[:3])
@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_api_controls_require_api_key_even_with_web_session(private_harness, path, kind, key):
    headers = {} if key is None else {"X-API-Key": key}
    response = private_harness.client.post(path, json=body_for(kind), headers=headers)
    assert_rejected(private_harness, response, 401)


@pytest.mark.parametrize("kind", ["shutter", "heating", "light"])
def test_web_controls_require_web_session_even_with_api_key(private_harness, kind):
    with private_harness.client.session_transaction() as session:
        session.clear()
    response = private_harness.client.post("/control/" + kind, json=body_for(kind), headers=private_harness.headers)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=/control/")
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


@pytest.mark.parametrize("kind", ["shutter", "heating", "light"])
@pytest.mark.parametrize("token", [None, "wrong-csrf"])
def test_web_controls_require_csrf(private_harness, kind, token):
    headers = {"X-API-Key": API_KEY}
    if token is not None:
        headers["X-CSRF-Token"] = token
    response = private_harness.client.post("/control/" + kind, json=body_for(kind), headers=headers)
    assert response.status_code == 403
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


def test_web_controls_need_csrf_even_if_api_auth_disabled(private_harness, monkeypatch):
    monkeypatch.setattr(state, "REQUIRE_API_KEY", False)
    response = private_harness.client.post("/control/shutter", json=body_for("shutter"))
    assert response.status_code == 403
    private_harness.send.assert_not_called()


def test_unknown_web_control_kind_rejected(private_harness):
    response = private_harness.client.post("/control/arbitrary", json={}, headers=private_harness.headers)
    assert_rejected(private_harness, response, 404)


@pytest.mark.parametrize("ack,status", [(None, 504), ({"body": {"code": 400}}, 502),
                                      ({"body": {"code": 503}}, 503)])
def test_group_rgb_partial_failure_reports_completed_and_failed_target(private_harness, ack, status):
    group = system(private_harness.snapshot)["groups"]["fixture-lights"]
    devices = system(private_harness.snapshot)["devices"]
    devices["fixture-rgb-c"] = deepcopy(devices["fixture-rgb-b"])
    devices["fixture-rgb-c"]["id"] = "fixture-rgb-c"
    group["channels"].append({"deviceId": "fixture-rgb-c", "channelIndex": 1})
    private_harness.ack.side_effect = [{"body": {"code": 200}}, ack]
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"group": "fixture-lights", "action": "rgb", "hue": 120})
    assert response.status_code == status
    result = response.get_json()
    assert result["completed"] == [{"request_id": "fixture-request-1", "target": "fixture-rgb-a"}]
    assert result["failed_target"] == "fixture-rgb-b"
    assert result["error"] and result["failure"]["request_id"] == "fixture-request-2"
    if status == 502:
        assert result["failure"]["hcu_code"] == 400
    assert private_harness.send.call_count == 2  # No third target after the failure.
    assert private_harness.ack.call_args_list == [call("fixture-request-1"), call("fixture-request-2")]


def test_group_rgb_waits_for_each_ack_and_reports_all_targets(private_harness):
    events = Mock()
    events.attach_mock(private_harness.send, "send")
    events.attach_mock(private_harness.ack, "ack")
    response = private_harness.client.post("/hmipLight", headers=private_harness.headers,
                                           json={"group": "fixture-lights", "action": "rgb", "hue": 0})
    assert response.status_code == 200
    assert response.get_json()["results"] == [
        {"request_id": "fixture-request-1", "target": "fixture-rgb-a"},
        {"request_id": "fixture-request-2", "target": "fixture-rgb-b"}]
    assert [entry[0] for entry in events.mock_calls] == ["send", "ack", "send", "ack"]


def test_send_error_does_not_wait_or_claim_acknowledgement(private_harness):
    private_harness.send.side_effect = OSError("synthetic connection failure")
    response = private_harness.client.post("/hmipShutter", json=body_for("shutter"), headers=private_harness.headers)
    assert response.status_code == 503
    assert response.get_json()["completed"] == []
    assert response.get_json()["failed_target"] == "fixture-shutter"
    private_harness.ack.assert_not_called()


@pytest.mark.parametrize("solution_active", [True, False, None])
@pytest.mark.parametrize("zone_values,expected", [
    ((False, False), "disarmed"), ((True, True), "armed"),
    ((True, False), "partly_armed"), ((False, True), "partly_armed"),
    ((None, False), "unknown"), ((True, None), "unknown"), ((None, None), "unknown"),
])
def test_security_arming_comes_from_zones_not_solution_active(private_snapshot, solution_active, zone_values, expected):
    security(private_snapshot)["active"] = solution_active
    groups = system(private_snapshot)["groups"]
    groups["fixture-internal"]["active"], groups["fixture-external"]["active"] = zone_values
    result = views.security_data(private_snapshot)
    assert result["armed_state"] == expected
    assert [zone["active"] for zone in result["zones"]] == list(zone_values)


@pytest.mark.parametrize("mutation", ["missing-zone", "missing-active", "wrong-type", "no-zones", "no-solution"])
def test_security_missing_or_partial_state_is_unknown_not_disarmed(private_snapshot, mutation):
    groups = system(private_snapshot)["groups"]
    if mutation == "missing-zone":
        groups.pop("fixture-external")
    elif mutation == "missing-active":
        groups["fixture-external"].pop("active")
    elif mutation == "wrong-type":
        groups["fixture-external"].update(type="SWITCHING", active=False)
    elif mutation == "no-zones":
        security(private_snapshot).pop("securityZones")
    else:
        system(private_snapshot)["home"]["functionalHomes"].clear()
    result = views.security_data(private_snapshot)
    assert result["armed_state"] == "unknown"
    if mutation in ("missing-zone", "missing-active", "wrong-type"):
        assert result["zones"][1]["active"] is None


@pytest.mark.parametrize("value", [0, 1, "false", "true", [], {}])
def test_security_zone_active_must_be_reported_boolean(private_snapshot, value):
    system(private_snapshot)["groups"]["fixture-external"]["active"] = value
    assert views.security_data(private_snapshot)["armed_state"] == "unknown"


def test_security_alarm_and_activation_are_independent_of_arming(private_snapshot):
    security(private_snapshot).update(alarmActive=True, safetyAlarmActive=True,
                                      intrusionAlarmActive=False, activationInProgress=True)
    result = views.security_data(private_snapshot)
    assert result["armed_state"] == "disarmed"
    assert result["alarm_active"] is True
    assert result["safety_alarm_active"] is True
    assert result["intrusion_alarm_active"] is False
    assert result["activation_in_progress"] is True


def test_security_sensor_fields_are_allowlisted_and_preserve_false(private_snapshot):
    result = views.security_data(private_snapshot)
    assert result["sensors"] == [{
        "id": "fixture-sensor", "label": "Synthetic contact/presence/smoke",
        "values": {"lowBat": True, "unreach": False, "sabotage": False,
                   "windowState": "OPEN", "presenceDetected": True, "motionDetected": False,
                   "smokeDetectorAlarmType": "IDLE", "smokeDetectorActive": False},
    }]
    assert "must-not-be-exposed" not in repr(result)
    assert result["zones"][0]["presenceDetected"] is None


def test_security_missing_sensor_values_are_not_invented_as_safe(private_snapshot):
    device = system(private_snapshot)["devices"]["fixture-sensor"]
    device["functionalChannels"] = {"0": {"lowBat": False}, "1": {"windowState": None}}
    result = views.security_data(private_snapshot)
    assert result["sensors"] == [{"id": "fixture-sensor", "label": device["label"],
                                   "values": {"lowBat": False, "windowState": None}}]


@pytest.mark.parametrize("nested,collections_as_lists", [(False, False), (True, True), (False, True)])
def test_planners_and_security_support_snapshot_envelopes_and_id_lists(private_snapshot, nested, collections_as_lists):
    data = system(private_snapshot)
    if collections_as_lists:
        data["devices"] = list(data["devices"].values())
        data["groups"] = list(data["groups"].values())
    snapshot = {"body": {"body": data}} if nested else {"body": data}
    for kind in ("shutter", "heating", "light"):
        assert len(plan_control(kind, body_for(kind), snapshot)) == 1
    assert views.security_data(snapshot)["armed_state"] == "disarmed"


@pytest.mark.parametrize("snapshot", [None, {}, {"body": {"devices": {}, "groups": {}, "home": {}}}])
def test_security_empty_snapshot_reports_unknown(private_harness, snapshot):
    private_harness.load.return_value = snapshot
    response = private_harness.client.get("/api/security", headers=private_harness.headers)
    assert response.status_code == 200
    result = response.get_json()
    assert result["armed_state"] == "unknown"
    assert result["zones"] == [] and result["sensors"] == []
    assert result["alarm_active"] is None
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_security_api_requires_key_not_just_web_session(private_harness, key):
    response = private_harness.client.get("/api/security", headers={} if key is None else {"X-API-Key": key})
    assert response.status_code == 401
    private_harness.load.assert_not_called()
    private_harness.send.assert_not_called()


def test_security_api_is_read_only_and_reports_connection_readiness(private_harness, monkeypatch):
    monkeypatch.setattr(state, "conn", None)
    monkeypatch.setattr(state, "WS_READY", False)
    response = private_harness.client.get("/api/security", headers=private_harness.headers)
    assert response.status_code == 200
    result = response.get_json()
    assert result["armed_state"] == "disarmed"
    assert result["snapshot_ready"] is False and result["ws_connected"] is False
    assert private_harness.client.post("/api/security", json={}, headers=private_harness.headers).status_code == 405
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


def test_controls_view_only_offers_configured_channels_and_normal_groups(private_harness):
    result = views.prepare_controls()
    assert [(item["id"], str(item["channel"]), item["level"]) for item in result["shutters"]] == [
        ("fixture-shutter", "1", 25)]
    lights = {item["id"]: item for item in result["lights"]}
    assert set(lights) == {"fixture-rgb-a", "fixture-rgb-b", "fixture-cct"}
    assert lights["fixture-rgb-a"]["capabilities"] == {"dim", "rgb", "scene"}
    assert lights["fixture-cct"]["capabilities"] == {"dim", "temperature"}
    assert lights["fixture-cct"]["minimum_kelvin"] == 2700
    assert lights["fixture-cct"]["maximum_kelvin"] == 6500
    assert result["light_groups"] == [{"id": "fixture-lights", "label": "Synthetic lights",
                                       "capabilities": {"dim", "rgb", "scene"}}]
    private_harness.send.assert_not_called()


@pytest.mark.parametrize("path", ["/controls", "/security", "/heating"])
def test_feature_pages_render_with_local_babel_and_escape_labels(private_harness, path):
    data = system(private_harness.snapshot)
    for device in data["devices"].values():
        device["label"] = MALICIOUS
    for group in data["groups"].values():
        group["label"] = MALICIOUS
    channel(private_harness.snapshot, "fixture-sensor")["windowState"] = MALICIOUS
    response = private_harness.client.get(path, headers={"Accept-Language": "en"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert MALICIOUS not in html
    assert str(escape(MALICIOUS)) in html
    if path in ("/controls", "/heating"):
        assert "X-CSRF-Token" in html and CSRF in html
        assert 'data-control="' in html
    else:
        assert 'data-control="' not in html
        assert "/hmipAlarm" not in html
    private_harness.send.assert_not_called()
    private_harness.ack.assert_not_called()


@pytest.mark.parametrize("path", ["/controls", "/security", "/heating"])
def test_feature_pages_require_web_login(private_harness, path):
    with private_harness.client.session_transaction() as session:
        session.clear()
    response = private_harness.client.get(path, headers=private_harness.headers)
    assert response.status_code == 302
    assert response.headers["Location"] == "/login?next=" + path
    private_harness.send.assert_not_called()
