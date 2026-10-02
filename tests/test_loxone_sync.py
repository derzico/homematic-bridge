# SPDX-License-Identifier: Apache-2.0
"""Loxone sync contracts; all UDP sockets are mocked, no device/network IO."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from app import loxone_udp as udp

HOST = "loxone.invalid"
PORT = 7777


@pytest.fixture(autouse=True)
def udp_socket(monkeypatch):
    factory = MagicMock()
    sock = factory.return_value.__enter__.return_value
    monkeypatch.setattr(udp.socket, "socket", factory)
    return factory, sock


def packets(udp_socket):
    _, sock = udp_socket
    result = []
    for call in sock.sendto.call_args_list:
        data, target = call.args
        assert target == (HOST, PORT)
        assert 0 < len(data) <= 1200
        assert data.endswith(b"\r\n")
        for line in data.decode("utf-8").split("\r\n")[:-1]:
            assert line.count("@") == 1
            assert "\r" not in line and "\n" not in line
        result.append(data)
    return result


def lines(udp_socket):
    return b"".join(packets(udp_socket)).decode().splitlines()


@pytest.fixture
def snapshot():
    # Existing private installation's capabilities: BROLL/RGBW, heating,
    # sensors and security. Synthetic IDs/values; no additional device class.
    return {
        "devices": {
            "BLIND": {"id": "BLIND", "modelType": "HmIP-BROLL", "functionalChannels": {
                "0": {"unreach": False, "lowBat": False},
                "1": {"shutterLevel": 0.25, "slatsLevel": 0.5},
                "2": {"functionalChannelType": "METADATA_ONLY"},
            }},
            "RGBW": {"id": "RGBW", "modelType": "HmIP-RGBW", "functionalChannels": {
                "0": {"unreach": True},
                "1": {"on": True, "dimLevel": 0.5, "colorTemperature": 2700},
                "3": {"on": False, "hue": 100, "saturationLevel": 0.75},
            }},
        },
        "groups": {
            "HEATING": {"id": "HEATING", "type": "HEATING", "controlMode": "AUTOMATIC",
                        "actualTemperature": 20.5, "setPointTemperature": 21.0,
                        "humidity": 45, "valvePosition": 0.1, "boostMode": False},
            "ZONE": {"id": "ZONE", "type": "SECURITY_ZONE", "active": True,
                     "silent": False, "windowState": "CLOSED",
                     "zoneAssignmentIndex": "ALARM_MODE_ZONE_1", "unreach": True},
            "SIREN": {"id": "SIREN", "type": "ALARM_SWITCHING", "on": False,
                      "onTime": 180.0, "signalAcoustic": "FREQUENCY_RISING",
                      "signalOptical": "DOUBLE_FLASHING_REPEATING"},
        },
        "home": {
            "id": "HOME_PRIVATE", "connected": True, "dutyCycle": 0.125,
            "carrierSense": 0.1, "updateState": "UP_TO_DATE",
            "weather": {"temperature": 10.0, "humidity": 89, "windDirection": 275,
                        "weatherCondition": "CLOUDY_WITH_RAIN"},
            "functionalHomes": {
                "SECURITY_AND_ALARM": {
                    "solution": "SECURITY_AND_ALARM", "active": True,
                    "alarmActive": False, "safetyAlarmActive": True,
                    "intrusionAlarmActive": False, "zoneActivationDelay": 30.0,
                    "activationInProgress": True,
                    "securityZoneActivationMode": "ACTIVATION_IF_ALL_IN_VALID_STATE",
                    "securityZones": {"INTERNAL": "ZONE"},
                    "alarmEventTimestamp": 1744111671079,
                    "alarmSecurityJournalEntryType": "WATER_DETECTION",
                },
                "INDOOR_CLIMATE": {"solution": "INDOOR_CLIMATE", "active": True,
                                   "absenceType": "NOT_ABSENT", "cooling": False},
            },
        },
    }


def event_message(events):
    return {"type": "HMIP_SYSTEM_EVENT", "body": {"eventTransaction": {"events": events}}}


def test_existing_channel_wire_format_and_whitelist(udp_socket):
    udp.push_channel_state(HOST, str(PORT), "DEVICE", "1", {
        "on": True, "motionDetected": False, "dimLevel": 0.5,
        "actualTemperature": 20, "windowState": "OPEN",
        "smokeDetectorAlarmType": "PRIMARY_ALARM",
        "functionalChannelType": "PRIVATE", "label": "Private name",
        "groupIndex": 10, "arbitrary": 99,
    })
    assert b"".join(packets(udp_socket)) == (
        b"hmip_DEVICE_ch1_on@1\r\n"
        b"hmip_DEVICE_ch1_motionDetected@0\r\n"
        b"hmip_DEVICE_ch1_dimLevel@0.5\r\n"
        b"hmip_DEVICE_ch1_actualTemperature@20\r\n"
        b"hmip_DEVICE_ch1_windowState@OPEN\r\n"
        b"hmip_DEVICE_ch1_smokeDetectorAlarmType@PRIMARY_ALARM\r\n"
    )


@pytest.mark.parametrize("wrap", [
    lambda state: state,
    lambda state: {"body": state},
    lambda state: {"body": {"code": 200, "body": state}},
    lambda state: {"type": "HMIP_SYSTEM_RESPONSE", "body": {"code": 200, "body": state}},
    lambda state: {"body": {"body": {"home": dict(state["home"],
                                                   devices=state["devices"], groups=state["groups"])}}},
])
def test_full_snapshot_all_channels_groups_home_and_availability(udp_socket, snapshot, wrap):
    original = deepcopy(snapshot)
    udp.push_system_state(HOST, PORT, wrap(snapshot))
    emitted = set(lines(udp_socket))
    assert {
        "hmip_BLIND_available@1", "hmip_BLIND_ch0_available@1",
        "hmip_BLIND_ch1_shutterLevel@0.25", "hmip_BLIND_ch1_slatsLevel@0.5",
        "hmip_BLIND_ch2_available@1", "hmip_RGBW_available@0",
        "hmip_RGBW_ch1_available@0", "hmip_RGBW_ch3_on@0", "hmip_RGBW_ch3_hue@100",
        "hmip_group_HEATING_controlMode@AUTOMATIC", "hmip_group_HEATING_boostMode@0",
        "hmip_group_HEATING_available@1", "hmip_group_ZONE_available@0",
        "hmip_group_ZONE_active@1", "hmip_group_ZONE_zoneAssignmentIndex@ALARM_MODE_ZONE_1",
        "hmip_group_SIREN_signalAcoustic@FREQUENCY_RISING",
        "hmip_group_SIREN_signalOptical@DOUBLE_FLASHING_REPEATING",
        "hmip_home_connected@1", "hmip_home_dutyCycle@0.125",
        "hmip_home_weather_weatherCondition@CLOUDY_WITH_RAIN",
        "hmip_home_security_safetyAlarmActive@1", "hmip_home_security_intrusionAlarmActive@0",
        "hmip_home_security_active@1", "hmip_home_security_activationInProgress@1",
        "hmip_home_security_zoneActivationDelay@30.0",
        "hmip_home_security_securityZoneActivationMode@ACTIVATION_IF_ALL_IN_VALID_STATE",
        "hmip_home_security_zone_INTERNAL_groupId@ZONE",
        "hmip_home_security_alarmEventTimestamp@1744111671079",
        "hmip_home_climate_absenceType@NOT_ABSENT",
    } <= emitted
    assert not any("_id@" in line or "_type@" in line or "_modelType@" in line for line in emitted)
    assert snapshot == original


def test_list_containers_explicit_channel_index_and_mapping_id_fallback(udp_socket):
    udp.push_system_state(HOST, PORT, {
        "devices": [{"id": "DEV", "functionalChannels": [
            {"index": 0, "unreach": False}, {"index": 5, "actualTemperature": 19.5},
        ]}],
        "groups": {"GROUP": {"on": True}},
        "home": {"functionalHomes": [{"solution": "SECURITY_AND_ALARM", "alarmActive": True}]},
    })
    assert "hmip_DEV_ch5_actualTemperature@19.5" in lines(udp_socket)
    assert "hmip_group_GROUP_on@1" in lines(udp_socket)
    assert "hmip_home_security_alarmActive@1" in lines(udp_socket)
    udp_socket[1].reset_mock()
    udp.push_system_state(HOST, PORT, {"devices": {"DEV": {"functionalChannels": {"0": {}}}},
                                      "groups": [{"id": "GROUP", "on": False}]})
    assert set(lines(udp_socket)) == {
        "hmip_DEV_available@1", "hmip_DEV_ch0_available@1",
        "hmip_group_GROUP_on@0", "hmip_group_GROUP_available@1",
    }


@pytest.mark.parametrize("unreach,available", [(True, 0), (False, 1), (None, 1), (1, 1), ("true", 1)])
def test_device_and_group_availability_uses_boolean_unreach(udp_socket, unreach, available):
    udp.push_system_state(HOST, PORT, {
        "devices": {"DEV": {"unreach": unreach, "functionalChannels": {"0": {}, "1": {"on": True}}}},
        "groups": {"GROUP": {"unreach": unreach}},
    })
    assert f"hmip_DEV_available@{available}" in lines(udp_socket)
    assert f"hmip_DEV_ch1_available@{available}" in lines(udp_socket)
    assert f"hmip_group_GROUP_available@{available}" in lines(udp_socket)


def test_events_numeric_order_added_changed_removed_and_home(udp_socket):
    udp.push_event_devices(HOST, PORT, event_message({
        "10": {"pushEventType": "DEVICE_REMOVED", "id": "DEV"},
        "2": {"pushEventType": "DEVICE_ADDED", "device": {
            "id": "DEV", "functionalChannels": {"0": {"unreach": False}, "1": {"on": True}}}},
        "3": {"pushEventType": "DEVICE_CHANGED", "device": {
            "id": "DEV", "functionalChannels": {"0": {"unreach": True}, "1": {"on": False}}}},
        "4": {"pushEventType": "GROUP_ADDED", "group": {"id": "GROUP", "on": True}},
        "5": {"pushEventType": "GROUP_CHANGED", "group": {"id": "GROUP", "on": False}},
        "6": {"pushEventType": "GROUP_REMOVED", "id": "GROUP"},
        "7": {"pushEventType": "HOME_CHANGED", "home": {"connected": False,
            "functionalHomes": {"SECURITY_AND_ALARM": {"active": False, "safetyAlarmActive": False}}}},
        "8": {"pushEventType": "CLIENT_ADDED", "device": {"id": "IGNORED"}},
    }))
    emitted = lines(udp_socket)
    assert emitted.index("hmip_DEV_ch1_on@1") < emitted.index("hmip_DEV_ch1_on@0")
    assert emitted[-1] == "hmip_DEV_available@0"
    assert emitted.count("hmip_DEV_available@0") == 2
    assert emitted.index("hmip_group_GROUP_on@1") < emitted.index("hmip_group_GROUP_on@0")
    assert "hmip_group_GROUP_available@0" in emitted
    assert "hmip_home_connected@0" in emitted
    assert "hmip_home_security_safetyAlarmActive@0" in emitted
    assert not any("IGNORED" in line for line in emitted)


def test_removal_without_prior_snapshot_does_not_replay_values(udp_socket):
    udp.push_event_devices(HOST, PORT, event_message({
        "0": {"pushEventType": "DEVICE_REMOVED", "id": "DEV", "device": {
            "id": "DEV", "functionalChannels": {"1": {"on": True}}}},
        "1": {"pushEventType": "GROUP_REMOVED", "id": "GROUP", "group": {"on": True}},
    }))
    assert lines(udp_socket) == ["hmip_DEV_available@0", "hmip_group_GROUP_available@0"]


def test_direct_group_and_home_helpers_match_snapshot(udp_socket, snapshot):
    udp.push_group_state(HOST, PORT, "HEATING", snapshot["groups"]["HEATING"])
    udp.push_home_state(HOST, PORT, snapshot["home"])
    direct = lines(udp_socket)
    udp_socket[1].reset_mock()
    udp.push_system_state(HOST, PORT, {"groups": {"HEATING": snapshot["groups"]["HEATING"]},
                                      "home": snapshot["home"]})
    assert direct == lines(udp_socket)


@pytest.mark.parametrize("solution", ["SECURITY_AND_ALARM", "SEC_AND_ALARM"])
def test_solution_active_and_activation_policy_never_imply_armed(udp_socket, solution):
    udp.push_system_state(HOST, PORT, {
        "groups": {"ZONE": {"type": "SECURITY_ZONE", "active": False}},
        "home": {"functionalHomes": {solution: {
            "solution": solution, "active": True, "activationInProgress": False,
            "securityZoneActivationMode": "ACTIVATION_IF_ALL_IN_VALID_STATE",
            "securityZones": {"INTERNAL": "ZONE"},
        }}},
    })
    emitted = lines(udp_socket)
    assert "hmip_home_security_active@1" in emitted  # Lösung, nicht Zone
    assert "hmip_group_ZONE_active@0" in emitted    # tatsächlicher Scharfzustand
    assert "hmip_home_security_zone_INTERNAL_groupId@ZONE" in emitted
    assert not any("armed" in line.lower() or "armingstate" in line.lower() for line in emitted)


def test_home_changed_exports_zone_references_without_guessing_zone_state(udp_socket):
    udp.push_event_devices(HOST, PORT, event_message({"0": {
        "pushEventType": "HOME_CHANGED", "home": {
            "alarmActive": False, "safetyAlarmActive": True, "intrusionAlarmActive": False,
            "activationInProgress": True, "securityZones": {"EXTERNAL": "ZONE"},
        },
    }}))
    assert set(lines(udp_socket)) == {
        "hmip_home_security_alarmActive@0", "hmip_home_security_safetyAlarmActive@1",
        "hmip_home_security_intrusionAlarmActive@0", "hmip_home_security_activationInProgress@1",
        "hmip_home_security_zone_EXTERNAL_groupId@ZONE",
    }


def test_security_zone_refs_are_validated_not_arbitrary_map_exports(udp_socket):
    udp.push_home_state(HOST, PORT, {"functionalHomes": {"SEC_AND_ALARM": {
        "active": True, "securityZones": {"INTERNAL": "GROUP", "EXTERNAL": "GROUP@1\r\n",
                                           "INTERNAL\r\n": "GROUP", "OTHER": None},
        "securitySwitchingGroups": {"SIREN": "PRIVATE"},
    }}})
    assert lines(udp_socket) == ["hmip_home_security_active@1", "hmip_home_security_zone_INTERNAL_groupId@GROUP"]


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf"),
                                       None, "21.5", [], {}, 10 ** 400])
def test_non_finite_bool_and_wrong_type_numeric_are_not_transmitted(udp_socket, value):
    udp.push_channel_state(HOST, PORT, "DEV", 1, {"actualTemperature": value})
    udp.push_shelly_energy(HOST, PORT, {"source_id": "EM", "channel": 0, "power_w": value})
    udp_socket[0].assert_not_called()
    udp.push_group_state(HOST, PORT, "GROUP", {"setPointTemperature": value})
    udp.push_home_state(HOST, PORT, {"dutyCycle": value, "functionalHomes": {
        "SECURITY_AND_ALARM": {"zoneActivationDelay": value}}})
    assert lines(udp_socket) == ["hmip_group_GROUP_available@1"]


@pytest.mark.parametrize("value", [0, 1, "true", None, [], {}])
def test_wrong_type_boolean_ignored(udp_socket, value):
    udp.push_channel_state(HOST, PORT, "DEV", 0, {"on": value})
    udp.push_home_state(HOST, PORT, {"connected": value})
    udp_socket[0].assert_not_called()


@pytest.mark.parametrize("bad_id", ["", None, True, 123, {}, "DEV\r\nhmip_fake", "DEV@0", "DEV\n",
                                    "DEV\x00", "DEV name", "DEV\u2028", "D" * 201])
def test_unsafe_identifiers_are_rejected_everywhere(udp_socket, bad_id):
    udp.push_channel_state(HOST, PORT, bad_id, 0, {"on": True})
    udp.push_group_state(HOST, PORT, bad_id, {"on": True})
    udp.push_shelly_energy(HOST, PORT, {"source_id": bad_id, "channel": 0, "power_w": 10})
    udp.push_system_state(HOST, PORT, {"devices": [{"id": bad_id, "functionalChannels": {"1": {"on": True}}}]})
    udp.push_event_devices(HOST, PORT, event_message({
        "0": {"pushEventType": "DEVICE_REMOVED", "id": bad_id},
        "1": {"pushEventType": "GROUP_REMOVED", "id": bad_id},
    }))
    udp_socket[0].assert_not_called()


@pytest.mark.parametrize("bad_channel", [True, False, -1, 1.5, None, [], "1@0", "1\r\n", "-1", "١", "1" * 11])
def test_channel_index_rejects_injection_and_non_indices(udp_socket, bad_channel):
    udp.push_channel_state(HOST, PORT, "DEV", bad_channel, {"on": True})
    udp.push_shelly_energy(HOST, PORT, {"source_id": "EM", "channel": bad_channel, "power_w": 10})
    udp_socket[0].assert_not_called()


@pytest.mark.parametrize("bad_text", ["OPEN@1", "OPEN\r\nforged@1", "OPEN\n", "OPEN\x00", "OPEN\x7f",
                                      "OPEN\u2028", "OPEN\ud800", "x" * 1201, False, 1, {}])
def test_strings_cannot_inject_protocol_lines_or_break_utf8(udp_socket, bad_text):
    udp.push_channel_state(HOST, PORT, "DEV", 1, {"windowState": bad_text})
    udp.push_group_state(HOST, PORT, "GROUP", {"controlMode": bad_text, "signalOptical": bad_text})
    udp.push_home_state(HOST, PORT, {"updateState": bad_text, "functionalHomes": {
        "SECURITY_AND_ALARM": {"securityZoneActivationMode": bad_text}}})
    assert lines(udp_socket) == ["hmip_group_GROUP_available@1"]


def test_unknown_fields_and_nested_objects_are_not_exported(udp_socket):
    udp.push_channel_state(HOST, PORT, "DEV", 0, {"label": "Private", "groups": ["group"],
                                                 "on@1\r\nother": True})
    udp.push_group_state(HOST, PORT, "GROUP", {"label": "Private", "devices": ["device"],
                                             "unknown": 1, "channels": [{"on": True}]})
    udp.push_home_state(HOST, PORT, {"location": {"latitude": 1}, "pin": "1234", "unknown": True,
                                    "functionalHomes": {"UNKNOWN": {"active": True}},
                                    "weather": {"private": "name"}})
    assert lines(udp_socket) == ["hmip_group_GROUP_available@1"]


def test_shelly_callback_contract_units_zero_and_signed_power(udp_socket):
    sample = {"source_id": "shellyem-aabbcc", "channel": "1", "power_w": -25.5,
              "import_kwh": 123.75, "export_kwh": 0, "voltage": 230, "name": "Private"}
    original = deepcopy(sample)
    udp.push_shelly_energy(HOST, PORT, sample)
    assert lines(udp_socket) == [
        "shelly_shellyem-aabbcc_ch1_power_w@-25.5",
        "shelly_shellyem-aabbcc_ch1_import_kwh@123.75",
        "shelly_shellyem-aabbcc_ch1_export_kwh@0",
    ]
    assert sample == original


def test_shelly_missing_readings_do_not_reset_energy_counters(udp_socket):
    udp.push_shelly_energy(HOST, PORT, {"source_id": "EM", "channel": 0,
                                      "power_w": 0.0, "import_kwh": None})
    assert lines(udp_socket) == ["shelly_EM_ch0_power_w@0.0"]


def test_large_snapshot_chunking_preserves_every_complete_field_line(udp_socket):
    devices = {f"DEV{i}": {"functionalChannels": {str(ch): {"on": ch % 2 == 0, "dimLevel": 0.5}
                                                for ch in range(8)}} for i in range(60)}
    udp.push_system_state(HOST, PORT, {"devices": devices})
    emitted = lines(udp_socket)
    assert len(packets(udp_socket)) > 10
    assert len(emitted) == 60 * (1 + 8 * 3)
    assert len(set(emitted)) == len(emitted)
    for i in range(60):
        for ch in range(8):
            assert f"hmip_DEV{i}_ch{ch}_available@1" in emitted
            assert f"hmip_DEV{i}_ch{ch}_on@{int(ch % 2 == 0)}" in emitted


def test_utf8_byte_limit_single_channel_and_oversized_line_skip(udp_socket):
    udp.push_channel_state(HOST, PORT, "DEV", 1, {
        "windowState": "ä" * 400, "alarmSignalType": "ö" * 400,
        "smokeDetectorAlarmType": "ü" * 600, "on": True,
    })
    assert len(packets(udp_socket)) == 2
    assert lines(udp_socket) == [
        "hmip_DEV_ch1_windowState@" + "ä" * 400,
        "hmip_DEV_ch1_alarmSignalType@" + "ö" * 400,
        "hmip_DEV_ch1_on@1",
    ]


def test_exact_datagram_boundary():
    first = "a@" + "x" * 1196 + "\r\n"
    assert len(first.encode()) == 1200
    assert list(udp._pack_lines([first, "b@1\r\n"])) == [first.encode(), b"b@1\r\n"]


@pytest.mark.parametrize("bad", [None, [], "bad", {}, {"body": None}, {"body": []},
                                   {"body": {"code": 500, "body": {"devices": {"DEV": {}}}}},
                                   {"type": "HMIP_SYSTEM_EVENT", "devices": {"DEV": {}}}])
def test_invalid_snapshots_are_noops(udp_socket, bad):
    udp.push_system_state(HOST, PORT, bad)
    udp_socket[0].assert_not_called()


@pytest.mark.parametrize("bad", [None, [], "bad", {}, {"body": []}, {"body": {"eventTransaction": []}},
                                   {"body": {"eventTransaction": {"events": []}}},
                                   {"type": "HMIP_SYSTEM_RESPONSE", "body": {}}])
def test_invalid_events_are_noops(udp_socket, bad):
    udp.push_event_devices(HOST, PORT, bad)
    udp_socket[0].assert_not_called()


def test_malformed_objects_do_not_stop_valid_siblings(udp_socket):
    udp.push_system_state(HOST, PORT, {
        "devices": {"BAD": [], "DEV": {"functionalChannels": {"0": [], "1": {"on": True}}}},
        "groups": [None, "bad", {"id": "GROUP", "on": False}],
        "home": {"functionalHomes": {"BAD": None, "SECURITY_AND_ALARM": {"alarmActive": False}}},
    })
    assert "hmip_DEV_ch1_on@1" in lines(udp_socket)
    assert "hmip_group_GROUP_on@0" in lines(udp_socket)
    udp.push_event_devices(HOST, PORT, event_message({
        "0": None, "1": {"pushEventType": "GROUP_CHANGED", "group": []},
        "2": {"pushEventType": "DEVICE_CHANGED", "device": []},
        "3": {"pushEventType": "HOME_CHANGED", "home": []},
        "4": {"pushEventType": "GROUP_ADDED", "group": {"id": "VALID", "on": True}},
    }))
    assert "hmip_group_VALID_on@1" in lines(udp_socket)


def test_disabled_host_never_creates_socket(udp_socket, snapshot):
    udp.push_system_state("", PORT, snapshot)
    udp.push_channel_state("", PORT, "DEV", 1, {"on": True})
    udp.push_group_state("", PORT, "GROUP", {"on": True})
    udp.push_home_state("", PORT, snapshot["home"])
    udp.push_event_devices("", PORT, event_message({"0": {"pushEventType": "DEVICE_REMOVED", "id": "DEV"}}))
    udp.push_shelly_energy("", PORT, {"source_id": "EM", "channel": 0, "power_w": 1})
    udp_socket[0].assert_not_called()


@pytest.mark.parametrize("failure", ["create", "send", "later_packet", "port"])
def test_udp_errors_are_best_effort_and_do_not_escape_callback(udp_socket, caplog, failure):
    factory, sock = udp_socket
    if failure == "create":
        factory.side_effect = OSError("offline")
    elif failure == "send":
        sock.sendto.side_effect = OSError("offline")
    elif failure == "later_packet":
        sock.sendto.side_effect = [1, OSError("offline")]
    state = {"devices": {f"D{i}": {"functionalChannels": {"1": {"on": True}}} for i in range(100)}}
    udp.push_system_state(HOST, "invalid" if failure == "port" else PORT, state)
    assert "UDP Push zu Loxone fehlgeschlagen" in caplog.text
    if failure != "create":
        factory.return_value.__exit__.assert_called_once()
