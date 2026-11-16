# SPDX-License-Identifier: Apache-2.0
"""1.21: Loxone-Zusatzwerte, Filter, Vorlage, Funk-Schutz, Rückmeldung, RGB, Namen,
Nur-Lese-Key und IP-Freigabe. Synthetische Snapshots, gemockte Sockets/Sends."""

import ipaddress
import json
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest

import app.loxone_udp as udp
import app.routes as routes
import app.state as state
from app.command_guard import CommandGuard, Decision, GuardSettings, channel_of, target_of
from app.inventory import build_loxone_mapping, build_loxone_template, filter_mapping
from app.targets import loxone_rgb, resolve_targets
from config.loader import validate_config

API_KEY = "extras-test-key-not-real"
READ_KEY = "extras-read-key-not-real"


@pytest.fixture
def snapshot():
    return {"type": "HMIP_SYSTEM_RESPONSE", "body": {"code": 200, "body": {
        "devices": {
            "WIN": {"id": "WIN", "label": "Fenster Bad", "type": "SHUTTER_CONTACT", "functionalChannels": {
                "0": {"functionalChannelType": "DEVICE_BASE", "unreach": False, "lowBat": True,
                      "rssiDeviceValue": -70, "dutyCycle": False},
                "1": {"functionalChannelType": "SHUTTER_CONTACT_CHANNEL", "windowState": "TILTED"}}},
            "SMOKE": {"id": "SMOKE", "label": "Rauchmelder Flur", "type": "SMOKE_DETECTOR", "functionalChannels": {
                "0": {"unreach": True, "sabotage": True},
                "1": {"smokeDetectorAlarmType": "PRIMARY_ALARM"}}},
            "LED": {"id": "LED", "label": "LED Küche", "type": "RGBW_DIMMER", "functionalChannels": {
                "0": {"unreach": False},
                "1": {"functionalChannelType": "UNIVERSAL_LIGHT_CHANNEL", "dimLevel": 0.25,
                      "supportedOptionalFeatures": {"IOptionalFeatureDimmerState": True,
                                                    "IOptionalFeatureHueSaturationValue": True,
                                                    "IOptionalFeatureColorTemperature": True},
                      "minimalColorTemperature": 2700, "maximumColorTemperature": 6500}}},
            "SW1": {"id": "SW1", "label": "Licht", "type": "PLUGABLE_SWITCH",
                    "functionalChannels": {"1": {"on": False}}},
            "SW2": {"id": "SW2", "label": "Licht", "type": "PLUGABLE_SWITCH",
                    "functionalChannels": {"1": {"on": True}}},
        },
        "groups": {
            "R-BAD": {"id": "R-BAD", "type": "META", "label": "Bad",
                      "channels": [{"deviceId": "WIN", "channelIndex": 1}, {"deviceId": "SW1", "channelIndex": 1}]},
            "R-KUE": {"id": "R-KUE", "type": "META", "label": "Küche",
                      "channels": [{"deviceId": "LED", "channelIndex": 1}, {"deviceId": "SW2", "channelIndex": 1}]},
            "H-BAD": {"id": "H-BAD", "type": "HEATING", "label": "Heizkreis 1", "controlMode": "MANUAL",
                      "valvePosition": 0.42, "channels": [{"deviceId": "WIN", "channelIndex": 1}],
                      "minTemperature": 5, "maxTemperature": 30},
            "SIREN": {"id": "SIREN", "type": "ALARM_SWITCHING", "label": "Sirene", "on": False},
        },
        "home": {"connected": True, "dutyCycle": 7.0,
                 "weather": {"temperature": 4.5, "weatherCondition": "FOGGY"},
                 "functionalHomes": {"C": {"solution": "INDOOR_CLIMATE", "absenceType": "PERIOD"}}},
    }}}


def names(lines):
    return {line.split("@")[0]: line.rstrip("\r\n").split("@", 1)[1] for line in lines}


# ── Loxone-Zusatzwerte ────────────────────────────────────────────────────────

