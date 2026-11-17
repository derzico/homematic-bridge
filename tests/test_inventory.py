# SPDX-License-Identifier: Apache-2.0
"""Read-only Inventar- und Loxone-Mapping-API mit synthetischem Snapshot."""

import json
from unittest.mock import Mock

import pytest

import app.adapters.shelly_adapter as shelly_mod
import app.state as state
from app.inventory import build_inventory, build_loxone_mapping
from app.loxone_udp import _system_lines

API_KEY = "inventory-test-key-not-a-real-secret"


@pytest.fixture
def snapshot():
    return {"type": "HMIP_SYSTEM_RESPONSE", "body": {"code": 200, "body": {
        "devices": {
            "dev-a": {"id": "dev-a", "label": "Licht Küche", "type": "PLUGABLE_SWITCH",
                      "modelType": "HmIP-PS", "firmwareVersion": "1.0",
                      "authToken": "must-not-be-exposed",
                      "functionalChannels": {
                          "0": {"functionalChannelType": "DEVICE_BASE", "unreach": False,
                                "rssiDeviceValue": -60, "secret": "must-not-be-exposed"},
                          "1": {"functionalChannelType": "SWITCH_CHANNEL", "label": "Decke",
                                "on": True, "nested": {"x": "must-not-be-exposed"}},
                      }},
            "dev-b": {"id": "dev-b", "label": "Fenster Bad", "type": "SHUTTER_CONTACT",
                      "functionalChannels": [
                          {"index": 0, "functionalChannelType": "DEVICE_BASE", "lowBat": False},
                          {"index": 1, "functionalChannelType": "SHUTTER_CONTACT_CHANNEL",
                           "windowState": "OPEN"},
                      ]},
        },
        "groups": {
            "room-kitchen": {"id": "room-kitchen", "type": "META", "label": "Küche",
                             "channels": [{"deviceId": "dev-a", "channelIndex": 1},
                                          {"deviceId": "dev-a", "channelIndex": 0}]},
            "heat-1": {"id": "heat-1", "type": "HEATING", "label": "Heizung Küche",
                       "setPointTemperature": 21.5, "controlMode": "AUTOMATIC",
                       "channels": [{"deviceId": "dev-a", "channelIndex": 1}]},
        },
        "home": {"connected": True, "weather": {"temperature": 7.5}},
    }}}


def test_inventory_structure_rooms_and_channels(snapshot):
    result = build_inventory(snapshot, [{"ip": "192.0.2.10", "name": "Shelly", "gen": 2,
                                         "mac": "AABB", "channels": {"0": {}, "1": {}}}])
    assert result["counts"] == {"rooms": 1, "devices": 2, "groups": 1, "shelly": 1}
    assert result["rooms"] == [{"id": "room-kitchen", "label": "Küche", "devices": ["dev-a"]}]
    dev_a = next(d for d in result["devices"] if d["id"] == "dev-a")
    assert dev_a["room"] == "Küche" and dev_a["modelType"] == "HmIP-PS"
    assert [c["index"] for c in dev_a["channels"]] == ["0", "1"]
    ch1 = dev_a["channels"][1]
    assert ch1["label"] == "Decke" and ch1["values"] == {"on": True}
    assert "hmip_dev-a_ch1_on" in ch1["loxone_variables"]
    assert dev_a["loxone_variables"] == ["hmip_dev-a_available"]
    dev_b = next(d for d in result["devices"] if d["id"] == "dev-b")
    assert dev_b["room"] is None and dev_b["channels"][1]["values"] == {"windowState": "OPEN"}
    group = result["groups"][0]
    assert group["members"] == [{"deviceId": "dev-a", "channelIndex": 1}]
    assert "hmip_group_heat-1_setPointTemperature" in group["loxone_variables"]
    assert result["shelly"] == [{"ip": "192.0.2.10", "name": "Shelly", "gen": 2, "outputs": 2,
                                "loxone_variables": []}]


def test_inventory_exposes_only_allowlisted_fields(snapshot):
    text = json.dumps(build_inventory(snapshot)) + json.dumps(build_loxone_mapping(snapshot))
    assert "must-not-be-exposed" not in text
    assert "mac" not in json.loads(json.dumps(build_inventory(snapshot, [{"mac": "AABB"}])))["shelly"][0]


