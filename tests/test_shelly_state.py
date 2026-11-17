# SPDX-License-Identifier: Apache-2.0
"""Shelly-Relaiszustände an Loxone: Abfrage, Änderungserkennung, Mapping.
Alle HTTP-Abfragen und UDP-Sends gemockt; keine Schaltbefehle."""

import json
from unittest.mock import MagicMock

import pytest

import app.adapters.shelly_adapter as shelly_mod
import app.routes as routes
import app.state as state
from app.inventory import build_inventory, build_loxone_mapping, build_loxone_template
from app.shelly_state import ShellyStatePoller, state_lines
from config.loader import validate_config

DEVICES = [
    {"ip": "192.0.2.142", "gen": 1, "id": "SHSW-1-96866f", "name": "2OG Wohnzimmer Licht",
     "channels": {"0": {"on": False}}},
    {"ip": "192.0.2.135", "gen": 2, "id": "shellyplus1-cc7b", "name": "Pool-Waermepumpe",
     "channels": {"0": {"on": True}}},
]


def test_get_relay_states_gen1_and_gen2(monkeypatch):
    responses = {
        ("192.0.2.1", "/status"): {"relays": [{"ison": True}, {"ison": False}], "meters": []},
        ("192.0.2.2", "/rpc/Shelly.GetStatus"): {"switch:0": {"output": False}, "switch:2": {"output": True},
                                                 "sys": {}},
    }
    calls = []
    monkeypatch.setattr(shelly_mod, "_get", lambda ip, path, timeout: calls.append(path) or responses.get((ip, path)))
    assert shelly_mod.get_relay_states("192.0.2.1", 1) == {"0": True, "1": False}
    assert shelly_mod.get_relay_states("192.0.2.2", 2) == {"0": False, "2": True}
    assert shelly_mod.get_relay_states("192.0.2.9", 1) is None
    assert "/ota" not in calls                      # keine teure Update-Abfrage


def test_state_lines():
    assert list(state_lines("SHSW-1-96866f", {"0": True})) == [
        "shelly_SHSW-1-96866f_available@1\r\n", "shelly_SHSW-1-96866f_relay0_on@1\r\n"]
    assert list(state_lines("SHSW-1-96866f", None)) == ["shelly_SHSW-1-96866f_available@0\r\n"]
    assert list(state_lines("bad id", {"0": True})) == []


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(state, "config", {"loxone": {"miniserver_ip": "loxone.invalid", "udp_port": 7777},
                                          "shelly": {"enabled": True}})


def make_poller(states):
    sent = []
    poller = ShellyStatePoller(fetch=lambda ip, gen: states[ip],
                               send=lambda host, port, lines: sent.append((host, port, list(lines))),
                               devices=lambda: DEVICES)
    return poller, sent


def test_first_poll_sends_everything_then_only_changes(configured):
    states = {"192.0.2.142": {"0": False}, "192.0.2.135": {"0": True}}
    poller, sent = make_poller(states)
    poller.poll()
    assert sent[0][:2] == ("loxone.invalid", 7777)
    assert set(sent[0][2]) == {"shelly_SHSW-1-96866f_available@1\r\n", "shelly_SHSW-1-96866f_relay0_on@0\r\n",
                               "shelly_shellyplus1-cc7b_available@1\r\n", "shelly_shellyplus1-cc7b_relay0_on@1\r\n"}
    sent.clear()
    assert poller.poll() == 0 and sent == []         # nichts geändert → nichts senden
    states["192.0.2.142"] = {"0": True}               # Wandschalter
    poller.poll()
    assert sent == [("loxone.invalid", 7777, ["shelly_SHSW-1-96866f_relay0_on@1\r\n"])]


def test_unreachable_and_recovery(configured):
    states = {"192.0.2.142": {"0": False}, "192.0.2.135": {"0": True}}
    poller, sent = make_poller(states)
    poller.poll()
    sent.clear()
    states["192.0.2.135"] = None
    poller.poll()
    assert sent[-1][2] == ["shelly_shellyplus1-cc7b_available@0\r\n"]
    states["192.0.2.135"] = {"0": False}
    poller.poll()
    assert sent[-1][2] == ["shelly_shellyplus1-cc7b_available@1\r\n", "shelly_shellyplus1-cc7b_relay0_on@0\r\n"]
    assert poller.known_states()["shellyplus1-cc7b"] == {"0": False}