def test_numeric_states_percent_and_aggregates(snapshot):
    values = names(udp._system_lines(snapshot))
    assert values["hmip_WIN_ch1_windowOpen"] == "2"           # gekippt
    assert values["hmip_WIN_ch1_windowState"] == "TILTED"    # Text bleibt erhalten
    assert values["hmip_SMOKE_ch1_smokeAlarm"] == "1"
    assert values["hmip_group_H-BAD_controlModeManual"] == "1"
    assert values["hmip_group_H-BAD_valvePositionPct"] == "42.0"
    assert values["hmip_LED_ch1_dimLevelPct"] == "25.0"
    assert values["hmip_home_climate_absenceActive"] == "1"
    assert values["hmip_home_weather_weatherConditionCode"] == "12"   # FOGGY
    assert values["hmip_home_deviceCount"] == "5"
    assert values["hmip_home_unreachCount"] == "1"
    assert values["hmip_home_lowBatCount"] == "1"
    assert values["hmip_home_sabotageCount"] == "1"


@pytest.mark.parametrize("state_text,code", [("CLOSED", 0), ("OPEN", 1), ("TILTED", 2)])
def test_window_codes(state_text, code):
    assert udp._state_codes({"windowState": state_text}) == {"windowOpen": code}


def test_smoke_idle_and_options_off(snapshot):
    assert udp._state_codes({"smokeDetectorAlarmType": "IDLE_OFF"}) == {"smokeAlarm": 0}
    udp.configure({"numeric_states": False, "percent_values": False, "aggregates": False})
    values = names(udp._system_lines(snapshot))
    assert not any(n.endswith(("windowOpen", "smokeAlarm", "Pct", "Count")) for n in values)


def test_aggregates_pushed_only_when_changed(snapshot, monkeypatch):
    sent = []
    monkeypatch.setattr(udp, "_send_lines", lambda host, port, lines: sent.append(list(lines)))
    udp.push_system_state("h", 1, snapshot)
    sent.clear()
    assert udp.push_aggregates_if_changed("h", 1, snapshot) is False and sent == []
    snapshot["body"]["body"]["devices"]["WIN"]["functionalChannels"]["0"]["unreach"] = True
    assert udp.push_aggregates_if_changed("h", 1, snapshot) is True
    assert "hmip_home_unreachCount@2\r\n" in sent[0]


def test_heartbeat_and_feedback_lines(monkeypatch):
    sent = []
    monkeypatch.setattr(udp, "_send_lines", lambda host, port, lines: sent.extend(lines))
    udp.push_heartbeat("h", 1, 7, False)
    udp.push_command_feedback("h", 1, "LED", False, False, 409)
    udp.push_command_feedback("h", 1, "H-BAD", True, True, 200)
    udp.push_command_feedback("h", 1, "bad id", False, True, 200)
    assert sent == ["bridge_heartbeat@7\r\n", "bridge_hcuConnected@0\r\n",
                    "hmip_LED_lastCmdOk@0\r\n", "hmip_LED_lastCmdCode@409\r\n",
                    "hmip_group_H-BAD_lastCmdOk@1\r\n", "hmip_group_H-BAD_lastCmdCode@200\r\n"]


# ── Auswahl (loxone.filter) ───────────────────────────────────────────────────

def test_parse_variable():
    assert udp.parse_variable("hmip_WIN_ch1_windowOpen") == {
        "entity": "device", "id": "WIN", "channel": "1", "field": "windowOpen"}
    assert udp.parse_variable("hmip_group_H-BAD_on")["id"] == "H-BAD"
    assert udp.parse_variable("hmip_home_weather_temperature")["entity"] == "home"
    assert udp.parse_variable("bridge_heartbeat")["entity"] == "bridge"


def test_filter_groups_diagnostics_fields_rooms_devices(snapshot):
    udp.update_topology(snapshot)
    udp.configure({"filter": {"groups": False, "diagnostics": False}})
    assert not udp.is_sent("hmip_group_H-BAD_controlMode")
    assert not udp.is_sent("hmip_WIN_ch0_rssiDeviceValue")
    assert udp.is_sent("hmip_home_dutyCycle")            # Home-Duty-Cycle bleibt
    assert udp.is_sent("bridge_heartbeat")
    udp.configure({"filter": {"include_rooms": ["bad"]}})
    assert udp.is_sent("hmip_WIN_ch1_windowOpen") and not udp.is_sent("hmip_LED_ch1_dimLevel")
    udp.configure({"filter": {"exclude_devices": ["led küche"], "include_fields": ["dimLevel", "windowOpen"]}})
    assert not udp.is_sent("hmip_LED_ch1_dimLevel") and udp.is_sent("hmip_WIN_ch1_windowOpen")
    assert not udp.is_sent("hmip_WIN_ch1_windowState")