def test_mapping_matches_udp_push_exactly(snapshot):
    mapping = build_loxone_mapping(snapshot)
    pushed = [line.split("@", 1)[0] for line in _system_lines(snapshot)]
    assert [m["variable"] for m in mapping] == pushed
    by_name = {m["variable"]: m for m in mapping}
    on = by_name["hmip_dev-a_ch1_on"]
    assert on == {"variable": "hmip_dev-a_ch1_on", "value": "1", "entity": "device", "id": "dev-a",
                  "label": "Licht Küche", "device_type": "PLUGABLE_SWITCH", "room": "Küche",
                  "channel": "1", "field": "on", "channel_type": "SWITCH_CHANNEL",
                  "channel_label": "Decke", "kind": "bool", "unit": None,
                  "command": "hmip_dev-a_ch1_on@\\v", "sent": True}
    assert by_name["hmip_dev-b_ch1_windowState"]["kind"] == "text"
    assert by_name["hmip_dev-b_ch1_windowState"]["command"] is None
    assert by_name["hmip_dev-b_ch1_windowState"]["channel_type"] == "SHUTTER_CONTACT_CHANNEL"
    assert by_name["hmip_dev-a_available"]["channel"] is None
    heat = by_name["hmip_group_heat-1_setPointTemperature"]
    assert heat["entity"] == "group" and heat["kind"] == "number" and heat["group_type"] == "HEATING"
    assert by_name["hmip_home_weather_temperature"]["kind"] == "number"
    assert by_name["hmip_home_connected"]["kind"] == "bool"


def test_shelly_outputs_count_list_and_missing():
    shelly = build_inventory(None, [{"ip": "a", "channels": [{}]}, {"ip": "b"}])["shelly"]
    assert [s["outputs"] for s in shelly] == [1, 0]


def test_devices_and_groups_nested_under_home(snapshot):
    system = snapshot["body"]["body"]
    home = system["home"]
    home["devices"] = system.pop("devices")
    home["groups"] = system.pop("groups")
    inventory = build_inventory(snapshot)
    assert inventory["counts"]["devices"] == 2 and inventory["counts"]["groups"] == 1
    assert inventory["rooms"][0]["label"] == "Küche"
    mapping = build_loxone_mapping(snapshot)
    assert [m["variable"] for m in mapping] == [l.split("@", 1)[0] for l in _system_lines(snapshot)]
    assert {m["entity"] for m in mapping} == {"device", "group", "home"}


def test_kind_is_entity_specific(snapshot):
    system = snapshot["body"]["body"]
    system["home"]["dutyCycle"] = 0.125
    system["devices"]["dev-a"]["functionalChannels"]["0"]["dutyCycle"] = False
    system["home"]["functionalHomes"] = {"sec": {
        "solution": "SECURITY_AND_ALARM", "alarmActive": False, "zoneActivationDelay": 30,
        "securityZones": {"INTERNAL": "heat-1"}}}
    by_name = {m["variable"]: m for m in build_loxone_mapping(snapshot)}
    assert by_name["hmip_home_dutyCycle"]["kind"] == "number"
    assert by_name["hmip_dev-a_ch0_dutyCycle"]["kind"] == "bool"
    assert by_name["hmip_home_security_alarmActive"]["kind"] == "bool"
    assert by_name["hmip_home_security_zoneActivationDelay"]["kind"] == "number"
    assert by_name["hmip_home_security_zone_INTERNAL_groupId"]["kind"] == "text"


@pytest.mark.parametrize("snap", [None, {}, {"body": "x"},
                                  {"type": "HMIP_SYSTEM_RESPONSE", "body": {"code": 500, "body": {"devices": {"x": {}}}}}])
def test_empty_snapshot_is_safe(snap):
    assert build_inventory(snap)["counts"] == {"rooms": 0, "devices": 0, "groups": 0, "shelly": 0}
    assert build_loxone_mapping(snap) == []


@pytest.fixture
def client(flask_client, tmp_path, snapshot, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(state, "config_internal", {"system_state_path": str(path)})
    monkeypatch.setattr(state, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(state, "API_KEY", API_KEY)
    monkeypatch.setattr(state, "LOXONE_HOST", "192.0.2.1")
    monkeypatch.setattr(state, "LOXONE_UDP_PORT", 7777)
    monkeypatch.setattr(shelly_mod, "load_cached", lambda: [])
    send = Mock()
    monkeypatch.setattr(state, "conn", send)
    return flask_client, send


@pytest.mark.parametrize("path", ["/api/inventory", "/api/loxone/mapping"])
def test_endpoints_require_api_key(client, path):
    flask_client, _ = client
    assert flask_client.get(path).status_code == 401
    assert flask_client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("path", ["/api/inventory", "/api/loxone/mapping"])
def test_endpoints_are_read_only(client, path):
    flask_client, conn = client
    headers = {"X-API-Key": API_KEY}
    response = flask_client.get(path, headers=headers)
    assert response.status_code == 200 and response.get_json()["snapshot_ready"] is True
    assert flask_client.post(path, headers=headers, json={}).status_code == 405
    assert conn.method_calls == []


def test_mapping_endpoint_payload(client):
    flask_client, _ = client
    result = flask_client.get("/api/loxone/mapping", headers={"X-API-Key": API_KEY}).get_json()
    assert result["miniserver_configured"] is True and result["udp_port"] == 7777
    assert result["count"] == len(result["variables"]) > 0