def test_force_full_and_single_device(configured):
    states = {"192.0.2.142": {"0": False}, "192.0.2.135": {"0": True}}
    poller, sent = make_poller(states)
    poller.poll()
    sent.clear()
    poller.poll(force_full=True)
    assert len(sent[0][2]) == 4
    sent.clear()
    states["192.0.2.142"] = {"0": True}
    poller.poll(only_ip="192.0.2.142")
    assert sent == [("loxone.invalid", 7777, ["shelly_SHSW-1-96866f_relay0_on@1\r\n"])]


def test_no_host_or_disabled_sends_nothing(monkeypatch):
    monkeypatch.setattr(state, "config", {"loxone": {}, "shelly": {"enabled": True}})
    poller, sent = make_poller({"192.0.2.142": {"0": True}, "192.0.2.135": {"0": True}})
    poller.poll()
    assert sent == []
    monkeypatch.setattr(state, "config", {"loxone": {"miniserver_ip": "x"}, "shelly": {"enabled": False}})
    assert ShellyStatePoller._settings()[2] == 0       # Polling aus, wenn Shelly deaktiviert


def test_fetch_exception_counts_as_unreachable(configured):
    def boom(ip, gen):
        raise OSError("timeout")
    sent = []
    poller = ShellyStatePoller(fetch=boom, send=lambda h, p, lines: sent.append(list(lines)),
                               devices=lambda: DEVICES[:1])
    poller.poll()
    assert sent == [["shelly_SHSW-1-96866f_available@0\r\n"]]


def test_mapping_inventory_and_template_include_shelly():
    mapping = build_loxone_mapping(None, DEVICES, {"SHSW-1-96866f": {"0": True}})
    by_name = {v["variable"]: v for v in mapping}
    relay = by_name["shelly_SHSW-1-96866f_relay0_on"]
    assert relay["value"] == "1" and relay["kind"] == "bool" and relay["entity"] == "shelly"
    assert relay["label"] == "2OG Wohnzimmer Licht" and relay["command"] == "shelly_SHSW-1-96866f_relay0_on@\\v"
    assert by_name["shelly_SHSW-1-96866f_available"]["value"] == "1"
    assert by_name["shelly_shellyplus1-cc7b_relay0_on"]["value"] == "1"        # aus Cache
    assert by_name["shelly_shellyplus1-cc7b_available"]["value"] is None       # noch nicht abgefragt
    inventory = build_inventory(None, DEVICES)
    assert "shelly_SHSW-1-96866f_relay0_on" in inventory["shelly"][0]["loxone_variables"]
    xml = build_loxone_template(mapping, title="Shelly", port=7777)
    assert 'Check="shelly_SHSW-1-96866f_relay0_on@\\v"' in xml and 'Analog="false"' in xml


def test_relay_api_reports_new_state(flask_client, monkeypatch):
    monkeypatch.setattr(state, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(state, "API_KEY", "shelly-test-key-not-real")
    monkeypatch.setattr(state, "config", {"shelly": {}})
    monkeypatch.setattr(shelly_mod, "load_cached", lambda: DEVICES)
    monkeypatch.setattr(shelly_mod, "set_relay", MagicMock(return_value=True))
    monkeypatch.setattr(shelly_mod, "refresh_device", MagicMock())
    poller = MagicMock()
    monkeypatch.setattr(state, "runtime_services", MagicMock(shelly_states=poller))
    response = flask_client.post("/api/shelly/192.0.2.142/relay/0", json={"on": True},
                                 headers={"X-API-Key": "shelly-test-key-not-real"})
    assert response.status_code == 200
    poller.poll.assert_called_once_with(only_ip="192.0.2.142")


def test_mapping_route_includes_shelly(flask_client, monkeypatch, tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"body": {"devices": {}, "groups": {}}}), encoding="utf-8")
    monkeypatch.setattr(state, "config_internal", {"system_state_path": str(path)})
    monkeypatch.setattr(state, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(state, "API_KEY", "k-not-real")
    monkeypatch.setattr(shelly_mod, "load_cached", lambda: DEVICES)
    monkeypatch.setattr(state, "runtime_services", None)
    data = flask_client.get("/api/loxone/mapping?entity=shelly&target=2OG Wohnzimmer Licht",
                            headers={"X-API-Key": "k-not-real"}).get_json()
    assert {v["variable"] for v in data["variables"]} == {"shelly_SHSW-1-96866f_available",
                                                          "shelly_SHSW-1-96866f_relay0_on"}
    _ = routes  # Blueprint geladen


@pytest.mark.parametrize("value,ok", [(10, True), (0, True), (1, False), (3601, False), (5.5, False)])
def test_poll_interval_validation(value, ok):
    config = {"homematic_hcu": "h", "homematic_token": "t", "plugin_id": "p",
              "shelly": {"state_poll_seconds": value}}
    assert (validate_config(config) == []) is ok