def test_send_lines_applies_filter(snapshot, monkeypatch):
    sock = MagicMock()
    monkeypatch.setattr(udp.socket, "socket", MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=sock), __exit__=MagicMock(return_value=False))))
    udp.configure({"filter": {"include_fields": ["windowOpen"]}})
    udp.push_system_state("loxone.invalid", 7777, snapshot)
    payload = b"".join(call.args[0] for call in sock.sendto.call_args_list).decode()
    assert payload == "hmip_WIN_ch1_windowOpen@2\r\n"


# ── Mapping und Vorlage ───────────────────────────────────────────────────────

def test_mapping_kinds_units_sent_and_filters(snapshot):
    udp.configure({"filter": {"groups": False}})
    mapping = {v["variable"]: v for v in build_loxone_mapping(snapshot)}
    assert mapping["hmip_WIN_ch1_windowOpen"]["kind"] == "number"
    assert mapping["hmip_SMOKE_ch1_smokeAlarm"]["kind"] == "bool"
    assert mapping["hmip_LED_ch1_dimLevelPct"]["unit"] == "%"
    assert mapping["hmip_home_unreachCount"]["kind"] == "number"
    assert mapping["hmip_group_H-BAD_controlModeManual"]["sent"] is False
    assert mapping["hmip_WIN_ch1_windowOpen"]["sent"] is True
    selected = filter_mapping(list(mapping.values()), room="bad", field="windowOpen,lowBat")
    assert {v["variable"] for v in selected} == {"hmip_WIN_ch1_windowOpen", "hmip_WIN_ch0_lowBat"}
    assert filter_mapping(list(mapping.values()), target="fenster bad", kind="text")[0]["field"] == "windowState"


def test_template_xml(snapshot):
    variables = filter_mapping(build_loxone_mapping(snapshot), room="bad")
    xml = build_loxone_template(variables, title="HmIP <Bad>", port=7777, address="192.0.2.28")
    root = ET.fromstring(xml.encode())
    assert root.tag == "VirtualInUdp" and root.get("Port") == "7777" and root.get("Title") == "HmIP <Bad>"
    commands = {c.get("Check"): c for c in root.iter("VirtualInUdpCmd")}
    window = commands["hmip_WIN_ch1_windowOpen@\\v"]
    assert window.get("Analog") == "true" and "Fenster Bad" in window.get("Title")
    assert commands["hmip_WIN_ch0_lowBat@\\v"].get("Analog") == "false"
    assert not any("windowState" in check for check in commands)      # Text nicht als Eingang


# ── Funk-Schutz ───────────────────────────────────────────────────────────────

def test_guard_dedupes_identical_commands():
    clock = [100.0]
    guard = CommandGuard(clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    settings = GuardSettings(dedupe_window_ms=500, min_interval_ms=0)
    args = ("LED", True, 1)
    assert guard.admit(settings, "LED", "/p", args, None) == Decision(True)
    guard.record("LED", "/p", args, "rid-1")
    assert guard.admit(settings, "LED", "/p", args, None) == Decision(False, "duplicate", "rid-1")
    clock[0] += 0.6
    assert guard.admit(settings, "LED", "/p", args, None).send is True


def test_guard_intervening_command_invalidates_duplicate():
    guard = CommandGuard(clock=lambda: 0.0, sleep=lambda _s: None)
    settings = GuardSettings(dedupe_window_ms=500, min_interval_ms=0)
    on, off = ("SW", True, 1), ("SW", False, 1)
    path = "/hmip/device/control/setSwitchState"
    for args, rid in ((on, "r1"), (off, "r2")):
        assert guard.admit(settings, "SW", path, args, None).send
        guard.record("SW", path, args, rid)
    assert guard.admit(settings, "SW", path, on, None).send          # AN → AUS → AN wird gesendet
    # Anderer Kanal desselben Geräts bleibt unabhängig
    guard.record("SW", path, ("SW", True, 2), "r3")
    assert guard.admit(settings, "SW", path, ("SW", True, 2), None).outcome == "duplicate"


def test_guard_pending_command_invalidates_before_ack():
    guard = CommandGuard(clock=lambda: 0.0, sleep=lambda _s: None)
    settings = GuardSettings(dedupe_window_ms=500, min_interval_ms=0)
    path = "/hmip/device/control/setSwitchState"
    guard.admit(settings, "SW", path, ("SW", True, 1), None)
    guard.record("SW", path, ("SW", True, 1), "r1")
    assert guard.admit(settings, "SW", path, ("SW", False, 1), None).send   # AUS noch ohne ACK
    assert guard.admit(settings, "SW", path, ("SW", True, 1), None).send    # AN darf nicht verschluckt werden


def test_guard_spaces_commands_per_target():
    clock, slept = [0.0], []
    guard = CommandGuard(clock=lambda: clock[0], sleep=slept.append)
    settings = GuardSettings(dedupe_window_ms=0, min_interval_ms=200)
    assert guard.admit(settings, "A", "/dim", ("A", 0.1, 1), None).send
    assert guard.admit(settings, "B", "/dim", ("B", 0.1, 1), None).send   # anderes Ziel: kein Warten
    assert guard.admit(settings, "A", "/dim", ("A", 0.2, 1), None).send
    assert slept == [pytest.approx(0.2)]


def test_guard_newest_command_wins_while_waiting():
    guard = CommandGuard(clock=lambda: 0.0)
    settings = GuardSettings(dedupe_window_ms=0, min_interval_ms=200)
    guard.admit(settings, "A", "/dim", ("A", 0.1, 1), None)
    # Während der Wartezeit kommt ein neuerer Dimmbefehl für denselben Kanal
    guard._sleep = lambda _s: guard.admit.__self__._latest_seq.__setitem__(("A", "/dim", "1"), 999)
    assert guard.admit(settings, "A", "/dim", ("A", 0.2, 1), None) == Decision(False, "superseded")


def test_guard_duty_cycle_blocks_all_but_alarm():
    guard = CommandGuard(clock=lambda: 0.0, sleep=lambda _s: None)
    settings = GuardSettings(duty_cycle_limit_percent=80)
    assert guard.admit(settings, "A", "/hmip/device/control/setSwitchState", ("A", True, 1), 85.0) \
        == Decision(False, "duty_cycle")
    assert guard.admit(settings, "S", "/hmip/group/switching/setState", ("S", True), 85.0).send
    assert guard.admit(settings, "A", "/hmip/device/control/setSwitchState", ("A", True, 1), 79.9).send
    assert guard.admit(GuardSettings(duty_cycle_limit_percent=0), "A", "/x", ("A",), 99.0).send


def test_guard_helpers():
    assert target_of(("/hmip/x", {"deviceId": "D", "channelIndex": 2})) == "D"
    assert target_of(("G", True)) == "G"
    assert channel_of("/hmip/device/control/setDimLevel", ("D", 0.5, 3)) == "3"
    assert channel_of("/hmip/group/switching/setState", ("G", True)) == ""


# ── Namen und RGB ─────────────────────────────────────────────────────────────

def test_resolve_device_by_label_room_and_ambiguity(snapshot):
    data = {"label": "Fenster Bad"}
    assert resolve_targets("/hmipSwitch", data, snapshot) is None and data["device"] == "WIN"
    body, status = resolve_targets("/hmipSwitch", {"label": "Licht"}, snapshot)
    assert status == 409 and {c["id"] for c in body["candidates"]} == {"SW1", "SW2"}
    data = {"label": "licht", "room": "küche"}
    assert resolve_targets("/hmipSwitch", data, snapshot) is None and data["device"] == "SW2"
    assert resolve_targets("/hmipSwitch", {"label": "Nichts"}, snapshot)[1] == 404
    assert resolve_targets("/hmipSwitch", {"label": "Licht", "room": "Keller"}, snapshot)[1] == 404
    explicit = {"device": "X", "label": "Licht"}
    assert resolve_targets("/hmipSwitch", explicit, snapshot) is None and explicit["device"] == "X"


def test_group_label_respects_room(snapshot):
    groups = snapshot["body"]["body"]["groups"]
    groups["H-KUE"] = {"id": "H-KUE", "type": "HEATING", "label": "Heizkreis 1",
                       "channels": [{"deviceId": "LED", "channelIndex": 1}]}
    assert resolve_targets("/hmipThermostat", {"groupLabel": "Heizkreis 1"}, snapshot)[1] == 409
    data = {"groupLabel": "Heizkreis 1", "room": "Küche"}
    assert resolve_targets("/hmipThermostat", data, snapshot) is None and data["group"] == "H-KUE"
    groups.pop("H-KUE")
    # Eindeutiger Name, aber anderer Raum → nicht gefunden statt falscher Gruppe
    assert resolve_targets("/hmipThermostat", {"groupLabel": "Heizkreis 1", "room": "Küche"}, snapshot)[1] == 404


def test_resolve_heating_group_by_room_and_label(snapshot):
    data = {"room": "Bad", "temperature": 21}
    assert resolve_targets("/hmipThermostat", data, snapshot) is None and data["group"] == "H-BAD"
    data = {"groupLabel": "sirene", "on": True}
    assert resolve_targets("/hmipAlarm", data, snapshot) is None and data["group"] == "SIREN"
    assert resolve_targets("/hmipThermostat", {"room": "Küche"}, snapshot)[1] == 404


@pytest.mark.parametrize("value,expected", [
    (100000000, {"rgb": (0.0, 0.0, 1.0)}), ("50100", {"rgb": (1.0, 0.5, 0.0)}),
    (0, {"rgb": (0.0, 0.0, 0.0)}), (201002700, {"brightness": 100, "kelvin": 2700}),
    (200506500, {"brightness": 50, "kelvin": 6500}),
    (101, None), (100101000, None), (-1, None), (True, None), ("1e3", None), (210002700, None),
])
def test_loxone_rgb(value, expected):
    assert loxone_rgb(value) == expected


# ── Routen ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(flask_client, tmp_path, snapshot, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(state, "config_internal", {"system_state_path": str(path)})
    monkeypatch.setattr(state, "config", {"loxone": {}})
    monkeypatch.setattr(state, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(state, "API_KEY", API_KEY)
    monkeypatch.setattr(state, "API_KEY_READONLY", READ_KEY)
    monkeypatch.setattr(state, "CONTROL_ALLOWED_NETS", [])
    monkeypatch.setattr(state, "conn", MagicMock())
    monkeypatch.setattr(state, "WS_READY", True)
    monkeypatch.setattr(state, "LOXONE_HOST", "")
    sends = []

    def fake_send(send_func, path, *args):
        sends.append((path, args))
        return f"rid-{len(sends)}", None
    monkeypatch.setattr(routes, "_send_hmip_command_raw", fake_send)
    routes._duty_cache.update(mtime=None, value=None)
    return flask_client, sends, path


def post(flask_client, path, body, key=API_KEY, **kwargs):
    return flask_client.post(path, json=body, headers={"X-API-Key": key}, **kwargs)


def test_rgb_route_accepts_loxone_number(client):
    flask_client, sends, _ = client
    response = post(flask_client, "/hmipRGB", {"label": "LED Küche", "rgb": 100000000})
    assert response.status_code == 200
    path, args = sends[0]
    assert path.endswith("setHueSaturationDimLevel") and args[:4] == ("LED", 240, 1.0, 1.0)


def test_rgb_route_lumitech_uses_color_temperature(client):
    flask_client, sends, _ = client
    response = post(flask_client, "/hmipRGB", {"device": "LED", "rgb": 200503000})
    assert response.status_code == 200
    path, args = sends[0]
    assert path.endswith("setColorTemperatureDimLevel")
    assert args[1] == {"deviceId": "LED", "channelIndex": 1, "dimLevel": 0.5, "colorTemperature": 3000}


def test_rgb_route_rejects_bad_number(client):
    flask_client, sends, _ = client
    assert post(flask_client, "/hmipRGB", {"device": "LED", "rgb": 101}).status_code == 400
    assert post(flask_client, "/hmipRGB", {"device": "LED", "rgb": True}).status_code == 400
    assert sends == []


def test_switch_by_label_and_ambiguous(client):
    flask_client, sends, _ = client
    assert post(flask_client, "/hmipSwitch", {"label": "Fenster Bad", "on": True, "channelIndex": 1}).status_code == 200
    assert sends[0][1][0] == "WIN"
    response = post(flask_client, "/hmipSwitch", {"label": "Licht", "on": True})
    assert response.status_code == 409 and len(sends) == 1


def test_heating_by_room(client):
    flask_client, sends, _ = client
    response = post(flask_client, "/hmipHeating", {"room": "Bad", "action": "temperature", "temperature": 21})
    assert response.status_code == 200
    assert sends[0][1][1]["groupId"] == "H-BAD"


def test_duplicate_command_is_not_resent(client):
    flask_client, sends, _ = client
    body = {"device": "SW1", "on": True, "channelIndex": 1}
    first = post(flask_client, "/hmipSwitch", body).get_json()
    second = post(flask_client, "/hmipSwitch", body).get_json()
    assert len(sends) == 1 and second["request_id"] == "duplicate:" + first["request_id"]


def test_duty_cycle_limit_returns_429(client, snapshot):
    flask_client, sends, path = client
    snapshot["body"]["body"]["home"]["dutyCycle"] = 95.0
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    response = post(flask_client, "/hmipSwitch", {"device": "SW1", "on": True, "channelIndex": 1})
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    assert post(flask_client, "/hmipAlarm", {"on": True}).status_code == 200
    assert [p for p, _ in sends] == ["/hmip/group/switching/setState"]


def test_command_feedback_pushed(client, monkeypatch):
    flask_client, _, _ = client
    monkeypatch.setattr(state, "LOXONE_HOST", "loxone.invalid")
    pushed = []
    monkeypatch.setattr(routes.loxone_udp, "push_command_feedback",
                        lambda *a: pushed.append(a[2:]))
    post(flask_client, "/hmipSwitch", {"device": "SW1", "on": True, "channelIndex": 1})
    post(flask_client, "/hmipLight", {"device": "SW1", "action": "dim", "dimLevel": 10})
    assert pushed == [("SW1", False, True, 200), ("SW1", False, False, 409)]


def test_read_key_reads_but_cannot_control(client):
    flask_client, sends, _ = client
    for path in ("/api/inventory", "/api/loxone/mapping", "/api/diagnostics", "/api/security"):
        assert flask_client.get(path, headers={"X-API-Key": READ_KEY}).status_code == 200, path
    assert post(flask_client, "/hmipSwitch", {"device": "SW1", "on": True}, key=READ_KEY).status_code == 401
    assert flask_client.get("/api/inventory", headers={"X-API-Key": "wrong"}).status_code == 401
    assert sends == []


def test_ip_allowlist_only_for_control(client, monkeypatch):
    flask_client, sends, _ = client
    monkeypatch.setattr(state, "CONTROL_ALLOWED_NETS", [ipaddress.ip_network("192.0.2.0/29")])
    blocked = post(flask_client, "/hmipSwitch", {"device": "SW1", "on": True},
                   environ_base={"REMOTE_ADDR": "198.51.100.7"})
    assert blocked.status_code == 403 and sends == []
    allowed = post(flask_client, "/hmipSwitch", {"device": "SW1", "on": True, "channelIndex": 1},
                   environ_base={"REMOTE_ADDR": "192.0.2.5"})
    assert allowed.status_code == 200
    read = flask_client.get("/api/inventory", headers={"X-API-Key": READ_KEY},
                            environ_base={"REMOTE_ADDR": "198.51.100.7"})
    assert read.status_code == 200


def test_mapping_paging_and_template_route(client):
    flask_client, _, _ = client
    headers = {"X-API-Key": READ_KEY}
    page = flask_client.get("/api/loxone/mapping?room=Bad&limit=2&offset=1", headers=headers).get_json()
    assert page["count"] == 2 and page["offset"] == 1 and page["total"] > 2
    response = flask_client.get("/api/loxone/template.xml?room=Bad&title=Bad", headers=headers)
    assert response.status_code == 200 and response.mimetype == "application/xml"
    assert 'filename="VIU_Bad.xml"' in response.headers["Content-Disposition"]
    assert ET.fromstring(response.data).get("Title") == "Bad"


# ── Konfiguration ─────────────────────────────────────────────────────────────

BASE = {"homematic_hcu": "h", "homematic_token": "t", "plugin_id": "p"}


def test_config_validation_new_sections():
    good = {**BASE, "loxone": {"heartbeat_interval_seconds": 60, "percent_values": False,
                               "filter": {"groups": False, "include_rooms": ["Bad"]}},
            "control_allowed_ips": ["192.168.2.10", "192.168.2.0/24"],
            "command_guard": {"dedupe_window_ms": 500, "duty_cycle_limit_percent": 80},
            "api_key_readonly": "x"}
    assert validate_config(good) == []
    bad = {**BASE, "loxone": {"heartbeat_interval_seconds": 2, "filter": {"include_rooms": "Bad"}},
           "control_allowed_ips": ["nope"], "command_guard": {"min_interval_ms": -1}}
    errors = " ".join(validate_config(bad))
    for key in ("heartbeat_interval_seconds", "include_rooms", "control_allowed_ips", "min_interval_ms"):
        assert key in errors
